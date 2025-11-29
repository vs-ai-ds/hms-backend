# app/background/tasks.py
from typing import Callable, Any

from fastapi import BackgroundTasks


def enqueue_task(
    background_tasks: BackgroundTasks,
    func: Callable[..., Any],
    *args: Any,
    **kwargs: Any,
) -> None:
    """
    Helper to add a background task in a consistent way.

    Usage in endpoints:
        from fastapi import BackgroundTasks
        from app.background.tasks import enqueue_task
        from app.services.notification_service import send_notification_email

        @router.post("/something")
        def handler(..., background_tasks: BackgroundTasks):
            enqueue_task(
                background_tasks,
                send_notification_email,
                to_email="user@example.com",
                subject="Hello",
                body="World",
            )
    """
    background_tasks.add_task(func, *args, **kwargs)