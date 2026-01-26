# app/utils/token_utils.py
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import Column, DateTime, ForeignKey, String, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Session

from app.models.base import Base


class VerificationToken(Base):
    """
    Stores email verification tokens in the public schema.
    """

    __tablename__ = "verification_tokens"
    __table_args__ = {"schema": "public"}

    id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        nullable=False,
    )
    tenant_id = Column(
        UUID(as_uuid=True),
        ForeignKey("public.tenants.id", ondelete="CASCADE"),
        nullable=False,
    )
    token = Column(String(64), unique=True, nullable=False, index=True)
    email = Column(String(255), nullable=False)
    expires_at = Column(
        DateTime(timezone=True),
        nullable=False,
    )
    used_at = Column(
        DateTime(timezone=True),
        nullable=True,
    )
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )


def generate_verification_token() -> str:
    """Generate a secure random token for email verification."""
    return secrets.token_urlsafe(32)


def create_verification_token(
    db: Session,
    tenant_id: uuid.UUID,
    email: str,
    expires_in_hours: int = 24,
) -> str:
    """
    Create a verification token for a tenant's email.
    Returns the token string.
    """
    token = generate_verification_token()
    expires_at = datetime.now(timezone.utc) + timedelta(hours=expires_in_hours)

    verification = VerificationToken(
        tenant_id=tenant_id,
        token=token,
        email=email,
        expires_at=expires_at,
    )
    db.add(verification)
    db.flush()
    return token


def verify_token(db: Session, token: str) -> Optional[VerificationToken]:
    """
    Verify a token and return the VerificationToken if valid.
    Returns None if token is invalid, expired, or already used.
    """
    verification = (
        db.query(VerificationToken).filter(VerificationToken.token == token).first()
    )
    if not verification:
        return None

    if verification.used_at is not None:
        return None

    # Use timezone-aware datetime for comparison
    if verification.expires_at < datetime.now(timezone.utc):
        return None

    return verification


def mark_token_used(db: Session, verification: VerificationToken) -> None:
    """Mark a verification token as used."""
    verification.used_at = datetime.now(timezone.utc)
    db.flush()


def create_password_reset_token(
    db: Session,
    tenant_id: uuid.UUID,
    email: str,
    expires_in_hours: int = 1,
) -> str:
    """
    Create a password reset token for a user's email.
    Returns the token string.
    """
    token = generate_verification_token()
    expires_at = datetime.now(timezone.utc) + timedelta(hours=expires_in_hours)

    verification = VerificationToken(
        tenant_id=tenant_id,
        token=token,
        email=email,
        expires_at=expires_at,
    )
    db.add(verification)
    db.flush()
    return token
