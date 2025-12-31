# app/notifications/email/resend_client.py
import httpx

from app.core.config import get_settings

settings = get_settings()


def send_via_resend(
    from_email: str,
    to_email: str,
    subject: str,
    html_body: str,
    attachments: list[dict] | None = None,
) -> None:
    """
    Send email via Resend API.
    attachments: List of dicts with 'filename' and 'content' (base64 encoded bytes)
    """
    if not settings.resend_api_key:
        raise ValueError("RESEND_API_KEY is not configured")

    url = "https://api.resend.com/emails"
    headers = {
        "Authorization": f"Bearer {settings.resend_api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "from": from_email,
        "to": to_email,
        "subject": subject,
        "html": html_body,
    }

    if attachments:
        import base64

        payload["attachments"] = [
            {
                "filename": att["filename"],
                "content": base64.b64encode(att["content"]).decode("utf-8"),
            }
            for att in attachments
        ]

    try:
        response = httpx.post(url, json=payload, headers=headers, timeout=10.0)
        response.raise_for_status()
    except httpx.HTTPError as exc:
        print(f"[RESEND ERROR] Failed to send email: {exc}")
        raise
