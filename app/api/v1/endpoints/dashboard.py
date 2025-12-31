# app/api/v1/endpoints/dashboard.py
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.api.v1.endpoints.auth import get_current_user
from app.core.database import get_db
from app.core.redis import cache_get, cache_set, is_redis_available
from app.core.tenant_context import get_tenant_context
from app.models.admission import Admission, AdmissionStatus
from app.models.appointment import Appointment, AppointmentStatus
from app.models.patient import Patient
from app.models.prescription import Prescription, PrescriptionStatus
from app.models.user import User

router = APIRouter()
logger = logging.getLogger(__name__)


class DashboardMetrics(BaseModel):
    patients_today: int
    upcoming_appointments: int
    appointments_today: int
    prescriptions_today: int
    ipd_admissions_today: int = 0
    appointments_by_status: dict[str, int]
    patient_registrations_last_7_days: list[dict]
    prescriptions_by_status: dict[str, int]

    opd_scheduled_today: int = 0
    opd_checked_in_today: int = 0
    opd_in_consultation_today: int = 0
    opd_completed_today: int = 0

    active_ipd_admissions: int = 0
    pending_prescriptions_draft: int = 0
    pending_prescriptions_issued: int = 0

    my_appointments_today: int = 0
    my_pending_prescriptions: int = 0
    active_staff_users: int = 0
    prescriptions_to_dispense: int = 0
    low_stock_items_count: int = 0

    doctor_pending_consultations: int = 0
    receptionist_pending_checkins: int = 0
    nurse_pending_vitals: int = 0
    no_show_risk_count: int = 0
    incomplete_clinical_notes: int = 0

    patient_gender_distribution: dict[str, int] = {}
    patient_age_distribution: dict[str, int] = {}

    total_tenants: Optional[int] = None
    total_users: Optional[int] = None
    total_patients: Optional[int] = None
    total_appointments: Optional[int] = None
    total_prescriptions: Optional[int] = None


@router.get("/metrics", response_model=DashboardMetrics, tags=["dashboard"])
def get_dashboard_metrics(
    trends_date_range: Optional[str] = Query(
        "last_7_days",
        description="Date range for trends section: today, last_7_days, last_30_days, last_90_days",
    ),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> DashboardMetrics:
    """
    Dashboard metrics for tenant users. SUPER_ADMIN gets platform totals.
    Cached for 60s to reduce load.
    """
    # SUPER_ADMIN platform view
    if current_user.tenant_id is None:
        from app.models.tenant_metrics import TenantMetrics

        metrics_row = db.query(TenantMetrics).first()
        if not metrics_row:
            logger.error("tenant_metrics row not found. Run: python -m scripts.setup_platform")
            return DashboardMetrics(
                patients_today=0,
                upcoming_appointments=0,
                appointments_today=0,
                prescriptions_today=0,
                ipd_admissions_today=0,
                appointments_by_status={},
                patient_registrations_last_7_days=[],
                prescriptions_by_status={},
                patient_gender_distribution={},
                patient_age_distribution={},
                total_tenants=0,
                total_users=0,
                total_patients=0,
                total_appointments=0,
                total_prescriptions=0,
            )

        return DashboardMetrics(
            patients_today=0,
            upcoming_appointments=0,
            appointments_today=0,
            prescriptions_today=0,
            ipd_admissions_today=0,
            appointments_by_status={},
            patient_registrations_last_7_days=[],
            prescriptions_by_status={},
            patient_gender_distribution={},
            patient_age_distribution={},
            total_tenants=metrics_row.total_tenants or 0,
            total_users=metrics_row.total_users or 0,
            total_patients=metrics_row.total_patients or 0,
            total_appointments=metrics_row.total_appointments or 0,
            total_prescriptions=metrics_row.total_prescriptions or 0,
        )

    ctx = get_tenant_context(db, current_user)

    from app.models.stock import StockItem
    from app.services.user_role_service import get_user_role_names

    # Cache key includes trends_date_range to avoid returning wrong trend charts
    cache_key = f"dashboard:tenant:{ctx.tenant.id}:user:{ctx.user.id}:trends:{trends_date_range}"
    if is_redis_available():
        cached = cache_get(cache_key)
        if cached:
            try:
                return DashboardMetrics(**json.loads(cached))
            except Exception:
                logger.warning("Dashboard cache corrupted. Recomputing.", exc_info=True)

    now = datetime.now(timezone.utc)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    today_end = today_start + timedelta(days=1)

    # Trends range
    if trends_date_range == "today":
        trends_start_date = today_start
    elif trends_date_range == "last_30_days":
        trends_start_date = today_start - timedelta(days=30)
    elif trends_date_range == "last_90_days":
        trends_start_date = today_start - timedelta(days=90)
    else:
        trends_start_date = today_start - timedelta(days=7)

    role_names = get_user_role_names(db, ctx.user, tenant_schema_name=ctx.tenant.schema_name)
    is_doctor = "DOCTOR" in role_names
    is_pharmacist = "PHARMACIST" in role_names
    is_receptionist = "RECEPTIONIST" in role_names
    is_nurse = "NURSE" in role_names
    is_admin = "HOSPITAL_ADMIN" in role_names or "SUPER_ADMIN" in role_names

    # Optional nurse dept filter
    nurse_department_id = None
    if is_nurse and getattr(ctx.user, "department", None) and not (is_admin or is_receptionist or is_doctor):
        from app.models.department import Department

        dept = db.query(Department).filter(Department.name == ctx.user.department).first()
        if dept:
            nurse_department_id = dept.id

    patients_today = (
        db.query(func.count(Patient.id)).filter(func.date(Patient.created_at) == today_start.date()).scalar() or 0
    )

    appointments_today = (
        db.query(func.count(Appointment.id))
        .filter(
            Appointment.scheduled_at >= today_start,
            Appointment.scheduled_at < today_end,
            Appointment.status == AppointmentStatus.SCHEDULED,
        )
        .scalar()
        or 0
    )

    upcoming_appointments = (
        db.query(func.count(Appointment.id))
        .filter(
            Appointment.scheduled_at >= today_start,
            Appointment.status == AppointmentStatus.SCHEDULED,
        )
        .scalar()
        or 0
    )

    prescriptions_today = (
        db.query(func.count(Prescription.id)).filter(func.date(Prescription.created_at) == today_start.date()).scalar()
        or 0
    )

    ipd_admissions_today = (
        db.query(func.count(Admission.id))
        .filter(
            func.date(Admission.admit_datetime) == today_start.date(),
            Admission.status == AdmissionStatus.ACTIVE,
        )
        .scalar()
        or 0
    )

    active_ipd_query = db.query(func.count(Admission.id)).filter(Admission.status == AdmissionStatus.ACTIVE)
    if is_doctor and not is_admin:
        active_ipd_query = active_ipd_query.filter(Admission.primary_doctor_user_id == ctx.user.id)
    elif nurse_department_id is not None:
        active_ipd_query = active_ipd_query.filter(Admission.department_id == nurse_department_id)
    active_ipd_admissions = active_ipd_query.scalar() or 0

    # OPD breakdown (today)
    def _opd_count(status: AppointmentStatus) -> int:
        q = db.query(func.count(Appointment.id)).filter(
            Appointment.scheduled_at >= today_start,
            Appointment.scheduled_at < today_end,
            Appointment.status == status,
        )
        if is_doctor and not is_admin:
            q = q.filter(Appointment.doctor_user_id == ctx.user.id)
        elif nurse_department_id is not None:
            q = q.filter(Appointment.department_id == nurse_department_id)
        return q.scalar() or 0

    opd_scheduled_today = _opd_count(AppointmentStatus.SCHEDULED)
    opd_checked_in_today = _opd_count(AppointmentStatus.CHECKED_IN)
    opd_in_consultation_today = _opd_count(AppointmentStatus.IN_CONSULTATION)
    opd_completed_today = _opd_count(AppointmentStatus.COMPLETED)

    pending_prescriptions_draft = (
        db.query(func.count(Prescription.id)).filter(Prescription.status == PrescriptionStatus.DRAFT).scalar() or 0
    )
    pending_prescriptions_issued = (
        db.query(func.count(Prescription.id)).filter(Prescription.status == PrescriptionStatus.ISSUED).scalar() or 0
    )

    my_appointments_today = 0
    my_pending_prescriptions = 0
    if is_doctor:
        my_appointments_today = (
            db.query(func.count(Appointment.id))
            .filter(
                Appointment.doctor_user_id == ctx.user.id,
                Appointment.scheduled_at >= today_start,
                Appointment.scheduled_at < today_end,
                Appointment.status == AppointmentStatus.SCHEDULED,
            )
            .scalar()
            or 0
        )
        my_pending_prescriptions = (
            db.query(func.count(Prescription.id))
            .filter(
                Prescription.doctor_user_id == ctx.user.id,
                Prescription.status.in_([PrescriptionStatus.DRAFT, PrescriptionStatus.ISSUED]),
            )
            .scalar()
            or 0
        )

    active_staff_users = 0
    if is_admin:
        from app.models.user import UserStatus

        active_staff_users = (
            db.query(func.count(User.id))
            .filter(User.tenant_id == ctx.tenant.id, User.status == UserStatus.ACTIVE)
            .scalar()
            or 0
        )

    prescriptions_to_dispense = 0
    if is_pharmacist or is_admin:
        prescriptions_to_dispense = (
            db.query(func.count(Prescription.id)).filter(Prescription.status == PrescriptionStatus.ISSUED).scalar() or 0
        )

    low_stock_items_count = 0
    if is_pharmacist or is_admin:
        try:
            low_stock_items_count = (
                db.query(func.count(StockItem.id))
                .filter(
                    StockItem.is_active.is_(True),
                    StockItem.current_stock <= StockItem.reorder_level,
                    StockItem.reorder_level > 0,
                )
                .scalar()
                or 0
            )
        except Exception:
            low_stock_items_count = 0

    # Trends: prescriptions by status
    rx_by_status_rows = (
        db.query(Prescription.status, func.count(Prescription.id).label("count"))
        .filter(Prescription.created_at >= trends_start_date)
        .group_by(Prescription.status)
        .all()
    )
    prescriptions_by_status = {(s.value if hasattr(s, "value") else str(s)): c for s, c in rx_by_status_rows}

    # Trends: appointment outcomes only
    outcomes_q = (
        db.query(Appointment.status, func.count(Appointment.id).label("count"))
        .filter(
            Appointment.status.in_(
                [AppointmentStatus.COMPLETED, AppointmentStatus.CANCELLED, AppointmentStatus.NO_SHOW]
            )
        )
        .filter(Appointment.scheduled_at >= trends_start_date)
        .filter(Appointment.linked_ipd_admission_id.is_(None))  # Only OPD appointments (exclude IPD)
    )
    if is_doctor and not is_admin:
        outcomes_q = outcomes_q.filter(Appointment.doctor_user_id == ctx.user.id)
    elif nurse_department_id is not None:
        outcomes_q = outcomes_q.filter(Appointment.department_id == nurse_department_id)

    outcomes_rows = outcomes_q.group_by(Appointment.status).all()
    appointments_outcomes = {(s.value if hasattr(s, "value") else str(s)): c for s, c in outcomes_rows}
    
    # Add "Active" appointments count (SCHEDULED + CHECKED_IN + IN_CONSULTATION)
    # Only OPD appointments (exclude IPD) to match appointments page behavior
    active_q = (
        db.query(func.count(Appointment.id))
        .filter(
            Appointment.status.in_(
                [AppointmentStatus.SCHEDULED, AppointmentStatus.CHECKED_IN, AppointmentStatus.IN_CONSULTATION]
            )
        )
        .filter(Appointment.scheduled_at >= trends_start_date)
        .filter(Appointment.linked_ipd_admission_id.is_(None))  # Only OPD appointments (exclude IPD)
    )
    if is_doctor and not is_admin:
        active_q = active_q.filter(Appointment.doctor_user_id == ctx.user.id)
    elif nurse_department_id is not None:
        active_q = active_q.filter(Appointment.department_id == nurse_department_id)
    
    active_count = active_q.scalar() or 0
    if active_count > 0:
        appointments_outcomes["ACTIVE"] = active_count

    # Patient registrations trend
    patient_reg_q = (
        db.query(func.date(Patient.created_at).label("date"), func.count(Patient.id).label("count"))
        .filter(Patient.created_at >= trends_start_date)
        .group_by(func.date(Patient.created_at))
        .order_by(func.date(Patient.created_at))
        .all()
    )
    patient_registrations_trend = [{"date": str(d), "count": c} for d, c in patient_reg_q]

    # Pending actions
    doctor_pending_consultations = 0
    if is_doctor:
        doctor_pending_consultations = (
            db.query(func.count(Appointment.id))
            .filter(
                Appointment.doctor_user_id == ctx.user.id,
                Appointment.scheduled_at >= today_start,
                Appointment.scheduled_at < today_end,
                Appointment.status.in_([AppointmentStatus.CHECKED_IN, AppointmentStatus.IN_CONSULTATION]),
            )
            .scalar()
            or 0
        )

    receptionist_pending_checkins = 0
    if is_receptionist or is_admin:
        checkin_window_end = now + timedelta(hours=4)
        receptionist_pending_checkins = (
            db.query(func.count(Appointment.id))
            .filter(
                Appointment.scheduled_at >= now,
                Appointment.scheduled_at <= checkin_window_end,
                Appointment.status == AppointmentStatus.SCHEDULED,
            )
            .scalar()
            or 0
        )

    nurse_pending_vitals = 0
    if is_nurse or is_admin:
        try:
            from app.models.vital import Vital

            nurse_pending_vitals = (
                db.query(Appointment.id)
                .filter(
                    Appointment.scheduled_at >= today_start,
                    Appointment.scheduled_at < today_end,
                    Appointment.status.in_([AppointmentStatus.CHECKED_IN, AppointmentStatus.IN_CONSULTATION]),
                    ~Appointment.id.in_(
                        db.query(Vital.appointment_id)
                        .filter(func.date(Vital.recorded_at) == today_start.date())
                        .subquery()
                    ),
                )
                .count()
            )
        except Exception:
            nurse_pending_vitals = 0

    grace_threshold = now - timedelta(minutes=30)
    no_show_risk_count = (
        db.query(func.count(Appointment.id))
        .filter(
            Appointment.scheduled_at < grace_threshold,
            Appointment.status.in_([AppointmentStatus.SCHEDULED, AppointmentStatus.NO_SHOW]),
        )
        .scalar()
        or 0
    )

    incomplete_threshold = now - timedelta(minutes=60)
    incomplete_clinical_notes = (
        db.query(func.count(Appointment.id))
        .filter(
            Appointment.scheduled_at >= today_start,
            Appointment.scheduled_at < today_end,
            Appointment.status.in_([AppointmentStatus.CHECKED_IN, AppointmentStatus.IN_CONSULTATION]),
            Appointment.checked_in_at.isnot(None),
            Appointment.checked_in_at < incomplete_threshold,
            ~Appointment.id.in_(
                db.query(Prescription.appointment_id).filter(Prescription.appointment_id.isnot(None)).subquery()
            ),
        )
        .scalar()
        or 0
    )

    patient_gender_distribution = {}
    patient_age_distribution = {}
    if is_admin:
        gender_rows = (
            db.query(Patient.gender, func.count(Patient.id).label("count"))
            .filter(Patient.created_at >= trends_start_date)
            .group_by(Patient.gender)
            .all()
        )
        patient_gender_distribution = {(g or "UNKNOWN"): c for g, c in gender_rows}
        patient_age_distribution = {}

    metrics = DashboardMetrics(
        patients_today=patients_today,
        upcoming_appointments=upcoming_appointments,
        appointments_today=appointments_today,
        prescriptions_today=prescriptions_today,
        ipd_admissions_today=ipd_admissions_today,
        appointments_by_status=appointments_outcomes,
        patient_registrations_last_7_days=patient_registrations_trend,
        prescriptions_by_status=prescriptions_by_status,
        opd_scheduled_today=opd_scheduled_today,
        opd_checked_in_today=opd_checked_in_today,
        opd_in_consultation_today=opd_in_consultation_today,
        opd_completed_today=opd_completed_today,
        active_ipd_admissions=active_ipd_admissions,
        pending_prescriptions_draft=pending_prescriptions_draft,
        pending_prescriptions_issued=pending_prescriptions_issued,
        my_appointments_today=my_appointments_today,
        my_pending_prescriptions=my_pending_prescriptions,
        active_staff_users=active_staff_users,
        prescriptions_to_dispense=prescriptions_to_dispense,
        low_stock_items_count=low_stock_items_count,
        doctor_pending_consultations=doctor_pending_consultations,
        receptionist_pending_checkins=receptionist_pending_checkins,
        nurse_pending_vitals=nurse_pending_vitals,
        no_show_risk_count=no_show_risk_count,
        incomplete_clinical_notes=incomplete_clinical_notes,
        patient_gender_distribution=patient_gender_distribution,
        patient_age_distribution=patient_age_distribution,
    )

    if is_redis_available():
        try:
            cache_set(cache_key, metrics.model_dump_json(), ttl=60)
        except Exception:
            logger.warning("Failed to cache dashboard metrics.", exc_info=True)

    return metrics
