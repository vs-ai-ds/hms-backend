# app/services/notification_service.py
from typing import Optional

from sqlalchemy.orm import Session
from sqlalchemy.exc import SQLAlchemyError

from app.models.notification import Notification, NotificationChannel, NotificationStatus
from app.models.user import User
from app.notifications.email.base import send_email
from app.notifications.sms.base import send_sms
from app.notifications.whatsapp.base import send_whatsapp


def _log_notification(
    db: Session,
    *,
    channel: NotificationChannel,
    recipient: str,
    subject: str | None,
    message: str,
    triggered_by: Optional[User],
    status: NotificationStatus,
    error_message: str | None = None,
) -> Notification:
    notif = Notification(
        channel=channel,
        recipient=recipient,
        subject=subject,
        message=message,
        triggered_by_id=triggered_by.id if triggered_by else None,
        status=status,
        error_message=error_message,
    )
    try:
        db.add(notif)
        db.commit()
        db.refresh(notif)
    except SQLAlchemyError:
        db.rollback()
        # For now, don't propagate; this should not break main flow.
        print("[NOTIFICATION LOG ERROR] Failed to log notification.")
    return notif


def send_notification_email(
    db: Session,
    *,
    to_email: str,
    subject: str,
    body: str,
    triggered_by: Optional[User] = None,
    reason: Optional[str] = None,
) -> None:
    """
    Send an email and log it in tenant notifications table.
    """
    try:
        send_email(to_email=to_email, subject=subject, body=body, reason=reason)
        _log_notification(
            db,
            channel=NotificationChannel.EMAIL,
            recipient=to_email,
            subject=subject,
            message=body,
            triggered_by=triggered_by,
            status=NotificationStatus.SENT,
        )
    except Exception as exc:
        _log_notification(
            db,
            channel=NotificationChannel.EMAIL,
            recipient=to_email,
            subject=subject,
            message=body,
            triggered_by=triggered_by,
            status=NotificationStatus.FAILED,
            error_message=str(exc),
        )


def send_notification_sms(
    db: Session,
    *,
    phone: str,
    message: str,
    triggered_by: Optional[User] = None,
    reason: Optional[str] = None,
) -> None:
    """
    Send an SMS and log it.
    """
    try:
        send_sms(phone=phone, message=message, reason=reason)
        _log_notification(
            db,
            channel=NotificationChannel.SMS,
            recipient=phone,
            subject=None,
            message=message,
            triggered_by=triggered_by,
            status=NotificationStatus.SENT,
        )
    except Exception as exc:
        _log_notification(
            db,
            channel=NotificationChannel.SMS,
            recipient=phone,
            subject=None,
            message=message,
            triggered_by=triggered_by,
            status=NotificationStatus.FAILED,
            error_message=str(exc),
        )


def send_notification_whatsapp(
    db: Session,
    *,
    phone: str,
    message: str,
    triggered_by: Optional[User] = None,
    reason: Optional[str] = None,
) -> None:
    """
    Send a WhatsApp message and log it.
    """
    try:
        send_whatsapp(phone=phone, message=message, reason=reason)
        _log_notification(
            db,
            channel=NotificationChannel.WHATSAPP,
            recipient=phone,
            subject=None,
            message=message,
            triggered_by=triggered_by,
            status=NotificationStatus.SENT,
        )
    except Exception as exc:
        _log_notification(
            db,
            channel=NotificationChannel.WHATSAPP,
            recipient=phone,
            subject=None,
            message=message,
            triggered_by=triggered_by,
            status=NotificationStatus.FAILED,
            error_message=str(exc),
        )