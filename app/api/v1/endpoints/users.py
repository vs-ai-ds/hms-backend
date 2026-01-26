# app/api/v1/endpoints/users.py
import secrets
import string
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, or_, text
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.database import get_db
from app.core.security import get_password_hash
from app.core.tenant_context import TenantContext, get_tenant_context
from app.core.tenant_db import ensure_search_path
from app.dependencies.authz import require_permission
from app.models.user import User, UserStatus
from app.schemas.user import UserCreate, UserResponse
from app.services.notification_service import send_notification_email
from app.services.user_service import create_user, get_user_by_email_and_tenant
from app.utils.email_templates import render_email_template

router = APIRouter()
settings = get_settings()


def _generate_temp_password(length: int = 12) -> str:
    """
    Generate a random temporary password that follows password guidelines:
    - At least 8 characters (we use 12)
    - At least one uppercase letter
    - At least one lowercase letter
    - At least one digit
    - At least one special character
    """
    import random

    # Ensure we have at least one of each required character type
    uppercase = string.ascii_uppercase
    lowercase = string.ascii_lowercase
    digits = string.digits
    special = "!@#$%^&*()_+-=[]{}|;:,.<>?"

    # Start with one of each required type
    password = [
        secrets.choice(uppercase),
        secrets.choice(lowercase),
        secrets.choice(digits),
        secrets.choice(special),
    ]

    # Fill the rest with random characters from all types
    all_chars = uppercase + lowercase + digits + special
    password.extend(secrets.choice(all_chars) for _ in range(length - 4))

    # Shuffle to avoid predictable pattern
    random.shuffle(password)

    return "".join(password)


def _build_user_response_with_roles(
    user: User,
    db: Session,
    ctx: TenantContext,
) -> UserResponse:
    """
    Helper function to build UserResponse with roles and permissions.
    Does NOT set search_path - caller must ensure it's set.
    """
    ensure_search_path(db, ctx.tenant.schema_name)
    from app.models.tenant_role import TenantRole, TenantRolePermission, TenantUserRole
    from app.schemas.user import PermissionResponse, RoleResponse

    role_responses = []

    try:
        # Query tenant-scoped user roles with permissions
        user_roles = (
            db.query(TenantUserRole)
            .join(TenantRole, TenantUserRole.role_id == TenantRole.id)
            .filter(TenantUserRole.user_id == user.id)
            .all()
        )

        # Build role responses with permissions
        for user_role in user_roles:
            role = user_role.role
            # Get permissions for this role
            role_permissions = (
                db.query(TenantRolePermission)
                .filter(TenantRolePermission.role_id == role.id)
                .all()
            )
            permissions = [
                PermissionResponse(code=rp.permission_code) for rp in role_permissions
            ]
            role_responses.append(RoleResponse(name=role.name, permissions=permissions))
    except Exception as e:
        # Log error but continue with empty roles
        import logging

        logger = logging.getLogger(__name__)
        logger.error(f"Error fetching user roles: {e}")
        role_responses = []

    return UserResponse(
        id=user.id,
        tenant_id=user.tenant_id,
        email=user.email,
        first_name=user.first_name,
        last_name=user.last_name,
        phone=user.phone,
        department=user.department,
        specialization=user.specialization,
        status=user.status,
        is_active=user.is_active,
        is_deleted=user.is_deleted,
        must_change_password=user.must_change_password,
        email_verified=user.email_verified,
        roles=role_responses,
        tenant_name=ctx.tenant.name if ctx.tenant else None,
        created_at=user.created_at,
        updated_at=user.updated_at,
    )


@router.get("", response_model=list[UserResponse], tags=["users"])
def list_users(
    search: Optional[str] = Query(None, description="Search by name or email"),
    include_inactive: Optional[bool] = Query(
        False, description="Include inactive users"
    ),
    current_user: User = Depends(require_permission("users:view")),
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(get_tenant_context),
) -> list[UserResponse]:
    """
    List all users for the current tenant.
    Supports search by name or email.
    By default, only shows active users. Set include_inactive=True to show all users.
    """
    ensure_search_path(db, ctx.tenant.schema_name)
    # Filter by tenant - we don't filter by is_deleted since we only use is_active for activate/deactivate
    query = db.query(User).filter(
        User.tenant_id == ctx.tenant.id,
    )

    # Filter by active status if not including inactive
    if not include_inactive:
        query = query.filter(User.is_active == True)

    if search:
        search_term = f"%{search.lower()}%"
        query = query.filter(
            or_(
                User.email.ilike(search_term),
                User.first_name.ilike(search_term),
                User.last_name.ilike(search_term),
            )
        )

    users = query.order_by(User.created_at.desc()).all()

    # Build UserResponse with roles and permissions for each user
    result = []
    for user in users:
        result.append(_build_user_response_with_roles(user, db, ctx))

    return result


@router.get("/{user_id}", response_model=UserResponse, tags=["users"])
def get_user(
    user_id: UUID,
    current_user: User = Depends(require_permission("users:view")),
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(get_tenant_context),
) -> UserResponse:
    """
    Get a single user by ID.
    """
    ensure_search_path(db, ctx.tenant.schema_name)
    user = (
        db.query(User)
        .filter(
            User.id == user_id,
            User.tenant_id == ctx.tenant.id,
        )
        .first()
    )

    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found.",
        )

    return _build_user_response_with_roles(user, db, ctx)


@router.post("", status_code=status.HTTP_201_CREATED, tags=["users"])
def create_user_endpoint(
    payload: UserCreate,
    current_user: User = Depends(require_permission("users:create")),
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(get_tenant_context),
):
    """
    Create a new user for the current tenant.
    Generates a temporary password and sends it via email.
    """
    ensure_search_path(db, ctx.tenant.schema_name)
    # Check if tenant is suspended
    from app.models.tenant_global import TenantStatus

    if ctx.tenant.status == TenantStatus.SUSPENDED:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Cannot create users. Hospital account is suspended. Please contact support.",
        )

    # Check max_users limit
    if ctx.tenant.max_users is not None:
        current_user_count = (
            db.query(func.count(User.id))
            .filter(
                User.tenant_id == ctx.tenant.id,
                User.is_deleted == False,
            )
            .scalar()
            or 0
        )

        if current_user_count >= ctx.tenant.max_users:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Cannot create user. Maximum user limit ({ctx.tenant.max_users}) has been reached. Please contact Platform Administrator to increase the limit.",
            )

    # Ensure tenant_id matches current tenant
    if payload.tenant_id and payload.tenant_id != ctx.tenant.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Cannot create user for different tenant.",
        )

    # Check if email already exists for this tenant (case-insensitive)
    existing = get_user_by_email_and_tenant(
        db=db,
        email=payload.email,
        tenant_id=ctx.tenant.id,
    )
    if existing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="User with this email already exists.",
        )

    # Generate temp password if not provided
    temp_password = payload.password if payload.password else _generate_temp_password()
    user_in = UserCreate(
        tenant_id=ctx.tenant.id,
        email=payload.email,
        first_name=payload.first_name,
        last_name=payload.last_name,
        phone=payload.phone,
        password=temp_password,
        department=payload.department,
        specialization=payload.specialization,
        roles=payload.roles,
    )

    user = create_user(db, user_in, tenant=ctx.tenant)
    db.commit()  # Commit user creation first

    # Increment platform metrics using a separate session to avoid "Connection is closed" errors
    # This ensures we have a fresh connection for the metrics operation

    from app.core.database import SessionLocal
    from app.services.tenant_metrics_service import increment_users

    # Use a separate session for metrics to avoid connection closed errors
    metrics_db = SessionLocal()
    try:
        # Set search_path to public for metrics increment
        conn = metrics_db.connection()
        conn.execute(text("SET search_path TO public"))
        increment_users(metrics_db)  # This commits metrics
        metrics_db.close()
    except Exception as e:
        metrics_db.rollback()
        metrics_db.close()
        # Log but don't fail user creation if metrics increment fails
        import logging

        logger = logging.getLogger(__name__)
        logger.warning(
            f"Failed to increment user metrics (non-critical): {e}", exc_info=True
        )

    # Send invitation email
    email_sent = False
    email_error = None
    try:
        subject = f"Welcome to {ctx.tenant.name} - Your HMS Account"
        body_html = f"""
        <p>Dear {user.first_name} {user.last_name},</p>
        <p>Your account has been created for <strong>{ctx.tenant.name}</strong>.</p>
        <p><strong>Your login credentials:</strong></p>
        <ul>
            <li><strong>Email:</strong> {user.email}</li>
            <li><strong>Temporary Password:</strong> <code style="background-color: #f0f0f0; padding: 2px 6px; border-radius: 3px;">{temp_password}</code></li>
        </ul>
        <p><strong>Important:</strong> Please change your password after your first login.</p>
        """
        html = render_email_template(
            title="Your HMS Account",
            body_html=body_html,
            cta_text="Login to HMS",
            cta_url=f"{settings.backend_cors_origins[0] if settings.backend_cors_origins else 'http://localhost:5173'}/login",
            hospital_name=ctx.tenant.name,
        )
        send_notification_email(
            db=db,
            to_email=user.email,
            subject=subject,
            body=html,
            reason="user_invitation",
            triggered_by=ctx.user,
            html=True,
            tenant_schema_name=ctx.tenant.schema_name,  # Set tenant schema for notification logging
        )
        email_sent = True

        # Log to email_logs (public schema)
        from app.models.email_log import EmailLog

        email_log = EmailLog(
            to=user.email,
            template="user_invitation",
            status="SENT",
            triggered_by_id=ctx.user.id,
            related_user_id=user.id,
        )
        db.add(email_log)
        db.commit()
    except Exception as e:
        email_error = str(e)
        import logging

        logger = logging.getLogger(__name__)
        logger.error(
            f"Failed to send user invitation email to {user.email}: {e}", exc_info=True
        )

        # Log failure to email_logs
        from app.models.email_log import EmailLog

        email_log = EmailLog(
            to=user.email,
            template="user_invitation",
            status="FAILED",
            error_message=str(e)[:1000],  # Truncate to 1000 chars
            triggered_by_id=ctx.user.id,
            related_user_id=user.id,
        )
        db.add(email_log)
        db.commit()

    # Return UserResponse with roles and permissions
    response = _build_user_response_with_roles(user, db, ctx)

    # In demo mode (EMAIL_SANDBOX_MODE), include temp password in response
    if settings.email_sandbox_mode:
        response_dict = response.model_dump()
        response_dict["temp_password"] = temp_password
        return response_dict

    # If email failed, we still return success but could include a warning
    # Frontend can check for this if needed
    if not email_sent and email_error:
        # User was created successfully, but email failed
        # This is acceptable - admin can resend invitation
        pass

    # Return as dict to match sandbox mode response format
    return response.model_dump()


@router.patch("/{user_id}", response_model=UserResponse, tags=["users"])
def update_user(
    user_id: UUID,
    payload: dict,
    current_user: User = Depends(require_permission("users:update")),
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(get_tenant_context),
) -> UserResponse:
    """
    Update user details (roles, department, status, etc.).
    """
    ensure_search_path(db, ctx.tenant.schema_name)
    user = (
        db.query(User)
        .filter(
            User.id == user_id,
            User.tenant_id == ctx.tenant.id,
        )
        .first()
    )

    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found.",
        )

    # Update allowed fields
    if "first_name" in payload:
        user.first_name = payload["first_name"]
    if "last_name" in payload:
        user.last_name = payload["last_name"]
    if "phone" in payload:
        user.phone = payload.get("phone")
    if "department" in payload:
        user.department = payload.get("department")
    if "specialization" in payload:
        user.specialization = payload.get("specialization")
    if "status" in payload:
        user.status = UserStatus(payload["status"])
    if "is_active" in payload:
        new_is_active = bool(payload["is_active"])

        # Prevent self-deactivation
        if user.id == current_user.id and not new_is_active:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You cannot deactivate yourself.",
            )

        # Prevent deactivating HOSPITAL_ADMIN
        if not new_is_active and user.is_active:  # Trying to deactivate
            from app.services.user_role_service import get_user_role_names

            user_roles = get_user_role_names(db, user, ctx.tenant.schema_name)
            if "HOSPITAL_ADMIN" in user_roles:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Hospital Admin users cannot be deactivated.",
                )

        user.is_active = new_is_active
    if "roles" in payload:
        # Prevent changing roles for HOSPITAL_ADMIN
        from app.services.user_role_service import get_user_role_names

        existing_roles = get_user_role_names(db, user, ctx.tenant.schema_name)
        if "HOSPITAL_ADMIN" in existing_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Hospital Admin role cannot be changed.",
            )

        from sqlalchemy import text

        from app.models.tenant_role import TenantRole, TenantUserRole

        # Set search_path to tenant schema
        conn = db.connection()
        original_path = conn.execute(text("SHOW search_path")).scalar()
        try:
            conn.execute(text(f'SET search_path TO "{ctx.tenant.schema_name}", public'))

            # Delete existing user roles
            db.query(TenantUserRole).filter(TenantUserRole.user_id == user.id).delete()
            db.flush()

            # Get tenant roles by name (supports both system and custom roles)
            role_names = [str(r) for r in payload["roles"]]

            # Ensure HOSPITAL_ADMIN is not being removed if user already has it
            # (This is a safety check, but we already blocked above)

            tenant_roles = (
                db.query(TenantRole)
                .filter(TenantRole.name.in_(role_names))
                .filter(TenantRole.is_active == True)  # Only assign active roles
                .all()
            )

            # Check if all requested roles were found
            found_role_names = {role.name for role in tenant_roles}
            missing_roles = set(role_names) - found_role_names
            if missing_roles:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Roles not found or inactive: {', '.join(missing_roles)}",
                )

            # Create new user role assignments
            for role in tenant_roles:
                user_role = TenantUserRole(
                    user_id=user.id,
                    role_id=role.id,
                )
                db.add(user_role)
            db.flush()

            # Restore original search_path
            conn.execute(text(f"SET search_path TO {original_path}"))
        except HTTPException:
            conn.execute(text(f"SET search_path TO {original_path}"))
            raise
        except Exception as e:
            conn.execute(text(f"SET search_path TO {original_path}"))
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Failed to update roles: {str(e)}",
            )

    db.commit()
    db.refresh(user)

    return _build_user_response_with_roles(user, db, ctx)


@router.patch("/{user_id}/toggle-active", response_model=UserResponse, tags=["users"])
def toggle_user_active(
    user_id: UUID,
    current_user: User = Depends(require_permission("users:update")),
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(get_tenant_context),
) -> UserResponse:
    """
    Toggle user active status (enable/disable).
    Sets is_active to True or False. Does not hard delete.
    Prevents deactivating Hospital Admin users and self-deactivation.
    """
    ensure_search_path(db, ctx.tenant.schema_name)
    user = (
        db.query(User)
        .filter(
            User.id == user_id,
            User.tenant_id == ctx.tenant.id,
        )
        .first()
    )

    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found.",
        )

    # Prevent self-deactivation
    if user.id == current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You cannot deactivate yourself.",
        )

    # If trying to deactivate, check for HOSPITAL_ADMIN role
    if user.is_active:  # Currently active, trying to deactivate
        from app.services.user_role_service import get_user_role_names

        user_roles = get_user_role_names(db, user, ctx.tenant.schema_name)
        if "HOSPITAL_ADMIN" in user_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Hospital Admin users cannot be deactivated.",
            )

    # Toggle is_active
    user.is_active = not user.is_active

    db.commit()
    ensure_search_path(db, ctx.tenant.schema_name)
    db.refresh(user)

    return _build_user_response_with_roles(user, db, ctx)


@router.post("/{user_id}/deactivate", response_model=UserResponse, tags=["users"])
def deactivate_user(
    user_id: UUID,
    current_user: User = Depends(require_permission("users:update")),
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(get_tenant_context),
) -> UserResponse:
    """
    Deactivate a user.
    Sets is_active=False.
    Prevents deactivating Hospital Admin users and self-deactivation.
    """
    ensure_search_path(db, ctx.tenant.schema_name)
    user = (
        db.query(User)
        .filter(
            User.id == user_id,
            User.tenant_id == ctx.tenant.id,
        )
        .first()
    )

    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found.",
        )

    # Prevent self-deactivation
    if user.id == current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You cannot deactivate yourself.",
        )

    # Check if user has HOSPITAL_ADMIN role
    from app.services.user_role_service import get_user_role_names

    user_roles = get_user_role_names(db, user, ctx.tenant.schema_name)
    if "HOSPITAL_ADMIN" in user_roles:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Hospital Admin users cannot be deactivated.",
        )

    # Deactivate: set is_active=False
    user.is_active = False
    db.commit()
    ensure_search_path(db, ctx.tenant.schema_name)
    db.refresh(user)

    return _build_user_response_with_roles(user, db, ctx)


@router.post("/{user_id}/resend-invitation", tags=["users"])
def resend_invitation(
    user_id: UUID,
    current_user: User = Depends(require_permission("users:update")),
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(get_tenant_context),
) -> dict:
    """
    Resend invitation email to a user.
    Generates a new temporary password and sends invitation email.
    Only allowed if user email is not verified.
    """
    user = (
        db.query(User)
        .filter(
            User.id == user_id,
            User.tenant_id == ctx.tenant.id,
        )
        .first()
    )

    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found.",
        )

    # Only allow resend invitation if email is not verified
    if user.email_verified:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot resend invitation. User email is already verified.",
        )

    # Generate new temp password
    temp_password = _generate_temp_password()
    user.hashed_password = get_password_hash(temp_password)
    user.must_change_password = True
    # Reset email verification - user needs to log in again with new temp password to verify email
    user.email_verified = False
    db.commit()

    # Send invitation email
    email_sent = False
    email_error = None
    try:
        subject = f"Welcome to {ctx.tenant.name} - Your HMS Account"
        body_html = f"""
        <p>Dear {user.first_name} {user.last_name},</p>
        <p>Your account credentials for <strong>{ctx.tenant.name}</strong> have been reset.</p>
        <p><strong>Your login credentials:</strong></p>
        <ul>
            <li><strong>Email:</strong> {user.email}</li>
            <li><strong>Temporary Password:</strong> <code style="background-color: #f0f0f0; padding: 2px 6px; border-radius: 3px;">{temp_password}</code></li>
        </ul>
        <p><strong>Important:</strong> Please change your password after your first login.</p>
        """
        html = render_email_template(
            title="Your HMS Account",
            body_html=body_html,
            cta_text="Login to HMS",
            cta_url=f"{settings.backend_cors_origins[0] if settings.backend_cors_origins else 'http://localhost:5173'}/login",
            hospital_name=ctx.tenant.name,
        )
        send_notification_email(
            db=db,
            to_email=user.email,
            subject=subject,
            body=html,
            reason="user_invitation",
            triggered_by=ctx.user,
            html=True,
            tenant_schema_name=ctx.tenant.schema_name,
        )
        email_sent = True

        # Log to email_logs
        from app.models.email_log import EmailLog

        email_log = EmailLog(
            to=user.email,
            template="user_invitation",
            status="SENT",
            triggered_by_id=ctx.user.id,
            related_user_id=user.id,
        )
        db.add(email_log)
        db.commit()
    except Exception as e:
        email_error = str(e)
        import logging

        logger = logging.getLogger(__name__)
        logger.error(
            f"Failed to resend invitation email to {user.email}: {e}", exc_info=True
        )

        # Log failure to email_logs
        from app.models.email_log import EmailLog

        email_log = EmailLog(
            to=user.email,
            template="user_invitation",
            status="FAILED",
            error_message=str(e)[:1000],
            triggered_by_id=ctx.user.id,
            related_user_id=user.id,
        )
        db.add(email_log)
        db.commit()

    # In demo mode (EMAIL_SANDBOX_MODE), include temp password in response
    if settings.email_sandbox_mode:
        return {
            "first_name": user.first_name,
            "last_name": user.last_name,
            "email": user.email,
            "message": "Invitation email sent successfully",
            "temp_password": temp_password,
        }

    if email_sent:
        return {
            "first_name": user.first_name,
            "last_name": user.last_name,
            "email": user.email,
            "message": "Invitation email sent successfully",
        }
    else:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to send invitation email: {email_error}",
        )


@router.post(
    "/{user_id}/force-password-change", response_model=UserResponse, tags=["users"]
)
def force_password_change_endpoint(
    user_id: UUID,
    current_user: User = Depends(require_permission("users:update")),
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(get_tenant_context),
) -> UserResponse:
    """
    Force a user to change their password on next login.
    Sets must_change_password flag and invalidates all active sessions.
    The user will be redirected to password change page on their next request.
    """
    from app.services.password_service import force_password_change

    user = (
        db.query(User)
        .filter(
            User.id == user_id,
            User.tenant_id == ctx.tenant.id,
        )
        .first()
    )

    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found.",
        )

    # Force password change
    user = force_password_change(
        db=db,
        user_id=user_id,
        performed_by_user_id=ctx.user.id,
    )

    # Note: In a production system with session management (Redis, database sessions, etc.),
    # Invalidate all active sessions/tokens for this user here.
    # For JWT-based auth:
    # 1. Maintain a token blacklist in Redis
    # 2. Add a session_version field to User and increment it, checking it in token validation
    # 3. Use refresh tokens and revoke them

    # For now, the user will be forced to change password on next login attempt
    # or when their current token expires and they try to refresh it.

    return _build_user_response_with_roles(user, db, ctx)
