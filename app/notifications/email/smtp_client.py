# app/notifications/email/smtp_client.py
import smtplib
from email.message import EmailMessage

from app.core.config import get_settings

settings = get_settings()


def send_via_smtp(
    from_email: str,
    to_email: str,
    subject: str,
    body: str,
) -> None:
    """
    Minimal SMTP client using Python's standard library.

    It respects:
        - settings.email_smtp_host
        - settings.email_smtp_port
        - settings.email_smtp_username
        - settings.email_smtp_password
    """
    msg = EmailMessage()
    msg["From"] = from_email
    msg["To"] = to_email
    msg["Subject"] = subject
    msg.set_content(body)

    host = settings.email_smtp_host
    port = settings.email_smtp_port
    username = settings.email_smtp_username
    password = settings.email_smtp_password

    try:
        with smtplib.SMTP(host, port) as server:
            server.ehlo()
            try:
                server.starttls()
                server.ehlo()
            except smtplib.SMTPException:
                # TLS not available, continue without it
                pass

            if username and password:
                server.login(username, password)

            server.send_message(msg)
    except OSError as exc:
        # For hackathon/demo, just log failure.
        print(f"[EMAIL ERROR] Failed to send email: {exc}")