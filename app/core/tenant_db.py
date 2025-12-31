# app/core/tenant_db.py
"""
Small Postgres search_path helpers.

- Endpoints and scripts both need to SET search_path safely.
- This stays dependency-free (no FastAPI Depends, no auth imports).
"""

from __future__ import annotations

import logging
from contextlib import contextmanager

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


def ensure_search_path(db: Session, tenant_schema_name: str) -> None:
    """
    Ensure tenant schema is first in search_path (with public as fallback).

    NOTE:
    - We never switch to only "public" inside request handling because ORM refresh/join-load
      may hit tenant tables again and crash.
    """
    if not tenant_schema_name or not tenant_schema_name.strip():
        raise HTTPException(status_code=500, detail="Tenant schema name missing in request context.")

    try:
        db.execute(text(f'SET search_path TO "{tenant_schema_name}", public'))
    except Exception:
        logger.exception("Failed to set search_path tenant=%s", tenant_schema_name)
        raise


@contextmanager
def tenant_search_path(db: Session, tenant_schema_name: str):
    """
    Context manager for scripts/admin jobs.
    Restores the previous search_path even if an exception happens.
    """
    original = db.execute(text("SHOW search_path")).scalar()
    ensure_search_path(db, tenant_schema_name)
    try:
        yield
    finally:
        try:
            # `SHOW search_path` returns a value safe for `SET search_path TO <value>`
            db.execute(text("SET search_path TO " + str(original)))
        except Exception:
            logger.exception("Failed to restore original search_path")