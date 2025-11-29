# app/notifications/email/base.py
from typing import Optional

from app.core.config import get_settings

settings = get_settings()


def send_email(
    to_email: str,
    subject: str,
    body: str,
    *,
    reason: Optional[str] = None,
) -> None:
    """
    Email sending stub / basic implementation.

    - If email_sandbox_mode is True:
        all emails are sent to EMAIL_TEST_RECIPIENT (if set),
        and we just log.
    - Otherwise:
        delegate to smtp_client.send_via_smtp (simple SMTP client).
    """
    from app.notifications.email.smtp_client import send_via_smtp

    debug_reason = f" [{reason}]" if reason else ""

    if settings.email_sandbox_mode:
        recipient = settings.email_test_recipient or settings.email_from
        print(
            f"[EMAIL SANDBOX{debug_reason}] "
            f"To: {recipient}, Subject: {subject!r}"
        )
        # Optionally still send via SMTP to test_recipient
        send_via_smtp(
            from_email=str(settings.email_from),
            to_email=str(recipient),
            subject=subject,
            body=body,
        )
        return

    print(
        f"[EMAIL SEND{debug_reason}] To: {to_email}, Subject: {subject!r}"
    )
    send_via_smtp(
        from_email=str(settings.email_from),
        to_email=to_email,
        subject=subject,
        body=body,
    )