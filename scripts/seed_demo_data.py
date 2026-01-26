#!/usr/bin/env python3
# scripts/seed_demo_data.py
"""
HMS demo data seeder (2 tenants) + reset + freshen.

Design goals (per our agreed spec):
- Two demo tenants with realistic address (city/state/pincode).
- Per tenant: 9 staff users:
  - 1 HOSPITAL_ADMIN
  - 2 DOCTOR
  - 2 NURSE
  - 2 PHARMACIST
  - 2 RECEPTIONIST
- Provide 5 demo logins per tenant (one per role) with known password Demo@12345.
- All demo users are login-ready:
  - ACTIVE
  - email_verified=true
  - must_change_password=false
  - mapped role
- ~100 patients per tenant with realistic demographics + addresses.
- Patient records feel “aged”: created/visited spread across time (best-effort if columns exist).
- ~500 OPD appointments across past 120 days + next 14 days, valid workflow timestamps.
- Includes a guaranteed “today bucket” so dashboards look alive.
- ~200 IPD admissions with realistic stays (some ACTIVE).
- Prescriptions in multiple statuses with valid transitions:
    DRAFT -> ISSUED -> DISPENSED
    DRAFT -> CANCELLED
  Linked to either appointment OR admission, never both.
- Vitals:
  - OPD triage for some appointments
  - IPD vitals multiple per day (bounded; not insane)
- Stock items across categories; dispensing decrements stock (like your endpoint).
- Reset safety without adding new columns:
  - We tag rows using existing text fields with a consistent marker:
    "DEMO|<A/B>|<entity>|<id>"

Run:
  python -m scripts.seed_demo_data --seed
  python -m scripts.seed_demo_data --reset
  python -m scripts.seed_demo_data --freshen --freshen-days 7
"""
from __future__ import annotations

import argparse
import json
import logging
import random
import sys
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Generator

from sqlalchemy import String, cast, create_engine, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import NullPool

# Allow "python -m scripts.seed_demo_data" from repo root
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from app.core.security import get_password_hash  # type: ignore
from app.core.config import get_settings  # type: ignore
from app.models.tenant_global import Tenant, TenantStatus  # type: ignore
from app.models.user import User, UserStatus  # type: ignore
from app.models.user_tenant import UserTenant  # type: ignore
from app.models.department import Department  # tenant schema
from app.models.patient import Patient  # tenant schema
from app.models.appointment import Appointment, AppointmentStatus  # tenant schema
from app.models.admission import Admission, AdmissionStatus  # tenant schema
from app.models.prescription import (  # tenant schema
    Prescription,
    PrescriptionStatus,
    PrescriptionItem,
)
from app.models.stock import StockItem, StockItemType  # tenant schema
from app.models.vital import Vital  # type: ignore  # tenant schema (table name vitals)
from app.models.tenant_role import TenantRole, TenantUserRole  # tenant schema
from app.services.tenant_service import register_tenant  # type: ignore
from app.services.seed_service import ensure_tenant_minimums  # type: ignore
from app.services.tenant_metrics_service import (  # type: ignore
    increment_patients,
    increment_appointments,
    increment_prescriptions,
    increment_users,
)

logger = logging.getLogger(__name__)

DEMO_PASSWORD = "Demo@12345"
DEMO_TENANT_A_LICENSE = "DEMO-TENANT-A-011"
DEMO_TENANT_B_LICENSE = "DEMO-TENANT-B-011"
DEMO_EMAIL_TLD = "hms"

DATA_DIR = REPO_ROOT / "scripts" / "demo_data"

# ----------------------------
# Engine / Session for seeding
# ----------------------------
_seed_settings = get_settings()

def _is_pooler_url(url: str) -> bool:
    return ":6543" in url or "pooler.supabase.com" in url

# Prefer direct URL for seed/DDL if provided, else fallback to normal DATABASE_URL
_seed_db_url = _seed_settings.database_url

connect_args: dict = {}
if _is_pooler_url(_seed_db_url):
    connect_args["prepare_threshold"] = None

# NullPool is deliberate for scripts (avoid reusing connections across schema switches).
_seed_engine = create_engine(
    _seed_db_url,
    future=True,
    pool_pre_ping=True,
    connect_args=connect_args,
    poolclass=NullPool,
)

SeedSessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=_seed_engine,
    future=True,
    expire_on_commit=False,
)

# ----------------------------
# Pooler-safe schema switching
# ----------------------------
def _set_search_path_local(db: Session, path: str) -> None:
    """
    Pooler-safe way to set search_path.
    set_config(..., true) makes it LOCAL to the current transaction.
    Works reliably with Supabase transaction pooler.
    """
    db.execute(text("SELECT set_config('search_path', :p, true)"), {"p": path})


@contextmanager
def public_scope(db: Session) -> Generator[None, None, None]:
    """
    Ensure all ORM work inside uses *public* schema (transaction-local).
    """
    try:
        _set_search_path_local(db, "public")
        yield
    except Exception:
        db.rollback()
        raise


@contextmanager
def tenant_scope(db: Session, schema_name: str) -> Generator[None, None, None]:
    """
    Ensure all ORM work inside uses tenant schema (transaction-local).
    Defensive: rolls back on error to avoid "transaction aborted" cascades.
    """
    try:
        _set_search_path_local(db, f'"{schema_name}", public')
        yield
    except Exception:
        db.rollback()
        raise


def _log_db_error(e: Exception) -> None:
    """
    Emit the underlying DB error (very useful on Render).
    """
    logger.error("Seed failed: %s", e, exc_info=True)
    if isinstance(e, SQLAlchemyError) and getattr(e, "orig", None) is not None:
        logger.error("DBAPI orig: %r", e.orig)


# ----------------------------
# Helpers
# ----------------------------
def _run_public_metrics(work) -> None:
    """
    Run metric updates using a short-lived Session pinned to the public schema.
    """
    dbm: Session = SeedSessionLocal()
    try:
        with public_scope(dbm):
            work(dbm)
        dbm.commit()
    except Exception:
        dbm.rollback()
        raise
    finally:
        dbm.close()


def _column_exists(db: Session, schema: str, table: str, column: str) -> bool:
    q = text(
        """
        SELECT 1
        FROM information_schema.columns
        WHERE table_schema = :schema
          AND table_name = :table
          AND column_name = :column
        LIMIT 1
        """
    )
    return db.execute(q, {"schema": schema, "table": table, "column": column}).first() is not None


def demo_email(suffix: str, username: str) -> str:
    return f"{username}@demo-tenant-{suffix.lower()}.{DEMO_EMAIL_TLD}"


def demo_tag(suffix: str, entity: str, ident: str) -> str:
    return f"DEMO|{suffix}|{entity}|{ident}"


def load_json(name: str) -> dict[str, Any]:
    path = DATA_DIR / name
    return json.loads(path.read_text(encoding="utf-8"))


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def in_15_min_block(dt: datetime) -> datetime:
    minute = (dt.minute // 15) * 15
    return dt.replace(minute=minute, second=0, microsecond=0)


def clinic_time(now: datetime, days_offset: int) -> datetime:
    base = now + timedelta(days=days_offset)
    hour = random.randint(8, 18)
    minute = random.choice([0, 15, 30, 45])
    return in_15_min_block(base.replace(hour=hour, minute=minute))


def choose_weighted(items: list[tuple[Any, float]]) -> Any:
    r = random.random()
    upto = 0.0
    for val, w in items:
        upto += w
        if r <= upto:
            return val
    return items[-1][0]


def rand_phone(i: int) -> str:
    base = 9000000000 + (i * 137) % 900000000
    return f"+91-{base:010d}"


def rand_dob() -> date | None:
    if random.random() < 0.22:
        return None
    years = random.randint(1, 78)
    return date.today() - timedelta(days=years * 365 + random.randint(0, 364))


def tenant_prefix(tenant_id) -> str:
    return str(tenant_id).replace("-", "")[:8]


def _safe_setattr(obj: Any, field: str, value: Any) -> None:
    if hasattr(obj, field):
        setattr(obj, field, value)


@dataclass(frozen=True)
class DemoTenantSpec:
    suffix: str  # "A" / "B"
    license_number: str
    admin_email: str


# ----------------------------
# Tenant setup
# ----------------------------
def get_or_create_demo_tenant(db: Session, spec: DemoTenantSpec) -> Tenant:
    """
    Tenant row + schema/table creation are global/public concerns.
    Minimums (roles/depts/etc.) are tenant-schema concerns.
    """
    with public_scope(db):
        existing = db.query(Tenant).filter(Tenant.license_number == spec.license_number).first()

        if existing:
            if existing.status != TenantStatus.ACTIVE:
                existing.status = TenantStatus.ACTIVE
                db.commit()

            from app.services.tenant_service import ensure_tenant_tables_exist  # type: ignore
            ensure_tenant_tables_exist(db, existing.schema_name)

            with tenant_scope(db, existing.schema_name):
                ensure_tenant_minimums(db)

            db.commit()
            return existing

        loc = load_json("locations_in.json")
        city = random.choice(loc["cities"])
        street = random.choice(loc["streets"])

        tenant = register_tenant(
            db=db,
            name=f"Demo Hospital {spec.suffix}",
            address=f"Plot 12, {street}, {city['city']}, {city['state']} {city['postal_code']}",
            contact_email=spec.admin_email,
            contact_phone=rand_phone(1 if spec.suffix == "A" else 2),
            license_number=spec.license_number,
        )
        tenant.status = TenantStatus.ACTIVE
        db.flush()

        from app.services.tenant_service import ensure_tenant_tables_exist  # type: ignore
        ensure_tenant_tables_exist(db, tenant.schema_name)

        with tenant_scope(db, tenant.schema_name):
            ensure_tenant_minimums(db)

        db.commit()
        return tenant


def fetch_system_roles_and_departments(db: Session) -> tuple[dict[str, TenantRole], dict[str, Department]]:
    roles = db.query(TenantRole).all()
    depts = db.query(Department).all()
    return (
        {r.system_key or r.name: r for r in roles},
        {d.name: d for d in depts},
    )


# ----------------------------
# Users + roles
# ----------------------------
def ensure_user_membership(db: Session, user: User, tenant: Tenant) -> None:
    # user_tenants is public
    with public_scope(db):
        existing = (
            db.query(UserTenant)
            .filter(UserTenant.user_id == user.id, UserTenant.tenant_id == tenant.id)
            .first()
        )
        if not existing:
            db.add(UserTenant(user_id=user.id, tenant_id=tenant.id))


def ensure_user_role(db: Session, user: User, role: TenantRole) -> None:
    existing = (
        db.query(TenantUserRole)
        .filter(TenantUserRole.user_id == user.id, TenantUserRole.role_id == role.id)
        .first()
    )
    if existing:
        return
    db.add(TenantUserRole(user_id=user.id, role_id=role.id))
    db.flush()


def upsert_demo_staff(db: Session, tenant: Tenant, spec: DemoTenantSpec) -> dict[str, User]:
    # Roles/depts are tenant tables
    with tenant_scope(db, tenant.schema_name):
        roles_map, dept_map = fetch_system_roles_and_departments(db)

    names = load_json("names_in.json")
    firsts: list[str] = names["first_names"]
    lasts: list[str] = names["last_names"]

    hashed = get_password_hash(DEMO_PASSWORD)

    def mk_user(
        email: str,
        first_name: str,
        last_name: str,
        department: str | None,
        specialization: str | None,
    ) -> User:
        # Users live in public schema
        with public_scope(db):
            existing = db.query(User).filter(User.email == email, User.tenant_id == tenant.id).first()
            if existing:
                existing.status = UserStatus.ACTIVE
                existing.is_active = True
                existing.is_deleted = False
                existing.must_change_password = False
                existing.email_verified = True
                if not getattr(existing, "hashed_password", None):
                    existing.hashed_password = hashed
                ensure_user_membership(db, existing, tenant)
                db.flush()
                return existing

            u = User(
                tenant_id=tenant.id,
                email=email,
                hashed_password=hashed,
                first_name=first_name,
                last_name=last_name,
                phone=rand_phone(abs(hash(email)) % 10000),
                department=department,
                specialization=specialization,
                status=UserStatus.ACTIVE,
                is_active=True,
                is_deleted=False,
                must_change_password=False,
                email_verified=True,
            )
            db.add(u)
            db.flush()
            ensure_user_membership(db, u, tenant)
            db.flush()
            return u

    admin_dept_name = dept_map.get("Administrator").name if dept_map.get("Administrator") else "Administrator"
    general_med_name = dept_map.get("General Medicine").name if dept_map.get("General Medicine") else "General Medicine"

    users: dict[str, User] = {}

    admin_email = demo_email(spec.suffix, "admin")
    admin = mk_user(admin_email, "Hospital", "Admin", admin_dept_name, None)
    with tenant_scope(db, tenant.schema_name):
        if "HOSPITAL_ADMIN" in roles_map:
            ensure_user_role(db, admin, roles_map["HOSPITAL_ADMIN"])
    users["HOSPITAL_ADMIN"] = admin

    def pick_name() -> tuple[str, str]:
        return random.choice(firsts), random.choice(lasts)

    for i in range(1, 3):
        fn, ln = pick_name()
        u = mk_user(demo_email(spec.suffix, f"doctor{i}"), fn, ln, general_med_name, "General Medicine")
        with tenant_scope(db, tenant.schema_name):
            if "DOCTOR" in roles_map:
                ensure_user_role(db, u, roles_map["DOCTOR"])
        users[f"DOCTOR_{i}"] = u

    for i in range(1, 3):
        fn, ln = pick_name()
        u = mk_user(demo_email(spec.suffix, f"nurse{i}"), fn, ln, general_med_name, None)
        with tenant_scope(db, tenant.schema_name):
            if "NURSE" in roles_map:
                ensure_user_role(db, u, roles_map["NURSE"])
        users[f"NURSE_{i}"] = u

    for i in range(1, 3):
        fn, ln = pick_name()
        u = mk_user(demo_email(spec.suffix, f"pharmacist{i}"), fn, ln, general_med_name, None)
        with tenant_scope(db, tenant.schema_name):
            if "PHARMACIST" in roles_map:
                ensure_user_role(db, u, roles_map["PHARMACIST"])
        users[f"PHARMACIST_{i}"] = u

    for i in range(1, 3):
        fn, ln = pick_name()
        u = mk_user(demo_email(spec.suffix, f"receptionist{i}"), fn, ln, admin_dept_name, None)
        with tenant_scope(db, tenant.schema_name):
            if "RECEPTIONIST" in roles_map:
                ensure_user_role(db, u, roles_map["RECEPTIONIST"])
        users[f"RECEPTIONIST_{i}"] = u

    db.commit()
    return users

# ----------------------------
# Stock
# ----------------------------
def upsert_stock_catalog(db: Session, tenant: Tenant, suffix: str, created_by_id) -> list[StockItem]:
    catalog = load_json("stock_catalog.json")
    out: list[StockItem] = []

    with tenant_scope(db, tenant.schema_name):
        # Debug: confirm we are in the right tenant
        try:
            sp = db.execute(text("SHOW search_path")).scalar()
            print(f" [debug] stock_catalog search_path={sp}")
        except Exception:
            pass

        def get_existing_id(type_: StockItemType, name: str, form: str | None, strength: str | None):
            """
            IMPORTANT:
            We query ONLY StockItem.id (UUID) to avoid selecting enum columns.
            """
            q = db.query(StockItem.id).filter(
                cast(StockItem.type, String) == type_.value,
                StockItem.name == name,
            )
            if form is not None:
                q = q.filter(StockItem.form == form)
            if strength is not None:
                q = q.filter(StockItem.strength == strength)
            return q.first()

        def get_existing(type_: StockItemType, name: str, form: str | None, strength: str | None) -> StockItem | None:
            row = get_existing_id(type_, name, form, strength)
            if not row:
                return None
            return db.query(StockItem).filter(StockItem.id == row[0]).first()

        # MEDICINES
        for m in catalog["medicines"]:
            name = f"Demo {suffix} - {m['name']}"
            form = m["form"]
            strength = m["strength"]
            existing = get_existing(StockItemType.MEDICINE, name, form, strength)
            if existing:
                out.append(existing)
                continue

            item = StockItem(
                created_by_id=created_by_id,
                type=StockItemType.MEDICINE,
                name=name,
                generic_name=m.get("generic_name"),
                form=form,
                strength=strength,
                route=m.get("route"),
                default_dosage=m.get("default_dosage"),
                default_frequency=m.get("default_frequency"),
                default_duration=m.get("default_duration"),
                default_instructions=m.get("default_instructions"),
                current_stock=random.randint(250, 1200),
                reorder_level=50,
                is_active=True,
            )
            db.add(item)
            out.append(item)

        # EQUIPMENT
        for e in catalog["equipment"]:
            name = f"Demo {suffix} - {e['name']}"
            existing = get_existing(StockItemType.EQUIPMENT, name, None, None)
            if existing:
                out.append(existing)
                continue

            item = StockItem(
                created_by_id=created_by_id,
                type=StockItemType.EQUIPMENT,
                name=name,
                current_stock=random.randint(5, 24),
                reorder_level=3,
                is_active=True,
            )
            db.add(item)
            out.append(item)

        # CONSUMABLE
        for c in catalog["consumables"]:
            name = f"Demo {suffix} - {c['name']}"
            existing = get_existing(StockItemType.CONSUMABLE, name, None, None)
            if existing:
                out.append(existing)
                continue

            item = StockItem(
                created_by_id=created_by_id,
                type=StockItemType.CONSUMABLE,
                name=name,
                current_stock=random.randint(80, 700),
                reorder_level=25,
                is_active=True,
            )
            db.add(item)
            out.append(item)

        db.commit()
        return out

# ----------------------------
# Patients
# ----------------------------
def upsert_patients(db: Session, tenant: Tenant, suffix: str, created_by_id, count: int = 100) -> list[Patient]:
    names = load_json("names_in.json")
    locs = load_json("locations_in.json")
    clinical = load_json("clinical_catalog.json")

    with tenant_scope(db, tenant.schema_name):
        existing = (
            db.query(Patient)
            .filter(Patient.clinical_notes.like(f"{demo_tag(suffix,'patient','%')}%"))
            .count()
        )
        if existing >= count:
            return (
                db.query(Patient)
                .filter(Patient.clinical_notes.like(f"{demo_tag(suffix,'patient','%')}%"))
                .limit(count)
                .all()
            )

        prefix = tenant_prefix(tenant.id)
        patients: list[Patient] = []
        start_seq = existing + 1

        for i in range(start_seq, count + 1):
            fn = random.choice(names["first_names"])
            ln = random.choice(names["last_names"])
            city = random.choice(locs["cities"])
            locality = random.choice(locs["localities"])
            street = random.choice(locs["streets"])

            dob = rand_dob()
            dob_unknown = dob is None
            #consent_email = random.random() < 0.35
            #consent_sms = random.random() < 0.55
            consent_email = True
            consent_sms = True

            p = Patient(
                patient_code=f"{prefix}-P-{i:05d}",
                created_by_id=created_by_id,
                updated_by_id=created_by_id,
                first_name=fn,
                middle_name=None if random.random() < 0.7 else random.choice(names["first_names"]),
                last_name=ln if random.random() < 0.9 else None,
                gender=random.choice(["MALE", "FEMALE", "OTHER", "UNKNOWN"]),
                dob=dob,
                dob_unknown=bool(dob_unknown),
                age_only=None if dob else random.randint(18, 65),
                phone_primary=rand_phone(i + (1 if suffix == "A" else 500)),
                phone_alternate=None if random.random() < 0.8 else rand_phone(i + 777),
                email=None
                if random.random() < 0.55
                else f"{fn.lower()}.{(ln or 'patient').lower()}{i}@example.com",
                address_line1=f"House {random.randint(1, 220)}, {locality}",
                address_line2=f"{street}",
                city=city["city"],
                state=city["state"],
                country="India",
                postal_code=city["postal_code"],
                emergency_contact_name=f"{random.choice(names['first_names'])} {random.choice(names['last_names'])}",
                emergency_contact_relation=random.choice(names["relations"]),
                emergency_contact_phone=rand_phone(i + 999),
                blood_group=random.choice(names["blood_groups"]),
                marital_status=random.choice(names["marital_status"]),
                preferred_language=random.choice(clinical["languages"]),
                known_allergies=None if random.random() < 0.7 else "Dust allergy",
                chronic_conditions=None if random.random() < 0.75 else "Hypertension",
                clinical_notes=f"{demo_tag(suffix,'patient',str(i))} | Seeded demo patient record",
                national_id_type=None if random.random() < 0.85 else "Aadhaar",
                national_id_number=None
                if random.random() < 0.85
                else f"{random.randint(1000,9999)}-{random.randint(1000,9999)}-{random.randint(1000,9999)}",
                consent_sms=bool(consent_sms),
                consent_email=bool(consent_email),
            )

            # “created over time”. DB enforces created_at default.
            created_dt = utcnow() - timedelta(days=random.randint(0, 365), hours=random.randint(0, 23))
            _safe_setattr(p, "created_at", created_dt)
            _safe_setattr(p, "updated_at", created_dt + timedelta(days=random.randint(0, 60)))

            db.add(p)
            patients.append(p)

        db.commit()

        if _column_exists(db, tenant.schema_name, "patients", "created_at"):
            has_updated_at = _column_exists(db, tenant.schema_name, "patients", "updated_at")
            now = utcnow()

            def pick_recent_created_at() -> datetime:
                """Create a realistic distribution that makes dashboards feel alive."""
                bucket = choose_weighted(
                    [
                        ("d7", 0.40),
                        ("d30", 0.30),
                        ("d90", 0.20),
                        ("old", 0.10),
                    ]
                )
                if bucket == "d7":
                    days = random.randint(0, 6)
                elif bucket == "d30":
                    days = random.randint(7, 29)
                elif bucket == "d90":
                    days = random.randint(30, 89)
                else:
                    days = random.randint(90, 540)

                return now - timedelta(days=days, hours=random.randint(0, 23), minutes=random.randint(0, 59))

            for p in patients:
                created_dt = pick_recent_created_at()
                updated_dt = created_dt + timedelta(days=random.randint(0, 25), hours=random.randint(0, 12))
                if updated_dt > now:
                    updated_dt = now - timedelta(hours=random.randint(0, 6))

                schema = tenant.schema_name
                if has_updated_at:
                    db.execute(
                        text(
                            f"""
                            UPDATE "{schema}"."patients"
                            SET created_at = :c, updated_at = :u
                            WHERE id = :id
                            """
                        ),
                        {"c": created_dt, "u": updated_dt, "id": p.id},
                    )
                else:
                    db.execute(
                        text(
                            f"""
                            UPDATE "{schema}"."patients"
                            SET created_at = :c
                            WHERE id = :id
                            """
                        ),
                        {"c": created_dt, "id": p.id},
                    )

            db.commit()

        return patients

def split_patients_for_ipd(patients: list[Patient], ipd_count: int) -> tuple[list[Patient], list[Patient]]:
    pts = patients[:]
    random.shuffle(pts)
    return pts[:ipd_count], pts[ipd_count:]

# ----------------------------
# Admissions (IPD)
# ----------------------------
def create_ipd_admissions(
    db: Session,
    tenant: Tenant,
    suffix: str,
    ipd_patients: list[Patient],
    doctors: list[User],
    departments: list[Department],
    count: int = 200,
) -> list[Admission]:
    clinical = load_json("clinical_catalog.json")
    reasons = clinical["ipd_reasons"]

    with tenant_scope(db, tenant.schema_name):
        already = db.query(Admission).filter(Admission.notes.like(f"{demo_tag(suffix,'ad','%')}%")).count()
        if already >= count:
            return (
                db.query(Admission)
                .filter(Admission.notes.like(f"{demo_tag(suffix,'ad','%')}%"))
                .limit(count)
                .all()
            )

        now = utcnow()
        dept_choices = [d for d in departments if d.name != "Administrator"] or departments[:]
        admissions: list[Admission] = []
        start = already + 1
        active_target = int(count * 0.30)
        active_patients = set(p.id for p in ipd_patients[:active_target])

        for i in range(start, count + 1):
            patient = ipd_patients[(i - 1) % len(ipd_patients)]
            doctor = random.choice(doctors)
            dept = random.choice(dept_choices)
            reason = random.choice(reasons)

            admit_dt = now - timedelta(days=random.randint(1, 90), hours=random.randint(0, 20))
            status = AdmissionStatus.ACTIVE if patient.id in active_patients else AdmissionStatus.DISCHARGED

            discharge_dt = None
            discharge_summary = None
            if status == AdmissionStatus.DISCHARGED:
                stay_days = random.randint(1, 14)
                discharge_dt = admit_dt + timedelta(days=stay_days, hours=random.randint(0, 10))
                if discharge_dt > now:
                    discharge_dt = now - timedelta(hours=random.randint(1, 12))
                discharge_summary = f"{demo_tag(suffix,'ad_sum',str(i))} | Improved. Discharged with advice."

            ad = Admission(
                patient_id=patient.id,
                department_id=dept.id,
                primary_doctor_user_id=doctor.id,
                admit_datetime=admit_dt,
                discharge_datetime=discharge_dt,
                discharge_summary=discharge_summary,
                status=status,
                notes=f"{demo_tag(suffix,'ad',str(i))} | {reason['reason']}",
                source_opd_appointment_id=None,
            )
            db.add(ad)
            admissions.append(ad)

        db.commit()
        return admissions

# ----------------------------
# Appointments (OPD)
# ----------------------------
def _make_lifecycle_for_appointment(ap: Appointment) -> None:
    """Keep timestamps consistent with the appointment status."""
    scheduled_at = ap.scheduled_at

    if ap.status in (
        AppointmentStatus.CHECKED_IN,
        AppointmentStatus.IN_CONSULTATION,
        AppointmentStatus.COMPLETED,
    ):
        ap.checked_in_at = scheduled_at + timedelta(minutes=random.randint(0, 20))

    if ap.status in (AppointmentStatus.IN_CONSULTATION, AppointmentStatus.COMPLETED):
        base = ap.checked_in_at or (scheduled_at + timedelta(minutes=10))
        ap.consultation_started_at = base + timedelta(minutes=random.randint(5, 20))

    if ap.status == AppointmentStatus.COMPLETED:
        base = ap.consultation_started_at or (scheduled_at + timedelta(minutes=30))
        ap.completed_at = base + timedelta(minutes=random.randint(10, 45))

    if ap.status == AppointmentStatus.NO_SHOW:
        ap.no_show_at = scheduled_at + timedelta(minutes=random.randint(45, 120))


def create_opd_appointments(
    db: Session,
    tenant: Tenant,
    suffix: str,
    patients: list[Patient],
    doctors: list[User],
    departments: list[Department],
    count: int = 500,
) -> list[Appointment]:
    clinical = load_json("clinical_catalog.json")
    complaints = clinical["opd_complaints"]
    cancel_notes = clinical.get("cancel_notes", ["Rescheduled", "Patient request", "Doctor unavailable"])

    with tenant_scope(db, tenant.schema_name):
        already = db.query(Appointment).filter(Appointment.notes.like(f"{demo_tag(suffix,'ap','%')}%")).count()
        if already >= count:
            return (
                db.query(Appointment)
                .filter(Appointment.notes.like(f"{demo_tag(suffix,'ap','%')}%"))
                .limit(count)
                .all()
            )

        now = utcnow()
        dept_choices = [d for d in departments if d.name != "Administrator"] or departments[:]
        appts: list[Appointment] = []
        start = already + 1

        # Guaranteed "today bucket" so the dashboard always looks alive.
        today_plan: list[tuple[AppointmentStatus, int]] = [
            (AppointmentStatus.SCHEDULED, 60),
            (AppointmentStatus.CHECKED_IN, 30),
            (AppointmentStatus.IN_CONSULTATION, 12),
            (AppointmentStatus.COMPLETED, 40),
        ]

        seq = start
        for status, n in today_plan:
            for _ in range(n):
                if seq > count:
                    break

                patient = random.choice(patients)
                doctor = random.choice(doctors)
                dept = random.choice(dept_choices)
                c = random.choice(complaints)

                scheduled_at = clinic_time(now, 0)
                if status in (
                    AppointmentStatus.CHECKED_IN,
                    AppointmentStatus.IN_CONSULTATION,
                    AppointmentStatus.COMPLETED,
                ):
                    scheduled_at = clinic_time(now, 0) - timedelta(hours=random.randint(0, 6))
                    scheduled_at = in_15_min_block(scheduled_at)

                note = f"{demo_tag(suffix,'ap',str(seq))} | {c['complaint']}"
                ap = Appointment(
                    patient_id=patient.id,
                    department_id=dept.id,
                    doctor_user_id=doctor.id,
                    scheduled_at=scheduled_at,
                    status=status,
                    notes=note,
                )
                _make_lifecycle_for_appointment(ap)
                db.add(ap)
                appts.append(ap)
                seq += 1

        status_pick = [
            (AppointmentStatus.COMPLETED, 0.52),
            (AppointmentStatus.SCHEDULED, 0.18),
            (AppointmentStatus.CHECKED_IN, 0.10),
            (AppointmentStatus.IN_CONSULTATION, 0.07),
            (AppointmentStatus.NO_SHOW, 0.08),
            (AppointmentStatus.CANCELLED, 0.05),
        ]

        remaining = max(0, count - len(appts))
        for _ in range(remaining):
            if seq > count:
                break

            status: AppointmentStatus = choose_weighted(status_pick)
            patient = random.choice(patients)
            doctor = random.choice(doctors)
            dept = random.choice(dept_choices)
            c = random.choice(complaints)

            days_offset = random.randint(-120, 14)
            if status in (AppointmentStatus.SCHEDULED, AppointmentStatus.CHECKED_IN, AppointmentStatus.IN_CONSULTATION):
                days_offset = random.randint(0, 14)

            scheduled_at = clinic_time(now, days_offset)
            note = f"{demo_tag(suffix,'ap',str(seq))} | {c['complaint']}"

            ap = Appointment(
                patient_id=patient.id,
                department_id=dept.id,
                doctor_user_id=doctor.id,
                scheduled_at=scheduled_at,
                status=status,
                notes=note,
            )
            _make_lifecycle_for_appointment(ap)

            if status == AppointmentStatus.CANCELLED:
                ap.cancelled_reason = random.choice(["PATIENT_REQUEST", "DOCTOR_UNAVAILABLE", "OTHER"])
                ap.cancelled_note = random.choice(cancel_notes)

            db.add(ap)
            appts.append(ap)
            seq += 1

        db.commit()
        return appts

# ----------------------------
# Vitals
# ----------------------------
def create_vitals_for_opd(
    db: Session,
    tenant: Tenant,
    suffix: str,
    appointments: list[Appointment],
    nurses_and_doctors: list[User],
) -> int:
    with tenant_scope(db, tenant.schema_name):
        candidates = [
            a
            for a in appointments
            if a.status
            in (
                AppointmentStatus.CHECKED_IN,
                AppointmentStatus.IN_CONSULTATION,
                AppointmentStatus.COMPLETED,
            )
        ]
        random.shuffle(candidates)
        candidates = candidates[: int(len(candidates) * 0.35)]

        created = 0
        for ap in candidates:
            marker = demo_tag(suffix, "vt_opd", str(ap.id))
            exists = db.query(Vital).filter(Vital.notes == marker).first()
            if exists:
                continue

            rby = random.choice(nurses_and_doctors)
            when = (ap.checked_in_at or ap.scheduled_at) + timedelta(minutes=random.randint(0, 10))

            v = Vital(
                patient_id=ap.patient_id,
                appointment_id=ap.id,
                admission_id=None,
                recorded_by_id=rby.id,
                systolic_bp=random.randint(100, 145),
                diastolic_bp=random.randint(60, 95),
                heart_rate=random.randint(60, 110),
                temperature_c=round(random.uniform(36.4, 38.4), 1),
                respiratory_rate=random.randint(12, 22),
                spo2=random.randint(94, 100),
                weight_kg=round(random.uniform(50, 95), 1),
                height_cm=round(random.uniform(150, 185), 1),
                notes=marker,
                recorded_at=when,
            )
            db.add(v)
            created += 1

        db.commit()
        return created


def create_vitals_for_ipd(
    db: Session,
    tenant: Tenant,
    suffix: str,
    admissions: list[Admission],
    nurses_and_doctors: list[User],
) -> int:
    with tenant_scope(db, tenant.schema_name):
        created = 0
        now = utcnow()

        for ad in admissions:
            start = ad.admit_datetime
            end = ad.discharge_datetime or now
            if (end - start).days > 21:
                start = end - timedelta(days=21)

            days = max(1, int((end - start).days) + 1)
            per_day = random.randint(2, 4) if ad.status == AdmissionStatus.ACTIVE else random.randint(1, 3)

            max_records = 40
            produced_for_ad = 0

            for d in range(days):
                if produced_for_ad >= max_records:
                    break

                day_base = start + timedelta(days=d)
                for n in range(per_day):
                    if produced_for_ad >= max_records:
                        break

                    marker = demo_tag(suffix, "vt_ipd", f"{ad.id}:{d}:{n}")
                    exists = db.query(Vital).filter(Vital.notes == marker).first()
                    if exists:
                        continue

                    when = day_base.replace(
                        hour=random.choice([8, 12, 16, 20]),
                        minute=random.choice([0, 15, 30, 45]),
                        second=0,
                        microsecond=0,
                    )
                    if when < start:
                        when = start + timedelta(minutes=30)
                    if when > end:
                        continue

                    rby = random.choice(nurses_and_doctors)

                    v = Vital(
                        patient_id=ad.patient_id,
                        appointment_id=None,
                        admission_id=ad.id,
                        recorded_by_id=rby.id,
                        systolic_bp=random.randint(100, 160),
                        diastolic_bp=random.randint(60, 105),
                        heart_rate=random.randint(60, 120),
                        temperature_c=round(random.uniform(36.3, 39.0), 1),
                        respiratory_rate=random.randint(12, 26),
                        spo2=random.randint(92, 100),
                        notes=marker,
                        recorded_at=when,
                    )
                    db.add(v)
                    created += 1
                    produced_for_ad += 1

        db.commit()
        return created

# ----------------------------
# Prescriptions
# ----------------------------

def _apply_prescription_cancel_fields(rx: Prescription, *, reason: str, when: datetime) -> None:
    _safe_setattr(rx, "cancelled_reason", reason)
    _safe_setattr(rx, "cancelled_at", when)


def prescription_transition_plan() -> PrescriptionStatus:
    return choose_weighted(
        [
            (PrescriptionStatus.DISPENSED, 0.38),
            (PrescriptionStatus.ISSUED, 0.28),
            (PrescriptionStatus.DRAFT, 0.22),
            (PrescriptionStatus.CANCELLED, 0.12),
        ]
    )

def create_prescriptions(
    db: Session,
    tenant: Tenant,
    suffix: str,
    appointments: list[Appointment],
    admissions: list[Admission],
    doctors: list[User],
    pharmacists: list[User],
    stock_items: list[StockItem],
    limit: int = 220,
) -> list[Prescription]:
    clinical = load_json("clinical_catalog.json")
    complaints = clinical["opd_complaints"]
    ipd_reasons = clinical["ipd_reasons"]

    with tenant_scope(db, tenant.schema_name):
        already = (
            db.query(Prescription)
            .filter(Prescription.chief_complaint.like(f"{demo_tag(suffix,'rx','%')}%"))
            .count()
        )
        if already >= limit:
            return (
                db.query(Prescription)
                .filter(Prescription.chief_complaint.like(f"{demo_tag(suffix,'rx','%')}%"))
                .limit(limit)
                .all()
            )

        med_stock = [s for s in stock_items if s.type == StockItemType.MEDICINE and s.is_active] or [
            s for s in stock_items if s.type == StockItemType.MEDICINE
        ]

        opd_candidates = [
            a
            for a in appointments
            if a.status
            in (
                AppointmentStatus.SCHEDULED,
                AppointmentStatus.CHECKED_IN,
                AppointmentStatus.IN_CONSULTATION,
                AppointmentStatus.COMPLETED,
            )
        ]

        now = utcnow()
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        today_end = today_start + timedelta(days=1)
        todays = [a for a in opd_candidates if today_start <= a.scheduled_at < today_end]
        others = [a for a in opd_candidates if a not in todays]
        random.shuffle(todays)
        random.shuffle(others)
        opd_candidates = todays + others
        random.shuffle(opd_candidates)

        ipd_candidates = admissions[:]
        random.shuffle(ipd_candidates)

        rx_list: list[Prescription] = []
        start = already + 1
        opd_target = int(limit * 0.7)
        ipd_target = limit - opd_target

        def add_items(rx: Prescription) -> None:
            n = random.randint(1, 3)
            for _ in range(n):
                stock = random.choice(med_stock)
                qty = random.randint(3, 14)
                item = PrescriptionItem(
                    prescription_id=rx.id,
                    stock_item_id=stock.id,
                    medicine_name=stock.name.replace(f"Demo {suffix} - ", ""),
                    dosage=getattr(stock, "default_dosage", None) or "1 tab",
                    frequency=getattr(stock, "default_frequency", None) or "BD",
                    duration=getattr(stock, "default_duration", None) or "3 days",
                    instructions=getattr(stock, "default_instructions", None) or "After food",
                    quantity=qty,
                )
                rx.items.append(item)

        # OPD prescriptions
        for idx in range(opd_target):
            seq = start + idx
            if seq > limit or idx >= len(opd_candidates):
                break

            ap = opd_candidates[idx]
            doctor = random.choice(doctors)
            status = prescription_transition_plan()
            c = random.choice(complaints)

            rx = Prescription(
                patient_id=ap.patient_id,
                doctor_user_id=doctor.id,
                appointment_id=ap.id,
                admission_id=None,
                status=status,
                chief_complaint=f"{demo_tag(suffix,'rx',str(seq))} | {c['complaint']}",
                diagnosis=c["diagnosis"],
            )
            db.add(rx)
            db.flush()
            add_items(rx)

            if status == PrescriptionStatus.CANCELLED:
                cancelled_when = (getattr(rx, "created_at", None) or utcnow()) + timedelta(minutes=random.randint(5, 180))
                _apply_prescription_cancel_fields(
                    rx,
                    reason=random.choice(
                        [
                            "Patient improved; medication not required",
                            "Duplicate prescription created by mistake",
                            "Treatment plan changed after review",
                            "Patient requested cancellation",
                        ]
                    ),
                    when=cancelled_when,
                )

            if status in (PrescriptionStatus.ISSUED, PrescriptionStatus.DISPENSED):
                if ap.status not in (AppointmentStatus.CANCELLED, AppointmentStatus.NO_SHOW):
                    ap.status = AppointmentStatus.COMPLETED
                if not ap.checked_in_at:
                    ap.checked_in_at = ap.scheduled_at + timedelta(minutes=5)
                if not ap.consultation_started_at:
                    ap.consultation_started_at = (ap.checked_in_at or ap.scheduled_at) + timedelta(minutes=10)
                if not ap.completed_at:
                    ap.completed_at = (ap.consultation_started_at or ap.scheduled_at) + timedelta(minutes=20)

            if status == PrescriptionStatus.DISPENSED:
                for it in rx.items:
                    if it.stock_item_id and it.quantity:
                        s = db.query(StockItem).filter(StockItem.id == it.stock_item_id).first()
                        if not s:
                            continue
                        if (s.current_stock or 0) < it.quantity:
                            it.quantity = max(0, int(s.current_stock or 0))
                        s.current_stock = max(0, int(s.current_stock or 0) - int(it.quantity or 0))

            rx_list.append(rx)

        # IPD prescriptions
        for idx in range(ipd_target):
            seq = start + opd_target + idx
            if seq > limit or idx >= len(ipd_candidates):
                break

            ad = ipd_candidates[idx]
            doctor = random.choice(doctors)
            status = prescription_transition_plan()
            reason = random.choice(ipd_reasons)

            rx = Prescription(
                patient_id=ad.patient_id,
                doctor_user_id=doctor.id,
                appointment_id=None,
                admission_id=ad.id,
                status=status,
                chief_complaint=f"{demo_tag(suffix,'rx',str(seq))} | IPD: {reason['reason']}",
                diagnosis="Supportive care / monitor vitals",
            )
            db.add(rx)
            db.flush()
            add_items(rx)

            if status == PrescriptionStatus.CANCELLED:
                cancelled_when = (getattr(rx, "created_at", None) or utcnow()) + timedelta(minutes=random.randint(5, 180))
                _apply_prescription_cancel_fields(rx, reason="Therapy changed", when=cancelled_when)

            if status == PrescriptionStatus.DISPENSED:
                for it in rx.items:
                    if it.stock_item_id and it.quantity:
                        s = db.query(StockItem).filter(StockItem.id == it.stock_item_id).first()
                        if not s:
                            continue
                        if (s.current_stock or 0) < it.quantity:
                            it.quantity = max(0, int(s.current_stock or 0))
                        s.current_stock = max(0, int(s.current_stock or 0) - int(it.quantity or 0))

            rx_list.append(rx)

        db.commit()
        return rx_list

# ----------------------------
# Reset + Freshen
# ----------------------------
def reset_demo_for_tenant(db: Session, tenant: Tenant, suffix: str) -> None:
    # Count records before deletion for metrics decrement
    with tenant_scope(db, tenant.schema_name):
        patient_count = (
            db.query(Patient)
            .filter(Patient.clinical_notes.like(f"{demo_tag(suffix,'patient','%')}%"))
            .count()
        )
        appointment_count = (
            db.query(Appointment).filter(Appointment.notes.like(f"{demo_tag(suffix,'ap','%')}%")).count()
        )
        prescription_count = (
            db.query(Prescription)
            .filter(Prescription.chief_complaint.like(f"{demo_tag(suffix,'rx','%')}%"))
            .count()
        )

    demo_email_domain = f"@demo-tenant-{suffix.lower()}.{DEMO_EMAIL_TLD}"
    user_count = (
        db.query(User)
        .filter(User.tenant_id == tenant.id, User.email.like(f"%{demo_email_domain}"))
        .count()
    )

    # Delete records
    with tenant_scope(db, tenant.schema_name):
        db.query(Vital).filter(Vital.notes.like(f"{demo_tag(suffix,'vt_%','%')}%")).delete(synchronize_session=False)

        rx_ids = (
            db.query(Prescription.id)
            .filter(Prescription.chief_complaint.like(f"{demo_tag(suffix,'rx','%')}%"))
            .all()
        )
        rx_ids_flat = [r[0] for r in rx_ids]
        if rx_ids_flat:
            db.query(PrescriptionItem).filter(PrescriptionItem.prescription_id.in_(rx_ids_flat)).delete(
                synchronize_session=False
            )

        db.query(Prescription).filter(Prescription.chief_complaint.like(f"{demo_tag(suffix,'rx','%')}%")).delete(
            synchronize_session=False
        )
        db.query(Appointment).filter(Appointment.notes.like(f"{demo_tag(suffix,'ap','%')}%")).delete(
            synchronize_session=False
        )
        db.query(Admission).filter(Admission.notes.like(f"{demo_tag(suffix,'ad','%')}%")).delete(
            synchronize_session=False
        )
        db.query(StockItem).filter(StockItem.name.like(f"Demo {suffix} - %")).delete(synchronize_session=False)
        db.query(Patient).filter(Patient.clinical_notes.like(f"{demo_tag(suffix,'patient','%')}%")).delete(
            synchronize_session=False
        )

    users = db.query(User).filter(
        User.tenant_id == tenant.id,
        User.email.like(f"%{demo_email_domain}"),
    ).all()

    with tenant_scope(db, tenant.schema_name):
        for u in users:
            db.query(TenantUserRole).filter(TenantUserRole.user_id == u.id).delete(synchronize_session=False)

    db.execute(
        text(
            """
            DELETE FROM public.user_tenants
            WHERE tenant_id = :tid
              AND user_id IN (
                SELECT id
                FROM public.users
                WHERE tenant_id = :tid
                  AND email LIKE :pat
              )
            """
        ),
        {"tid": str(tenant.id), "pat": f"%{demo_email_domain}"},
    )

    db.query(User).filter(
        User.tenant_id == tenant.id,
        User.email.like(f"%{demo_email_domain}"),
    ).delete(synchronize_session=False)

    db.commit()

    # Decrement metrics
    try:
        def _do_metrics(dbm: Session) -> None:
            from app.services.tenant_metrics_service import get_or_create_metrics

            metrics = get_or_create_metrics(dbm)

            if patient_count > 0:
                metrics.total_patients = max(0, (metrics.total_patients or 0) - patient_count)
            if appointment_count > 0:
                metrics.total_appointments = max(0, (metrics.total_appointments or 0) - appointment_count)
            if prescription_count > 0:
                metrics.total_prescriptions = max(0, (metrics.total_prescriptions or 0) - prescription_count)
            if user_count > 0:
                metrics.total_users = max(0, (metrics.total_users or 0) - user_count)

        _run_public_metrics(_do_metrics)

    except Exception as e:
        logger.warning(f"Failed to decrement metrics during reset (non-critical): {e}", exc_info=True)


def freshen_demo_for_tenant(db: Session, tenant: Tenant, suffix: str, shift_days: int) -> dict[str, int]:
    """
    Freshen demo data by shifting dates forward uniformly.
    Maintains relative timestamp ordering and shifts related entities (prescriptions, linked admissions).
    Uses tenant_scope to ensure proper schema selection for all tenant-scoped entities.
    """
    delta = timedelta(days=shift_days)
    
    with tenant_scope(db, tenant.schema_name):
        # 1. Shift appointments and collect their IDs for related entity updates
        appts = db.query(Appointment).filter(Appointment.notes.like(f"{demo_tag(suffix,'ap','%')}%")).all()
        appointment_ids = [a.id for a in appts]
        
        for a in appts:
            a.scheduled_at = in_15_min_block(a.scheduled_at + delta)
            if a.checked_in_at:
                a.checked_in_at = a.checked_in_at + delta
            if a.consultation_started_at:
                a.consultation_started_at = a.consultation_started_at + delta
            if a.completed_at:
                a.completed_at = a.completed_at + delta
            if a.no_show_at:
                a.no_show_at = a.no_show_at + delta

        # 2. Shift prescriptions linked to appointments (within tenant scope)
        prescriptions = []
        if appointment_ids:
            prescriptions = db.query(Prescription).filter(
                Prescription.appointment_id.in_(appointment_ids)
            ).all()
            for rx in prescriptions:
                rx.created_at = rx.created_at + delta
                if rx.cancelled_at:
                    rx.cancelled_at = rx.cancelled_at + delta

        # 3. Shift all admissions (all are in tenant scope, including those linked to appointments)
        # Note: Admissions linked to appointments via source_opd_appointment_id are also shifted
        # to maintain logical consistency (admission happened after appointment that triggered it)
        ads = db.query(Admission).filter(Admission.notes.like(f"{demo_tag(suffix,'ad','%')}%")).all()
        for ad in ads:
            ad.admit_datetime = ad.admit_datetime + delta
            if ad.discharge_datetime:
                ad.discharge_datetime = ad.discharge_datetime + delta

        # 5. Shift vitals (within tenant scope)
        vs = db.query(Vital).filter(Vital.notes.like(f"{demo_tag(suffix,'vt_%','%')}%")).all()
        for v in vs:
            v.recorded_at = v.recorded_at + delta

        db.commit()
        return {
            "appointments": len(appts),
            "admissions": len(ads),
            "vitals": len(vs),
            "prescriptions": len(prescriptions),
        }

# ----------------------------
# Main seeding orchestration
# ----------------------------
def seed_one_tenant(spec: DemoTenantSpec) -> None:
    """Seed one tenant using a fresh Session/connection."""
    db = SeedSessionLocal()
    db.rollback()
    try:
        print(f"\n=== Seeding Tenant {spec.suffix} ===")
        tenant = get_or_create_demo_tenant(db, spec)

        # Count existing records before seeding
        demo_email_domain = f"@demo-tenant-{spec.suffix.lower()}.{DEMO_EMAIL_TLD}"
        with public_scope(db):
            existing_user_count = (
                db.query(User)
                .filter(User.tenant_id == tenant.id, User.email.like(f"%{demo_email_domain}"))
                .count()
            )

        with tenant_scope(db, tenant.schema_name):
            existing_patients_before = (
                db.query(Patient)
                .filter(Patient.clinical_notes.like(f"{demo_tag(spec.suffix,'patient','%')}%"))
                .count()
            )
            existing_appointments_before = (
                db.query(Appointment).filter(Appointment.notes.like(f"{demo_tag(spec.suffix,'ap','%')}%")).count()
            )
            existing_prescriptions_before = (
                db.query(Prescription)
                .filter(Prescription.chief_complaint.like(f"{demo_tag(spec.suffix,'rx','%')}%"))
                .count()
            )

        staff = upsert_demo_staff(db, tenant, spec)
        doctors = [staff["DOCTOR_1"], staff["DOCTOR_2"]]
        nurses = [staff["NURSE_1"], staff["NURSE_2"]]
        pharmacists = [staff["PHARMACIST_1"], staff["PHARMACIST_2"]]
        nurse_or_doc = doctors + nurses

        with tenant_scope(db, tenant.schema_name):
            departments = db.query(Department).all()

        stock = upsert_stock_catalog(db, tenant, spec.suffix, created_by_id=staff["HOSPITAL_ADMIN"].id)

        patients = upsert_patients(
            db,
            tenant,
            spec.suffix,
            created_by_id=staff["RECEPTIONIST_1"].id,
            count=100,
        )

        ipd_patients, _ = split_patients_for_ipd(patients, ipd_count=60)
        admissions = create_ipd_admissions(db, tenant, spec.suffix, ipd_patients, doctors, departments, count=200)

        with tenant_scope(db, tenant.schema_name):
            active_ipd_patient_ids = {
                pid
                for (pid,) in db.query(Admission.patient_id).filter(Admission.status == AdmissionStatus.ACTIVE).all()
            }

        # Build the list while we *know* search_path is correct
        opd_only_patients = [p for p in patients if p.id not in active_ipd_patient_ids]
        appointments = create_opd_appointments(
            db,
            tenant,
            spec.suffix,
            opd_only_patients,
            doctors,
            departments,
            count=500,
        )

        v_opd = create_vitals_for_opd(db, tenant, spec.suffix, appointments, nurse_or_doc)
        v_ipd = create_vitals_for_ipd(db, tenant, spec.suffix, admissions, nurse_or_doc)

        rx = create_prescriptions(
            db,
            tenant,
            spec.suffix,
            appointments,
            admissions,
            doctors,
            pharmacists,
            stock,
            limit=220,
        )

        # Count new records created (difference between before and after)
        with tenant_scope(db, tenant.schema_name):
            total_patients_after = (
                db.query(Patient)
                .filter(Patient.clinical_notes.like(f"{demo_tag(spec.suffix,'patient','%')}%"))
                .count()
            )
            total_appointments_after = (
                db.query(Appointment).filter(Appointment.notes.like(f"{demo_tag(spec.suffix,'ap','%')}%")).count()
            )
            total_prescriptions_after = (
                db.query(Prescription)
                .filter(Prescription.chief_complaint.like(f"{demo_tag(spec.suffix,'rx','%')}%"))
                .count()
            )
            total_user_count_after = (
                db.query(User)
                .filter(User.tenant_id == tenant.id, User.email.like(f"%{demo_email_domain}"))
                .count()
            )

        new_user_count = max(0, total_user_count_after - existing_user_count)
        new_patient_count = max(0, total_patients_after - existing_patients_before)
        new_appointment_count = max(0, total_appointments_after - existing_appointments_before)
        new_prescription_count = max(0, total_prescriptions_after - existing_prescriptions_before)

        # Increment metrics
        try:
            def _do_metrics(dbm: Session) -> None:
                if new_patient_count > 0:
                    increment_patients(dbm, new_patient_count)
                if new_appointment_count > 0:
                    increment_appointments(dbm, new_appointment_count)
                if new_prescription_count > 0:
                    increment_prescriptions(dbm, new_prescription_count)
                if new_user_count > 0:
                    increment_users(dbm, new_user_count)

            _run_public_metrics(_do_metrics)

        except Exception as e:
            logger.warning(f"Failed to increment metrics during seed (non-critical): {e}", exc_info=True)

        print(f"Patients: {len(patients)} (new: {new_patient_count})")
        print(f"Appointments: {len(appointments)} (new: {new_appointment_count})")
        print(f"Admissions: {len(admissions)}")
        print(f"Vitals: OPD={v_opd}, IPD={v_ipd}")
        print(f"Prescriptions: {len(rx)} (new: {new_prescription_count})")
        print(f"Users: {len(staff)} (new: {new_user_count})")

        print("\nDemo logins (password is the same for all):")
        print(f" Admin: {demo_email(spec.suffix, 'admin')}")
        print(f" Doctor: {demo_email(spec.suffix, 'doctor1')}")
        print(f" Nurse: {demo_email(spec.suffix, 'nurse1')}")
        print(f" Pharmacist: {demo_email(spec.suffix, 'pharmacist1')}")
        print(f" Receptionist: {demo_email(spec.suffix, 'receptionist1')}")
        print(f" Password: {DEMO_PASSWORD}")

    except Exception:
        import traceback

        traceback.print_exc()
        db.rollback()
        raise
    finally:
        db.close()


def seed_two_tenants() -> None:
    specs = [
        DemoTenantSpec(
            suffix="A",
            license_number=DEMO_TENANT_A_LICENSE,
            admin_email=demo_email("A", "admin"),
        ),
        DemoTenantSpec(
            suffix="B",
            license_number=DEMO_TENANT_B_LICENSE,
            admin_email=demo_email("B", "admin"),
        ),
    ]

    failures: list[tuple[str, str]] = []
    for spec in specs:
        try:
            seed_one_tenant(spec)
        except Exception as e:
            _log_db_error(e)
            failures.append((spec.suffix, str(e)))

    if failures:
        raise RuntimeError(f"Seed finished with failures: {failures}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed / reset / freshen HMS demo data")
    parser.add_argument("--seed", action="store_true", help="Seed demo tenants and realistic demo data")
    parser.add_argument("--reset", action="store_true", help="Delete demo data for demo tenants only")
    parser.add_argument("--freshen", action="store_true", help="Time-shift demo data forward")
    parser.add_argument("--freshen-days", type=int, default=7, help="Days to shift forward (default: 7)")
    args = parser.parse_args()

    if not (args.seed or args.reset or args.freshen):
        parser.print_help()
        raise SystemExit(1)

    def _with_fresh_session(work) -> None:
        db: Session = SeedSessionLocal()
        try:
            work(db)
        except Exception:
            import traceback

            traceback.print_exc()
            db.rollback()
            raise
        finally:
            db.close()

    if args.reset:
        for suffix, lic in [("A", DEMO_TENANT_A_LICENSE), ("B", DEMO_TENANT_B_LICENSE)]:

            def _do_reset(db: Session) -> None:
                tenant = db.query(Tenant).filter(Tenant.license_number == lic).first()
                if not tenant:
                    print(f"Tenant {suffix} not found, skipping reset.")
                    return
                print(f"\nResetting Tenant {suffix} ({tenant.schema_name})...")
                reset_demo_for_tenant(db, tenant, suffix)
                print("Reset done.")

            _with_fresh_session(_do_reset)

    if args.seed:
        seed_two_tenants()

    if args.freshen:
        for suffix, lic in [("A", DEMO_TENANT_A_LICENSE), ("B", DEMO_TENANT_B_LICENSE)]:

            def _do_freshen(db: Session) -> None:
                tenant = db.query(Tenant).filter(Tenant.license_number == lic).first()
                if not tenant:
                    print(f"Tenant {suffix} not found, skipping freshen.")
                    return
                print(f"\nFreshening Tenant {suffix} by {args.freshen_days} days...")
                stats = freshen_demo_for_tenant(db, tenant, suffix, shift_days=args.freshen_days)
                print(f"Freshened: {stats}")

            _with_fresh_session(_do_freshen)


if __name__ == "__main__":
    main()