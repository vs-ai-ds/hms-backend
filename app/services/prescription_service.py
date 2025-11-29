# app/services/prescription_service.py
from uuid import UUID

from sqlalchemy.orm import Session
from sqlalchemy.exc import SQLAlchemyError

from app.models.patient import Patient
from app.models.prescription import Prescription, PrescriptionItem
from app.schemas.prescription import PrescriptionCreate


class PatientNotFoundError(Exception):
    pass


class PrescriptionNotFoundError(Exception):
    pass


def create_prescription(
    db: Session,
    *,
    patient_id: UUID,
    doctor_id: UUID,
    appointment_id: UUID | None,
    payload: PrescriptionCreate,
) -> Prescription:
    """
    Create a prescription and its items for a patient.
    """
    patient = db.query(Patient).filter(Patient.id == patient_id).first()
    if not patient:
        raise PatientNotFoundError("Patient not found")

    prescription = Prescription(
        patient_id=patient_id,
        doctor_id=doctor_id,
        appointment_id=appointment_id,
    )

    try:
        db.add(prescription)
        db.flush()  # get prescription.id

        items: list[PrescriptionItem] = []
        for item_in in payload.items:
            item = PrescriptionItem(
                prescription_id=prescription.id,
                medicine_name=item_in.medicine_name,
                dosage=item_in.dosage,
                frequency=item_in.frequency,
                duration=item_in.duration,
                instructions=item_in.instructions,
            )
            db.add(item)
            items.append(item)

        db.commit()
        db.refresh(prescription)
    except SQLAlchemyError:
        db.rollback()
        raise

    return prescription


def get_prescription(
    db: Session,
    *,
    prescription_id: UUID,
) -> Prescription:
    prescription = (
        db.query(Prescription)
        .filter(Prescription.id == prescription_id)
        .first()
    )
    if not prescription:
        raise PrescriptionNotFoundError("Prescription not found")
    return prescription


def list_prescriptions_for_patient(
    db: Session,
    *,
    patient_id: UUID,
) -> list[Prescription]:
    return (
        db.query(Prescription)
        .filter(Prescription.patient_id == patient_id)
        .order_by(Prescription.created_at.desc())
        .all()
    )