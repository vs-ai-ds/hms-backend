# app/services/patient_share_service.py
import secrets
from datetime import datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.models.admission import Admission
from app.models.appointment import Appointment
from app.models.department import Department
from app.models.patient import Patient
from app.models.patient_share import PatientShare, PatientShareAccessLog, PatientShareLink, ShareMode, ShareStatus
from app.models.prescription import Prescription, PrescriptionItem
from app.models.tenant_global import Tenant
from app.models.user import User
from app.models.vital import Vital
from app.schemas.patient_share import SharedPatientSummary


def generate_share_token() -> str:
    """Generate a secure random token for share links"""
    return secrets.token_urlsafe(48)


def create_patient_share(
    db: Session,
    *,
    source_tenant_id: UUID,
    patient_id: UUID,
    target_tenant_id: UUID | None,
    share_mode: ShareMode,
    validity_days: int,
    created_by_user_id: UUID,
    note: str | None = None,
) -> PatientShare:
    """
    Create a patient share record.
    For CREATE_RECORD mode, patient record will be created when receiver imports it.
    """
    expires_at = datetime.now(timezone.utc) + timedelta(days=validity_days) if validity_days > 0 else None
    token = generate_share_token()

    share = PatientShare(
        source_tenant_id=source_tenant_id,
        target_tenant_id=target_tenant_id,
        patient_id=patient_id,
        share_mode=share_mode,
        token=token,
        expires_at=expires_at,
        created_by_user_id=created_by_user_id,
        note=note,
        status=ShareStatus.ACTIVE,
    )

    db.add(share)
    db.flush()

    # Note: CREATE_RECORD mode no longer creates patient immediately
    # Patient will be created when receiver calls the import endpoint

    db.commit()
    db.refresh(share)
    return share


def get_shared_patient_summary(
    db: Session,
    *,
    share_id: UUID,
    token: str | None = None,
) -> SharedPatientSummary:
    """
    Get summary data for a shared patient.
    Validates token and expiration if token is provided.
    """
    share = db.query(PatientShare).filter(PatientShare.id == share_id).first()
    if not share:
        raise ValueError("Share not found")

    # Validate token if provided
    if token:
        if share.token != token:
            raise ValueError("Invalid token")
        if share.status != ShareStatus.ACTIVE:
            raise ValueError("Share is not active")
        if share.expires_at and share.expires_at < datetime.now(timezone.utc):
            share.status = ShareStatus.EXPIRED
            db.commit()
            raise ValueError("Share has expired")

    # Get source tenant
    source_tenant = db.query(Tenant).filter(Tenant.id == share.source_tenant_id).first()
    if not source_tenant:
        raise ValueError("Source tenant not found")

    # Switch to source tenant schema to get patient data
    conn = db.connection()
    original_path = conn.execute(text("SHOW search_path")).scalar()

    try:
        conn.execute(text(f'SET search_path TO "{source_tenant.schema_name}", public'))

        patient = db.query(Patient).filter(Patient.id == share.patient_id).first()
        if not patient:
            raise ValueError("Patient not found")

        # Get ALL related records for sharing
        all_appointments = (
            db.query(Appointment)
            .filter(Appointment.patient_id == patient.id)
            .order_by(Appointment.scheduled_at.desc())
            .all()
        )
        
        all_prescriptions = (
            db.query(Prescription)
            .filter(Prescription.patient_id == patient.id)
            .order_by(Prescription.created_at.desc())
            .all()
        )
        
        all_admissions = (
            db.query(Admission)
            .filter(Admission.patient_id == patient.id)
            .order_by(Admission.admit_datetime.desc())
            .all()
        )
        
        all_vitals = (
            db.query(Vital)
            .filter(Vital.patient_id == patient.id)
            .order_by(Vital.recorded_at.desc())
            .all()
        )

        # Build appointments data with department/doctor names
        appointments_data = []
        for apt in all_appointments:
            dept_name = None
            doctor_name = None
            try:
                dept = db.query(Department).filter(Department.id == apt.department_id).first()
                if dept:
                    dept_name = dept.name
                doctor = db.query(User).filter(User.id == apt.doctor_user_id).first()
                if doctor:
                    doctor_name = f"{doctor.first_name} {doctor.last_name or ''}".strip() or doctor.email
            except Exception:
                pass
            
            appointments_data.append({
                "id": str(apt.id),
                "scheduled_at": apt.scheduled_at.isoformat() if apt.scheduled_at else None,
                "status": apt.status.value if hasattr(apt.status, 'value') else str(apt.status),
                "notes": apt.notes,
                "checked_in_at": apt.checked_in_at.isoformat() if apt.checked_in_at else None,
                "consultation_started_at": apt.consultation_started_at.isoformat() if apt.consultation_started_at else None,
                "completed_at": apt.completed_at.isoformat() if apt.completed_at else None,
                "department_id": str(apt.department_id),
                "department_name": dept_name,
                "doctor_user_id": str(apt.doctor_user_id),
                "doctor_name": doctor_name,
            })

        # Build prescriptions data with items
        prescriptions_data = []
        for prx in all_prescriptions:
            doctor_name = None
            try:
                doctor = db.query(User).filter(User.id == prx.doctor_user_id).first()
                if doctor:
                    doctor_name = f"{doctor.first_name} {doctor.last_name or ''}".strip() or doctor.email
            except Exception:
                pass
            
            items_data = []
            for item in prx.items if hasattr(prx, 'items') else []:
                items_data.append({
                    "medicine_name": item.medicine_name,
                    "dosage": item.dosage,
                    "frequency": item.frequency,
                    "duration": item.duration,
                    "instructions": item.instructions,
                    "quantity": item.quantity,
                    "stock_item_id": str(item.stock_item_id) if item.stock_item_id else None,
                })
            
            prescriptions_data.append({
                "id": str(prx.id),
                "prescription_code": prx.prescription_code,
                "status": prx.status.value if hasattr(prx.status, 'value') else str(prx.status),
                "chief_complaint": prx.chief_complaint,
                "diagnosis": prx.diagnosis,
                "cancelled_reason": prx.cancelled_reason if hasattr(prx, 'cancelled_reason') else None,
                "cancelled_at": prx.cancelled_at.isoformat() if hasattr(prx, 'cancelled_at') and prx.cancelled_at else None,
                "created_at": prx.created_at.isoformat() if prx.created_at else None,
                "doctor_user_id": str(prx.doctor_user_id),
                "doctor_name": doctor_name,
                "appointment_id": str(prx.appointment_id) if prx.appointment_id else None,
                "admission_id": str(prx.admission_id) if prx.admission_id else None,
                "items": items_data,
            })

        # Build admissions data
        admissions_data = []
        for adm in all_admissions:
            dept_name = None
            doctor_name = None
            try:
                dept = db.query(Department).filter(Department.id == adm.department_id).first()
                if dept:
                    dept_name = dept.name
                doctor = db.query(User).filter(User.id == adm.primary_doctor_user_id).first()
                if doctor:
                    doctor_name = f"{doctor.first_name} {doctor.last_name or ''}".strip() or doctor.email
            except Exception:
                pass
            
            admissions_data.append({
                "id": str(adm.id),
                "admit_datetime": adm.admit_datetime.isoformat() if adm.admit_datetime else None,
                "discharge_datetime": adm.discharge_datetime.isoformat() if adm.discharge_datetime else None,
                "discharge_summary": adm.discharge_summary,
                "notes": adm.notes,
                "status": adm.status.value if hasattr(adm.status, 'value') else str(adm.status),
                "department_id": str(adm.department_id),
                "department_name": dept_name,
                "primary_doctor_user_id": str(adm.primary_doctor_user_id),
                "doctor_name": doctor_name,
            })

        # Build vitals data
        vitals_data = [
            {
                "id": str(v.id),
                "recorded_at": v.recorded_at.isoformat() if v.recorded_at else None,
                "systolic_bp": v.systolic_bp,
                "diastolic_bp": v.diastolic_bp,
                "heart_rate": v.heart_rate,
                "temperature_c": v.temperature_c,
                "respiratory_rate": v.respiratory_rate,
                "spo2": v.spo2,
                "weight_kg": v.weight_kg,
                "height_cm": v.height_cm,
                "notes": v.notes,
            }
            for v in all_vitals
        ]

        # Legacy format for backward compatibility
        last_visits = [
            {
                "date": str(apt.scheduled_at.date()),
                "type": "OPD",
                "department": None,
            }
            for apt in all_appointments[:5]
        ]
        
        last_prescriptions = [
            {
                "date": str(prx.created_at.date()),
                "diagnosis": prx.diagnosis,
                "medicines_count": len(prx.items) if hasattr(prx, 'items') else 0,
            }
            for prx in all_prescriptions[:5]
        ]
        
        recent_vitals = [
            {
                "date": str(v.recorded_at.date()),
                "time": str(v.recorded_at.time())[:5],
                "systolic_bp": v.systolic_bp,
                "diastolic_bp": v.diastolic_bp,
                "heart_rate": v.heart_rate,
                "temperature_c": v.temperature_c,
                "respiratory_rate": v.respiratory_rate,
                "spo2": v.spo2,
                "weight_kg": v.weight_kg,
                "height_cm": v.height_cm,
                "notes": v.notes,
            }
            for v in all_vitals[:10]
        ]

        summary = SharedPatientSummary(
            first_name=patient.first_name,
            last_name=patient.last_name,
            middle_name=patient.middle_name,
            patient_code=patient.patient_code,
            dob=str(patient.dob) if patient.dob else None,
            gender=patient.gender,
            blood_group=getattr(patient, "blood_group", None),
            phone_primary=patient.phone_primary,
            phone_alternate=patient.phone_alternate,
            email=patient.email,
            city=patient.city,
            state=patient.state,
            country=patient.country,
            postal_code=patient.postal_code,
            address_line1=patient.address_line1,
            address_line2=patient.address_line2,
            known_allergies=patient.known_allergies,
            chronic_conditions=patient.chronic_conditions,
            clinical_notes=getattr(patient, "clinical_notes", None),
            emergency_contact_name=patient.emergency_contact_name,
            emergency_contact_relation=patient.emergency_contact_relation,
            emergency_contact_phone=patient.emergency_contact_phone,
            national_id_type=patient.national_id_type,
            national_id_number=patient.national_id_number,
            marital_status=patient.marital_status,
            preferred_language=patient.preferred_language,
            is_dnr=getattr(patient, "is_dnr", False),
            is_deceased=getattr(patient, "is_deceased", False),
            date_of_death=str(patient.date_of_death) if getattr(patient, "date_of_death", None) else None,
            vitals=vitals_data,
            appointments=appointments_data,
            prescriptions=prescriptions_data,
            admissions=admissions_data,
            last_visits=last_visits,
            last_prescriptions=last_prescriptions,
            recent_vitals=recent_vitals,
        )

    finally:
        conn.execute(text(f"SET search_path TO {original_path}"))

    return summary


def log_share_access(
    db: Session,
    *,
    share_id: UUID,
    accessed_by_user_id: UUID | None = None,
    ip_address: str | None = None,
    user_agent: str | None = None,
) -> None:
    """Log access to a shared patient record"""
    log = PatientShareAccessLog(
        share_id=share_id,
        accessed_by_user_id=accessed_by_user_id,
        ip_address=ip_address,
        user_agent=user_agent,
    )
    db.add(log)
    db.commit()


def revoke_share(
    db: Session,
    *,
    share_id: UUID,
    revoked_by_user_id: UUID,
) -> PatientShare:
    """Revoke a patient share"""
    share = db.query(PatientShare).filter(PatientShare.id == share_id).first()
    if not share:
        raise ValueError("Share not found")

    share.status = ShareStatus.REVOKED
    share.revoked_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(share)
    return share
