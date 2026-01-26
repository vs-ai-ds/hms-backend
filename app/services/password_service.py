# app/services/password_service.py
"""
Password management service with history tracking.
"""

import re
from uuid import UUID

from sqlalchemy import desc
from sqlalchemy.orm import Session

from app.core.security import get_password_hash, verify_password
from app.models.password_history import PasswordHistory
from app.models.user import User


def validate_password_strength(password: str) -> None:
    """
    Validate password strength.
    Raises ValueError with descriptive message if password is too weak.

    Requirements:
    - At least 8 characters
    - At least one uppercase letter
    - At least one lowercase letter
    - At least one digit
    - At least one special character (!@#$%^&*()_+-=[]{}|;:,.<>?)
    """
    if len(password) < 8:
        raise ValueError("Password must be at least 8 characters long")

    if not re.search(r"[A-Z]", password):
        raise ValueError("Password must contain at least one uppercase letter")

    if not re.search(r"[a-z]", password):
        raise ValueError("Password must contain at least one lowercase letter")

    if not re.search(r"[0-9]", password):
        raise ValueError("Password must contain at least one digit")

    if not re.search(r"[!@#$%^&*()_+\-=\[\]{}|;:,.<>?]", password):
        raise ValueError(
            "Password must contain at least one special character (!@#$%^&*()_+-=[]{}|;:,.<>?)"
        )


def check_password_history(
    db: Session,
    *,
    user_id: UUID,
    new_password_hash: str,
    current_password_hash: str | None = None,
    history_count: int = 3,
) -> bool:
    """
    Check if the new password hash matches any of the last N password hashes (including current password).
    Returns True if password can be reused (not in history), False if it's in history.

    Args:
        db: Database session
        user_id: User ID to check
        new_password_hash: Hash of the new password to check
        current_password_hash: Hash of the current password (to include in check)
        history_count: Number of historical passwords to check (default 3)
    """
    from app.models.user import User

    # First check if new password matches current password
    if current_password_hash and new_password_hash == current_password_hash:
        return False  # Cannot reuse current password

    # Get user's current password hash if not provided
    if current_password_hash is None:
        user = db.query(User).filter(User.id == user_id).first()
        if user and new_password_hash == user.hashed_password:
            return False  # Cannot reuse current password
        current_password_hash = user.hashed_password if user else None

    # Check password history table (ensure it exists and query works)
    try:
        recent_passwords = (
            db.query(PasswordHistory.password_hash)
            .filter(PasswordHistory.user_id == user_id)
            .order_by(desc(PasswordHistory.created_at))
            .limit(history_count)
            .all()
        )

        for old_hash_tuple in recent_passwords:
            old_hash = (
                old_hash_tuple[0]
                if isinstance(old_hash_tuple, tuple)
                else old_hash_tuple
            )
            if old_hash == new_password_hash:
                return False  # Password is in history, cannot reuse
    except Exception as e:
        # If password_history table doesn't exist or query fails, log and continue
        # This allows the system to work even if password_history hasn't been migrated yet
        import logging

        logger = logging.getLogger(__name__)
        logger.warning(f"Could not check password history for user {user_id}: {e}")
        # Still check current password
        if current_password_hash and new_password_hash == current_password_hash:
            return False

    return True  # Password not in history, can use


def add_password_to_history(
    db: Session,
    *,
    user_id: UUID,
    password_hash: str,
    keep_last_n: int = 3,
) -> None:
    """
    Add a password hash to history and prune old entries beyond keep_last_n.

    NOTE:
    - This function does NOT commit or roll back the session.
    - The caller is responsible for transaction management.
    - This ensures we don't accidentally roll back other changes
      (e.g. updated user password) if history tracking fails.
    """
    # Add new entry
    history_entry = PasswordHistory(
        user_id=user_id,
        password_hash=password_hash,
    )
    db.add(history_entry)
    db.flush()

    # Prune old entries (keep only last N)
    all_entries = (
        db.query(PasswordHistory.id)
        .filter(PasswordHistory.user_id == user_id)
        .order_by(desc(PasswordHistory.created_at))
        .all()
    )

    if len(all_entries) > keep_last_n:
        # Delete entries beyond keep_last_n
        ids_to_delete = [entry[0] for entry in all_entries[keep_last_n:]]
        db.query(PasswordHistory).filter(PasswordHistory.id.in_(ids_to_delete)).delete(
            synchronize_session=False
        )


def change_user_password(
    db: Session,
    *,
    user_id: UUID,
    old_password: str | None,
    new_password: str,
    performed_by_user_id: UUID | None = None,
) -> User:
    """
    Unified password change function for both first-login and voluntary changes.

    - If must_change_password is True: old_password is optional (user already authenticated)
    - If must_change_password is False: old_password is required

    Returns the updated user.
    Raises ValueError if validation fails.
    """
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise ValueError("User not found")

    # If not in forced-change mode, require old password
    if not user.must_change_password:
        if not old_password:
            raise ValueError("Current password is required")
        if not verify_password(old_password, user.hashed_password):
            raise ValueError("Current password is incorrect")
    else:
        # For first-login password change, verify that new password is not the same as current (temp) password
        if verify_password(new_password, user.hashed_password):
            raise ValueError(
                "New password cannot be the same as your temporary password. Please choose a different password."
            )

    # Validate password strength
    validate_password_strength(new_password)

    # Hash new password
    new_password_hash = get_password_hash(new_password)

    # Check password history (last 3, including current password)
    if not check_password_history(
        db,
        user_id=user_id,
        new_password_hash=new_password_hash,
        current_password_hash=user.hashed_password,
        history_count=3,
    ):
        raise ValueError(
            "Cannot reuse the last 3 passwords (including your current password). Please choose a different password."
        )

    # Add current password to history before changing
    # This ensures we track it even if it's a temp password
    try:
        add_password_to_history(
            db, user_id=user_id, password_hash=user.hashed_password, keep_last_n=3
        )
    except Exception as e:
        # If password_history table doesn't exist, log warning but continue
        import logging

        logger = logging.getLogger(__name__)
        logger.warning(f"Could not add password to history for user {user_id}: {e}")

    # Update user password
    user.hashed_password = new_password_hash
    user.must_change_password = False  # Clear force change flag
    # Email is already verified (user logged in), but ensure it's set
    if not user.email_verified:
        user.email_verified = True
    db.flush()

    # Add new password to history
    try:
        add_password_to_history(
            db, user_id=user_id, password_hash=new_password_hash, keep_last_n=3
        )
    except Exception as e:
        # If password_history table doesn't exist, log warning but continue
        import logging

        logger = logging.getLogger(__name__)
        logger.warning(f"Could not add new password to history for user {user_id}: {e}")

    # Log password change event (if audit log exists)
    try:
        from app.models.audit_log import AuditEventType, AuditLog

        audit_log = AuditLog(
            user_id=user_id,
            performed_by_id=performed_by_user_id or user_id,
            event_type=AuditEventType.PASSWORD_CHANGE
            if performed_by_user_id
            else AuditEventType.PASSWORD_CHANGE,
            metadata={
                "self_change": performed_by_user_id is None
                or performed_by_user_id == user_id
            },
        )
        db.add(audit_log)
    except ImportError:
        # Audit log model might not exist yet
        pass

    db.commit()
    db.refresh(user)
    return user


def force_password_change(
    db: Session,
    *,
    user_id: UUID,
    performed_by_user_id: UUID,
) -> User:
    """
    Force a user to change their password on next login.
    Sets must_change_password flag.
    """
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise ValueError("User not found")

    user.must_change_password = True
    db.commit()
    db.refresh(user)

    # Log force password change event
    try:
        from app.models.audit_log import AuditEventType, AuditLog

        audit_log = AuditLog(
            user_id=user_id,
            performed_by_id=performed_by_user_id,
            event_type=AuditEventType.FORCE_PASSWORD_CHANGE,
        )
        db.add(audit_log)
        db.commit()
    except ImportError:
        pass

    return user
