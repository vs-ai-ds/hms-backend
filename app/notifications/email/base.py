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
    html: bool = False,
    attachments: list[dict] | None = None,
) -> None:
    """
    Unified email sending abstraction supporting SMTP and Resend.

    - If email_sandbox_mode is True:
        all emails are sent to EMAIL_TEST_RECIPIENT (if set).
    - Otherwise:
        uses EMAIL_BACKEND to choose between SMTP and Resend.
    """
    debug_reason = f" [{reason}]" if reason else ""

    # Apply sandbox mode
    actual_recipient = to_email
    if settings.email_sandbox_mode:
        actual_recipient = str(settings.email_test_recipient or settings.email_from)
        print(
            f"[EMAIL SANDBOX{debug_reason}] "
            f"Original: {to_email}, Redirected to: {actual_recipient}, Subject: {subject!r}"
        )

    # Choose backend
    if settings.email_backend.lower() == "resend":
        from app.notifications.email.resend_client import send_via_resend

        send_via_resend(
            from_email=str(settings.email_from),
            to_email=actual_recipient,
            subject=subject,
            html_body=body if html else f"<pre>{body}</pre>",
            attachments=attachments,
        )
    else:
        from app.notifications.email.smtp_client import send_via_smtp

        send_via_smtp(
            from_email=str(settings.email_from),
            to_email=actual_recipient,
            subject=subject,
            body=body,
            attachments=attachments,
        )

    # Print success message
    if settings.email_sandbox_mode:
        print(f"[EMAIL SANDBOX SENT{debug_reason}] To: {actual_recipient} (original: {to_email}), Subject: {subject!r}")
    else:
        print(f"[EMAIL SENT{debug_reason}] To: {actual_recipient}, Subject: {subject!r}")
