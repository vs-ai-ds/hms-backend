# app/services/prescription_service.py
from __future__ import annotations

import logging
from uuid import UUID

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.models.patient import Patient
from app.models.prescription import Prescription, PrescriptionItem
from app.models.user import User
from app.models.user_tenant import Tenant
from app.schemas.prescription import PrescriptionCreate
from app.utils.id_generators import generate_prescription_code

logger = logging.getLogger(__name__)


class PatientNotFoundError(Exception):
    pass


class PrescriptionNotFoundError(Exception):
    pass


def create_prescription(
    db: Session,
    *,
    patient_id: UUID,
    doctor_user_id: UUID,
    appointment_id: UUID | None = None,
    admission_id: UUID | None = None,
    payload: PrescriptionCreate,
) -> Prescription:
    """
    Create a prescription and items.
    Must be linked to either appointment_id (OPD) or admission_id (IPD), never both.
    """
    from sqlalchemy import text

    from app.core.tenant_context import _set_tenant_search_path

    patient = db.query(Patient).filter(Patient.id == patient_id).first()
    if not patient:
        raise PatientNotFoundError("Patient not found")

    if appointment_id and admission_id:
        raise ValueError("Prescription cannot be linked to both appointment and admission")
    if not appointment_id and not admission_id:
        raise ValueError("Prescription must be linked to either appointment (OPD) or admission (IPD)")

    doctor_user = db.query(User).filter(User.id == doctor_user_id).first()
    if not doctor_user or not doctor_user.tenant_id:
        raise ValueError("Doctor user must belong to a tenant")

    tenant = db.query(Tenant).filter(Tenant.id == doctor_user.tenant_id).first()
    if not tenant or not tenant.schema_name:
        raise ValueError("Tenant schema not found for doctor user")

    tenant_schema_name = tenant.schema_name

    # Ensure tenant search_path before touching tenant tables
    _set_tenant_search_path(db, tenant_schema_name)
    db.execute(text(f'SET search_path TO "{tenant_schema_name}", public'))

    prescription_code = generate_prescription_code(db, doctor_user.tenant_id)

    prescription = Prescription(
        prescription_code=prescription_code,
        patient_id=patient_id,
        doctor_user_id=doctor_user_id,
        appointment_id=appointment_id,
        admission_id=admission_id,
        chief_complaint=getattr(payload, "chief_complaint", None),
        diagnosis=getattr(payload, "diagnosis", None),
    )

    try:
        db.add(prescription)
        db.flush()  # assigns prescription.id

        for item_in in payload.items:
            if item_in.stock_item_id and not item_in.quantity:
                raise ValueError(
                    f"Quantity is required for medicine '{item_in.medicine_name}' when linked to stock item"
                )

            db.add(
                PrescriptionItem(
                    prescription_id=prescription.id,
                    stock_item_id=item_in.stock_item_id,
                    medicine_name=item_in.medicine_name,
                    dosage=item_in.dosage,
                    frequency=item_in.frequency,
                    duration=item_in.duration,
                    instructions=item_in.instructions,
                    quantity=item_in.quantity,
                )
            )

        db.commit()

        # Non-critical platform metrics update
        try:
            from app.services.tenant_metrics_service import increment_prescriptions

            with db.begin_nested():
                db.execute(text("SET LOCAL search_path TO public"))
                increment_prescriptions(db)
        except Exception as e:
            logger.warning("Failed to increment prescription metrics (non-critical): %s", e, exc_info=True)

        # Restore tenant path after the nested metric update
        _set_tenant_search_path(db, tenant_schema_name)
        db.execute(text(f'SET search_path TO "{tenant_schema_name}", public'))

        db.refresh(prescription)
        return prescription

    except SQLAlchemyError:
        db.rollback()
        raise


def get_prescription(db: Session, *, prescription_id: UUID) -> Prescription:
    from sqlalchemy.orm import joinedload

    prescription = (
        db.query(Prescription)
        .options(joinedload(Prescription.patient), joinedload(Prescription.doctor), joinedload(Prescription.items))
        .filter(Prescription.id == prescription_id)
        .first()
    )
    if not prescription:
        raise PrescriptionNotFoundError("Prescription not found")
    return prescription


def list_prescriptions_for_patient(db: Session, *, patient_id: UUID) -> list[Prescription]:
    return (
        db.query(Prescription)
        .filter(Prescription.patient_id == patient_id)
        .order_by(Prescription.created_at.desc())
        .all()
    )
