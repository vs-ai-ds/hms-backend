# app/api/v1/endpoints/appointments.py
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from sqlalchemy.exc import SQLAlchemyError

from app.core.database import get_db
from app.core.tenant_context import TenantContext, get_tenant_context
from app.models.patient import Patient
from app.models.appointment import Appointment
from app.schemas.appointment import AppointmentCreate, AppointmentResponse

router = APIRouter()


@router.post(
    "/",
    response_model=AppointmentResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_appointment(
    payload: AppointmentCreate,
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(get_tenant_context),
) -> AppointmentResponse:
    """
    Create an appointment in the current tenant schema.

    Rules:
    - Patient must exist in this tenant schema (search_path already set).
    - doctor_id:
        * if provided, use that doctor
        * otherwise, default to ctx.user.id
    """
    # Ensure patient exists in this tenant schema
    patient = db.query(Patient).filter(Patient.id == payload.patient_id).first()
    if not patient:
        raise HTTPException(status_code=404, detail="Patient not found")

    doctor_id = payload.doctor_id or ctx.user.id

    appt = Appointment(
        patient_id=payload.patient_id,
        doctor_id=doctor_id,
        scheduled_at=payload.scheduled_at,
        notes=payload.notes,
    )

    try:
        db.add(appt)
        db.commit()
        db.refresh(appt)
    except SQLAlchemyError:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create appointment.",
        )

    return AppointmentResponse.model_validate(appt)