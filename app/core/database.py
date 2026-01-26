# app/core/database.py
from __future__ import annotations

from contextlib import contextmanager
from typing import Generator

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import NullPool

from app.core.config import get_settings

settings = get_settings()


def _is_pooler_url(url: str) -> bool:
    # Supabase pooler commonly uses port 6543
    return ":6543" in url or "pooler.supabase.com" in url


# Use app URL for runtime (pooler is OK), but make it pooler-safe.
DATABASE_URL = settings.database_url

connect_args: dict = {}
if _is_pooler_url(DATABASE_URL):
    # psycopg3 prepared statements + transaction pooler = DuplicatePreparedStatement
    connect_args["prepare_threshold"] = None

# When DB itself is a pooler, don't double-pool client-side.
engine = create_engine(
    DATABASE_URL,
    future=True,
    pool_pre_ping=True,
    connect_args=connect_args,
    poolclass=NullPool if _is_pooler_url(DATABASE_URL) else None,  # type: ignore[arg-type]
)

SessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=engine,
    future=True,
)


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@contextmanager
def tenant_schema_session(schema_name: str) -> Generator[Session, None, None]:
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
