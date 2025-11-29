import secrets
import string

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.schemas.tenant import TenantRegisterRequest, TenantResponse
from app.services.tenant_service import register_tenant
from app.services.user_service import create_hospital_admin_for_tenant

router = APIRouter()


@router.get("/health", tags=["tenants"])
async def tenant_health_check() -> dict:
    """
    Simple health check for the tenants module.
    """
    return {"status": "tenants-ok"}


def _generate_temp_password(length: int = 12) -> str:
    """
    Generate a random temporary password for the hospital admin.

    NOTE: For hackathon/demo only. In production, you would:
    - Enforce stronger rules.
    - Send via email with a reset link instead of returning in API.
    """
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))


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

        db.commit()
    except ValueError as ex:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(ex),
        ) from ex
    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to register tenant.",
        )

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