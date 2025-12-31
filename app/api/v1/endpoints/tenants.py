import secrets
import string

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.database import get_db
from app.models.tenant_global import Tenant, TenantStatus
from app.schemas.tenant import TenantRegisterRequest, TenantResponse
from app.services.notification_service import send_notification_email
from app.services.tenant_service import register_tenant
from app.services.user_service import create_hospital_admin_for_tenant
from app.utils.email_templates import render_registration_email
from app.utils.token_utils import create_verification_token, mark_token_used, verify_token

settings = get_settings()

router = APIRouter()


@router.get("/health", tags=["tenants"])
async def tenant_health_check() -> dict:
    """
    Simple health check for the tenants module.
    """
    return {"status": "tenants-ok"}


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


@router.post(
    "/register",
    response_model=TenantResponse,
    status_code=status.HTTP_201_CREATED,
    tags=["tenants"],
)
def tenant_self_register(
    payload: TenantRegisterRequest,
    db: Session = Depends(get_db),
) -> TenantResponse:
    """
    FR-1: Hospital Self-Registration.

    - Accepts basic hospital details.
    - Ensures license number is unique.
    - Creates tenant record with schema-per-tenant.
    - Auto-creates a HOSPITAL_ADMIN user for this tenant.
    - Returns the tenant info plus admin email + temp password (for dev/demo).
    """
    try:
        tenant = register_tenant(
            db=db,
            name=payload.name,
            address=payload.address,
            contact_email=payload.contact_email,
            contact_phone=payload.contact_phone,
            license_number=payload.license_number,
        )
        # Auto-create admin user
        temp_password = _generate_temp_password()
        admin_user = create_hospital_admin_for_tenant(db, tenant, temp_password)

        # Generate verification token
        verification_token = create_verification_token(
            db=db,
            tenant_id=tenant.id,
            email=tenant.contact_email,
        )

        db.commit()

        # Re-query tenant to ensure fresh state after commit
        from app.models.tenant_global import Tenant

        tenant = db.query(Tenant).filter(Tenant.id == tenant.id).first()
        if not tenant:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to retrieve tenant after registration.",
            )

        # Increment platform metrics (user is created as part of tenant registration)
        from sqlalchemy import text

        from app.services.tenant_metrics_service import increment_users

        try:
            # Get original search_path before incrementing metrics
            conn = db.connection()
            original_path = conn.execute(text("SHOW search_path")).scalar()

            # Set search_path to public and increment metrics (this will commit)
            conn.execute(text("SET search_path TO public"))
            increment_users(db)  # This commits and may close the connection

            # Restore search_path - get fresh connection since increment_users committed
            try:
                conn.execute(text(f"SET search_path TO {original_path}"))
            except Exception:
                # Connection was closed after commit, get a fresh one
                conn = db.connection()
                conn.execute(text(f"SET search_path TO {original_path}"))
        except Exception as e:
            # Log but don't fail registration if metrics increment fails
            import logging

            logger = logging.getLogger(__name__)
            logger.warning(f"Could not increment user metrics: {e}", exc_info=True)

        # Send registration email with verification link
        try:
            verification_url = f"{settings.backend_cors_origins[0] if settings.backend_cors_origins else 'http://localhost:5173'}/verify-email?token={verification_token}"
            subject, html_body = render_registration_email(
                hospital_name=tenant.name,
                admin_email=admin_user.email,
                temp_password=temp_password,
                verification_url=verification_url,
            )
            send_notification_email(
                db=db,
                to_email=tenant.contact_email,
                subject=subject,
                body=html_body,
                reason="tenant_registration",
                html=True,
                tenant_schema_name=tenant.schema_name,  # Set tenant schema for notification logging
            )
            import logging

            logger = logging.getLogger(__name__)
            logger.info(f"Registration email sent successfully to {tenant.contact_email}")
        except Exception as e:
            # Log but don't fail registration if email fails
            import logging

            logger = logging.getLogger(__name__)
            logger.warning(f"Failed to send registration email: {e}", exc_info=True)
    except ValueError as ex:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(ex),
        ) from ex
    except Exception as e:
        db.rollback()
        import logging

        logger = logging.getLogger(__name__)
        logger.error(f"Tenant registration failed: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to register tenant. Please try again or contact support.",
        ) from e

    # Build response manually to include admin credentials
    resp = TenantResponse(
        id=tenant.id,
        name=tenant.name,
        address=tenant.address,
        contact_email=tenant.contact_email,
        contact_phone=tenant.contact_phone,
        license_number=tenant.license_number,
        status=tenant.status,
        schema_name=tenant.schema_name,
        created_at=tenant.created_at,
        updated_at=tenant.updated_at,
        admin_email=admin_user.email,
        admin_temp_password=temp_password,
    )
    return resp


@router.get("/verify", tags=["tenants"])
def verify_tenant_email(
    token: str = Query(..., description="Email verification token"),
    db: Session = Depends(get_db),
) -> dict:
    """
    Verify tenant email and activate tenant.
    Moves tenant from PENDING -> VERIFIED -> ACTIVE (auto-activation).
    """
    verification = verify_token(db, token)
    if not verification:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired verification token.",
        )

    tenant = db.query(Tenant).filter(Tenant.id == verification.tenant_id).first()
    if not tenant:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Tenant not found.",
        )

    if tenant.status == TenantStatus.ACTIVE:
        return {"message": "Email already verified and tenant is active."}

    # Mark token as used
    mark_token_used(db, verification)

    # Update tenant status: PENDING -> VERIFIED -> ACTIVE (auto-activate)
    if tenant.status == TenantStatus.PENDING:
        tenant.status = TenantStatus.VERIFIED
    if tenant.status == TenantStatus.VERIFIED:
        tenant.status = TenantStatus.ACTIVE

    db.commit()

    return {
        "message": "Email verified successfully. Your hospital account is now active.",
        "tenant_id": str(tenant.id),
        "status": tenant.status.value,
    }
