# app/services/patient_service.py
from uuid import UUID

from sqlalchemy.orm import Session

from app.models.patient import Patient


def get_patient_by_id(db: Session, patient_id: UUID) -> Patient | None:
    return db.query(Patient).filter(Patient.id == patient_id).first()