# app/services/tenant_service.py
import uuid
from typing import Optional

from sqlalchemy.orm import Session
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.models.tenant_global import Tenant, TenantStatus
from app.models.tenant_domain import TENANT_TABLES


def _generate_schema_name() -> str:
    """
    Generate a PostgreSQL schema name for a tenant.

    Uses a short UUID; ensures it's a safe identifier:
    - lower-case
    - alphanumeric + underscore
    """
    short_id = uuid.uuid4().hex[:8]
    return f"tenant_{short_id}"


def _create_tenant_schema_and_tables(db: Session, schema_name: str) -> None:
    """
    Create the tenant schema and all tenant-domain tables (patients, appointments, etc.)
    inside that schema.

    Uses search_path to make SQLAlchemy models target this schema.
    """
    conn = db.connection()
    try:
        # Create schema
        conn.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{schema_name}"'))
        # Switch search_path
        conn.execute(text(f'SET search_path TO "{schema_name}", public'))

        # Create tables for this tenant
        for table in TENANT_TABLES:
            table.create(bind=conn, checkfirst=True)

    except SQLAlchemyError as exc:
        # Restore default search_path before raising
        conn.execute(text('SET search_path TO public'))
        raise RuntimeError(f"Failed to create tenant schema '{schema_name}': {exc}") from exc

    # Restore default search_path (public first)
    conn.execute(text('SET search_path TO public'))


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
    """
    existing = (
        db.query(Tenant)
        .filter(Tenant.license_number == license_number)
        .first()
    )
    if existing:
        raise ValueError("Hospital with this license number already exists.")

    schema_name = _generate_schema_name()

    tenant = Tenant(
        name=name,
        address=address,
        contact_email=contact_email,
        contact_phone=contact_phone,
        license_number=license_number,
        status=TenantStatus.PENDING,
        schema_name=schema_name,
    )
    db.add(tenant)
    db.flush()  # assign tenant.id

    # Create schema + tenant tables
    _create_tenant_schema_and_tables(db, schema_name)

    return tenant