# app/services/patient_service.py
import json
from datetime import date, datetime
from typing import Optional
from uuid import UUID

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.models.patient import Patient
from app.models.patient_audit import PatientAuditLog
from app.schemas.patient import (
    DuplicateCheckResponse,
    PatientUpdate,
    ProfileCompleteRequest,
    QuickRegisterRequest,
)
from app.services.patient_duplicate_service import find_duplicate_candidates
from app.utils.id_generators import generate_patient_code


def create_patient_quick_register(
    db: Session,
    *,
    payload: QuickRegisterRequest,
    created_by_id: UUID,
) -> tuple[Patient, DuplicateCheckResponse]:
    """
    Create a patient via quick register.
    Returns the patient and duplicate check results.
    """
    # Ensure patients table exists before creating patient
    # This should have been done in get_tenant_context, but ensure it here too
    from app.core.tenant_context import _set_tenant_search_path
    from app.models.tenant_global import Tenant
    from app.models.user import User

    # Get tenant from user (we need schema_name)
    user = db.query(User).filter(User.id == created_by_id).first()
    if user and user.tenant_id:
        tenant = db.query(Tenant).filter(Tenant.id == user.tenant_id).first()
        if tenant:
            from app.services.tenant_service import ensure_tenant_tables_exist

            try:
                ensure_tenant_tables_exist(db, tenant.schema_name)
                _set_tenant_search_path(db, tenant.schema_name)
            except Exception as e:
                import logging

                logger = logging.getLogger(__name__)
                logger.error(f"Could not ensure tenant tables exist: {e}", exc_info=True)

    # Check for duplicates
    duplicate_candidates = find_duplicate_candidates(
        db=db,
        first_name=payload.first_name,
        last_name=payload.last_name,
        dob=payload.dob,
        phone_primary=payload.phone_primary,
    )

    duplicate_response = DuplicateCheckResponse(
        has_duplicates=len(duplicate_candidates) > 0,
        candidates=duplicate_candidates,
    )

    # Generate patient code with tenant ID
    patient_code = generate_patient_code(db, tenant.id)

    # Calculate DOB from age if needed
    dob = payload.dob
    if payload.dob_unknown and payload.age_only:
        # Approximate DOB from age (use Jan 1 of birth year)
        today = date.today()
        birth_year = today.year - payload.age_only
        dob = date(birth_year, 1, 1)

    # Create patient
    # NOTE: patient_type is derived from active admission, not stored
    # NOTE: department_id removed - department is per-visit (appointment/admission), not per-patient
    # Set consent flags to True by default (patient can opt-out later)
    patient = Patient(
        patient_code=patient_code,
        first_name=payload.first_name,
        last_name=payload.last_name,
        dob=dob,
        dob_unknown=payload.dob_unknown,
        age_only=payload.age_only if payload.dob_unknown else None,
        gender=payload.gender,
        phone_primary=payload.phone_primary,
        email=payload.email,  # Added email field
        city=payload.city,
        consent_email=True,  # Default to True - patient can opt-out
        consent_sms=True,  # Default to True - patient can opt-out
        created_by_id=created_by_id,
        updated_by_id=created_by_id,
    )

    try:
        db.add(patient)
        db.flush()  # Get ID without committing
        patient_id = patient.id

        # Create audit log
        audit_log = PatientAuditLog(
            patient_id=patient_id,
            action="CREATE",
            changed_by_id=created_by_id,
            change_reason="Quick register",
            new_values=json.dumps(
                {
                    "patient_code": patient_code,
                    "first_name": payload.first_name,
                    "last_name": payload.last_name,
                    "phone_primary": payload.phone_primary,
                }
            ),
        )
        db.add(audit_log)
        db.commit()

        # Re-query to ensure we get the patient with all fields (created_at, updated_at)
        # Ensure search_path is set again after commit (it might have been reset)
        if user and user.tenant_id:
            tenant = db.query(Tenant).filter(Tenant.id == user.tenant_id).first()
            if tenant:
                _set_tenant_search_path(db, tenant.schema_name)

        patient = db.query(Patient).filter(Patient.id == patient_id).first()
        if not patient:
            raise SQLAlchemyError("Failed to retrieve created patient after commit")

        return patient, duplicate_response
    except SQLAlchemyError:
        db.rollback()
        raise


def update_patient_profile(
    db: Session,
    *,
    patient_id: UUID,
    payload: ProfileCompleteRequest | PatientUpdate,
    updated_by_id: UUID,
    change_reason: Optional[str] = None,
    schema_name: Optional[str] = None,
) -> Patient:
    """
    Update patient profile with extended fields.
    """
    patient = db.query(Patient).filter(Patient.id == patient_id).first()
    if not patient:
        raise ValueError("Patient not found")

    # Store old values for audit
    old_values = {
        "first_name": patient.first_name,
        "last_name": patient.last_name,
        "phone_primary": patient.phone_primary,
        "email": patient.email,
    }

    # Update fields from payload
    update_data = payload.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        if hasattr(patient, field):
            setattr(patient, field, value)

    patient.updated_by_id = updated_by_id
    patient.updated_at = datetime.utcnow()

    try:
        db.flush()

        # Create audit log
        new_values = {
            "first_name": patient.first_name,
            "last_name": patient.last_name,
            "phone_primary": patient.phone_primary,
            "email": patient.email,
        }
        audit_log = PatientAuditLog(
            patient_id=patient.id,
            action="UPDATE",
            changed_by_id=updated_by_id,
            change_reason=change_reason or "Profile update",
            old_values=json.dumps(old_values),
            new_values=json.dumps(new_values),
        )
        db.add(audit_log)
        db.commit()

        # Re-query patient to ensure fresh state and avoid lazy loading issues
        # Ensure search_path is set before re-querying (it may have been reset after commit)
        if schema_name:
            from app.core.tenant_context import _set_tenant_search_path

            _set_tenant_search_path(db, schema_name)

        patient_id = patient.id
        patient = db.query(Patient).filter(Patient.id == patient_id).first()
        if not patient:
            raise ValueError("Failed to retrieve updated patient after commit")

        return patient
    except SQLAlchemyError:
        db.rollback()
        raise


def get_patient_with_summary(
    db: Session,
    *,
    patient_id: UUID,
) -> Patient:
    """Get patient with all relationships loaded."""
    patient = db.query(Patient).filter(Patient.id == patient_id).first()
    if not patient:
        raise ValueError("Patient not found")
    return patient


def check_duplicates(
    db: Session,
    *,
    first_name: str,
    last_name: Optional[str],
    dob: Optional[date],
    phone_primary: str,
    national_id_number: Optional[str] = None,
    exclude_patient_id: Optional[UUID] = None,
) -> DuplicateCheckResponse:
    """Check for duplicate patients."""
    candidates = find_duplicate_candidates(
        db=db,
        first_name=first_name,
        last_name=last_name,
        dob=dob,
        phone_primary=phone_primary,
        national_id_number=national_id_number,
        exclude_patient_id=exclude_patient_id,
    )

    return DuplicateCheckResponse(
        has_duplicates=len(candidates) > 0,
        candidates=candidates,
    )
