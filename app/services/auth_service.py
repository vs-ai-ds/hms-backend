from uuid import UUID

from sqlalchemy.orm import Session

from app.core.security import verify_password, create_access_token
from app.models.user import User
from app.schemas.auth import LoginRequest


class AuthenticationError(Exception):
    pass


def authenticate_user(
    db: Session,
    login_data: LoginRequest,
    tenant_id: UUID | None,
) -> User:
    """
    Authenticate a user given email, password, and optional tenant_id.
    Currently tenant_id is required for hospital users; SUPER_ADMIN is tenant_id=None.
    """
    from app.services.user_service import get_user_by_email_and_tenant

    user = get_user_by_email_and_tenant(
        db=db,
        email=login_data.email,
        tenant_id=tenant_id,
    )
    if not user:
        raise AuthenticationError("Invalid email or password")

    if not verify_password(login_data.password, user.hashed_password):
        raise AuthenticationError("Invalid email or password")

    return user


def issue_access_token_for_user(user: User) -> str:
    tenant_id = str(user.tenant_id) if user.tenant_id else None
    roles = [role.name for role in user.roles]
    return create_access_token(
        subject=str(user.id),
        tenant_id=tenant_id,
        roles=roles,
    )