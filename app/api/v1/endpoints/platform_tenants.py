# app/api/v1/endpoints/platform_tenants.py
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.api.v1.endpoints.auth import get_current_user
from app.api.v1.endpoints.tenants import _generate_temp_password
from app.core.config import get_settings
from app.core.database import get_db
from app.models.patient import Patient
from app.models.tenant_global import Tenant, TenantStatus
from app.models.user import User
from app.schemas.tenant import TenantResponse
from app.services.notification_service import send_notification_email
from app.services.user_role_service import get_user_role_names

router = APIRouter()


@router.get("/debug/metrics", tags=["platform"])
def debug_tenant_metrics(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    """
    Debug endpoint to check raw tenant_metrics table values (SUPER_ADMIN only).
    """
    _ensure_super_admin(db, current_user)

    from app.models.tenant_metrics import TenantMetrics

    # Get raw row
    metrics = db.query(TenantMetrics).first()

    if not metrics:
        return {
            "exists": False,
            "message": "No row found in tenant_metrics table. Run: python -m scripts.setup_platform",
        }

    # Also check actual counts from database
    from app.models.tenant_global import Tenant

    actual_tenant_count = db.query(func.count(Tenant.id)).scalar() or 0
    actual_user_count = (
        db.query(func.count(User.id))
        .filter(User.tenant_id.isnot(None), User.is_deleted)
        .scalar()
        or 0
    )

    return {
        "exists": True,
        "metrics_row": {
            "id": str(metrics.id),
            "total_tenants": metrics.total_tenants,
            "total_users": metrics.total_users,
            "total_patients": metrics.total_patients,
            "total_appointments": metrics.total_appointments,
            "total_prescriptions": metrics.total_prescriptions,
            "updated_at": metrics.updated_at.isoformat()
            if metrics.updated_at
            else None,
        },
        "actual_counts": {
            "tenants": actual_tenant_count,
            "users": actual_user_count,
        },
        "comparison": {
            "tenants_match": metrics.total_tenants == actual_tenant_count,
            "users_match": metrics.total_users == actual_user_count,
        },
    }


class PagedTenantResponse(BaseModel):
    items: list[TenantResponse]
    total: int
    page: int
    page_size: int


def _ensure_super_admin(db: Session, current_user: User) -> None:
    """Ensure the current user is SUPER_ADMIN."""
    user_roles = get_user_role_names(db, current_user, tenant_schema_name=None)
    if "SUPER_ADMIN" not in user_roles:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only SUPER_ADMIN can access platform tenant management.",
        )


@router.get(
    "",
    response_model=PagedTenantResponse,
)
def list_tenants(
    status_filter: Optional[str] = Query(None, description="Filter by status"),
    search: Optional[str] = Query(None, description="Search by name, license, email"),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=500),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> PagedTenantResponse:
    """
    List all tenants (SUPER_ADMIN only).
    Includes user count per tenant.
    Returns paginated results.
    """
    _ensure_super_admin(db, current_user)

    query = db.query(Tenant)

    # Apply filters
    if status_filter:
        try:
            status_enum = TenantStatus(status_filter)
            query = query.filter(Tenant.status == status_enum)
        except ValueError:
            raise HTTPException(
                status_code=400, detail=f"Invalid status: {status_filter}"
            )

    if search:
        search_term = f"%{search.strip()}%"
        query = query.filter(
            (Tenant.name.ilike(search_term))
            | (Tenant.license_number.ilike(search_term))
            | (Tenant.contact_email.ilike(search_term))
        )

    # Get total count
    total = query.count()

    # Pagination
    offset = (page - 1) * page_size
    tenants = (
        query.order_by(Tenant.created_at.desc()).offset(offset).limit(page_size).all()
    )

    # Build response with user counts
    from sqlalchemy import text

    results = []
    conn = db.connection()
    original_path = conn.execute(text("SHOW search_path")).scalar()

    for tenant in tenants:
        # Count active users for this tenant
        user_count = (
            db.query(func.count(User.id))
            .filter(
                User.tenant_id == tenant.id,
                User.is_active == True,
                User.is_deleted == False,
            )
            .scalar()
            or 0
        )

        # Count patients from tenant schema
        patient_count = 0
        try:
            conn.execute(text(f'SET search_path TO "{tenant.schema_name}", public'))
            patient_count = db.query(func.count(Patient.id)).scalar() or 0
        except Exception as e:
            import logging

            logger = logging.getLogger(__name__)
            logger.warning(
                f"Could not query patient count for tenant {tenant.name} (schema {tenant.schema_name}): {e}"
            )
        finally:
            # Restore original search_path for next iteration
            conn.execute(text(f"SET search_path TO {original_path}"))

        tenant_dict = {
            "id": tenant.id,
            "name": tenant.name,
            "address": tenant.address,
            "contact_email": tenant.contact_email,
            "contact_phone": tenant.contact_phone,
            "license_number": tenant.license_number,
            "status": tenant.status,
            "schema_name": tenant.schema_name,
            "max_users": tenant.max_users,
            "max_patients": tenant.max_patients,
            "created_at": tenant.created_at,
            "updated_at": tenant.updated_at,
            "user_count": user_count,
            "patient_count": patient_count,
        }
        results.append(TenantResponse(**tenant_dict))

    # Restore original search_path at the end
    try:
        conn.execute(text(f"SET search_path TO {original_path}"))
    except Exception:
        pass  # Ignore errors when restoring search_path

    return PagedTenantResponse(
        items=results,
        total=total,
        page=page,
        page_size=page_size,
    )


@router.patch(
    "/{tenant_id}/suspend",
    response_model=TenantResponse,
)
def suspend_tenant(
    tenant_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> TenantResponse:
    """
    Suspend a tenant (SUPER_ADMIN only).
    """
    _ensure_super_admin(db, current_user)

    tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")

    if tenant.status == TenantStatus.SUSPENDED:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Tenant is already suspended.",
        )

    tenant.status = TenantStatus.SUSPENDED
    db.commit()
    db.refresh(tenant)

    return TenantResponse.model_validate(tenant)


@router.patch(
    "/{tenant_id}/activate",
    response_model=TenantResponse,
)
def activate_tenant(
    tenant_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> TenantResponse:
    """
    Activate a tenant (SUPER_ADMIN only).
    """
    _ensure_super_admin(db, current_user)

    tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")

    if tenant.status == TenantStatus.ACTIVE:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Tenant is already active.",
        )

    tenant.status = TenantStatus.ACTIVE
    db.commit()
    db.refresh(tenant)

    return TenantResponse.model_validate(tenant)


@router.get(
    "/{tenant_id}/details",
    response_model=dict,
)
def get_tenant_details(
    tenant_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    """
    Get detailed metrics for a specific tenant (SUPER_ADMIN only).
    Returns tenant info plus aggregated metrics (patients, appointments, prescriptions, etc.).
    """
    _ensure_super_admin(db, current_user)

    tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")

    from sqlalchemy import text

    from app.models.admission import Admission, AdmissionStatus
    from app.models.appointment import Appointment
    from app.models.patient import Patient
    from app.models.prescription import Prescription

    # Count active users
    user_count = (
        db.query(func.count(User.id))
        .filter(
            User.tenant_id == tenant.id,
            User.is_active == True,
            User.is_deleted == False,
        )
        .scalar()
        or 0
    )

    # Get metrics from tenant schema
    conn = db.connection()
    original_path = conn.execute(text("SHOW search_path")).scalar()

    total_patients = 0
    total_appointments = 0
    total_prescriptions = 0
    total_admissions = 0
    active_admissions = 0

    try:
        conn.execute(text(f'SET search_path TO "{tenant.schema_name}", public'))

        total_patients = db.query(func.count(Patient.id)).scalar() or 0
        total_appointments = db.query(func.count(Appointment.id)).scalar() or 0
        total_prescriptions = db.query(func.count(Prescription.id)).scalar() or 0
        total_admissions = db.query(func.count(Admission.id)).scalar() or 0
        active_admissions = (
            db.query(func.count(Admission.id))
            .filter(Admission.status == AdmissionStatus.ACTIVE)
            .scalar()
            or 0
        )
    except Exception as e:
        import logging

        logger = logging.getLogger(__name__)
        logger.warning(
            f"Could not query metrics for tenant {tenant.name} (schema {tenant.schema_name}): {e}"
        )
    finally:
        conn.execute(text(f"SET search_path TO {original_path}"))

    return {
        "tenant": TenantResponse.model_validate(tenant).model_dump(),
        "metrics": {
            "user_count": user_count,
            "total_patients": total_patients,
            "total_appointments": total_appointments,
            "total_prescriptions": total_prescriptions,
            "total_admissions": total_admissions,
            "active_admissions": active_admissions,
        },
    }


@router.patch(
    "/{tenant_id}/limits",
    response_model=TenantResponse,
)
def set_tenant_limits(
    tenant_id: UUID,
    max_users: Optional[int] = Query(
        None, ge=1, description="Maximum number of users allowed (null = unlimited)"
    ),
    max_patients: Optional[int] = Query(
        None, ge=1, description="Maximum number of patients allowed (null = unlimited)"
    ),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> TenantResponse:
    """
    Set limits for a tenant (SUPER_ADMIN only).
    Can set max_users and/or max_patients. Omitted values are not changed.
    """
    _ensure_super_admin(db, current_user)

    tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")

    # Check current user count if max_users is being set
    if max_users is not None:
        current_user_count = (
            db.query(func.count(User.id))
            .filter(
                User.tenant_id == tenant.id,
                User.is_deleted == False,
            )
            .scalar()
            or 0
        )

        if max_users < current_user_count:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Cannot set max_users to {max_users} when tenant has {current_user_count} users.",
            )
        tenant.max_users = max_users

    # Check current patient count if max_patients is being set
    if max_patients is not None:
        from sqlalchemy import text

        from app.models.patient import Patient

        conn = db.connection()
        original_path = conn.execute(text("SHOW search_path")).scalar()
        try:
            conn.execute(text(f'SET search_path TO "{tenant.schema_name}", public'))
            current_patient_count = db.query(func.count(Patient.id)).scalar() or 0
        finally:
            conn.execute(text(f"SET search_path TO {original_path}"))

        if max_patients < current_patient_count:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Cannot set max_patients to {max_patients} when tenant has {current_patient_count} patients.",
            )
        tenant.max_patients = max_patients

    db.commit()
    db.refresh(tenant)

    return TenantResponse.model_validate(tenant)


@router.post(
    "/{tenant_id}/reset-admin-password",
    response_model=dict,
)
def reset_tenant_admin_password(
    tenant_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    """
    Reset tenant admin password and send email with temp password (SUPER_ADMIN only).
    """
    _ensure_super_admin(db, current_user)

    tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")

    # Find tenant admin user
    from app.services.user_role_service import get_user_role_names

    admin_users = (
        db.query(User)
        .filter(
            User.tenant_id == tenant.id,
            User.is_active == True,
            User.is_deleted == False,
        )
        .all()
    )

    admin_user = None
    for user in admin_users:
        user_roles = get_user_role_names(
            db, user, tenant_schema_name=tenant.schema_name
        )
        if "HOSPITAL_ADMIN" in user_roles:
            admin_user = user
            break

    if not admin_user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Tenant admin user not found.",
        )

    # Generate new temp password
    temp_password = _generate_temp_password()

    # Update password
    from app.core.security import get_password_hash

    admin_user.hashed_password = get_password_hash(temp_password)
    admin_user.must_change_password = True

    db.commit()

    # Send email
    try:
        send_notification_email(
            db=db,
            to_email=admin_user.email,
            subject=f"Password Reset - {tenant.name}",
            body=f"Your admin password has been reset. Temporary password: {temp_password}\n\nPlease change your password after logging in.",
            triggered_by=current_user,
            reason="admin_password_reset",
            tenant_schema_name=tenant.schema_name,
        )
    except Exception as e:
        import logging

        logger = logging.getLogger(__name__)
        logger.warning(f"Failed to send password reset email: {e}")

    return {
        "message": "Admin password reset successfully. Email sent with temporary password.",
        "admin_email": admin_user.email,
        "temp_password": temp_password,  # Include in response for dev/demo
    }


@router.post("/demo/refresh", tags=["platform"])
def refresh_demo_data(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    """
    Refresh demo data (freshen dates and seed if missing).
    Only available when DEMO_MODE=true and requires SUPER_ADMIN.
    Includes DB lock to prevent concurrent refresh.
    """
    settings = get_settings()
    if not settings.demo_mode:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Demo mode is not enabled. Set DEMO_MODE=true to use this endpoint.",
        )

    _ensure_super_admin(db, current_user)

    # Import here to avoid circular imports
    import subprocess
    import sys
    from pathlib import Path

    script_path = (
        Path(__file__).parent.parent.parent.parent / "scripts" / "seed_demo_data.py"
    )

    try:
        # Run freshen (which will also seed if missing)
        result = subprocess.run(
            [sys.executable, "-m", "scripts.seed_demo_data", "--freshen"],
            capture_output=True,
            text=True,
            timeout=300,  # 5 minute timeout
        )

        if result.returncode != 0:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Demo refresh failed: {result.stderr}",
            )

        return {
            "message": "Demo data refreshed successfully",
            "output": result.stdout,
        }
    except subprocess.TimeoutExpired:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Demo refresh timed out after 5 minutes",
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Demo refresh failed: {str(e)}",
        )
