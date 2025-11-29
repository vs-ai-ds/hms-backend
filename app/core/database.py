from contextlib import contextmanager
from typing import Generator

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker, Session

from app.core.config import get_settings

settings = get_settings()

# Main SQLAlchemy engine (shared across public + tenant schemas)
engine = create_engine(
    str(settings.database_url),
    future=True,
    pool_pre_ping=True,
)

# Session factory
SessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=engine,
    future=True,
)


def get_db() -> Generator[Session, None, None]:
    """
    FastAPI dependency that yields a DB session.

    For now this uses the default search_path.
    Later, when we have authenticated tenants, we will
    set the search_path based on the tenant's schema.
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@contextmanager
def tenant_schema_session(schema_name: str) -> Generator[Session, None, None]:
    """
    Context manager for running queries in a specific tenant schema.

    Usage (outside of FastAPI dependencies):
        with tenant_schema_session("tenant_abc123") as db:
            db.query(...)

    Inside a FastAPI endpoint, we will more commonly use get_db()
    and set the search_path using middleware based on JWT tenant info.
    """
    db = SessionLocal()
    try:
        db.execute(text(f'SET search_path TO "{schema_name}", public'))
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()