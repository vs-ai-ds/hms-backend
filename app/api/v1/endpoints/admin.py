# app/api/v1/endpoints/admin.py
"""
Admin endpoints for SUPER_ADMIN operations.
Currently includes demo maintenance controls.
"""
import logging
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, OperationalError
from sqlalchemy.orm import Session

from app.api.v1.endpoints.auth import get_current_user
from app.core.config import get_settings
from app.core.database import get_db
from app.models.user import User
from app.services.user_role_service import get_user_role_names

router = APIRouter()
logger = logging.getLogger(__name__)

settings = get_settings()

# In-memory TTL for auto-refresh check
_last_demo_freshen_at: Optional[datetime] = None

# Postgres advisory lock ID for demo refresh operations
# Using a fixed ID to ensure only one operation runs at a time
DEMO_REFRESH_LOCK_ID = 1234567890


def _ensure_super_admin(db: Session, current_user: User) -> None:
    """Ensure the current user is SUPER_ADMIN."""
    user_roles = get_user_role_names(db, current_user, tenant_schema_name=None)
    if "SUPER_ADMIN" not in user_roles:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only SUPER_ADMIN can access admin endpoints.",
        )


def _acquire_advisory_lock(db: Session, lock_id: int) -> bool:
    """
    Try to acquire the advisory lock. Returns True if acquired, else False.
    Must not leave the session in an aborted transaction state.
    """
    try:
        val = db.execute(
            text("SELECT pg_try_advisory_xact_lock(:lock_id)"),
            {"lock_id": lock_id},
        ).scalar()
        return bool(val)
    except Exception as e:
        logger.warning("Failed to acquire advisory xact lock (treat as not acquired): %s", e, exc_info=True)
        try:
            db.rollback()
        except Exception:
            pass
        return False


def _run_seed_script(action: str, freshen_days: Optional[int] = None) -> tuple[bool, str]:
    """
    Run the seed_demo_data.py script with the given action.
    Returns (success, message).
    """
    repo_root = Path(__file__).resolve().parent.parent.parent.parent.parent
    script_path = repo_root / "scripts" / "seed_demo_data.py"

    if not script_path.exists():
        return False, f"Seed script not found at {script_path}"

    try:
        cmd = [sys.executable, "-m", "scripts.seed_demo_data"]
        if action == "seed":
            cmd.append("--seed")
        elif action == "freshen":
            cmd.append("--freshen")
            if freshen_days:
                cmd.extend(["--freshen-days", str(freshen_days)])
        elif action == "reset":
            cmd.append("--reset")
        else:
            return False, f"Unknown action: {action}"

        result = subprocess.run(
            cmd,
            cwd=repo_root,
            capture_output=True,
            text=True,
            timeout=600,  # 10 minute timeout
        )

        if result.returncode == 0:
            return True, result.stdout or f"Demo {action} completed successfully"
        else:
            error_msg = result.stderr or result.stdout or "Unknown error"
            logger.error(f"Seed script failed: {error_msg}")
            return False, f"Seed script failed: {error_msg[:500]}"  # Limit error message length

    except subprocess.TimeoutExpired:
        return False, "Seed script timed out after 10 minutes"
    except Exception as e:
        logger.error(f"Failed to run seed script: {e}", exc_info=True)
        return False, f"Failed to run seed script: {str(e)}"


class DemoRefreshRequest(BaseModel):
    action: str = Field(..., description="Action to perform: 'seed' or 'freshen'")
    freshen_days: int = Field(7, ge=1, le=365, description="Days to shift forward for freshen (only used for freshen action)")


class DemoRefreshResponse(BaseModel):
    status: str
    action: str
    freshen_days: Optional[int] = None
    message: str


@router.post("/demo/refresh", response_model=DemoRefreshResponse, tags=["admin"])
def refresh_demo_data(
    payload: DemoRefreshRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> DemoRefreshResponse:
    """
    Refresh demo data (seed or freshen).
    
    Only available when DEMO_MODE=true and requires SUPER_ADMIN.
    Uses Postgres advisory lock to ensure only one operation runs at a time.
    """
    # Check DEMO_MODE
    if not settings.demo_mode:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Demo mode is not enabled. Set DEMO_MODE=true to use this endpoint.",
        )

    # Check SUPER_ADMIN
    _ensure_super_admin(db, current_user)

    # Validate action
    if payload.action not in ("seed", "freshen", "reset"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Action must be 'seed', 'freshen', or 'reset'",
        )

    if not _acquire_advisory_lock(db, DEMO_REFRESH_LOCK_ID):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Demo refresh already running. Please wait for the current operation to complete.",
        )

    try:
        # Run the seed script
        freshen_days = payload.freshen_days if payload.action == "freshen" else None
        success, message = _run_seed_script(payload.action, freshen_days)

        if not success:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Demo refresh failed: {message}",
            )

        return DemoRefreshResponse(
            status="ok",
            action=payload.action,
            freshen_days=freshen_days,
            message=message,
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Unexpected error during demo refresh: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred during demo refresh. Check server logs for details.",
        )


def check_and_freshen_demo_on_login(db: Session) -> None:
    """
    Check if demo data should be auto-freshened on login.
    This is called after successful login (token issued).
    
    Only runs if:
    - DEMO_MODE=true
    - DEMO_AUTO_REFRESH_ON_LOGIN=true
    - TTL has expired (default 24h)
    - Lock can be acquired (skip silently if not)
    """
    global _last_demo_freshen_at

    if not settings.demo_mode or not settings.demo_auto_refresh_on_login:
        return

    # Check TTL
    now = datetime.now(timezone.utc)
    ttl_hours = settings.demo_refresh_ttl_hours
    ttl_delta = timedelta(hours=ttl_hours)

    if _last_demo_freshen_at and (now - _last_demo_freshen_at) < ttl_delta:
        # Still within TTL, skip
        return

    # Try to acquire lock (non-blocking)
    if not _acquire_advisory_lock(db, DEMO_REFRESH_LOCK_ID):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Demo refresh already running. Please wait for the current operation to complete.",
        )

    try:
        # Run freshen with default days
        freshen_days = settings.demo_freshen_days
        success, message = _run_seed_script("freshen", freshen_days)

        if success:
            _last_demo_freshen_at = now
            logger.info(f"Demo auto-freshen completed: {message}")
        else:
            logger.warning(f"Demo auto-freshen failed: {message}")

    except Exception as e:
        logger.error(f"Error during demo auto-freshen: {e}", exc_info=True)

