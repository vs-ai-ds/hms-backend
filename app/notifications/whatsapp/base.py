# app/notifications/whatsapp/base.py
from typing import Optional

from app.core.config import get_settings

settings = get_settings()


def send_whatsapp(
    phone: str,
    message: str,
    *,
    reason: Optional[str] = None,
) -> None:
    """
    WhatsApp sending stub.

    - If whatsapp_enabled is False:
        just log.
    - Otherwise:
        here is where you'd integrate Twilio / other API.
    """
    debug_reason = f" [{reason}]" if reason else ""

    if not settings.whatsapp_enabled:
        print(f"[WHATSAPP DISABLED{debug_reason}] To: {phone}, Message: {message}")
        return

    # TODO: integrate provider API here.
    print(f"[WHATSAPP SENT{debug_reason}] To: {phone}, Message: {message}")
