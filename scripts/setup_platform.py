#!/usr/bin/env python3
# scripts/setup_platform.py
"""
Platform setup (public schema).
This script is safe to run many times (idempotent).

Design notes:
- SUPER_ADMIN creation is idempotent:
  - if user exists -> it is updated to "login-ready"
  - if missing -> it is created
- When you run both operations, metrics runs first (so schema sanity happens before user ops).

Examples:
  # Only init metrics
  python -m scripts.setup_platform --init-metrics

  # Only ensure super admin (from args)
  python -m scripts.setup_platform --ensure-super-admin --email admin@platform.local --password "Admin@12345"

  # Ensure both (metrics first)
  python -m scripts.setup_platform --init-metrics --ensure-super-admin --email admin@platform.local 
  --password "Admin@12345"

Examples - env-driven:
  # Ensure both, credentials read from env
  python -m scripts.setup_platform --init-metrics --ensure-super-admin
"""

from __future__ import annotations

import argparse
import logging
import sys
from uuid import UUID

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.database import SessionLocal
from app.core.security import get_password_hash
from app.models.tenant_metrics import TenantMetrics
from app.models.user import User, UserStatus

logger = logging.getLogger(__name__)

TENANT_METRICS_ID = UUID("00000000-0000-0000-0000-000000000001")


def ensure_tenant_metrics_row(db: Session) -> None:
    """
    Ensure the singleton TenantMetrics row exists.
    Safe to run on every deploy.
    """
    existing = db.query(TenantMetrics).filter(TenantMetrics.id == TENANT_METRICS_ID).first()
    if existing:
        print("tenant_metrics row exists")
        return

    db.add(TenantMetrics(id=TENANT_METRICS_ID))
    db.commit()
    print("tenant_metrics initialized")


def ensure_super_admin(
    db: Session,
    *,
    email: str,
    password: str,
    first_name: str = "Super",
    last_name: str = "Admin",
) -> User:
    """
    Ensure a platform SUPER_ADMIN exists (tenant_id is NULL).
    Idempotent and safe to run during deploy.

    Behavior:
    - If user exists: update fields to be login-ready + refresh password if provided.
    - If missing: create it.
    """
    existing = db.query(User).filter(User.tenant_id.is_(None), User.email == email).first()

    hashed = get_password_hash(password)

    if existing:
        # Keep it login-ready and predictable.
        existing.first_name = existing.first_name or first_name
        existing.last_name = existing.last_name or last_name

        existing.status = UserStatus.ACTIVE
        existing.is_active = True
        existing.is_deleted = False
        existing.must_change_password = False
        existing.email_verified = True

        # If password changes in env, we intentionally rotate it.
        existing.hashed_password = hashed

        db.commit()
        print(f"UPER_ADMIN ensured (updated if needed): {email}")
        return existing

    user = User(
        tenant_id=None,
        email=email,
        hashed_password=hashed,
        first_name=first_name,
        last_name=last_name,
        status=UserStatus.ACTIVE,
        is_active=True,
        is_deleted=False,
        must_change_password=False,
        email_verified=True,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    print(f"SUPER_ADMIN created: {email}")
    return user


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="HMS platform setup (public schema)")
    p.add_argument("--init-metrics", action="store_true", help="Ensure tenant_metrics row exists")
    p.add_argument(
        "--ensure-super-admin",
        action="store_true",
        help="Ensure SUPER_ADMIN exists (from args if provided, else from env)",
    )

    # Optional CLI overrides (otherwise env is used)
    p.add_argument("--email", type=str, help="SUPER_ADMIN email (or use env SUPER_ADMIN_EMAIL)")
    p.add_argument("--password", type=str, help="SUPER_ADMIN password (or use env SUPER_ADMIN_PASSWORD)")
    p.add_argument("--first-name", type=str, default=None, help="Default: env SUPER_ADMIN_FIRST_NAME or 'Super'")
    p.add_argument("--last-name", type=str, default=None, help="Default: env SUPER_ADMIN_LAST_NAME or 'Admin'")
    return p.parse_args()


def main() -> None:
    args = parse_args()

    if not args.init_metrics and not args.ensure_super_admin:
        print("Nothing to do. Use --init-metrics and/or --ensure-super-admin.")
        sys.exit(1)

    # Load settings (validates .env and provides typed access to config)
    settings = get_settings()

    # Resolve super-admin inputs only if needed
    email: str | None = None
    password: str | None = None
    first_name: str = "Super"
    last_name: str = "Admin"

    if args.ensure_super_admin:
        # CLI args take precedence, then settings (from .env), then defaults
        email = args.email or settings.super_admin_email
        password = args.password or settings.super_admin_password
        first_name = args.first_name or settings.super_admin_first_name
        last_name = args.last_name or settings.super_admin_last_name

        if not email or not password:
            raise SystemExit(
                "✗ SUPER_ADMIN credentials missing.\n"
                "Provide --email/--password OR set env SUPER_ADMIN_EMAIL and SUPER_ADMIN_PASSWORD."
            )

    db: Session = SessionLocal()
    try:
        # If both flags are present, metrics runs first (as requested).
        if args.init_metrics:
            ensure_tenant_metrics_row(db)

        if args.ensure_super_admin:
            ensure_super_admin(
                db,
                email=email,  # type: ignore[arg-type]
                password=password,  # type: ignore[arg-type]
                first_name=first_name,
                last_name=last_name,
            )

    except Exception:
        db.rollback()
        logger.exception("Platform setup failed")
        raise
    finally:
        db.close()


if __name__ == "__main__":
    main()