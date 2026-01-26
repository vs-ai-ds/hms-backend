from uuid import UUID

from sqlalchemy.orm import Session

from app.core.security import create_access_token, verify_password
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
    Enforces tenant status check: only ACTIVE tenants can login.
    """
    from app.models.tenant_global import Tenant, TenantStatus
    from app.services.user_service import get_user_by_email_and_tenant

    user = get_user_by_email_and_tenant(
        db=db,
        email=login_data.email,
        tenant_id=tenant_id,
    )
    if not user:
        raise AuthenticationError("Invalid email or password")

    # Check if user is deleted
    if user.is_deleted:
        raise AuthenticationError("Account has been deleted. Please contact support.")

    # Check if user is active
    if not user.is_active:
        raise AuthenticationError(
            "Account is inactive. Please contact your administrator."
        )

    # Check user status
    from app.models.user import UserStatus

    if user.status == UserStatus.LOCKED:
        raise AuthenticationError(
            "Account is locked. Please contact your administrator."
        )
    elif user.status == UserStatus.PASSWORD_EXPIRED:
        raise AuthenticationError(
            "Your password has expired. Please reset your password."
        )

    if not verify_password(login_data.password, user.hashed_password):
        raise AuthenticationError("Invalid email or password")

    # Password verified - user successfully authenticated
    # Get settings to check EMAIL_SANDBOX_MODE
    from app.core.config import get_settings

    settings = get_settings()

    # In EMAIL_SANDBOX_MODE: auto-verify email when user successfully logs in
    # If they can log in with the password, they have access to the email
    if settings.email_sandbox_mode:
        if not user.email_verified:
            user.email_verified = True
            db.commit()

    # Check tenant status for tenant users
    if user.tenant_id is not None:
        tenant = db.query(Tenant).filter(Tenant.id == user.tenant_id).first()
        if not tenant:
            raise AuthenticationError("Tenant not found")

        # In EMAIL_SANDBOX_MODE, auto-verify tenant status and allow login
        if settings.email_sandbox_mode:
            # Auto-verify tenant: PENDING -> VERIFIED -> ACTIVE
            if tenant.status == TenantStatus.PENDING:
                tenant.status = TenantStatus.VERIFIED
            if tenant.status == TenantStatus.VERIFIED:
                tenant.status = TenantStatus.ACTIVE
            # Ensure user email is verified (in case it wasn't set above)
            if not user.email_verified:
                user.email_verified = True
            db.commit()
        else:
            # Production mode: require email verification
            if not user.email_verified:
                raise AuthenticationError("Please verify your email before logging in.")
            if (
                tenant.status == TenantStatus.PENDING
                or tenant.status == TenantStatus.VERIFIED
            ):
                raise AuthenticationError(
                    "Hospital registration is not fully activated yet. Please verify your email."
                )

        # Check for suspended/inactive status (applies in both modes)
        if tenant.status == TenantStatus.SUSPENDED:
            raise AuthenticationError(
                "Hospital account is suspended. Please contact support."
            )
        elif tenant.status == TenantStatus.INACTIVE:
            raise AuthenticationError(
                "Hospital account is inactive. Please contact support."
            )
        elif tenant.status != TenantStatus.ACTIVE:
            raise AuthenticationError(
                "Hospital account is not active. Please contact support."
            )
    else:
        # For SUPER_ADMIN or users without tenant_id
        # In production mode, still require email verification
        if not settings.email_sandbox_mode and not user.email_verified:
            raise AuthenticationError("Please verify your email before logging in.")

    return user


def issue_access_token_for_user(user: User, db: Session) -> str:
    from app.models.tenant_global import Tenant
    from app.services.permission_service import get_user_permissions
    from app.services.user_role_service import get_user_role_names

    tenant_id = str(user.tenant_id) if user.tenant_id else None
    role_names = get_user_role_names(db, user)
    roles = list(role_names)

    # Get user permissions
    permissions = []
    if user.tenant_id is None:
        # SUPER_ADMIN - grant platform-level permissions
        permissions = ["tenants:manage", "dashboard:view"]
    elif user.tenant_id:
        # Tenant user - get permissions from tenant schema
        tenant = db.query(Tenant).filter(Tenant.id == user.tenant_id).first()
        if tenant:
            from sqlalchemy import text

            conn = db.connection()
            original_path = conn.execute(text("SHOW search_path")).scalar()
            try:
                conn.execute(text(f'SET search_path TO "{tenant.schema_name}", public'))
                permissions = list(get_user_permissions(db, user, user.tenant_id))
                conn.execute(text(f"SET search_path TO {original_path}"))
            except Exception:
                conn.execute(text(f"SET search_path TO {original_path}"))
                permissions = []

    return create_access_token(
        subject=str(user.id),
        tenant_id=tenant_id,
        roles=roles,
        permissions=permissions,
    )
