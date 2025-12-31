from datetime import datetime, timedelta, timezone
from typing import Any

from jose import JWTError, jwt
from passlib.context import CryptContext

from app.core.config import get_settings

settings = get_settings()

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

ALGORITHM = "HS256"


def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)


def get_password_hash(password: str) -> str:
    return pwd_context.hash(password)


def create_access_token(
    subject: str | int,
    tenant_id: str | None,
    roles: list[str],
    permissions: list[str] | None = None,
    expires_delta_minutes: int | None = None,
) -> str:
    """
    Create a JWT access token with subject (user id), tenant_id, roles, and permissions.
    """
    if expires_delta_minutes is None:
        expires_delta_minutes = settings.access_token_expire_minutes

    expire = datetime.now(timezone.utc) + timedelta(minutes=expires_delta_minutes)
    to_encode: dict[str, Any] = {
        "sub": str(subject),
        "tenant_id": tenant_id,
        "roles": roles,
        "permissions": permissions or [],
        "exp": expire,
    }
    encoded_jwt = jwt.encode(to_encode, settings.secret_key, algorithm=ALGORITHM)
    return encoded_jwt


def decode_token(token: str) -> dict[str, Any]:
    """
    Decode and validate a JWT token.
    Raises ValueError with descriptive message if token is invalid or expired.
    """
    try:
        payload = jwt.decode(token, settings.secret_key, algorithms=[ALGORITHM])
    except JWTError as exc:
        # Check if it's an expiration error
        error_str = str(exc).lower()
        if "expired" in error_str or "exp" in error_str:
            raise ValueError("Token has expired. Please log in again.") from None
        raise ValueError("Invalid token") from exc
    return payload
