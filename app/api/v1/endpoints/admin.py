# app/api/v1/endpoints/admin.py
"""
Admin endpoints for SUPER_ADMIN operations.
Currently includes demo maintenance controls.
"""

import logging
import subprocess
import sys
import threading
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.api.v1.endpoints.auth import get_current_user
from app.core.config import get_settings
from app.core.database import get_db, SessionLocal
from app.models.user import User
from app.services.task_service import (
    create_task,
    update_task_status,
    get_task_status,
    get_user_active_task,
)
from app.services.user_role_service import get_user_role_names
from app.models.background_task import TaskStatus, TaskType

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
        logger.warning(
            "Failed to acquire advisory xact lock (treat as not acquired): %s",
            e,
            exc_info=True,
        )
        try:
            db.rollback()
        except Exception:
            pass
        return False


def _run_seed_script(
    action: str, freshen_days: Optional[int] = None
) -> tuple[bool, str]:
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
            return (
                False,
                f"Seed script failed: {error_msg[:500]}",
            )  # Limit error message length

    except subprocess.TimeoutExpired:
        return False, "Seed script timed out after 10 minutes"
    except Exception as e:
        logger.error(f"Failed to run seed script: {e}", exc_info=True)
        return False, f"Failed to run seed script: {str(e)}"


def _run_seed_script_with_progress(
    task_id: uuid.UUID,
    action: str,
    freshen_days: Optional[int] = None,
) -> None:
    """
    Run the seed script in background thread and update task progress.
    Parses HMS_PROGRESS markers from stdout for real-time progress updates.
    """
    import re

    db = SessionLocal()
    try:
        update_task_status(
            db,
            task_id,
            TaskStatus.RUNNING,
            progress=0,
            message=f"Starting {action} operation...",
        )

        repo_root = Path(__file__).resolve().parent.parent.parent.parent.parent
        script_path = repo_root / "scripts" / "seed_demo_data.py"

        if not script_path.exists():
            update_task_status(
                db,
                task_id,
                TaskStatus.FAILED,
                progress=0,
                error=f"Seed script not found at {script_path}",
            )
            return

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
            update_task_status(
                db,
                task_id,
                TaskStatus.FAILED,
                progress=0,
                error=f"Unknown action: {action}",
            )
            return

        process = subprocess.Popen(
            cmd,
            cwd=repo_root,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )

        progress_regex = re.compile(r"^HMS_PROGRESS\|(\d{1,3})\|(.*)$")
        last_progress = 0
        last_message = None

        for line in process.stdout:
            line = line.rstrip()
            if not line:
                continue

            match = progress_regex.match(line)
            if match:
                pct = int(match.group(1))
                msg = match.group(2).strip()

                pct = max(0, min(100, pct))

                if pct > last_progress or msg != last_message:
                    update_task_status(
                        db,
                        task_id,
                        TaskStatus.RUNNING,
                        progress=pct,
                        message=msg,
                    )
                    last_progress = pct
                    last_message = msg

        stdout, stderr = process.communicate()

        if process.returncode == 0:
            update_task_status(
                db,
                task_id,
                TaskStatus.COMPLETED,
                progress=100,
                message="Completed",
            )
        else:
            stderr_text = stderr.strip() if stderr else "Unknown error"
            logger.error(f"Seed script failed: {stderr_text}")
            update_task_status(
                db,
                task_id,
                TaskStatus.FAILED,
                progress=last_progress,
                error=f"Seed script failed: {stderr_text[:500]}",
            )

    except subprocess.TimeoutExpired:
        update_task_status(
            db,
            task_id,
            TaskStatus.FAILED,
            progress=0,
            error="Seed script timed out after 10 minutes",
        )
    except Exception as e:
        logger.error(f"Failed to run seed script: {e}", exc_info=True)
        update_task_status(
            db,
            task_id,
            TaskStatus.FAILED,
            progress=0,
            error=f"Failed to run seed script: {str(e)}",
        )
    finally:
        db.close()


class DemoRefreshRequest(BaseModel):
    action: str = Field(
        ..., description="Action to perform: 'seed', 'freshen', or 'reset'"
    )
    freshen_days: int = Field(
        7,
        ge=1,
        le=365,
        description="Days to shift forward for freshen (only used for freshen action)",
    )


class DemoRefreshResponse(BaseModel):
    status: str
    action: str
    freshen_days: Optional[int] = None
    message: str


class TaskStartResponse(BaseModel):
    task_id: str
    status: str
    message: str


class TaskStatusResponse(BaseModel):
    id: str
    user_id: str
    task_type: str
    status: str
    progress: int
    message: Optional[str] = None
    error: Optional[str] = None
    created_at: str
    started_at: Optional[str] = None
    completed_at: Optional[str] = None


@router.post("/demo/refresh/start", response_model=TaskStartResponse, tags=["admin"])
def start_demo_refresh(
    payload: DemoRefreshRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> TaskStartResponse:
    """
    Start demo data refresh operation in background.
    Returns immediately with task_id. Use /demo/refresh/status to check progress.

    Only available when DEMO_MODE=true and requires SUPER_ADMIN.
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

    # Check if user already has an active task
    task_type_map = {
        "seed": TaskType.DEMO_SEED.value,
        "freshen": TaskType.DEMO_FRESHEN.value,
        "reset": TaskType.DEMO_RESET.value,
    }
    task_type = task_type_map[payload.action]

    active_task = get_user_active_task(db, current_user.id, task_type)
    if active_task and active_task["status"] in (
        TaskStatus.PENDING.value,
        TaskStatus.RUNNING.value,
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Demo {payload.action} operation already in progress. Please wait for it to complete.",
        )

    # Check advisory lock (prevent multiple operations system-wide)
    if not _acquire_advisory_lock(db, DEMO_REFRESH_LOCK_ID):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Demo refresh already running. Please wait for the current operation to complete.",
        )

    try:
        # Create task
        freshen_days = payload.freshen_days if payload.action == "freshen" else None
        parameters = {"action": payload.action, "freshen_days": freshen_days}
        task_id = create_task(
            db,
            current_user.id,
            task_type,
            parameters,
        )

        # Start background thread
        thread = threading.Thread(
            target=_run_seed_script_with_progress,
            args=(task_id, payload.action, freshen_days),
            daemon=True,
        )
        thread.start()

        return TaskStartResponse(
            task_id=str(task_id),
            status="started",
            message=f"Demo {payload.action} operation started. Use task_id to check status.",
        )

    except Exception as e:
        logger.error(f"Failed to start demo refresh: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to start demo refresh operation. Check server logs for details.",
        )


@router.get("/demo/refresh/status", response_model=TaskStatusResponse, tags=["admin"])
def get_demo_refresh_status(
    task_id: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> TaskStatusResponse:
    """
    Get status of demo refresh task.
    If task_id is not provided, returns the user's active task.
    """
    # Check SUPER_ADMIN
    _ensure_super_admin(db, current_user)

    if task_id:
        try:
            task_uuid = uuid.UUID(task_id)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid task_id format",
            )

        task_data = get_task_status(db, task_uuid, current_user.id)
    else:
        # Get user's active task
        task_data = get_user_active_task(db, current_user.id)

    if not task_data:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Task not found",
        )

    return TaskStatusResponse(**task_data)


# Keep old endpoint for backward compatibility (deprecated)
@router.post("/demo/refresh", response_model=DemoRefreshResponse, tags=["admin"])
def refresh_demo_data(
    payload: DemoRefreshRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> DemoRefreshResponse:
    """
    Refresh demo data (seed or freshen) - SYNCHRONOUS (deprecated).

    This endpoint is kept for backward compatibility but is deprecated.
    Use /demo/refresh/start for async operation with progress tracking.

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
