# app/services/patient_type_service.py
"""
Service for computing patient type (OPD/IPD) from active admission.
"""

from uuid import UUID

from sqlalchemy.orm import Session

from app.models.admission import Admission, AdmissionStatus
from app.models.patient import PatientType


def get_patient_type(db: Session, patient_id: UUID) -> PatientType:
    """
    Derive patient type from active admission.
    Returns IPD if patient has active admission, else OPD.
    """
    active_admission = (
        db.query(Admission)
        .filter(
            Admission.patient_id == patient_id,
            Admission.status == AdmissionStatus.ACTIVE,
        )
        .first()
    )
    return PatientType.IPD if active_admission else PatientType.OPD


def enrich_patient_response_with_type(
    db: Session, patient_dict: dict, patient_id: UUID
) -> dict:
    """
    Add computed patient_type to patient response dict.
    """
    patient_type = get_patient_type(db, patient_id)
    patient_dict["patient_type"] = patient_type.value
    return patient_dict
