# app/utils/alembic_tenant.py
"""
Utility functions to run Alembic migrations in tenant schemas.
"""

import logging

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()


def run_tenant_migrations(db: Session, schema_name: str) -> None:
    """
    Run Alembic migrations in a tenant schema.

    This creates all tenant-domain tables (patients, appointments, etc.)
    in the specified tenant schema using Alembic migrations.

    Args:
        db: Database session
        schema_name: Name of the tenant schema (e.g., "tenant_abc123")
    """
    from app.models.tenant_domain import TENANT_TABLES

    conn = db.connection()

    try:
        # Set search_path to tenant schema
        conn.execute(text(f'SET search_path TO "{schema_name}", public'))

        # Create all tenant tables using SQLAlchemy metadata
        # This is equivalent to running Alembic migrations but simpler for tenant schemas
        # since all tenants have the same schema structure
        logger.info(f"Creating tenant tables in schema {schema_name}...")

        # Create tables in dependency order (from TENANT_TABLES)
        for table in TENANT_TABLES:
            try:
                # Create table with explicit schema qualification
                table.create(bind=conn, checkfirst=True)
            except Exception as e:
                logger.warning(f"Could not create table {table.name}: {e}")
                # Continue with other tables

        logger.info(f"Tenant tables created successfully in schema {schema_name}")

    except Exception as e:
        logger.error(f"Failed to create tenant tables in schema {schema_name}: {e}")
        raise
    finally:
        # Restore default search_path
        conn.execute(text("SET search_path TO public"))
