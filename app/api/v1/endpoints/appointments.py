# app/api/v1/endpoints/appointments.py
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, or_
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, joinedload

from app.core.config import get_settings
from app.core.database import get_db
from app.core.tenant_context import TenantContext, get_tenant_context
from app.core.tenant_db import ensure_search_path
from app.models.admission import Admission, AdmissionStatus
from app.models.appointment import Appointment, AppointmentStatus
from app.models.department import Department
from app.models.patient import Patient
from app.models.prescription import Prescription, PrescriptionStatus
from app.models.user import User
from app.schemas.appointment import AppointmentCreate, AppointmentListResponse, AppointmentResponse
from app.schemas.appointment_actions import (
    AppointmentCancelRequest,
    AppointmentCheckInRequest,
    AppointmentCompleteRequest,
    AppointmentNoShowRequest,
    AppointmentRescheduleRequest,
    AppointmentStartConsultationRequest,
)
from app.services.notification_service import send_notification_email, send_notification_sms
from app.services.tenant_service import ensure_tenant_tables_exist
from app.services.user_role_service import get_user_role_names
from app.utils.datetime_utils import is_valid_15_minute_interval
from app.utils.email_templates import render_email_template

router = APIRouter()
logger = logging.getLogger(__name__)


# -------------------------
# Helpers
# -------------------------
def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(dt: datetime) -> datetime:
    """
    Convert dt to tz-aware UTC.
    If dt is naive, we treat it as UTC (consistent with existing behavior).
    """
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _get_roles(db: Session, ctx: TenantContext) -> set[str]:
    return set(get_user_role_names(db, ctx.user, tenant_schema_name=ctx.tenant.schema_name))


def _is_admin(roles: set[str]) -> bool:
    return "HOSPITAL_ADMIN" in roles or "SUPER_ADMIN" in roles


def _is_receptionist(roles: set[str]) -> bool:
    return "RECEPTIONIST" in roles


def _is_doctor(roles: set[str]) -> bool:
    return "DOCTOR" in roles


def _require_receptionist_or_admin(roles: set[str]) -> None:
    if not (_is_receptionist(roles) or _is_admin(roles)):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only receptionists and admins can perform this action.",
        )


def _require_doctor_or_admin(roles: set[str]) -> None:
    if not (_is_doctor(roles) or _is_admin(roles)):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only doctors and admins can perform this action.",
        )


def _require_assigned_doctor_or_admin(roles: set[str], appointment: Appointment, ctx: TenantContext) -> None:
    if _is_admin(roles):
        return
    if not _is_doctor(roles):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only doctors and admins can perform this action.",
        )
    if appointment.doctor_user_id != ctx.user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You can only perform this action for appointments assigned to you.",
        )


def _block_if_terminal(status_value: AppointmentStatus) -> None:
    if status_value in (AppointmentStatus.COMPLETED, AppointmentStatus.CANCELLED, AppointmentStatus.NO_SHOW):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Appointment is already {status_value.value}. No further actions are allowed.",
        )


def _block_if_any_draft_rx(db: Session, appointment_id: UUID) -> None:
    draft = (
        db.query(Prescription)
        .filter(
            Prescription.appointment_id == appointment_id,
            Prescription.status == PrescriptionStatus.DRAFT,
        )
        .first()
    )
    if draft:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot complete appointment while a draft prescription exists. Please issue or cancel the prescription first.",
        )


def _reload_appointment_with_relations(
    db: Session,
    appointment_id: UUID,
    tenant_schema_name: str | None,
) -> Appointment:
    if not tenant_schema_name or not tenant_schema_name.strip():
        raise HTTPException(
            status_code=500,
            detail="Tenant schema name missing in request context.",
        )

    appointment = (
        db.query(Appointment)
        .options(
            joinedload(Appointment.patient),
            joinedload(Appointment.doctor),
        )
        .filter(Appointment.id == appointment_id)
        .first()
    )
    if not appointment:
        raise HTTPException(status_code=404, detail="Appointment not found")
    return appointment


def _build_appointment_response(
    appointment: Appointment,
    db: Session,
    tenant_schema_name: str | None = None,
) -> dict:
    if not tenant_schema_name or not tenant_schema_name.strip():
        raise HTTPException(
            status_code=500,
            detail="Tenant schema name missing in request context.",
        )

    apt_dict = {
        "id": appointment.id,
        "patient_id": appointment.patient_id,
        "department_id": appointment.department_id,
        "doctor_user_id": appointment.doctor_user_id,
        "scheduled_at": appointment.scheduled_at,
        "status": appointment.status,
        "notes": appointment.notes,
        "created_at": appointment.created_at,
        "checked_in_at": appointment.checked_in_at,
        "consultation_started_at": appointment.consultation_started_at,
        "completed_at": appointment.completed_at,
        "no_show_at": appointment.no_show_at,
        "cancelled_reason": appointment.cancelled_reason,
        "cancelled_note": appointment.cancelled_note,
        "linked_ipd_admission_id": appointment.linked_ipd_admission_id,
        "patient_name": None,
        "patient_code": None,
        "doctor_name": None,
        "department": None,
        "has_prescription": False,
        "prescription_count": 0,
        "prescription_status": None,
    }

    if appointment.patient:
        apt_dict["patient_name"] = f"{appointment.patient.first_name} {appointment.patient.last_name or ''}".strip()
        apt_dict["patient_code"] = appointment.patient.patient_code

    if appointment.doctor:
        apt_dict["doctor_name"] = f"{appointment.doctor.first_name} {appointment.doctor.last_name or ''}".strip()

    if appointment.department_id:
        try:
            dept = db.query(Department).filter(Department.id == appointment.department_id).first()
            apt_dict["department"] = dept.name if dept else None
        except Exception:
            apt_dict["department"] = None

    try:
        prescription_count = db.query(Prescription).filter(Prescription.appointment_id == appointment.id).count()
        apt_dict["has_prescription"] = prescription_count > 0
        apt_dict["prescription_count"] = prescription_count

        latest = (
            db.query(Prescription)
            .filter(Prescription.appointment_id == appointment.id)
            .order_by(Prescription.created_at.desc())
            .first()
        )
        apt_dict["prescription_status"] = latest.status if latest else None
    except Exception:
        apt_dict["has_prescription"] = False
        apt_dict["prescription_count"] = 0
        apt_dict["prescription_status"] = None

    return apt_dict


# -------------------------
# Create
# -------------------------


@router.post(
    "",
    response_model=AppointmentResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_appointment(
    payload: AppointmentCreate,
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(get_tenant_context),
) -> AppointmentResponse:
    """
    Create an OPD appointment.

    Rules:
    - Patient must exist.
    - No appointment for deceased patient.
    - Block OPD appointment if patient has ACTIVE admission.
    - doctor_user_id required (must have DOCTOR role).
    - department_id required (must exist).
    - scheduled_at must not be in the past (UTC).
    - Prevent conflicts: same patient + with in 15 minutes (scheduled_at) where status != CANCELLED.
    """
    ensure_search_path(db, ctx.tenant.schema_name)
    ensure_tenant_tables_exist(db, ctx.tenant.schema_name)
    ensure_search_path(db, ctx.tenant.schema_name)

    # Patient exists?
    patient = db.query(Patient).filter(Patient.id == payload.patient_id).first()
    if not patient:
        raise HTTPException(status_code=404, detail="Patient not found")

    if getattr(patient, "is_deceased", False):
        raise HTTPException(status_code=400, detail="Cannot create appointment for deceased patient.")

    # Block if active admission exists
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
            status_code=400,
            detail="Cannot create OPD appointment for patient with active admission. Please discharge the patient first.",
        )

    # Auto-set doctor/department for current doctor user
    doctor_user_id = payload.doctor_user_id
    department_id = payload.department_id

    current_roles = set(get_user_role_names(db, ctx.user, tenant_schema_name=ctx.tenant.schema_name))
    current_is_doctor_only = (
        ("DOCTOR" in current_roles) and ("HOSPITAL_ADMIN" not in current_roles) and ("SUPER_ADMIN" not in current_roles)
    )

    if current_is_doctor_only:
        if not doctor_user_id:
            doctor_user_id = ctx.user.id

        if not department_id:
            if getattr(ctx.user, "department", None):
                dept = db.query(Department).filter(Department.name == ctx.user.department).first()
                if not dept:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Department '{ctx.user.department}' not found. Please contact administrator.",
                    )
                department_id = dept.id
            else:
                raise HTTPException(
                    status_code=400, detail="Department information not available. Please contact administrator."
                )

    if not doctor_user_id:
        raise HTTPException(status_code=400, detail="Doctor is required.")
    if not department_id:
        raise HTTPException(status_code=400, detail="Department is required.")

    # Doctor user exists and has DOCTOR role
    doctor_user = db.query(User).filter(User.id == doctor_user_id).first()
    if not doctor_user:
        raise HTTPException(status_code=404, detail="Doctor not found")

    doctor_roles = set(get_user_role_names(db, doctor_user, tenant_schema_name=ctx.tenant.schema_name))
    if "DOCTOR" not in doctor_roles:
        doctor_name = (
            f"{doctor_user.first_name} {doctor_user.last_name}".strip() or doctor_user.email or "the selected user"
        )
        raise HTTPException(
            status_code=400,
            detail=f"Selected user ({doctor_name}) does not have the DOCTOR role. Please select a user with the DOCTOR role to create an appointment.",
        )

    # Department exists
    department = db.query(Department).filter(Department.id == department_id).first()
    if not department:
        raise HTTPException(status_code=400, detail="Department not found.")

    # scheduled_at not in past (UTC)
    scheduled_utc = _as_utc(payload.scheduled_at)
    now = _utcnow()
    if scheduled_utc < now:
        raise HTTPException(status_code=400, detail="Appointment date cannot be in the past.")

    # Validate 15-minute interval (00, 15, 30, 45)
    if not is_valid_15_minute_interval(scheduled_utc):
        raise HTTPException(
            status_code=422, detail="Please select a time in 15-minute steps (e.g., 08:00, 08:15, 08:30, 08:45)."
        )

    # Conflict check: same patient + same minute, status != CANCELLED
    scheduled_minute = func.date_trunc("minute", scheduled_utc)
    conflicting = (
        db.query(Appointment)
        .filter(
            Appointment.patient_id == payload.patient_id,
            func.date_trunc("minute", Appointment.scheduled_at) == scheduled_minute,
            Appointment.status != AppointmentStatus.CANCELLED,
        )
        .first()
    )
    if conflicting:
        raise HTTPException(status_code=400, detail="An appointment already exists for this patient at this time.")

    appt = Appointment(
        patient_id=payload.patient_id,
        department_id=department_id,
        doctor_user_id=doctor_user_id,
        scheduled_at=scheduled_utc,
        notes=payload.notes,
        status=AppointmentStatus.SCHEDULED,
    )

    # 1) Commit appointment creation
    try:
        db.add(appt)
        db.commit()
        ensure_search_path(db, ctx.tenant.schema_name)
    except SQLAlchemyError:
        db.rollback()
        raise HTTPException(status_code=500, detail="Failed to create appointment.")

    # 2) Reload with relations (prevents lazy-load/search_path issues)
    appointment = _reload_appointment_with_relations(db, appt.id, ctx.tenant.schema_name)

    # 3) Notifications (best-effort)
    patient = appointment.patient
    if patient and patient.consent_email and patient.email:
        try:
            scheduled_time = scheduled_utc.strftime("%Y-%m-%d %H:%M")
            doctor_name = f"{appointment.doctor.first_name if appointment.doctor else 'TBD'} {appointment.doctor.last_name if appointment.doctor else ''}".strip()
            body_html = f"""
            <p>Dear {patient.first_name} {patient.last_name or ""},</p>
            <p>Your appointment has been scheduled successfully at <strong>{ctx.tenant.name}</strong>.</p>
            <p><strong>Appointment Details:</strong></p>
            <ul>
                <li><strong>Date & Time:</strong> {scheduled_time}</li>
                <li><strong>Doctor:</strong> {doctor_name}</li>
                <li><strong>Department:</strong> {department.name}</li>
            </ul>
            <p>Please arrive 10 minutes before your scheduled time.</p>
            """
            html = render_email_template(
                title="Appointment Scheduled",
                body_html=body_html,
                hospital_name=ctx.tenant.name,
            )
            send_notification_email(
                db=db,
                to_email=patient.email,
                subject=f"Appointment Scheduled - {ctx.tenant.name}",
                body=html,
                triggered_by=ctx.user,
                reason="appointment_created",
                tenant_schema_name=ctx.tenant.schema_name,
                html=True,
                check_patient_flag=True,
            )
        except Exception as e:
            logger.exception("Non-fatal: appointment email notification failed. apt=%s", appt.id)

    if patient and patient.consent_sms and patient.phone_primary:
        try:
            scheduled_time = scheduled_utc.strftime("%Y-%m-%d %H:%M")
            doctor_name = (
                f"{appointment.doctor.first_name if appointment.doctor else ''} {appointment.doctor.last_name if appointment.doctor else ''}".strip()
                or "Doctor"
            )
            msg = f"Appointment scheduled at {ctx.tenant.name} on {scheduled_time} with Dr. {doctor_name}. Please arrive 10 mins early."
            send_notification_sms(
                db=db,
                phone=patient.phone_primary,
                message=msg,
                triggered_by=ctx.user,
                reason="appointment_created",
                check_patient_flag=True,
            )
        except Exception as e:
            logger.exception("Non-fatal: appointment SMS notification failed. apt=%s", appt.id)

    # 4) Build and return response
    return AppointmentResponse(**_build_appointment_response(appointment, db, ctx.tenant.schema_name))


# -------------------------
# List / Get
# -------------------------


@router.get("", response_model=AppointmentListResponse)
def list_appointments(
    search: Optional[str] = Query(None, description="Search by patient name, patient_code, doctor name"),
    status: Optional[str] = Query(None, description="Comma-separated statuses"),
    date_from: Optional[date] = Query(None, description="Filter from date"),
    date_to: Optional[date] = Query(None, description="Filter to date"),
    visit_type: Optional[str] = Query(None, description="(OPD only) kept for compatibility"),
    patient_id: Optional[UUID] = Query(None),
    doctor_user_id: Optional[UUID] = Query(None),
    department_id: Optional[UUID] = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=500),
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(get_tenant_context),
):
    ensure_search_path(db, ctx.tenant.schema_name)
    roles = _get_roles(db, ctx)

    query = db.query(Appointment).options(
        joinedload(Appointment.patient),
        joinedload(Appointment.doctor),
    )

    # ABAC: doctor sees only their own (unless admin/receptionist)
    if _is_doctor(roles) and not _is_admin(roles) and not _is_receptionist(roles):
        query = query.filter(Appointment.doctor_user_id == ctx.user.id)

    if search:
        term = f"%{search.strip()}%"
        query = (
            query.join(Patient)
            .join(User, Appointment.doctor_user_id == User.id, isouter=True)
            .filter(
                or_(
                    Patient.first_name.ilike(term),
                    Patient.last_name.ilike(term),
                    Patient.patient_code.ilike(term),
                    User.first_name.ilike(term),
                    User.last_name.ilike(term),
                )
            )
        )

    if status:
        parts = [s.strip() for s in status.split(",") if s.strip()]
        if parts:
            try:
                enums = [AppointmentStatus(s) for s in parts]
            except ValueError as e:
                raise HTTPException(status_code=400, detail=f"Invalid status: {str(e)}")
            query = query.filter(Appointment.status.in_(enums))

    if date_from:
        query = query.filter(func.date(Appointment.scheduled_at) >= date_from)
    if date_to:
        query = query.filter(func.date(Appointment.scheduled_at) <= date_to)

    if patient_id:
        query = query.filter(Appointment.patient_id == patient_id)
    if doctor_user_id:
        query = query.filter(Appointment.doctor_user_id == doctor_user_id)
    if department_id:
        query = query.filter(Appointment.department_id == department_id)

    # Auto mark no-shows (safe side-effect, consistent)
    settings = get_settings()
    now = _utcnow()
    threshold = now - timedelta(minutes=settings.opd_no_show_minutes_after_scheduled)

    no_show_apts = (
        db.query(Appointment)
        .filter(
            Appointment.status == AppointmentStatus.SCHEDULED,
            Appointment.scheduled_at < threshold,
            Appointment.checked_in_at.is_(None),
        )
        .all()
    )
    if no_show_apts:
        for apt in no_show_apts:
            apt.status = AppointmentStatus.NO_SHOW
            apt.no_show_at = now
        try:
            db.commit()
            ensure_search_path(db, ctx.tenant.schema_name)
        except SQLAlchemyError:
            db.rollback()

    query = query.order_by(Appointment.scheduled_at.desc())
    total = query.count()

    offset = (page - 1) * page_size
    appointments = query.offset(offset).limit(page_size).all()

    items = [AppointmentResponse(**_build_appointment_response(a, db, ctx.tenant.schema_name)) for a in appointments]
    return {"items": items, "total": total, "page": page, "page_size": page_size}


@router.get("/{appointment_id}", response_model=AppointmentResponse)
def get_appointment(
    appointment_id: UUID,
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(get_tenant_context),
) -> AppointmentResponse:
    ensure_search_path(db, ctx.tenant.schema_name)
    appointment = (
        db.query(Appointment)
        .options(joinedload(Appointment.patient), joinedload(Appointment.doctor))
        .filter(Appointment.id == appointment_id)
        .first()
    )
    if not appointment:
        raise HTTPException(status_code=404, detail="Appointment not found")

    roles = _get_roles(db, ctx)
    if _is_doctor(roles) and not _is_admin(roles) and not _is_receptionist(roles):
        if appointment.doctor_user_id != ctx.user.id:
            raise HTTPException(status_code=403, detail="You can only view appointments assigned to you.")

    return AppointmentResponse(**_build_appointment_response(appointment, db, ctx.tenant.schema_name))


# -------------------------
# Generic PATCH (restricted)
# -------------------------


@router.patch("/{appointment_id}", response_model=AppointmentResponse)
def update_appointment(
    appointment_id: UUID,
    payload: dict,
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(get_tenant_context),
) -> AppointmentResponse:
    """
    Restricted PATCH endpoint.

    We DO NOT allow status transitions here to avoid bypassing:
    - timestamps
    - role checks
    - draft-Rx completion block
    - notifications

    Use action endpoints instead:
    - /check-in
    - /start-consultation
    - /complete
    - /cancel
    - /no-show
    - /reschedule

    Allowed fields:
    - notes (string)
    """
    ensure_search_path(db, ctx.tenant.schema_name)
    appointment = _reload_appointment_with_relations(db, appointment_id, ctx.tenant.schema_name)

    if "status" in payload:
        raise HTTPException(
            status_code=400,
            detail="Status updates are not allowed on this endpoint. Use action endpoints instead.",
        )

    roles = _get_roles(db, ctx)
    # Allow doctor (assigned) or receptionist/admin to edit notes, but block terminal changes.
    _block_if_terminal(appointment.status)
    if _is_admin(roles) or _is_receptionist(roles):
        pass
    else:
        _require_assigned_doctor_or_admin(roles, appointment, ctx)

    if "notes" in payload:
        if payload["notes"] is not None and not isinstance(payload["notes"], str):
            raise HTTPException(status_code=400, detail="notes must be a string")
        appointment.notes = payload["notes"]

    # 1) Commit notes update
    try:
        db.commit()
        ensure_search_path(db, ctx.tenant.schema_name)
    except SQLAlchemyError:
        db.rollback()
        raise HTTPException(status_code=500, detail="Failed to update appointment.")

    # 2) Reload with relations (prevents lazy-load/search_path issues)
    appointment = _reload_appointment_with_relations(db, appointment_id, ctx.tenant.schema_name)

    # 3) Return response
    return AppointmentResponse(**_build_appointment_response(appointment, db, ctx.tenant.schema_name))


# -------------------------
# Action endpoints
# -------------------------


@router.patch("/{appointment_id}/check-in", response_model=AppointmentResponse)
def check_in_appointment(
    appointment_id: UUID,
    payload: AppointmentCheckInRequest,
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(get_tenant_context),
) -> AppointmentResponse:
    ensure_search_path(db, ctx.tenant.schema_name)
    appointment = _reload_appointment_with_relations(db, appointment_id, ctx.tenant.schema_name)

    roles = _get_roles(db, ctx)
    _require_receptionist_or_admin(roles)
    _block_if_terminal(appointment.status)

    if appointment.status != AppointmentStatus.SCHEDULED:
        raise HTTPException(status_code=400, detail="Only SCHEDULED appointments can be checked in.")

    settings = get_settings()
    now = _utcnow()
    scheduled = _as_utc(appointment.scheduled_at)

    # check-in window: from grace minutes before scheduled to end-of-day (UTC day)
    window_start = scheduled - timedelta(minutes=settings.opd_checkin_grace_minutes)
    end_of_day = datetime.combine(scheduled.date() + timedelta(days=1), datetime.min.time()).replace(
        tzinfo=timezone.utc
    )

    if now < window_start:
        raise HTTPException(
            status_code=400,
            detail=f"Cannot check in more than {settings.opd_checkin_grace_minutes} minutes before scheduled time.",
        )
    if now > end_of_day:
        raise HTTPException(status_code=400, detail="Cannot check in after end of scheduled day.")

    # 1) Commit check-in
    try:
        appointment.status = AppointmentStatus.CHECKED_IN
        appointment.checked_in_at = now
        if appointment.patient:
            appointment.patient.last_visited_at = now
        db.commit()
        ensure_search_path(db, ctx.tenant.schema_name)
    except SQLAlchemyError:
        db.rollback()
        raise HTTPException(status_code=500, detail="Failed to check in appointment.")

    # 2) Reload with relations (prevents lazy-load/search_path issues)
    appointment = _reload_appointment_with_relations(db, appointment_id, ctx.tenant.schema_name)

    # 3) Notifications (best-effort)
    patient = appointment.patient
    if patient and patient.consent_sms and patient.phone_primary:
        try:
            scheduled_time = scheduled.strftime("%Y-%m-%d %H:%M")
            doctor_name = (
                f"{appointment.doctor.first_name if appointment.doctor else ''} {appointment.doctor.last_name if appointment.doctor else ''}".strip()
                or "Doctor"
            )
            msg = f"Checked in at {ctx.tenant.name} for appointment on {scheduled_time} with Dr. {doctor_name}."
            send_notification_sms(
                db=db,
                phone=patient.phone_primary,
                message=msg,
                triggered_by=ctx.user,
                reason="appointment_checked_in",
                check_patient_flag=True,
            )
        except Exception as e:
            logger.exception("Non-fatal: check-in SMS notification failed. apt=%s", appointment_id)

    # 4) Return response
    return AppointmentResponse(**_build_appointment_response(appointment, db, ctx.tenant.schema_name))


@router.patch("/{appointment_id}/start-consultation", response_model=AppointmentResponse)
def start_consultation(
    appointment_id: UUID,
    payload: AppointmentStartConsultationRequest,
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(get_tenant_context),
) -> AppointmentResponse:
    ensure_search_path(db, ctx.tenant.schema_name)
    appointment = _reload_appointment_with_relations(db, appointment_id, ctx.tenant.schema_name)

    roles = _get_roles(db, ctx)
    _require_assigned_doctor_or_admin(roles, appointment, ctx)
    _block_if_terminal(appointment.status)

    if appointment.status not in (AppointmentStatus.SCHEDULED, AppointmentStatus.CHECKED_IN):
        raise HTTPException(
            status_code=400, detail=f"Cannot start consultation from status {appointment.status.value}."
        )

    # Keep your current rule: only today's appointments.
    now = _utcnow()
    scheduled = _as_utc(appointment.scheduled_at)
    if scheduled.date() != now.date():
        raise HTTPException(status_code=400, detail="Can only start consultation for appointments scheduled today.")

    # 1) Commit status change
    try:
        appointment.status = AppointmentStatus.IN_CONSULTATION
        appointment.consultation_started_at = now
        if not appointment.checked_in_at:
            appointment.checked_in_at = now
        db.commit()
        ensure_search_path(db, ctx.tenant.schema_name)
    except SQLAlchemyError:
        db.rollback()
        raise HTTPException(status_code=500, detail="Failed to start consultation.")

    # 2) Reload with relations (prevents lazy-load/search_path issues)
    appointment = _reload_appointment_with_relations(db, appointment_id, ctx.tenant.schema_name)

    # 3) Notifications (best-effort)
    patient = appointment.patient
    if patient and patient.consent_sms and patient.phone_primary:
        try:
            doctor_name = (
                f"{appointment.doctor.first_name if appointment.doctor else ''} {appointment.doctor.last_name if appointment.doctor else ''}".strip()
                or "Doctor"
            )
            msg = f"Consultation started at {ctx.tenant.name} with Dr. {doctor_name}."
            send_notification_sms(
                db=db,
                phone=patient.phone_primary,
                message=msg,
                triggered_by=ctx.user,
                reason="consultation_started",
                check_patient_flag=True,
            )
        except Exception as e:
            logger.exception("Non-fatal: consultation start SMS notification failed. apt=%s", appointment_id)

    # 4) Return response
    return AppointmentResponse(**_build_appointment_response(appointment, db, ctx.tenant.schema_name))


@router.patch("/{appointment_id}/complete", response_model=AppointmentResponse)
def complete_appointment(
    appointment_id: UUID,
    payload: AppointmentCompleteRequest,
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(get_tenant_context),
) -> AppointmentResponse:
    ensure_search_path(db, ctx.tenant.schema_name)
    appointment = _reload_appointment_with_relations(db, appointment_id, ctx.tenant.schema_name)

    roles = _get_roles(db, ctx)
    _require_assigned_doctor_or_admin(roles, appointment, ctx)
    _block_if_terminal(appointment.status)

    if appointment.status not in (AppointmentStatus.CHECKED_IN, AppointmentStatus.IN_CONSULTATION):
        raise HTTPException(
            status_code=400, detail=f"Cannot complete appointment from status {appointment.status.value}."
        )

    # Block completion if any DRAFT Rx exists
    _block_if_any_draft_rx(db, appointment_id)

    # 1) Commit completion
    try:
        now = _utcnow()
        appointment.status = AppointmentStatus.COMPLETED
        appointment.completed_at = now

        visit_time = appointment.completed_at or appointment.checked_in_at or now
        if appointment.patient:
            appointment.patient.last_visited_at = visit_time

        # store closure note if provided (kept)
        if getattr(payload, "closure_note", None):
            appointment.notes = (appointment.notes or "") + f"\n[Closed without Rx: {payload.closure_note}]"

        db.commit()
        ensure_search_path(db, ctx.tenant.schema_name)
    except SQLAlchemyError:
        db.rollback()
        raise HTTPException(status_code=500, detail="Failed to complete appointment.")

    # 2) Reload with relations (prevents lazy-load/search_path issues)
    appointment = _reload_appointment_with_relations(db, appointment_id, ctx.tenant.schema_name)

    # 3) Notifications (best-effort)
    patient = appointment.patient
    if patient and patient.consent_sms and patient.phone_primary:
        try:
            msg = (
                f"Appointment completed at {ctx.tenant.name} on {appointment.scheduled_at.strftime('%Y-%m-%d %H:%M')}."
            )
            if getattr(payload, "with_rx", False):
                msg += " Prescription issued. Please collect from pharmacy."
            send_notification_sms(
                db=db,
                phone=patient.phone_primary,
                message=msg,
                triggered_by=ctx.user,
                reason="appointment_completed",
                check_patient_flag=True,
            )
        except Exception as e:
            logger.exception("Non-fatal: completion SMS notification failed. apt=%s", appointment_id)

    if patient and patient.consent_email and patient.email:
        try:
            from app.utils.email_templates import render_email_template

            body_html = f"""
            <p>Dear {patient.first_name} {patient.last_name or ""},</p>
            <p>Your appointment at <strong>{ctx.tenant.name}</strong> has been completed.</p>
            <ul>
                <li><strong>Date & Time:</strong> {appointment.scheduled_at.strftime("%Y-%m-%d %H:%M")}</li>
                <li><strong>Doctor:</strong> Dr. {appointment.doctor.first_name if appointment.doctor else ""} {appointment.doctor.last_name if appointment.doctor else ""}</li>
            </ul>
            """
            if getattr(payload, "with_rx", False):
                body_html += "<p>Your prescription has been issued. Please collect it from the pharmacy.</p>"
            html = render_email_template(
                title="Appointment Completed",
                body_html=body_html,
                hospital_name=ctx.tenant.name,
            )
            send_notification_email(
                db=db,
                to_email=patient.email,
                subject=f"Appointment Completed - {ctx.tenant.name}",
                body=html,
                triggered_by=ctx.user,
                reason="appointment_completed",
                tenant_schema_name=ctx.tenant.schema_name,
                html=True,
                check_patient_flag=True,
            )
        except Exception as e:
            logger.exception("Non-fatal: completion email notification failed. apt=%s", appointment_id)

    # 4) Return response
    return AppointmentResponse(**_build_appointment_response(appointment, db, ctx.tenant.schema_name))


@router.patch("/{appointment_id}/cancel", response_model=AppointmentResponse)
def cancel_appointment(
    appointment_id: UUID,
    payload: AppointmentCancelRequest,
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(get_tenant_context),
) -> AppointmentResponse:
    ensure_search_path(db, ctx.tenant.schema_name)
    appointment = _reload_appointment_with_relations(db, appointment_id, ctx.tenant.schema_name)

    roles = _get_roles(db, ctx)
    _require_receptionist_or_admin(roles)
    _block_if_terminal(appointment.status)

    if appointment.status not in (AppointmentStatus.SCHEDULED, AppointmentStatus.CHECKED_IN):
        raise HTTPException(
            status_code=400,
            detail=f"Cannot cancel appointment from status {appointment.status.value}.",
        )

    valid_reasons = {"PATIENT_REQUEST", "ADMITTED_TO_IPD", "DOCTOR_UNAVAILABLE", "OTHER"}
    if payload.reason not in valid_reasons:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid cancellation reason. Must be one of: {', '.join(sorted(valid_reasons))}",
        )

    # 1) Commit cancel
    try:
        appointment.status = AppointmentStatus.CANCELLED
        appointment.cancelled_reason = payload.reason
        appointment.cancelled_note = payload.note
        db.commit()
        ensure_search_path(db, ctx.tenant.schema_name)
    except SQLAlchemyError:
        db.rollback()
        raise HTTPException(status_code=500, detail="Failed to cancel appointment.")

    # 2) Reload with relations (prevents lazy-load/search_path issues)
    appointment = _reload_appointment_with_relations(db, appointment_id, ctx.tenant.schema_name)

    # 3) Notify (best-effort)
    patient = appointment.patient
    if patient and patient.consent_email and patient.email:
        try:
            body_html = f"""
            <p>Dear {patient.first_name} {patient.last_name or ""},</p>
            <p>Your appointment at <strong>{ctx.tenant.name}</strong> has been cancelled.</p>
            <ul>
                <li><strong>Date & Time:</strong> {appointment.scheduled_at.strftime("%Y-%m-%d %H:%M")}</li>
                <li><strong>Reason:</strong> {payload.reason.replace("_", " ").title()}</li>
            </ul>
            """
            if payload.note:
                body_html += f"<p><strong>Note:</strong> {payload.note}</p>"

            html = render_email_template(
                title="Appointment Cancelled",
                body_html=body_html,
                hospital_name=ctx.tenant.name,
            )

            send_notification_email(
                db=db,
                to_email=patient.email,
                subject=f"Appointment Cancelled - {ctx.tenant.name}",
                body=html,
                triggered_by=ctx.user,
                reason="appointment_cancelled",
                tenant_schema_name=ctx.tenant.schema_name,
                html=True,
                check_patient_flag=True,
            )
        except Exception as e:
            logger.exception("Non-fatal: cancel email notification failed. apt=%s", appointment_id)

    if patient and patient.consent_sms and patient.phone_primary:
        try:
            msg = f"Your appointment at {ctx.tenant.name} scheduled for {appointment.scheduled_at.strftime('%Y-%m-%d %H:%M')} has been cancelled. Reason: {payload.reason.replace('_', ' ').title()}."
            send_notification_sms(
                db=db,
                phone=patient.phone_primary,
                message=msg,
                triggered_by=ctx.user,
                reason="appointment_cancelled",
                check_patient_flag=True,
            )
        except Exception as e:
            logger.exception("Non-fatal: cancel SMS notification failed. apt=%s", appointment_id)

    # 4) Build + return response
    return AppointmentResponse(**_build_appointment_response(appointment, db, ctx.tenant.schema_name))


@router.patch("/{appointment_id}/no-show", response_model=AppointmentResponse)
def mark_no_show(
    appointment_id: UUID,
    payload: AppointmentNoShowRequest,
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(get_tenant_context),
) -> AppointmentResponse:
    ensure_search_path(db, ctx.tenant.schema_name)
    appointment = _reload_appointment_with_relations(db, appointment_id, ctx.tenant.schema_name)

    roles = _get_roles(db, ctx)
    _require_receptionist_or_admin(roles)
    _block_if_terminal(appointment.status)

    if appointment.status != AppointmentStatus.SCHEDULED:
        raise HTTPException(status_code=400, detail="Only SCHEDULED appointments can be marked as no-show.")

    now = _utcnow()
    scheduled = _as_utc(appointment.scheduled_at)
    if scheduled > now:
        raise HTTPException(status_code=400, detail="Cannot mark no-show before scheduled time.")

    if appointment.checked_in_at:
        raise HTTPException(status_code=400, detail="Cannot mark no-show because patient is already checked in.")

    # 1) Commit no-show status
    try:
        appointment.status = AppointmentStatus.NO_SHOW
        appointment.no_show_at = now
        db.commit()
        ensure_search_path(db, ctx.tenant.schema_name)
    except SQLAlchemyError:
        db.rollback()
        raise HTTPException(status_code=500, detail="Failed to mark appointment as no-show.")

    # 2) Reload with relations (prevents lazy-load/search_path issues)
    appointment = _reload_appointment_with_relations(db, appointment_id, ctx.tenant.schema_name)

    # 3) Notifications (best-effort)
    patient = appointment.patient
    if patient and patient.consent_email and patient.email:
        try:
            from app.utils.email_templates import render_email_template

            body_html = f"""
            <p>Dear {patient.first_name} {patient.last_name or ""},</p>
            <p>Your appointment at <strong>{ctx.tenant.name}</strong> scheduled for {appointment.scheduled_at.strftime("%Y-%m-%d %H:%M")} was marked as no-show.</p>
            <p>Please contact us to reschedule if needed.</p>
            """
            html = render_email_template(
                title="Appointment No-Show",
                body_html=body_html,
                hospital_name=ctx.tenant.name,
            )
            send_notification_email(
                db=db,
                to_email=patient.email,
                subject=f"Appointment No-Show - {ctx.tenant.name}",
                body=html,
                triggered_by=ctx.user,
                reason="appointment_no_show",
                tenant_schema_name=ctx.tenant.schema_name,
                html=True,
                check_patient_flag=True,
            )
        except Exception as e:
            logger.exception("Non-fatal: no-show email notification failed. apt=%s", appointment_id)

    # 4) Return response
    return AppointmentResponse(**_build_appointment_response(appointment, db, ctx.tenant.schema_name))


@router.patch("/{appointment_id}/reschedule", response_model=AppointmentResponse)
def reschedule_appointment(
    appointment_id: UUID,
    payload: AppointmentRescheduleRequest,
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(get_tenant_context),
) -> AppointmentResponse:
    """
    Reschedule:
    - Allowed for SCHEDULED or CHECKED_IN (NOT for IN_CONSULTATION, COMPLETED, CANCELLED, NO_SHOW)
    - If CHECKED_IN, it resets queue: status -> SCHEDULED and clears check-in/consultation/completion/no-show timestamps.
    """

    ensure_tenant_tables_exist(db, ctx.tenant.schema_name)
    ensure_search_path(db, ctx.tenant.schema_name)

    appointment = _reload_appointment_with_relations(db, appointment_id, ctx.tenant.schema_name)

    roles = _get_roles(db, ctx)
    _require_receptionist_or_admin(roles)

    _block_if_terminal(appointment.status)
    if appointment.status not in (AppointmentStatus.SCHEDULED, AppointmentStatus.CHECKED_IN):
        raise HTTPException(
            status_code=400, detail=f"Cannot reschedule appointment from status {appointment.status.value}."
        )

    new_scheduled = _as_utc(payload.scheduled_at)
    now = _utcnow()
    if new_scheduled < now:
        raise HTTPException(status_code=400, detail="New appointment date cannot be in the past.")

    # Validate 15-minute interval (00, 15, 30, 45)
    if not is_valid_15_minute_interval(new_scheduled):
        raise HTTPException(
            status_code=422, detail="Please select a time in 15-minute steps (e.g., 08:00, 08:15, 08:30, 08:45)."
        )

    # 1) Commit reschedule
    try:
        appointment.scheduled_at = new_scheduled

        # If checked-in, reschedule resets queue state
        if appointment.status == AppointmentStatus.CHECKED_IN:
            appointment.status = AppointmentStatus.SCHEDULED
            appointment.checked_in_at = None
            appointment.consultation_started_at = None
            appointment.completed_at = None
            appointment.no_show_at = None

        db.commit()
        ensure_search_path(db, ctx.tenant.schema_name)
    except SQLAlchemyError:
        db.rollback()
        raise HTTPException(status_code=500, detail="Failed to reschedule appointment.")

    # 2) Reload with relations (prevents lazy-load/search_path issues)
    appointment = _reload_appointment_with_relations(db, appointment_id, ctx.tenant.schema_name)

    # 3) Notifications (best-effort)
    patient = appointment.patient
    if patient and patient.consent_email and patient.email:
        try:
            from app.models.department import Department
            from app.utils.email_templates import render_email_template

            dept_name = "N/A"
            if appointment.department_id:
                dept = db.query(Department).filter(Department.id == appointment.department_id).first()
                dept_name = dept.name if dept else "N/A"

            body_html = f"""
            <p>Dear {patient.first_name} {patient.last_name or ""},</p>
            <p>Your appointment at <strong>{ctx.tenant.name}</strong> has been rescheduled.</p>
            <ul>
                <li><strong>Date & Time:</strong> {new_scheduled.strftime("%Y-%m-%d %H:%M")}</li>
                <li><strong>Doctor:</strong> Dr. {appointment.doctor.first_name if appointment.doctor else ""} {appointment.doctor.last_name if appointment.doctor else ""}</li>
                <li><strong>Department:</strong> {dept_name}</li>
            </ul>
            """
            html = render_email_template(
                title="Appointment Rescheduled",
                body_html=body_html,
                hospital_name=ctx.tenant.name,
            )
            send_notification_email(
                db=db,
                to_email=patient.email,
                subject=f"Appointment Rescheduled - {ctx.tenant.name}",
                body=html,
                triggered_by=ctx.user,
                reason="appointment_rescheduled",
                tenant_schema_name=ctx.tenant.schema_name,
                html=True,
                check_patient_flag=True,
            )
        except Exception as e:
            logger.exception("Non-fatal: reschedule email notification failed. apt=%s", appointment_id)

    if patient and patient.consent_sms and patient.phone_primary:
        try:
            doctor_name = (
                f"{appointment.doctor.first_name if appointment.doctor else ''} {appointment.doctor.last_name if appointment.doctor else ''}".strip()
                or "Doctor"
            )
            msg = f"Your appointment at {ctx.tenant.name} has been rescheduled to {new_scheduled.strftime('%Y-%m-%d %H:%M')} with Dr. {doctor_name}. Please arrive 10 mins early."
            send_notification_sms(
                db=db,
                phone=patient.phone_primary,
                message=msg,
                triggered_by=ctx.user,
                reason="appointment_rescheduled",
                check_patient_flag=True,
            )
        except Exception as e:
            logger.exception("Non-fatal: reschedule SMS notification failed. apt=%s", appointment_id)

    # 4) Return response
    return AppointmentResponse(**_build_appointment_response(appointment, db, ctx.tenant.schema_name))
