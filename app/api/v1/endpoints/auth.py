from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.database import get_db
from app.core.security import decode_token
from app.models.user import User
from app.schemas.auth import (
    ChangePasswordRequest,
    ForgotPasswordRequest,
    LoginRequest,
    ResetPasswordRequest,
    TokenResponse,
)
from app.schemas.user import UserResponse
from app.services.auth_service import (
    AuthenticationError,
    authenticate_user,
    issue_access_token_for_user,
)
from app.services.notification_service import send_notification_email
from app.services.user_service import get_user_by_email
from app.utils.email_templates import render_email_template
from app.utils.token_utils import (
    create_password_reset_token,
    mark_token_used,
    verify_token,
)

router = APIRouter()

settings = get_settings()

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login")


@router.get("/health", tags=["auth"])
async def auth_health_check() -> dict:
    """
    Simple health check for the auth module.
    """
    return {"status": "auth-ok"}


@router.post("/login", response_model=TokenResponse, tags=["auth"])
def login(
    form_data: OAuth2PasswordRequestForm = Depends(),
    db: Session = Depends(get_db),
) -> TokenResponse:
    """
    OAuth2-style login.

    For now, we expect:
    - username: email
    - password: password
    - tenant_id: optional, passed in `scopes` (hack) or we will refine later.

    To keep it simple for now, we'll assume a single tenant context per user login:
    - For hospital admins/doctor, pass tenant_id in the `scope` field or we'll
      infer later via query param. For hackathon/demo, we can also skip tenant_id
      and just look up by email if unique.
    """

    # Simple interpretation: we ignore tenant_id for now and assume email+tenant is unique enough.
    login_data = LoginRequest(email=form_data.username, password=form_data.password)

    # NOTE: For now, tenant_id=None (SUPER_ADMIN or unique email per tenant).
    # Later we can parse a tenant_id from form_data.scopes or query params.
    try:
        user = authenticate_user(db, login_data, tenant_id=None)
    except AuthenticationError as exc:
        # Preserve specific error messages from AuthenticationError
        import logging

        logger = logging.getLogger(__name__)
        logger.warning(
            f"Login failed for email: {login_data.email[:3]}*** - {str(exc)}"
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc),
        ) from exc
    except Exception as exc:
        # Log error but don't expose sensitive details for unexpected errors
        import logging

        logger = logging.getLogger(__name__)
        logger.warning(f"Login failed for email: {login_data.email[:3]}***")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
        ) from exc

    token = issue_access_token_for_user(user, db)
    # Email verification is handled in authenticate_user() based on EMAIL_SANDBOX_MODE
    # No need to duplicate the logic here

    # Auto-freshen demo data on login (if enabled)
    if settings.demo_mode and settings.demo_auto_refresh_on_login:
        from app.api.v1.endpoints.admin import check_and_freshen_demo_on_login

        try:
            check_and_freshen_demo_on_login(db)
        except Exception as e:
            # Log but don't fail login if auto-freshen fails
            import logging

            logger = logging.getLogger(__name__)
            logger.warning(f"Demo auto-freshen on login failed: {e}", exc_info=True)

    # Include must_change_password in response for frontend to handle first-login flow
    return TokenResponse(
        access_token=token,
        must_change_password=user.must_change_password,
    )


def get_current_user(
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db),
) -> User:
    """
    Dependency to retrieve the current user from a JWT bearer token.
    Returns 401 with appropriate message if token is expired or invalid.
    """
    from app.models.user import User  # local import to avoid cycles

    try:
        payload = decode_token(token)
    except ValueError as e:
        # decode_token raises ValueError with descriptive message (e.g., "Token has expired")
        error_message = str(e)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=error_message,
            headers={"WWW-Authenticate": "Bearer"},
        )

    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token payload",
            headers={"WWW-Authenticate": "Bearer"},
        )

    user = db.query(User).filter(User.id == UUID(user_id)).first()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Check if user is inactive - force logout
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account is inactive. Please contact your administrator.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Check if user must change password - this will be handled by frontend redirect
    # But we still allow the request to proceed so frontend can check must_change_password flag

    # Check tenant status for tenant users - if suspended, force logout
    if user.tenant_id is not None:
        from app.models.tenant_global import Tenant, TenantStatus

        tenant = db.query(Tenant).filter(Tenant.id == user.tenant_id).first()
        if tenant and tenant.status == TenantStatus.SUSPENDED:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Hospital account is suspended. Please contact support.",
                headers={"WWW-Authenticate": "Bearer"},
            )

    return user


@router.get("/me", tags=["auth"])
def read_current_user(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """
    Return the current authenticated user with roles from tenant schema.
    """
    from sqlalchemy import text

    from app.models.tenant_global import Tenant

    # Get roles from tenant schema if user has tenant_id
    from app.models.tenant_role import TenantRole, TenantRolePermission, TenantUserRole
    from app.schemas.user import PermissionResponse, RoleResponse

    role_responses = []
    # Handle SUPER_ADMIN (tenant_id is None)
    if current_user.tenant_id is None:
        # SUPER_ADMIN - return SUPER_ADMIN role with platform permissions
        from app.schemas.user import PermissionResponse, RoleResponse

        role_responses.append(
            RoleResponse(
                name="SUPER_ADMIN",
                permissions=[
                    PermissionResponse(code="tenants:manage"),
                    PermissionResponse(code="dashboard:view"),
                ],
            )
        )
    elif current_user.tenant_id:
        tenant = db.query(Tenant).filter(Tenant.id == current_user.tenant_id).first()
        if tenant:
            # Set search_path to tenant schema
            conn = db.connection()
            original_path = conn.execute(text("SHOW search_path")).scalar()
            try:
                conn.execute(text(f'SET search_path TO "{tenant.schema_name}", public'))

                # Query tenant-scoped user roles with permissions
                user_roles = (
                    db.query(TenantUserRole)
                    .join(TenantRole, TenantUserRole.role_id == TenantRole.id)
                    .filter(TenantUserRole.user_id == current_user.id)
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
                        PermissionResponse(code=rp.permission_code)
                        for rp in role_permissions
                    ]
                    role_responses.append(
                        RoleResponse(name=role.name, permissions=permissions)
                    )

                # Restore original search_path
                conn.execute(text(f"SET search_path TO {original_path}"))
            except Exception as e:
                conn.execute(text(f"SET search_path TO {original_path}"))
                # Log error but continue with empty roles
                import logging

                logger = logging.getLogger(__name__)
                logger.error(f"Error fetching user roles: {e}")
                role_responses = []

    # Include tenant info if available
    tenant_name = None
    tenant_info = None
    if current_user.tenant_id:
        tenant = db.query(Tenant).filter(Tenant.id == current_user.tenant_id).first()
        if tenant:
            tenant_name = tenant.name
            # Include tenant limits and contact info for frontend pre-checks
            tenant_info = {
                "id": str(tenant.id),
                "name": tenant.name,
                "status": tenant.status.value,
                "max_users": tenant.max_users,
                "max_patients": tenant.max_patients,
                "address": tenant.address,
                "contact_phone": tenant.contact_phone,
                "contact_email": tenant.contact_email,
            }

    user_response = UserResponse(
        id=current_user.id,
        tenant_id=current_user.tenant_id,
        email=current_user.email,
        first_name=current_user.first_name,
        last_name=current_user.last_name,
        phone=current_user.phone,
        department=current_user.department,
        specialization=current_user.specialization,
        status=current_user.status,
        is_active=current_user.is_active,
        is_deleted=current_user.is_deleted,
        must_change_password=current_user.must_change_password,
        email_verified=current_user.email_verified,
        roles=role_responses,
        tenant_name=tenant_name,
        created_at=current_user.created_at,
        updated_at=current_user.updated_at,
    )

    # Add tenant info if available (not in UserResponse schema, but needed for frontend)
    # Convert to dict and add tenant info
    response_dict = user_response.model_dump()
    if tenant_info:
        response_dict["tenant"] = tenant_info

    return response_dict


@router.post("/forgot-password", tags=["auth"])
def forgot_password(
    payload: ForgotPasswordRequest,
    db: Session = Depends(get_db),
) -> dict:
    """
    Initiate password reset flow.
    Generates a token and sends reset email.
    Always returns success to prevent email enumeration.
    """
    from app.models.tenant_global import Tenant

    user = get_user_by_email(db, payload.email)

    # Always return success to prevent email enumeration
    if not user:
        return {
            "message": "If an account exists with this email, a password reset link has been sent."
        }

    if not user.tenant_id:
        # SUPER_ADMIN - handle differently if needed
        return {
            "message": "If an account exists with this email, a password reset link has been sent."
        }

    # Get tenant to access schema_name
    tenant = db.query(Tenant).filter(Tenant.id == user.tenant_id).first()
    tenant_schema_name = tenant.schema_name if tenant else None

    try:
        # Create password reset token (expires in 1 hour)
        reset_token = create_password_reset_token(
            db=db,
            tenant_id=user.tenant_id,
            email=user.email,
            expires_in_hours=1,
        )

        db.commit()

        # Build reset URL
        frontend_url = (
            settings.backend_cors_origins[0]
            if settings.backend_cors_origins
            else "http://localhost:5173"
        )
        reset_url = f"{frontend_url}/reset-password?token={reset_token}"

        # Send reset email
        subject = "Reset Your Password"
        body_html = f"""
        <p>Dear {user.first_name} {user.last_name},</p>
        <p>You requested to reset your password for your Hospital Management System account.</p>
        <p>Click the button below to reset your password. This link will expire in 1 hour.</p>
        <p><strong>If you did not request this, please ignore this email.</strong></p>
        """

        html = render_email_template(
            title="Password Reset Request",
            body_html=body_html,
            cta_text="Reset Password",
            cta_url=reset_url,
            hospital_name=None,
        )

        send_notification_email(
            db=db,
            to_email=user.email,
            subject=subject,
            body=html,
            reason="password_reset",
            html=True,
            tenant_schema_name=tenant_schema_name,  # Set tenant schema for notification logging
        )
    except Exception as e:
        # Log error but still return success to prevent email enumeration
        import logging

        logger = logging.getLogger(__name__)
        logger.warning(
            f"Failed to send password reset email to {user.email[:3]}***: {e}"
        )
        db.rollback()

    return {
        "message": "If an account exists with this email, a password reset link has been sent."
    }


@router.get("/validate-reset-token", tags=["auth"])
def validate_reset_token(
    token: str = Query(..., description="Password reset token"),
    db: Session = Depends(get_db),
) -> dict:
    """
    Validate a password reset token without resetting the password.
    Returns whether the token is valid and not expired.
    """
    verification = verify_token(db, token)
    if not verification:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired reset token.",
        )

    return {
        "valid": True,
        "email": verification.email,
    }


@router.post("/reset-password", tags=["auth"])
def reset_password(
    payload: ResetPasswordRequest,
    db: Session = Depends(get_db),
) -> dict:
    """
    Reset password using a valid token.
    """
    try:
        # Validate token
        verification = verify_token(db, payload.token)
        if not verification:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid or expired reset token.",
            )

        # Find user by email
        user = get_user_by_email(db, verification.email)
        if not user:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="User not found.",
            )

        # Update password with history tracking using the unified service
        from app.services.password_service import change_user_password

        # Temporarily set must_change_password to True so we can change password without old_password
        # (since reset via token doesn't require old password)
        original_must_change = user.must_change_password
        user.must_change_password = True
        db.flush()

        try:
            # Use the unified password change function which handles:
            # - Password strength validation
            # - Password history checking (last 3 including current)
            # - Adding passwords to history
            change_user_password(
                db=db,
                user_id=user.id,
                old_password=None,  # Reset via token, no old password needed
                new_password=payload.new_password,
                performed_by_user_id=user.id,
            )
        except ValueError as e:
            # Restore original must_change_password state
            user.must_change_password = original_must_change
            db.rollback()
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=str(e),
            )

        # Mark token as used
        mark_token_used(db, verification)

        # If user has a tenant, activate the tenant if it's still PENDING or VERIFIED
        # This allows users who reset password to log in without email verification
        if user.tenant_id:
            from app.models.tenant_global import Tenant, TenantStatus

            tenant = db.query(Tenant).filter(Tenant.id == user.tenant_id).first()
            if tenant and (
                tenant.status == TenantStatus.PENDING
                or tenant.status == TenantStatus.VERIFIED
            ):
                # Activate tenant since user has successfully reset password (email access confirmed)
                tenant.status = TenantStatus.ACTIVE

        db.commit()

        return {
            "message": "Password has been reset successfully. You can now log in with your new password."
        }
    except HTTPException:
        raise
    except Exception as e:
        import logging

        logger = logging.getLogger(__name__)
        logger.error(f"Password reset failed: {e}", exc_info=True)
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to reset password. Please try again or request a new reset link.",
        ) from e


@router.get("/verify-email", tags=["auth"])
def verify_email(
    token: str = Query(..., description="Email verification token"),
    db: Session = Depends(get_db),
) -> dict:
    """
    Verify tenant email and activate tenant.
    Moves tenant from PENDING -> VERIFIED -> ACTIVE (auto-activation).
    """
    from datetime import datetime, timezone

    from app.models.tenant_global import Tenant, TenantStatus
    from app.utils.token_utils import VerificationToken, mark_token_used

    try:
        # First check if token exists
        verification = (
            db.query(VerificationToken).filter(VerificationToken.token == token).first()
        )

        if not verification:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid verification link. Please check the link or request a new verification email.",
            )

        # Check if already used
        if verification.used_at is not None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="This verification link has already been used. Your email is already verified.",
            )

        # Check if expired
        if verification.expires_at < datetime.now(timezone.utc):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="This verification link has expired. Please request a new verification email.",
            )

        tenant = db.query(Tenant).filter(Tenant.id == verification.tenant_id).first()
        if not tenant:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Tenant not found.",
            )

        if tenant.status == TenantStatus.ACTIVE:
            return {
                "message": "Email already verified and tenant is active. You can now log in.",
                "tenant_id": str(tenant.id),
                "status": tenant.status.value,
                "already_verified": True,
            }

        # Mark token as used
        mark_token_used(db, verification)

        # Update tenant status: PENDING -> VERIFIED -> ACTIVE (auto-activate)
        if tenant.status == TenantStatus.PENDING:
            tenant.status = TenantStatus.VERIFIED
        if tenant.status == TenantStatus.VERIFIED:
            tenant.status = TenantStatus.ACTIVE

        db.commit()

        return {
            "message": "Email verified successfully. Your hospital account is now active. You can now log in.",
            "tenant_id": str(tenant.id),
            "status": tenant.status.value,
            "already_verified": False,
        }
    except HTTPException:
        raise
    except Exception as e:
        import logging

        logger = logging.getLogger(__name__)
        logger.error(f"Email verification failed: {e}", exc_info=True)
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Email verification failed. Please try again or contact support.",
        ) from e


@router.post("/change-password", tags=["auth"])
def change_password(
    payload: ChangePasswordRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """
    Unified password change endpoint for both first-login and voluntary changes.

    - If must_change_password is True: old_password is optional (user already authenticated)
    - If must_change_password is False: old_password is required

    Validates password strength and checks password history (cannot reuse last 3).
    """
    from app.services.password_service import change_user_password

    try:
        change_user_password(
            db=db,
            user_id=current_user.id,
            old_password=payload.old_password,
            new_password=payload.new_password,
            performed_by_user_id=current_user.id,
        )

        # Optionally return a new token (for security, we'll require re-login)
        # For now, we'll just return success
        return {
            "message": "Password changed successfully",
            "must_change_password": False,
        }
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )
    except Exception as e:
        import logging

        logger = logging.getLogger(__name__)
        logger.error(f"Password change failed: {e}", exc_info=True)
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to change password. Please try again.",
        ) from e
