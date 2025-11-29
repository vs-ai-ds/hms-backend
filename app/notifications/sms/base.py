# app/notifications/sms/base.py
from typing import Optional

from app.core.config import get_settings

settings = get_settings()


def send_sms(
    phone: str,
    message: str,
    *,
    reason: Optional[str] = None,
) -> None:
    """
    SMS sending stub.

    - If settings.sms_enabled is False:
        just log/print that SMS would have been sent.
    - If you later integrate a provider (e.g., Twilio),
      plug it into the enabled branch.

    `reason` is a free-text label like:
      - "APPOINTMENT_CONFIRMATION"
      - "PASSWORD_RESET"
      - "PRESCRIPTION_READY"
    """
    if not settings.sms_enabled:
        # Sandbox / hackathon mode: no real SMS; just log.
        debug_reason = f" [{reason}]" if reason else ""
        print(f"[SMS DISABLED{debug_reason}] To: {phone}, Message: {message}")
        return

    # TODO: Integrate actual SMS provider here.
    # Example pseudo-code:
    #
    # client = TwilioClient(settings.sms_provider_sid, settings.sms_provider_auth)
    # client.messages.create(
    #     body=message,
    #     to=phone,
    #     from_=settings.sms_from_number,
    # )
    #
    # For now, log the call:
    debug_reason = f" [{reason}]" if reason else ""
    print(f"[SMS SENT{debug_reason}] To: {phone}, Message: {message}")