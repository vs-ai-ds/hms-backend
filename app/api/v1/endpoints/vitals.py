# app/api/v1/endpoints/vitals.py
import logging
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.tenant_context import TenantContext, get_tenant_context
from app.core.tenant_db import ensure_search_path
from app.models.patient import Patient
from app.models.vital import Vital
from app.schemas.vital import VitalCreate, VitalResponse
from app.services.user_role_service import get_user_role_names

logger = logging.getLogger(__name__)
router = APIRouter()


@router.post(
    "",
    response_model=VitalResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_vital(
    payload: VitalCreate,
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(get_tenant_context),
) -> VitalResponse:
    """
    Record vitals for a patient.

    Rules:
    - Patient must exist
    - Only DOCTOR/NURSE can record vitals
    - Can optionally link to appointment_id (OPD) or admission_id (IPD)
    - Append-only: no editing past vitals
    - recorded_at defaults to now if not provided
    """
    # Check permissions
    user_roles = get_user_role_names(
        db, ctx.user, tenant_schema_name=ctx.tenant.schema_name
    )
    is_doctor = "DOCTOR" in user_roles
    is_nurse = "NURSE" in user_roles
    is_admin = "HOSPITAL_ADMIN" in user_roles or "SUPER_ADMIN" in user_roles

    if not (is_doctor or is_nurse or is_admin):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only doctors and nurses can record vitals.",
        )

    # Ensure patient exists
    patient = db.query(Patient).filter(Patient.id == payload.patient_id).first()
    if not patient:
        raise HTTPException(status_code=404, detail="Patient not found")

    # Check if patient is deceased
    if patient.is_deceased:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot record vitals for deceased patient.",
        )

    # Validate: cannot link to both appointment and admission
    if payload.appointment_id and payload.admission_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Vitals cannot be linked to both appointment and admission",
        )

    from datetime import datetime, timezone

    recorded_at = payload.recorded_at or datetime.now(timezone.utc)

    # Ensure tenant tables exist before creating (defensive check)
    from app.services.tenant_service import ensure_tenant_tables_exist

    ensure_tenant_tables_exist(db, ctx.tenant.schema_name)
    ensure_search_path(db, ctx.tenant.schema_name)

    vital = Vital(
        patient_id=payload.patient_id,
        appointment_id=payload.appointment_id,
        admission_id=payload.admission_id,
        recorded_by_id=ctx.user.id,
        systolic_bp=payload.systolic_bp,
        diastolic_bp=payload.diastolic_bp,
        heart_rate=payload.heart_rate,
        temperature_c=payload.temperature_c,
        respiratory_rate=payload.respiratory_rate,
        spo2=payload.spo2,
        weight_kg=payload.weight_kg,
        height_cm=payload.height_cm,
        notes=payload.notes,
        recorded_at=recorded_at,
    )

    try:
        db.add(vital)
        db.flush()  # Get ID without committing
        vital_id = vital.id
        db.commit()
        ensure_search_path(db, ctx.tenant.schema_name)

        # Re-query the vital to ensure we have a fresh object
        vital = db.query(Vital).filter(Vital.id == vital_id).first()
        if not vital:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to retrieve created vital after commit.",
            )
    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to record vitals: {str(e)}",
        )

    return VitalResponse.model_validate(vital)


@router.get(
    "",
    response_model=list[VitalResponse],
)
def list_vitals(
    patient_id: UUID = Query(..., description="Filter by patient ID"),
    appointment_id: Optional[UUID] = Query(
        None, description="Filter by appointment ID"
    ),
    admission_id: Optional[UUID] = Query(None, description="Filter by admission ID"),
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(get_tenant_context),
) -> list[VitalResponse]:
    """
    List vitals for a patient.
    Ordered by recorded_at descending (most recent first).
    """
    query = db.query(Vital).filter(Vital.patient_id == patient_id)

    if appointment_id:
        query = query.filter(Vital.appointment_id == appointment_id)
    if admission_id:
        query = query.filter(Vital.admission_id == admission_id)

    # Order by recorded_at descending
    vitals = query.order_by(Vital.recorded_at.desc()).all()

    return [VitalResponse.model_validate(v) for v in vitals]
