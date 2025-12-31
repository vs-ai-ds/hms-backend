# app/api/v1/endpoints/admissions.py
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, joinedload

from app.core.database import get_db
from app.core.tenant_context import TenantContext, get_tenant_context
from app.core.tenant_db import ensure_search_path
from app.models.admission import Admission, AdmissionStatus
from app.models.appointment import Appointment, AppointmentStatus
from app.models.department import Department
from app.models.patient import Patient
from app.models.user import User
from app.schemas.admission import (
    AdmissionCreate,
    AdmissionDischargeRequest,
    AdmissionResponse,
)
from app.services.tenant_service import ensure_tenant_tables_exist
from app.services.user_role_service import get_user_role_names

router = APIRouter()
logger = logging.getLogger(__name__)


def _reload_admission_with_relations(
    db: Session,
    admission_id: UUID,
    tenant_schema_name: str,
) -> Admission:
    """
    Reload admission with all necessary relationships.
    Does NOT set search_path - caller must ensure it's set.
    """
    admission = (
        db.query(Admission)
        .options(
            joinedload(Admission.patient),
            joinedload(Admission.primary_doctor),
        )
        .filter(Admission.id == admission_id)
        .first()
    )
    if not admission:
        raise HTTPException(status_code=404, detail="Admission not found")
    return admission


@router.post(
    "",
    response_model=AdmissionResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_admission(
    payload: AdmissionCreate,
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(get_tenant_context),
) -> AdmissionResponse:
    """
    Admit a patient to IPD.

    Rules:
    - Patient must exist in this tenant schema
    - primary_doctor_user_id must be a User with role DOCTOR
    - Patient must not have an active admission
    - admit_datetime must not be in the future
    """
    ensure_search_path(db, ctx.tenant.schema_name)
    ensure_tenant_tables_exist(db, ctx.tenant.schema_name)
    ensure_search_path(db, ctx.tenant.schema_name)

    # Ensure patient exists
    patient = db.query(Patient).filter(Patient.id == payload.patient_id).first()
    if not patient:
        raise HTTPException(status_code=404, detail="Patient not found")

    # Check if patient is deceased
    if patient.is_deceased:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot admit deceased patient.",
        )

    # Auto-set primary_doctor_user_id and department_id for doctors if not provided
    primary_doctor_user_id = payload.primary_doctor_user_id
    department_id = payload.department_id

    # Check if current user is a doctor
    current_user_roles = get_user_role_names(db, ctx.user, tenant_schema_name=ctx.tenant.schema_name)
    is_current_user_doctor = (
        "DOCTOR" in current_user_roles
        and "HOSPITAL_ADMIN" not in current_user_roles
        and "SUPER_ADMIN" not in current_user_roles
    )

    if is_current_user_doctor:
        # For doctors, auto-set primary_doctor_user_id and department_id from current user
        if not primary_doctor_user_id:
            primary_doctor_user_id = ctx.user.id

        if not department_id:
            # Get department_id from user's department name
            if ctx.user.department:
                user_dept = db.query(Department).filter(Department.name == ctx.user.department).first()
                if user_dept:
                    department_id = user_dept.id
                else:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail=f"Department '{ctx.user.department}' not found. Please contact administrator.",
                    )
            else:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Department information not available. Please contact administrator.",
                )

    # Validate department_id is provided (required for IPD admissions)
    if not department_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Department is required for admissions.",
        )

    # Validate department exists
    department = db.query(Department).filter(Department.id == department_id).first()
    if not department:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Department not found.",
        )

    # Validate doctor is a user with DOCTOR role
    doctor_user = db.query(User).filter(User.id == primary_doctor_user_id).first()
    if not doctor_user:
        raise HTTPException(status_code=404, detail="Doctor not found")

    doctor_roles = get_user_role_names(db, doctor_user, tenant_schema_name=ctx.tenant.schema_name)
    if "DOCTOR" not in doctor_roles:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Selected user is not a doctor.",
        )

    # Check for active admission
    active_admission = (
        db.query(Admission)
        .filter(
            Admission.patient_id == payload.patient_id,
            Admission.status == AdmissionStatus.ACTIVE,
        )
        .first()
    )
    if active_admission:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Patient already has an active admission. Please discharge the current admission first.",
        )

    # C4) OPD/IPD Conflict Resolution: Check for OPD appointments that need to be cancelled
    now = datetime.now(timezone.utc)
    # Find OPD appointments for this patient with status SCHEDULED or CHECKED_IN or IN_CONSULTATION
    # where scheduled_at is today or future
    opd_appointments = (
        db.query(Appointment)
        .filter(
            Appointment.patient_id == payload.patient_id,
            Appointment.status.in_(
                [AppointmentStatus.SCHEDULED, AppointmentStatus.CHECKED_IN, AppointmentStatus.IN_CONSULTATION]
            ),
            Appointment.scheduled_at >= (now - timedelta(hours=2)),  # Include appointments from last 2 hours (same-day)
        )
        .all()
    )

    # C4) Business Rule: If OPD is SCHEDULED or CHECKED_IN (before consultation) => CANCELLED
    # If OPD is IN_CONSULTATION => COMPLETED with note
    for opd in opd_appointments:
        if opd.status == AppointmentStatus.IN_CONSULTATION:
            # OPD is in consultation - mark as completed and link to admission
            opd.status = AppointmentStatus.COMPLETED
            opd.completed_at = now
            opd.linked_ipd_admission_id = None  # Will be set after admission is created
            if not opd.notes:
                opd.notes = ""
            opd.notes += f"\n[Converted/Admitted to IPD on {payload.admit_datetime.strftime('%Y-%m-%d %H:%M')}]"
        else:
            # OPD is SCHEDULED or CHECKED_IN - cancel it
            opd.status = AppointmentStatus.CANCELLED
            opd.cancelled_reason = "ADMITTED_TO_IPD"
            opd.cancelled_note = f"Patient admitted to IPD on {payload.admit_datetime.strftime('%Y-%m-%d %H:%M')}"
            opd.linked_ipd_admission_id = None  # Will be set after admission is created

    # Validate admit_datetime is not in the future
    now = datetime.now(timezone.utc)
    if payload.admit_datetime.tzinfo is None:
        admit_utc = payload.admit_datetime.replace(tzinfo=timezone.utc)
    else:
        admit_utc = payload.admit_datetime.astimezone(timezone.utc)
    if admit_utc > now:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Admission date cannot be in the future.",
        )

    # No 15-minute interval restriction for IPD admissions - allow any minute selection

    admission = Admission(
        patient_id=payload.patient_id,
        department_id=department_id,  # Use auto-set value
        primary_doctor_user_id=primary_doctor_user_id,  # Use auto-set value
        admit_datetime=payload.admit_datetime,
        notes=payload.notes,
        status=AdmissionStatus.ACTIVE,
    )

    # 1) Commit admission creation
    try:
        db.add(admission)
        db.flush()  # Flush to get admission.id

        # Update patient's last_visited_at when admitted to IPD
        patient = db.query(Patient).filter(Patient.id == payload.patient_id).first()
        if patient:
            patient.last_visited_at = admit_utc

        # Link cancelled/completed appointments to this admission
        for opd in opd_appointments:
            opd.linked_ipd_admission_id = admission.id

        db.commit()
        ensure_search_path(db, ctx.tenant.schema_name)
    except SQLAlchemyError:
        db.rollback()
        raise HTTPException(status_code=500, detail="Failed to create admission.")

    # 2) Reload with relations (prevents lazy-load/search_path issues)
    admission = _reload_admission_with_relations(db, admission.id, ctx.tenant.schema_name)

    patient = admission.patient

    # Get department name for notification
    department_name = "N/A"
    if admission.department_id:
        dept = db.query(Department).filter(Department.id == admission.department_id).first()
        department_name = dept.name if dept else "N/A"

    # 3) Notifications (best-effort)
    if patient and patient.consent_email and patient.email:
        try:
            from app.services.notification_service import send_notification_email
            from app.utils.email_templates import render_email_template

            admit_date = (
                payload.admit_datetime.strftime("%Y-%m-%d %H:%M")
                if hasattr(payload.admit_datetime, "strftime")
                else str(payload.admit_datetime)
            )
            doctor_name = f"{admission.primary_doctor.first_name if admission.primary_doctor else ''} {admission.primary_doctor.last_name if admission.primary_doctor else ''}".strip()

            body_html = f"""
            <p>Dear {patient.first_name} {patient.last_name or ""},</p>
            <p>You have been admitted to <strong>{ctx.tenant.name}</strong>.</p>
            <p><strong>Admission Details:</strong></p>
            <ul>
                <li><strong>Admission Date & Time:</strong> {admit_date}</li>
                <li><strong>Primary Doctor:</strong> Dr. {doctor_name}</li>
                <li><strong>Department:</strong> {department_name}</li>
            </ul>
            <p>Our medical team will provide you with the best care during your stay.</p>
            <p>If you have any questions or concerns, please don't hesitate to contact your healthcare provider.</p>
            <p><strong>Note:</strong> This is an automated notification. For urgent matters, please contact the hospital directly.</p>
            """
            html = render_email_template(
                title="IPD Admission",
                body_html=body_html,
                hospital_name=ctx.tenant.name,
            )
            send_notification_email(
                db=db,
                to_email=patient.email,
                subject=f"IPD Admission - {ctx.tenant.name}",
                body=html,
                triggered_by=ctx.user,
                reason="ipd_admission",
                tenant_schema_name=ctx.tenant.schema_name,
                html=True,
                check_patient_flag=True,
            )
        except Exception as e:
            logger.exception("Non-fatal: admission email notification failed. adm=%s", admission.id)

    if patient and patient.consent_sms and patient.phone_primary:
        try:
            from app.services.notification_service import send_notification_sms

            admit_date = (
                payload.admit_datetime.strftime("%Y-%m-%d %H:%M")
                if hasattr(payload.admit_datetime, "strftime")
                else str(payload.admit_datetime)
            )
            message = f"You have been admitted to {ctx.tenant.name} on {admit_date}. Our medical team will provide you with the best care during your stay."
            send_notification_sms(
                db=db,
                phone=patient.phone_primary,
                message=message,
                triggered_by=ctx.user,
                reason="ipd_admission",
                check_patient_flag=True,
            )
        except Exception as e:
            logger.exception("Non-fatal: admission SMS notification failed. adm=%s", admission.id)

    # 4) Build and return response
    admission_dict = AdmissionResponse.model_validate(admission).model_dump()
    if admission.patient:
        admission_dict["patient_name"] = f"{admission.patient.first_name} {admission.patient.last_name or ''}".strip()
        admission_dict["patient_code"] = admission.patient.patient_code
    if admission.primary_doctor:
        admission_dict["doctor_name"] = (
            f"{admission.primary_doctor.first_name} {admission.primary_doctor.last_name}".strip()
        )
    admission_dict["department"] = department_name
    return AdmissionResponse(**admission_dict)


@router.get(
    "",
    response_model=list[AdmissionResponse],
)
def list_admissions(
    patient_id: Optional[UUID] = Query(None, description="Filter by patient ID"),
    status: Optional[str] = Query(
        None, description="Filter by status (ACTIVE, DISCHARGED, CANCELLED) - alias for status_filter"
    ),
    status_filter: Optional[str] = Query(None, description="Filter by status (ACTIVE, DISCHARGED, CANCELLED)"),
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(get_tenant_context),
) -> list[AdmissionResponse]:
    """
    List admissions for the current tenant.
    """
    ensure_search_path(db, ctx.tenant.schema_name)

    query = db.query(Admission).options(
        joinedload(Admission.patient),
        joinedload(Admission.primary_doctor),
    )

    # Apply ABAC filters
    user_roles = get_user_role_names(db, ctx.user, tenant_schema_name=ctx.tenant.schema_name)
    is_doctor = "DOCTOR" in user_roles
    is_admin = "HOSPITAL_ADMIN" in user_roles or "SUPER_ADMIN" in user_roles

    # Doctors can only see admissions where they are the primary doctor
    if is_doctor and not is_admin:
        query = query.filter(Admission.primary_doctor_user_id == ctx.user.id)

    # Apply filters
    if patient_id:
        query = query.filter(Admission.patient_id == patient_id)

    # Support both 'status' and 'status_filter' parameters for compatibility
    status_to_filter = status or status_filter
    if status_to_filter:
        try:
            status_enum = AdmissionStatus(status_to_filter)
            query = query.filter(Admission.status == status_enum)
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Invalid status: {status_to_filter}")

    # Order by admit_datetime descending
    query = query.order_by(Admission.admit_datetime.desc())
    admissions = query.all()

    # Build response with computed fields
    results = []
    for admission in admissions:
        admission_dict = AdmissionResponse.model_validate(admission).model_dump()
        if admission.patient:
            admission_dict["patient_name"] = (
                f"{admission.patient.first_name} {admission.patient.last_name or ''}".strip()
            )
            admission_dict["patient_code"] = admission.patient.patient_code
        # Get department from admission (not patient)
        if admission.department_id:
            from app.models.department import Department

            dept = db.query(Department).filter(Department.id == admission.department_id).first()
            admission_dict["department"] = dept.name if dept else None
        if admission.primary_doctor:
            admission_dict["doctor_name"] = (
                f"{admission.primary_doctor.first_name} {admission.primary_doctor.last_name}".strip()
            )
        results.append(AdmissionResponse(**admission_dict))

    return results


@router.get(
    "/{admission_id}",
    response_model=AdmissionResponse,
)
def get_admission(
    admission_id: UUID,
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(get_tenant_context),
) -> AdmissionResponse:
    """
    Get a single admission by ID.
    """
    ensure_search_path(db, ctx.tenant.schema_name)
    admission = (
        db.query(Admission)
        .options(
            joinedload(Admission.patient),
            joinedload(Admission.primary_doctor),
        )
        .filter(Admission.id == admission_id)
        .first()
    )
    if not admission:
        raise HTTPException(status_code=404, detail="Admission not found")

    # ABAC: Doctors can only view their own admissions
    user_roles = get_user_role_names(db, ctx.user, tenant_schema_name=ctx.tenant.schema_name)
    is_doctor = "DOCTOR" in user_roles
    is_admin = "HOSPITAL_ADMIN" in user_roles or "SUPER_ADMIN" in user_roles

    if is_doctor and not is_admin and admission.primary_doctor_user_id != ctx.user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You can only view admissions assigned to you.",
        )

    # Build response
    admission_dict = AdmissionResponse.model_validate(admission).model_dump()
    if admission.patient:
        admission_dict["patient_name"] = f"{admission.patient.first_name} {admission.patient.last_name or ''}".strip()
        admission_dict["patient_code"] = admission.patient.patient_code
    if admission.primary_doctor:
        admission_dict["doctor_name"] = (
            f"{admission.primary_doctor.first_name} {admission.primary_doctor.last_name}".strip()
        )
    if admission.department_id:
        from app.models.department import Department

        dept = db.query(Department).filter(Department.id == admission.department_id).first()
        admission_dict["department"] = dept.name if dept else None

    return AdmissionResponse(**admission_dict)


@router.patch(
    "/{admission_id}/discharge",
    response_model=AdmissionResponse,
)
def discharge_admission(
    admission_id: UUID,
    payload: AdmissionDischargeRequest,
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(get_tenant_context),
) -> AdmissionResponse:
    """
    Discharge a patient from IPD.

    Rules:
    - Admission must be ACTIVE
    - discharge_datetime must not be before admit_datetime
    - discharge_summary is required
    """
    ensure_search_path(db, ctx.tenant.schema_name)
    admission = db.query(Admission).filter(Admission.id == admission_id).first()
    if not admission:
        raise HTTPException(status_code=404, detail="Admission not found")

    if admission.status != AdmissionStatus.ACTIVE:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Cannot discharge admission with status {admission.status.value}",
        )

    # Validate discharge_datetime is not before admit_datetime
    if payload.discharge_datetime.tzinfo is None:
        discharge_utc = payload.discharge_datetime.replace(tzinfo=timezone.utc)
    else:
        discharge_utc = payload.discharge_datetime

    if discharge_utc < admission.admit_datetime:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Discharge date cannot be before admission date.",
        )

    # Validate discharge_datetime is not in the future
    now = datetime.now(timezone.utc)
    if discharge_utc > now:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Discharge date cannot be in the future.",
        )

    # Update admission
    admission.discharge_datetime = payload.discharge_datetime
    admission.discharge_summary = payload.discharge_summary
    admission.status = AdmissionStatus.DISCHARGED

    try:
        db.commit()
        ensure_search_path(db, ctx.tenant.schema_name)
        db.refresh(admission)
    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to discharge admission: {str(e)}",
        )

    # Build response
    admission_dict = AdmissionResponse.model_validate(admission).model_dump()
    if admission.patient:
        admission_dict["patient_name"] = f"{admission.patient.first_name} {admission.patient.last_name or ''}".strip()
        admission_dict["patient_code"] = admission.patient.patient_code
    if admission.primary_doctor:
        admission_dict["doctor_name"] = (
            f"{admission.primary_doctor.first_name} {admission.primary_doctor.last_name}".strip()
        )
    if admission.department_id:
        from app.models.department import Department

        dept = db.query(Department).filter(Department.id == admission.department_id).first()
        admission_dict["department"] = dept.name if dept else None

    return AdmissionResponse(**admission_dict)
