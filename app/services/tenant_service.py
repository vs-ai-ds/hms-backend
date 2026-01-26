# app/services/tenant_service.py
"""
Tenant schema + table lifecycle utilities.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import inspect, text
from sqlalchemy.exc import DBAPIError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.tenant_domain import TENANT_TABLES
from app.models.tenant_global import Tenant, TenantStatus
from app.services.seed_service import seed_tenant_defaults

logger = logging.getLogger(__name__)

# ------------------------------------------------------------------------------
# Tenant enums: single source of truth
# ------------------------------------------------------------------------------

# NOTE:
# - Keep ONLY tenant-scoped enums here (per-tenant schema)
# - If any enum is truly global/public, it should not be created here and models
#   should reference it as public.<enum_name>
TENANT_ENUMS: List[Tuple[str, List[str]]] = [
    ("stock_item_type_enum", ["MEDICINE", "EQUIPMENT", "CONSUMABLE"]),
    (
        "appointment_status_enum",
        [
            "SCHEDULED",
            "CHECKED_IN",
            "IN_CONSULTATION",
            "COMPLETED",
            "CANCELLED",
            "NO_SHOW",
        ],
    ),
    ("admission_status_enum", ["ACTIVE", "DISCHARGED", "CANCELLED"]),
    ("prescription_status_enum", ["DRAFT", "ISSUED", "DISPENSED", "CANCELLED"]),
    ("notification_channel_enum", ["EMAIL", "SMS", "WHATSAPP"]),
    ("notification_status_enum", ["PENDING", "SENT", "FAILED"]),
]


# ------------------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------------------


def _generate_schema_name() -> str:
    """
    Generate a PostgreSQL schema name for a tenant.

    Uses a short UUID; ensures it's a safe identifier:
    - lower-case
    - alphanumeric + underscore
    """
    short_id = uuid.uuid4().hex[:8]
    return f"tenant_{short_id}"


def _schema_exists(conn, schema_name: str) -> bool:
    return (
        conn.execute(
            text("SELECT 1 FROM information_schema.schemata WHERE schema_name = :s"),
            {"s": schema_name},
        ).fetchone()
        is not None
    )


def _enum_exists(conn, schema_name: str, enum_name: str) -> bool:
    return (
        conn.execute(
            text(
                """
                SELECT 1
                FROM pg_type t
                JOIN pg_namespace n ON n.oid = t.typnamespace
                WHERE t.typname = :t AND n.nspname = :s
                """
            ),
            {"t": enum_name, "s": schema_name},
        ).fetchone()
        is not None
    )


def _find_enum_locations(conn, enum_name: str) -> List[str]:
    """
    Return schemas where enum exists (usually 0 or 1, but could be multiple schemas with same typname).
    """
    rows = conn.execute(
        text(
            """
            SELECT n.nspname
            FROM pg_type t
            JOIN pg_namespace n ON n.oid = t.typnamespace
            WHERE t.typname = :t
            ORDER BY n.nspname
            """
        ),
        {"t": enum_name},
    ).fetchall()
    return [r[0] for r in rows]


def _debug_db_context(conn, schema_name: str) -> Dict[str, Any]:
    """
    Lightweight diagnostics for runtime debugging (safe to keep).
    """
    out: Dict[str, Any] = {}
    try:
        out["current_database"] = conn.execute(
            text("SELECT current_database()")
        ).scalar()
        out["current_user"] = conn.execute(text("SELECT current_user")).scalar()
        out["search_path"] = conn.execute(text("SHOW search_path")).scalar()
        out["schema_exists"] = _schema_exists(conn, schema_name)
    except Exception as e:
        out["diagnostics_error"] = str(e)
    return out


def _set_search_path(conn, schema_name: str) -> None:
    # Keep public after tenant schema so public tables (users, tenants, etc.) can be referenced.
    conn.execute(text(f'SET search_path TO "{schema_name}", public'))


def _reset_search_path(conn) -> None:
    conn.execute(text("SET search_path TO public"))


def _create_enum_in_schema(
    conn, schema_name: str, enum_name: str, enum_values: List[str]
) -> None:
    """
    Create enum in tenant schema if missing.
    Uses schema-qualified CREATE TYPE so it does NOT rely on search_path.
    """
    if _enum_exists(conn, schema_name, enum_name):
        return

    # Build values list safely. Values here are hard-coded constants (not user input).
    values_sql = ", ".join([f"'{v}'" for v in enum_values])

    # schema-qualified create
    conn.execute(
        text(f'CREATE TYPE "{schema_name}".{enum_name} AS ENUM ({values_sql})')
    )


def _drop_schema_objects_for_reset(conn, schema_name: str) -> None:
    """
    DEV ONLY: Drop all tables and enums inside the tenant schema.
    Gated by hms_dev_allow_tenant_schema_reset config setting.
    """
    inspector = inspect(conn)

    # Drop tables
    for table_name in inspector.get_table_names(schema=schema_name):
        conn.execute(
            text(f'DROP TABLE IF EXISTS "{schema_name}"."{table_name}" CASCADE')
        )

    # Drop known enums (tenant scoped)
    for enum_name, _ in TENANT_ENUMS:
        conn.execute(text(f'DROP TYPE IF EXISTS "{schema_name}".{enum_name} CASCADE'))


# ------------------------------------------------------------------------------
# Public APIs
# ------------------------------------------------------------------------------


def ensure_tenant_tables_exist(db: Session, schema_name: str) -> None:
    """
    Ensure all tenant tables exist in the schema.
    Creates missing tables and adds missing columns without dropping existing ones.

    This function is meant for upgrades / drift repair, not for brand-new tenant creation.
    """
    conn = db.connection()

    try:
        if not _schema_exists(conn, schema_name):
            raise RuntimeError(f"Tenant schema does not exist: {schema_name}")

        # Always set search_path for SQLAlchemy create() and inspector reads
        _set_search_path(conn, schema_name)

        # Ensure tenant enums exist first (schema-qualified)
        for enum_name, enum_values in TENANT_ENUMS:
            _create_enum_in_schema(conn, schema_name, enum_name, enum_values)

        inspector = inspect(conn)
        existing_tables = set(inspector.get_table_names(schema=schema_name))

        # Create missing tables using SQLAlchemy table metadata.
        # This is more reliable than regex-rewriting compiled CREATE TABLE.
        for table in TENANT_TABLES:
            if table.name in existing_tables:
                continue
            logger.info(
                "Creating missing tenant table=%s schema=%s", table.name, schema_name
            )
            # Ensure search_path still correct (connection pool can reuse sessions in odd ways)
            _set_search_path(conn, schema_name)
            table.create(bind=conn, checkfirst=False)

        # Add missing columns (best-effort, additive only)
        # NOTE: This assumes model definitions are compatible with existing data.
        inspector = inspect(conn)
        for table in TENANT_TABLES:
            table_name = table.name
            if table_name not in existing_tables and table_name not in set(
                inspector.get_table_names(schema=schema_name)
            ):
                # table was just created; skip column diff
                continue

            try:
                existing_columns = {
                    c["name"]
                    for c in inspector.get_columns(table_name, schema=schema_name)
                }
                model_columns = {c.name for c in table.columns}
                missing_columns = model_columns - existing_columns
                if not missing_columns:
                    continue

                logger.info(
                    "Adding missing columns to table=%s schema=%s missing=%s",
                    table_name,
                    schema_name,
                    sorted(missing_columns),
                )

                for col_name in sorted(missing_columns):
                    col = table.columns[col_name]
                    col_type = col.type.compile(conn.dialect)
                    nullable = "NULL" if col.nullable else "NOT NULL"

                    default_clause = ""
                    # Prefer server_default if present
                    if col.server_default is not None and hasattr(
                        col.server_default, "arg"
                    ):
                        default_arg = str(col.server_default.arg)
                        default_clause = f" DEFAULT {default_arg}"
                    elif col.default is not None and hasattr(col.default, "arg"):
                        dv = col.default.arg
                        if isinstance(dv, bool):
                            default_clause = f" DEFAULT {str(dv).upper()}"
                        elif isinstance(dv, str):
                            default_clause = f" DEFAULT '{dv}'"
                        else:
                            default_clause = f" DEFAULT {dv}"

                    alter_sql = f'ALTER TABLE "{schema_name}"."{table_name}" ADD COLUMN "{col_name}" {col_type} {nullable}{default_clause}'
                    conn.execute(text(alter_sql))

            except Exception as e:
                logger.warning(
                    "Could not diff/add columns for table=%s schema=%s err=%s",
                    table_name,
                    schema_name,
                    e,
                    exc_info=True,
                )

        # Cleanup: drop obsolete columns (best-effort)
        try:
            inspector = inspect(conn)
            if "patients" in set(inspector.get_table_names(schema=schema_name)):
                cols = {
                    c["name"]
                    for c in inspector.get_columns("patients", schema=schema_name)
                }
                if "patient_type" in cols:
                    conn.execute(
                        text(
                            f'ALTER TABLE "{schema_name}"."patients" DROP COLUMN IF EXISTS patient_type'
                        )
                    )
                if "department_id" in cols:
                    conn.execute(
                        text(
                            f'ALTER TABLE "{schema_name}"."patients" DROP COLUMN IF EXISTS department_id CASCADE'
                        )
                    )
        except Exception as e:
            logger.warning(
                "Could not clean obsolete columns for schema=%s err=%s",
                schema_name,
                e,
                exc_info=True,
            )

    except Exception:
        # Let caller decide commit/rollback; do not swallow exceptions
        raise
    finally:
        # Always restore search_path
        try:
            _reset_search_path(conn)
        except Exception:
            pass


def _create_tenant_schema_and_tables(db: Session, schema_name: str) -> None:
    """
    Create tenant schema + tenant tables for NEW tenant registration.

    This should be deterministic and safe:
    - Create schema (fail fast if cannot)
    - Optionally reset schema objects in dev reset mode
    - Create tenant enums (schema-qualified)
    - Create tables (with circular dependency handling)
    """
    conn = db.connection()

    # Debug context can be invaluable later
    dbg = _debug_db_context(conn, schema_name)
    logger.info("Tenant create start schema=%s ctx=%s", schema_name, dbg)

    try:
        # Create schema
        conn.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{schema_name}"'))

        if not _schema_exists(conn, schema_name):
            raise RuntimeError(f"Schema creation did not take effect: {schema_name}")

        # Check if schema already has tables (shouldn't happen for fresh registration)
        # If it does and dev reset is not enabled, fail to prevent accidental data loss
        settings = get_settings()
        inspector = inspect(conn)
        existing_tables = inspector.get_table_names(schema=schema_name)
        if existing_tables:
            if settings.hms_dev_allow_tenant_schema_reset:
                logger.warning(
                    "DEV RESET enabled: dropping all objects in schema=%s", schema_name
                )
                _drop_schema_objects_for_reset(conn, schema_name)
            else:
                raise RuntimeError(
                    f"Tenant schema '{schema_name}' already contains tables ({len(existing_tables)}). "
                    f"Refusing to reset. Set HMS_DEV_ALLOW_TENANT_SCHEMA_RESET=true in .env ONLY in dev to allow reset."
                )

        # Set search_path for SQLAlchemy create paths.
        _set_search_path(conn, schema_name)

        # Create enums FIRST (schema-qualified, idempotent)
        for enum_name, enum_values in TENANT_ENUMS:
            try:
                _create_enum_in_schema(conn, schema_name, enum_name, enum_values)
            except DBAPIError as e:
                # Add extra diagnostics on enum creation failures
                locations = _find_enum_locations(conn, enum_name)
                ctx = _debug_db_context(conn, schema_name)
                logger.error(
                    "Enum create failed enum=%s schema=%s enum_locations=%s ctx=%s err=%s",
                    enum_name,
                    schema_name,
                    locations,
                    ctx,
                    e,
                    exc_info=True,
                )
                raise

        # Circular dependency: admissions <-> appointments handling
        from app.models.appointment import Appointment  # noqa: F401
        # from app.models.admission import Admission  # imported for side effects/metadata consistency

        # Create all tables except appointments, admissions, vitals, prescriptions, and prescription_items first
        # (vitals and prescriptions depend on appointments; prescription_items depends on prescriptions)
        for table in TENANT_TABLES:
            if table.name in (
                "appointments",
                "admissions",
                "vitals",
                "prescriptions",
                "prescription_items",
            ):
                continue
            _set_search_path(conn, schema_name)
            table.create(bind=conn, checkfirst=False)
            logger.info("Created tenant table=%s schema=%s", table.name, schema_name)

        # Create admissions without FK to appointments (manual SQL)
        # IMPORTANT: set search_path so unqualified enums resolve inside tenant schema.
        _set_search_path(conn, schema_name)
        create_admissions_sql = f"""
        CREATE TABLE "{schema_name}"."admissions" (
            id UUID NOT NULL,
            patient_id UUID NOT NULL,
            department_id UUID NOT NULL,
            primary_doctor_user_id UUID NOT NULL,
            admit_datetime TIMESTAMP WITH TIME ZONE NOT NULL,
            discharge_datetime TIMESTAMP WITH TIME ZONE,
            discharge_summary TEXT,
            notes VARCHAR(1000),
            status admission_status_enum DEFAULT 'ACTIVE' NOT NULL,
            source_opd_appointment_id UUID,
            created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP NOT NULL,
            updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP NOT NULL,
            PRIMARY KEY (id),
            FOREIGN KEY(patient_id) REFERENCES "{schema_name}"."patients" (id) ON DELETE CASCADE,
            FOREIGN KEY(department_id) REFERENCES "{schema_name}"."departments" (id) ON DELETE RESTRICT,
            FOREIGN KEY(primary_doctor_user_id) REFERENCES public.users (id) ON DELETE SET NULL
        )
        """
        conn.execute(text(create_admissions_sql))
        logger.info(
            "Created tenant table=admissions schema=%s (manual, without cross-FK)",
            schema_name,
        )

        # Create appointments (now can reference admissions)
        _set_search_path(conn, schema_name)
        Appointment.__table__.create(bind=conn, checkfirst=False)
        logger.info("Created tenant table=appointments schema=%s", schema_name)

        # Add FK from admissions -> appointments (best-effort)
        try:
            conn.execute(
                text(
                    f"""
                    ALTER TABLE "{schema_name}"."admissions"
                    ADD CONSTRAINT fk_admissions_source_opd_appointment_id
                    FOREIGN KEY (source_opd_appointment_id)
                    REFERENCES "{schema_name}"."appointments" (id)
                    ON DELETE SET NULL
                    """
                )
            )
        except Exception as e:
            # If already exists (or created by earlier attempt), don't fail creation
            logger.warning(
                "Could not add admissions->appointments FK schema=%s err=%s",
                schema_name,
                e,
            )

        # Now create vitals and prescriptions (they depend on appointments and admissions)
        from app.models.prescription import Prescription, PrescriptionItem
        from app.models.vital import Vital

        _set_search_path(conn, schema_name)
        Vital.__table__.create(bind=conn, checkfirst=False)
        logger.info("Created tenant table=vitals schema=%s", schema_name)

        _set_search_path(conn, schema_name)
        Prescription.__table__.create(bind=conn, checkfirst=False)
        logger.info("Created tenant table=prescriptions schema=%s", schema_name)

        # Create prescription_items after prescriptions (it depends on prescriptions)
        _set_search_path(conn, schema_name)
        PrescriptionItem.__table__.create(bind=conn, checkfirst=False)
        logger.info("Created tenant table=prescription_items schema=%s", schema_name)

        # Post-creation cleanup for obsolete columns (best-effort)
        try:
            inspector = inspect(conn)
            patients_cols = {
                c["name"] for c in inspector.get_columns("patients", schema=schema_name)
            }
            if "patient_type" in patients_cols:
                conn.execute(
                    text(
                        f'ALTER TABLE "{schema_name}"."patients" DROP COLUMN IF EXISTS patient_type'
                    )
                )
            if "department_id" in patients_cols:
                conn.execute(
                    text(
                        f'ALTER TABLE "{schema_name}"."patients" DROP COLUMN IF EXISTS department_id CASCADE'
                    )
                )
        except Exception as e:
            logger.warning(
                "Post-creation cleanup failed schema=%s err=%s",
                schema_name,
                e,
                exc_info=True,
            )

    except SQLAlchemyError as exc:
        # Do NOT commit/rollback here beyond ensuring search_path reset.
        # Caller (register_tenant) will handle transaction boundaries.
        ctx = _debug_db_context(conn, schema_name)
        logger.error(
            "Tenant schema/table creation failed schema=%s ctx=%s err=%s",
            schema_name,
            ctx,
            exc,
            exc_info=True,
        )
        raise RuntimeError(
            f"Failed to create tenant schema '{schema_name}': {exc}"
        ) from exc
    finally:
        try:
            _reset_search_path(conn)
        except Exception:
            pass


def register_tenant(
    db: Session,
    name: str,
    address: Optional[str],
    contact_email: str,
    contact_phone: Optional[str],
    license_number: str,
) -> Tenant:
    """
    FR-1: Hospital Self-Registration.

    - Ensure license_number is unique.
    - Create tenant in public.tenants.
    - Create schema and all tenant tables inside that schema.
    - Seed default roles, permissions, and departments.
    - Increment platform metrics.

    Transaction behavior:
    - This function does NOT call db.commit() on success.
      Caller / request lifecycle should commit.
    - On error, this function will rollback before raising.
    """
    existing = db.query(Tenant).filter(Tenant.license_number == license_number).first()
    if existing:
        raise ValueError("Hospital with this license number already exists.")

    conn = db.connection()

    # Generate unique schema name (rare collisions)
    schema_name = None
    settings = get_settings()
    for _ in range(settings.hms_tenant_schema_name_max_attempts):
        candidate = _generate_schema_name()
        if not _schema_exists(conn, candidate):
            schema_name = candidate
            break
    if not schema_name:
        raise RuntimeError(
            "Could not generate a unique tenant schema name after multiple attempts."
        )

    tenant = Tenant(
        name=name,
        address=address,
        contact_email=contact_email,
        contact_phone=contact_phone,
        license_number=license_number,
        status=TenantStatus.PENDING,
        schema_name=schema_name,
    )

    try:
        db.add(tenant)
        db.flush()  # assign tenant.id

        # Create schema + tenant tables (no internal commits)
        _create_tenant_schema_and_tables(db, schema_name)

        # Seed defaults inside tenant schema
        _set_search_path(conn, schema_name)
        seed_tenant_defaults(db)
        _reset_search_path(conn)

        # Increment platform metrics (public schema)
        from app.services.tenant_metrics_service import increment_tenants

        increment_tenants(db)

        return tenant

    except Exception as exc:
        # Always rollback and reset search_path for safety
        db.rollback()
        try:
            _reset_search_path(conn)
        except Exception:
            pass
        raise RuntimeError(f"Tenant registration failed: {exc}") from exc
