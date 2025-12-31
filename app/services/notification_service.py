# app/services/notification_service.py
from typing import Optional

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.models.notification import Notification, NotificationChannel, NotificationStatus
from app.models.user import User
from app.notifications.email.base import send_email
from app.notifications.sms.base import send_sms
from app.notifications.whatsapp.base import send_whatsapp


def _resolve_tenant_schema_name(
    db: Session, tenant_schema_name: Optional[str], triggered_by: Optional[User]
) -> Optional[str]:
    """
    Rules:
    - If tenant_schema_name is provided, use it.
    - Else, if we only have a User and they have tenant_id, lookup Tenant.schema_name.
    """
    if tenant_schema_name:
        return tenant_schema_name

    if triggered_by and getattr(triggered_by, "tenant_id", None):
        try:
            from app.models.tenant import Tenant

            tenant = db.query(Tenant).filter(Tenant.id == triggered_by.tenant_id).first()
            if tenant and tenant.schema_name:
                return tenant.schema_name
        except Exception:
            return None

    return None


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
    tenant_schema_name: Optional[str] = None,
    reason: Optional[str] = None,
) -> Optional[Notification]:
    """
    Log a notification in the tenant schema.

    """
    import logging

    logger = logging.getLogger(__name__)
    schema_name = _resolve_tenant_schema_name(db, tenant_schema_name, triggered_by)

    log_message = message or ""
    if len(log_message) > 2000:
        if log_message.strip().startswith("<!DOCTYPE") or log_message.strip().startswith("<html"):
            log_message = f"[HTML Email - {reason or 'email'}] Subject: {subject or 'N/A'}. Email sent."
        else:
            log_message = log_message[:1997] + "..."

    try:
        with db.begin_nested():
            if schema_name:
                db.execute(text(f'SET LOCAL search_path TO "{schema_name}", public'))

            notif = Notification(
                channel=channel,
                recipient=recipient,
                subject=subject,
                message=log_message,
                triggered_by_id=triggered_by.id if triggered_by else None,
                status=status,
                error_message=error_message,
            )
            db.add(notif)
            db.flush()
            db.refresh(notif)
            return notif

    except SQLAlchemyError as e:
        logger.warning(f"[NOTIFICATION LOG ERROR] Failed to log notification: {e}", exc_info=True)
        return None
    except Exception as e:
        logger.warning(f"[NOTIFICATION LOG ERROR] Failed to log notification: {e}", exc_info=True)
        return None


def send_notification_email(
    db: Session,
    *,
    to_email: str,
    subject: str,
    body: str,
    triggered_by: Optional[User] = None,
    reason: Optional[str] = None,
    html: bool = False,
    tenant_schema_name: Optional[str] = None,
    check_patient_flag: bool = False,
    attachments: list[dict] | None = None,
) -> None:
    """
    Send an email and log it. Logging must never break main flow.
    """
    from app.core.config import get_settings

    settings = get_settings()

    if check_patient_flag and not settings.send_email_to_patients:
        import logging

        logger = logging.getLogger(__name__)
        logger.warning(f"Email to patient skipped (SEND_EMAIL_TO_PATIENTS=False): {to_email}, Subject: {subject}")
        # Still log the notification attempt as skipped
        _log_notification(
            db,
            channel=NotificationChannel.EMAIL,
            recipient=to_email,
            subject=subject,
            message=body[:200] + "..." if len(body) > 200 else body,  # Truncate for logging
            triggered_by=triggered_by,
            status=NotificationStatus.PENDING,
            error_message="Skipped: SEND_EMAIL_TO_PATIENTS=False",
            tenant_schema_name=tenant_schema_name,
            reason=reason,
        )
        return

    try:
        send_email(to_email=to_email, subject=subject, body=body, reason=reason, html=html, attachments=attachments)
        _log_notification(
            db,
            channel=NotificationChannel.EMAIL,
            recipient=to_email,
            subject=subject,
            message=body,
            triggered_by=triggered_by,
            status=NotificationStatus.SENT,
            tenant_schema_name=tenant_schema_name,
            reason=reason,
        )
    except Exception as exc:
        import logging

        logger = logging.getLogger(__name__)
        logger.error(f"Failed to send email to {to_email}, Subject: {subject}, Error: {exc}", exc_info=True)
        _log_notification(
            db,
            channel=NotificationChannel.EMAIL,
            recipient=to_email,
            subject=subject,
            message=body,
            triggered_by=triggered_by,
            status=NotificationStatus.FAILED,
            error_message=str(exc),
            tenant_schema_name=tenant_schema_name,
            reason=reason,
        )


def send_notification_sms(
    db: Session,
    *,
    phone: str,
    message: str,
    triggered_by: Optional[User] = None,
    reason: Optional[str] = None,
    check_patient_flag: bool = False,
    tenant_schema_name: Optional[str] = None,
) -> None:
    """
    Send an SMS and log it. Logging must never break main flow.
    """
    from app.core.config import get_settings

    settings = get_settings()

    if check_patient_flag and not settings.send_sms_to_patients:
        import logging

        logger = logging.getLogger(__name__)
        logger.warning(f"SMS to patient skipped (SEND_SMS_TO_PATIENTS=False): {phone}")
        return

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
            tenant_schema_name=tenant_schema_name,
            reason=reason,
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
            tenant_schema_name=tenant_schema_name,
            reason=reason,
        )


def send_notification_whatsapp(
    db: Session,
    *,
    phone: str,
    message: str,
    triggered_by: Optional[User] = None,
    reason: Optional[str] = None,
    tenant_schema_name: Optional[str] = None,
) -> None:
    """
    Send a WhatsApp message and log it. Logging must never break main flow.
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
            tenant_schema_name=tenant_schema_name,
            reason=reason,
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
            tenant_schema_name=tenant_schema_name,
            reason=reason,
        )
