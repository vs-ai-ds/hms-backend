# app/services/vital_service.py
from uuid import UUID

from sqlalchemy.orm import Session

from app.models.vital import Vital
from app.schemas.vital import VitalCreate


def add_vital_reading(
    db: Session,
    *,
    patient_id: UUID,
    recorded_by_id: UUID | None,
    payload: VitalCreate,
) -> Vital:
    vital = Vital(
        patient_id=patient_id,
        recorded_by_id=recorded_by_id,
        systolic_bp=payload.systolic_bp,
        diastolic_bp=payload.diastolic_bp,
        heart_rate=payload.heart_rate,
        temperature_c=payload.temperature_c,
        respiratory_rate=payload.respiratory_rate,
        spo2=payload.spo2,
        notes=payload.notes,
    )
    db.add(vital)
    db.commit()
    db.refresh(vital)
    return vital