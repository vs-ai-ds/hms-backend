# app/api/v1/endpoints/prescriptions.py
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.orm import Session
from sqlalchemy.exc import SQLAlchemyError

from app.core.database import get_db
from app.core.tenant_context import TenantContext, get_tenant_context
from app.schemas.prescription import (
    PrescriptionCreate,
    PrescriptionResponse,
)
from app.services.prescription_service import (
    create_prescription,
    get_prescription,
    list_prescriptions_for_patient,
    PatientNotFoundError,
    PrescriptionNotFoundError,
)
from app.models.user import RoleName

router = APIRouter()


def _ensure_doctor_or_admin(ctx: TenantContext) -> None:
    """
    Ensure the current user is DOCTOR or HOSPITAL_ADMIN / SUPER_ADMIN.
    """
    role_names = {r.name for r in ctx.user.roles}
    if not (
        RoleName.DOCTOR.value in role_names
        or RoleName.HOSPITAL_ADMIN.value in role_names
        or RoleName.SUPER_ADMIN.value in role_names
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only doctors or admins can manage prescriptions.",
        )


@router.post(
    "/",
    response_model=PrescriptionResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_prescription_endpoint(
    payload: PrescriptionCreate,
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(get_tenant_context),
) -> PrescriptionResponse:
    """
    Create a prescription for a patient in the current tenant.

    - Only DOCTOR / HOSPITAL_ADMIN / SUPER_ADMIN can create.
    - doctor_id is taken from ctx.user.id (doctor writing the prescription).
    """
    _ensure_doctor_or_admin(ctx)

    try:
        prescription = create_prescription(
            db=db,
            patient_id=payload.patient_id,
            doctor_id=ctx.user.id,
            appointment_id=payload.appointment_id,
            payload=payload,
        )
    except PatientNotFoundError:
        raise HTTPException(status_code=404, detail="Patient not found")
    except SQLAlchemyError:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create prescription.",
        )

    # thanks to from_attributes=True, nested items will also work
    return PrescriptionResponse.model_validate(prescription)


@router.get(
    "/",
    response_model=list[PrescriptionResponse],
)
def list_prescriptions_endpoint(
    patient_id: UUID = Query(...),
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(get_tenant_context),
) -> list[PrescriptionResponse]:
    """
    List prescriptions for a given patient in the current tenant.
    """
    prescriptions = list_prescriptions_for_patient(db=db, patient_id=patient_id)
    return [PrescriptionResponse.model_validate(p) for p in prescriptions]


@router.get(
    "/{prescription_id}",
    response_model=PrescriptionResponse,
)
def get_prescription_endpoint(
    prescription_id: UUID,
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(get_tenant_context),
) -> PrescriptionResponse:
    """
    Get a single prescription by ID.
    """
    try:
        prescription = get_prescription(db=db, prescription_id=prescription_id)
    except PrescriptionNotFoundError:
        raise HTTPException(status_code=404, detail="Prescription not found")
    return PrescriptionResponse.model_validate(prescription)