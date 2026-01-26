# app/services/task_service.py
"""
Service for managing background tasks with Redis + database fallback.
"""

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy.orm import Session

from app.core.redis import cache_get, cache_set, cache_delete, is_redis_available
from app.models.background_task import BackgroundTask, TaskStatus

logger = logging.getLogger(__name__)

# Redis key prefix for task status
REDIS_TASK_KEY_PREFIX = "task:"
# TTL for task status in Redis (24 hours)
REDIS_TASK_TTL = 86400


def _get_redis_task_key(task_id: str) -> str:
    """Get Redis key for task status."""
    return f"{REDIS_TASK_KEY_PREFIX}{task_id}"


def create_task(
    db: Session,
    user_id: uuid.UUID,
    task_type: str,
    parameters: Optional[dict] = None,
) -> uuid.UUID:
    """
    Create a new background task.
    Returns the task ID.
    """
    task_id = uuid.uuid4()
    task_data = {
        "id": str(task_id),
        "user_id": str(user_id),
        "task_type": task_type,
        "status": TaskStatus.PENDING.value,
        "progress": 0,
        "message": "Task queued",
        "error": None,
        "parameters": json.dumps(parameters) if parameters else None,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "started_at": None,
        "completed_at": None,
    }

    # Try Redis first
    if is_redis_available():
        try:
            cache_set(
                _get_redis_task_key(str(task_id)),
                json.dumps(task_data),
                ttl=REDIS_TASK_TTL,
            )
            logger.info(f"Task {task_id} created in Redis")
        except Exception as e:
            logger.warning(
                f"Failed to create task in Redis: {e}, falling back to database"
            )

    # Always create in database as fallback
    try:
        db_task = BackgroundTask(
            id=task_id,
            user_id=user_id,
            task_type=task_type,
            status=TaskStatus.PENDING,
            progress=0,
            message="Task queued",
            parameters=json.dumps(parameters) if parameters else None,
        )
        db.add(db_task)
        db.commit()
        logger.info(f"Task {task_id} created in database")
    except Exception as e:
        db.rollback()
        logger.error(f"Failed to create task in database: {e}")
        raise

    return task_id


def update_task_status(
    db: Session,
    task_id: uuid.UUID,
    status: TaskStatus,
    progress: Optional[int] = None,
    message: Optional[str] = None,
    error: Optional[str] = None,
) -> None:
    """
    Update task status. Updates both Redis and database.
    """
    now = datetime.now(timezone.utc)

    # Update Redis if available
    if is_redis_available():
        try:
            redis_key = _get_redis_task_key(str(task_id))
            cached_data = cache_get(redis_key)
            if cached_data:
                task_data = json.loads(cached_data)
                task_data["status"] = status.value
                if progress is not None:
                    task_data["progress"] = progress
                if message is not None:
                    task_data["message"] = message
                if error is not None:
                    task_data["error"] = error
                if status == TaskStatus.RUNNING and not task_data.get("started_at"):
                    task_data["started_at"] = now.isoformat()
                if status in (TaskStatus.COMPLETED, TaskStatus.FAILED):
                    task_data["completed_at"] = now.isoformat()

                cache_set(redis_key, json.dumps(task_data), ttl=REDIS_TASK_TTL)
        except Exception as e:
            logger.warning(f"Failed to update task in Redis: {e}")

    # Always update database
    try:
        db_task = db.query(BackgroundTask).filter(BackgroundTask.id == task_id).first()
        if db_task:
            db_task.status = status
            if progress is not None:
                db_task.progress = progress
            if message is not None:
                db_task.message = message
            if error is not None:
                db_task.error = error
            if status == TaskStatus.RUNNING and not db_task.started_at:
                db_task.started_at = now
            if status in (TaskStatus.COMPLETED, TaskStatus.FAILED):
                db_task.completed_at = now

            db.commit()
    except Exception as e:
        db.rollback()
        logger.error(f"Failed to update task in database: {e}")


def get_task_status(
    db: Session,
    task_id: uuid.UUID,
    user_id: Optional[uuid.UUID] = None,
) -> Optional[dict]:
    """
    Get task status. Checks Redis first, falls back to database.
    Returns None if task not found.
    """
    # Try Redis first
    if is_redis_available():
        try:
            redis_key = _get_redis_task_key(str(task_id))
            cached_data = cache_get(redis_key)
            if cached_data:
                task_data = json.loads(cached_data)
                # Verify user_id if provided
                if user_id and task_data.get("user_id") != str(user_id):
                    return None
                return task_data
        except Exception as e:
            logger.warning(f"Failed to get task from Redis: {e}")

    # Fallback to database
    try:
        query = db.query(BackgroundTask).filter(BackgroundTask.id == task_id)
        if user_id:
            query = query.filter(BackgroundTask.user_id == user_id)
        db_task = query.first()

        if not db_task:
            return None

        return {
            "id": str(db_task.id),
            "user_id": str(db_task.user_id),
            "task_type": db_task.task_type,
            "status": db_task.status.value,
            "progress": db_task.progress,
            "message": db_task.message,
            "error": db_task.error,
            "parameters": db_task.parameters,
            "created_at": db_task.created_at.isoformat()
            if db_task.created_at
            else None,
            "started_at": db_task.started_at.isoformat()
            if db_task.started_at
            else None,
            "completed_at": db_task.completed_at.isoformat()
            if db_task.completed_at
            else None,
        }
    except Exception as e:
        logger.error(f"Failed to get task from database: {e}")
        return None


def get_user_active_task(
    db: Session,
    user_id: uuid.UUID,
    task_type: Optional[str] = None,
) -> Optional[dict]:
    """
    Get the user's active (pending or running) task.
    Checks database (Redis doesn't support querying by user_id easily).
    """
    try:
        query = db.query(BackgroundTask).filter(
            BackgroundTask.user_id == user_id,
            BackgroundTask.status.in_([TaskStatus.PENDING, TaskStatus.RUNNING]),
        )
        if task_type:
            query = query.filter(BackgroundTask.task_type == task_type)

        db_task = query.order_by(BackgroundTask.created_at.desc()).first()

        if not db_task:
            return None

        # Also check Redis and merge if available
        task_data = {
            "id": str(db_task.id),
            "user_id": str(db_task.user_id),
            "task_type": db_task.task_type,
            "status": db_task.status.value,
            "progress": db_task.progress,
            "message": db_task.message,
            "error": db_task.error,
            "parameters": db_task.parameters,
            "created_at": db_task.created_at.isoformat()
            if db_task.created_at
            else None,
            "started_at": db_task.started_at.isoformat()
            if db_task.started_at
            else None,
            "completed_at": db_task.completed_at.isoformat()
            if db_task.completed_at
            else None,
        }

        # If Redis has more recent data, use it
        if is_redis_available():
            try:
                redis_key = _get_redis_task_key(str(db_task.id))
                cached_data = cache_get(redis_key)
                if cached_data:
                    redis_data = json.loads(cached_data)
                    # Use Redis data if it's more recent (has started_at or completed_at)
                    if redis_data.get("started_at") or redis_data.get("completed_at"):
                        task_data.update(redis_data)
            except Exception:
                pass

        return task_data
    except Exception as e:
        logger.error(f"Failed to get user active task: {e}")
        return None


def delete_task(
    db: Session,
    task_id: uuid.UUID,
) -> None:
    """
    Delete task from both Redis and database.
    """
    # Delete from Redis
    if is_redis_available():
        try:
            cache_delete(_get_redis_task_key(str(task_id)))
        except Exception as e:
            logger.warning(f"Failed to delete task from Redis: {e}")

    # Delete from database
    try:
        db.query(BackgroundTask).filter(BackgroundTask.id == task_id).delete()
        db.commit()
    except Exception as e:
        db.rollback()
        logger.error(f"Failed to delete task from database: {e}")
