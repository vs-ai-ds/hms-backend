# app/api/v1/endpoints/patients.py
from __future__ import annotations

import logging
import os
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Optional
from uuid import UUID

import sqlalchemy as sa
from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from fastapi.responses import FileResponse
from sqlalchemy import func, or_, text
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.tenant_context import TenantContext, get_tenant_context
from app.core.tenant_db import ensure_search_path
from app.models.patient import Patient, PatientType
from app.schemas.patient import (
    DuplicateCheckResponse,
    PatientCreate,
    PatientResponse,
    PatientUpdate,
    ProfileCompleteRequest,
    QuickRegisterRequest,
)
from app.services.patient_service import (
    check_duplicates,
    create_patient_quick_register,
)
from app.services.patient_service import (
    update_patient_profile as update_patient_profile_service,
)
from app.services.user_role_service import get_user_role_names
from app.utils.file_storage import resolve_storage_path, save_bytes_to_storage

logger = logging.getLogger(__name__)
router = APIRouter()


def _parse_include(include: Optional[str]) -> set[str]:
    if not include:
        return set()
    return {part.strip().lower() for part in include.split(",") if part.strip()}


def _batch_visit_flags_for_page(
    db: Session,
    patient_ids: list[UUID],
) -> tuple[set[UUID], dict[UUID, datetime]]:
    """
    Returns:
      - active_admission_patient_ids: set of patient_ids with ACTIVE admission
      - next_eligible_opd_by_patient_id: {patient_id: min(scheduled_at)} for eligible OPD appointments
    """
    if not patient_ids:
        return set(), {}

    from app.models.admission import Admission, AdmissionStatus
    from app.models.appointment import Appointment, AppointmentStatus

    # ACTIVE admissions for these patients (single query)
    active_rows = (
        db.query(Admission.patient_id)
        .filter(
            Admission.patient_id.in_(patient_ids),
            Admission.status == AdmissionStatus.ACTIVE,
        )
        .distinct()
        .all()
    )
    active_patient_ids = {row[0] for row in active_rows}

    # Eligible OPD definition aligned with your current UI logic:
    # status in (SCHEDULED, CHECKED_IN, IN_CONSULTATION) and scheduled_at >= start of today (UTC)
    today_start = datetime.now(timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0
    )

    eligible_statuses = (
        AppointmentStatus.SCHEDULED,
        AppointmentStatus.CHECKED_IN,
        AppointmentStatus.IN_CONSULTATION,
    )

    # Next eligible OPD per patient (single grouped query)
    # Note: we compute it regardless; caller can null it out for active IPD.
    rows = (
        db.query(Appointment.patient_id, func.min(Appointment.scheduled_at))
        .filter(
            Appointment.patient_id.in_(patient_ids),
            Appointment.status.in_(eligible_statuses),
            Appointment.scheduled_at >= today_start,
        )
        .group_by(Appointment.patient_id)
        .all()
    )
    next_opd = {pid: min_dt for (pid, min_dt) in rows if min_dt is not None}

    return active_patient_ids, next_opd


@router.post(
    "/quick-register",
    response_model=PatientResponse,
    status_code=status.HTTP_201_CREATED,
)
def quick_register_patient(
    payload: QuickRegisterRequest,
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(get_tenant_context),
) -> PatientResponse:
    """
    Quick register a patient with minimal required fields.
    """
    ensure_search_path(db, ctx.tenant.schema_name)

    from app.models.tenant_global import TenantStatus

    if ctx.tenant.status == TenantStatus.SUSPENDED:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Cannot create patients. Hospital account is suspended. Please contact support.",
        )

    if ctx.tenant.max_patients is not None:
        current_patient_count = db.query(func.count(Patient.id)).scalar() or 0
        if current_patient_count >= ctx.tenant.max_patients:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    f"Cannot create patient. Maximum patient limit ({ctx.tenant.max_patients}) has been reached. "
                    "Please contact Platform Administrator to increase the limit."
                ),
            )

    try:
        patient, _duplicate_response = create_patient_quick_register(
            db=db,
            payload=payload,
            created_by_id=ctx.user.id,
        )

        # Re-query for a clean instance after service commit/flush
        ensure_search_path(db, ctx.tenant.schema_name)
        patient = db.query(Patient).filter(Patient.id == patient.id).first()
        if not patient:
            raise HTTPException(
                status_code=500,
                detail="Patient created but failed to retrieve. Please refresh the page.",
            )

        # Increment platform metrics (public schema)
        from app.services.tenant_metrics_service import increment_patients

        conn = db.connection()
        original_path = conn.execute(text("SHOW search_path")).scalar()
        try:
            conn.execute(text("SET search_path TO public"))
            increment_patients(db)
        finally:
            conn = db.connection()
            conn.execute(text(f"SET search_path TO {original_path}"))

        # patient_type computed without per-patient N+1:
        # At creation time, patient has no active admission => OPD
        patient_dict = PatientResponse.model_validate(patient).model_dump()
        patient_dict["patient_type"] = PatientType.OPD
        # optional flags may exist in schema; keep them null by default here
        return PatientResponse(**patient_dict)

    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        import logging

        logger = logging.getLogger(__name__)
        logger.error("Error creating patient: %s", str(e), exc_info=True)
        raise HTTPException(
            status_code=500, detail=f"Failed to create patient: {str(e)}"
        )


@router.post(
    "",
    response_model=PatientResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_patient(
    payload: PatientCreate,
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(get_tenant_context),
) -> PatientResponse:
    """
    Create a patient (use quick-register for new patients).
    """
    ensure_search_path(db, ctx.tenant.schema_name)

    from app.models.tenant_global import TenantStatus

    if ctx.tenant.status == TenantStatus.SUSPENDED:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Cannot create patients. Hospital account is suspended. Please contact support.",
        )

    if ctx.tenant.max_patients is not None:
        current_patient_count = db.query(func.count(Patient.id)).scalar() or 0
        if current_patient_count >= ctx.tenant.max_patients:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    f"Cannot create patient. Maximum patient limit ({ctx.tenant.max_patients}) has been reached. "
                    "Please contact Platform Administrator to increase the limit."
                ),
            )

    patient = Patient(
        first_name=payload.first_name,
        last_name=payload.last_name,
        middle_name=payload.middle_name,
        dob=payload.dob,
        dob_unknown=getattr(payload, "dob_unknown", False),
        age_only=getattr(payload, "age_only", None),
        gender=payload.gender,
        phone_primary=payload.phone_primary,
        phone_alternate=payload.phone_alternate,
        email=payload.email,
        city=payload.city,
        address_line1=payload.address_line1,
        address_line2=payload.address_line2,
        postal_code=payload.postal_code,
        state=payload.state,
        country=payload.country,
        blood_group=payload.blood_group,
        marital_status=payload.marital_status,
        preferred_language=payload.preferred_language,
        emergency_contact_name=payload.emergency_contact_name,
        emergency_contact_relation=payload.emergency_contact_relation,
        emergency_contact_phone=payload.emergency_contact_phone,
        known_allergies=payload.known_allergies,
        chronic_conditions=payload.chronic_conditions,
        clinical_notes=payload.clinical_notes,
        is_dnr=getattr(payload, "is_dnr", False),
        is_deceased=getattr(payload, "is_deceased", False),
        date_of_death=payload.date_of_death,
        national_id_type=payload.national_id_type,
        national_id_number=payload.national_id_number,
        photo_path=payload.photo_path,
        consent_sms=payload.consent_sms if payload.consent_sms is not None else True,
        consent_email=payload.consent_email
        if payload.consent_email is not None
        else True,
        created_by_id=ctx.user.id,
        updated_by_id=ctx.user.id,
    )

    db.add(patient)
    db.flush()
    patient_id = patient.id
    db.commit()

    ensure_search_path(db, ctx.tenant.schema_name)
    patient = db.query(Patient).filter(Patient.id == patient_id).first()
    if not patient:
        raise HTTPException(
            status_code=500, detail="Failed to retrieve created patient"
        )

    # Increment platform metrics (public schema)
    from app.services.tenant_metrics_service import increment_patients

    conn = db.connection()
    original_path = conn.execute(text("SHOW search_path")).scalar()
    try:
        conn.execute(text("SET search_path TO public"))
        increment_patients(db)
    finally:
        conn = db.connection()
        conn.execute(text(f"SET search_path TO {original_path}"))

    patient_dict = PatientResponse.model_validate(patient).model_dump()
    patient_dict["patient_type"] = PatientType.OPD
    return PatientResponse(**patient_dict)


@router.get("", response_model=dict)
def list_patients(
    search: Optional[str] = Query(
        None, description="Search by name, phone, patient_code, or national_id"
    ),
    department_id: Optional[UUID] = Query(None, description="Filter by department"),
    doctor_user_id: Optional[UUID] = Query(
        None, description="Filter by doctor (via appointments)"
    ),
    patient_type: Optional[str] = Query(
        None, description="Filter by patient type (OPD/IPD)"
    ),
    visit_type: Optional[str] = Query(
        None,
        description="Filter by visit type - patients with OPD appointments or IPD admissions",
    ),
    date_from: Optional[date] = Query(
        None, description="Filter by last visit date from"
    ),
    date_to: Optional[date] = Query(None, description="Filter by last visit date to"),
    registered_from: Optional[date] = Query(
        None, description="Filter by registration date from"
    ),
    registered_to: Optional[date] = Query(
        None, description="Filter by registration date to"
    ),
    gender: Optional[str] = Query(
        None, description="Filter by gender (MALE, FEMALE, OTHER, UNKNOWN)"
    ),
    include: Optional[str] = Query(
        None, description="Comma-separated includes. Supports: visit_flags"
    ),
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(20, ge=1, le=500, description="Items per page"),
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(get_tenant_context),
) -> dict:
    """
    List patients for the current tenant with search and filters.

    ABAC rule:
    - Doctor: only patients linked via appointments/admissions or created by them.
    - Nurse with department: only patients linked via appointments/admissions in their department.
    - Receptionist/Admin: can see all patients.
    """
    ensure_search_path(db, ctx.tenant.schema_name)
    includes = _parse_include(include)

    query = db.query(Patient)

    # ABAC filters
    user_roles = get_user_role_names(
        db, ctx.user, tenant_schema_name=ctx.tenant.schema_name
    )
    user_department = ctx.user.department
    is_hospital_admin = "HOSPITAL_ADMIN" in user_roles or "SUPER_ADMIN" in user_roles
    is_receptionist = "RECEPTIONIST" in user_roles
    is_doctor = "DOCTOR" in user_roles
    is_nurse = "NURSE" in user_roles

    if is_doctor and not is_hospital_admin and not is_receptionist:
        from app.models.admission import Admission
        from app.models.appointment import Appointment

        appointment_patient_ids = (
            db.query(Appointment.patient_id)
            .filter(Appointment.doctor_user_id == ctx.user.id)
            .distinct()
            .subquery()
        )
        admission_patient_ids = (
            db.query(Admission.patient_id)
            .filter(Admission.primary_doctor_user_id == ctx.user.id)
            .distinct()
            .subquery()
        )

        query = query.filter(
            sa.or_(
                Patient.id.in_(db.query(appointment_patient_ids.c.patient_id)),
                Patient.id.in_(db.query(admission_patient_ids.c.patient_id)),
                Patient.created_by_id == ctx.user.id,
            )
        )

    if (
        is_nurse
        and user_department
        and not is_hospital_admin
        and not is_receptionist
        and not is_doctor
    ):
        from app.models.admission import Admission
        from app.models.appointment import Appointment
        from app.models.department import Department

        dept = db.query(Department).filter(Department.name == user_department).first()
        if dept:
            appointment_patient_ids = (
                db.query(Appointment.patient_id)
                .filter(Appointment.department_id == dept.id)
                .distinct()
                .subquery()
            )
            admission_patient_ids = (
                db.query(Admission.patient_id)
                .filter(Admission.department_id == dept.id)
                .distinct()
                .subquery()
            )
            query = query.filter(
                sa.or_(
                    Patient.id.in_(db.query(appointment_patient_ids.c.patient_id)),
                    Patient.id.in_(db.query(admission_patient_ids.c.patient_id)),
                )
            )

    # Search
    if search:
        search_term = f"%{search.strip()}%"
        query = query.filter(
            or_(
                Patient.first_name.ilike(search_term),
                Patient.last_name.ilike(search_term),
                Patient.phone_primary.ilike(search_term),
                Patient.patient_code.ilike(search_term),
                Patient.national_id_number.ilike(search_term),
            )
        )

    # Filters
    if gender:
        query = query.filter(Patient.gender == gender)

    if registered_from:
        query = query.filter(func.date(Patient.created_at) >= registered_from)

    if registered_to:
        query = query.filter(func.date(Patient.created_at) <= registered_to)

    if department_id:
        from app.models.admission import Admission
        from app.models.appointment import Appointment

        appointment_patient_ids = (
            db.query(Appointment.patient_id)
            .filter(Appointment.department_id == department_id)
            .distinct()
            .subquery()
        )
        admission_patient_ids = (
            db.query(Admission.patient_id)
            .filter(Admission.department_id == department_id)
            .distinct()
            .subquery()
        )
        query = query.filter(
            sa.or_(
                Patient.id.in_(db.query(appointment_patient_ids.c.patient_id)),
                Patient.id.in_(db.query(admission_patient_ids.c.patient_id)),
            )
        )

    if doctor_user_id:
        from app.models.appointment import Appointment

        patient_ids_with_appointments = (
            db.query(Appointment.patient_id)
            .filter(Appointment.doctor_user_id == doctor_user_id)
            .distinct()
            .subquery()
        )
        query = query.filter(
            Patient.id.in_(db.query(patient_ids_with_appointments.c.patient_id))
        )

    if patient_type:
        from app.models.admission import Admission, AdmissionStatus

        pt = patient_type.upper()
        active_admission_patient_ids = (
            db.query(Admission.patient_id)
            .filter(Admission.status == AdmissionStatus.ACTIVE)
            .distinct()
            .subquery()
        )
        if pt == "IPD":
            query = query.filter(
                Patient.id.in_(db.query(active_admission_patient_ids.c.patient_id))
            )
        elif pt == "OPD":
            query = query.filter(
                ~Patient.id.in_(db.query(active_admission_patient_ids.c.patient_id))
            )

    if visit_type:
        from app.models.admission import Admission
        from app.models.appointment import Appointment, AppointmentStatus

        vt = visit_type.upper()

        if vt in ("OPD", "OPD_ELIGIBLE"):
            if vt == "OPD_ELIGIBLE":
                today_start = datetime.now(timezone.utc).replace(
                    hour=0, minute=0, second=0, microsecond=0
                )
                appointment_patient_ids = (
                    db.query(Appointment.patient_id)
                    .filter(
                        Appointment.status.in_(
                            [
                                AppointmentStatus.SCHEDULED,
                                AppointmentStatus.CHECKED_IN,
                                AppointmentStatus.IN_CONSULTATION,
                            ]
                        ),
                        Appointment.scheduled_at >= today_start,
                    )
                    .distinct()
                    .subquery()
                )
            else:
                appointment_patient_ids = (
                    db.query(Appointment.patient_id).distinct().subquery()
                )
            query = query.filter(
                Patient.id.in_(db.query(appointment_patient_ids.c.patient_id))
            )

        elif vt == "IPD":
            admission_patient_ids = db.query(Admission.patient_id).distinct().subquery()
            query = query.filter(
                Patient.id.in_(db.query(admission_patient_ids.c.patient_id))
            )

    if date_from or date_to:
        # last_visited_at must be present when filtering by last visit date
        query = query.filter(Patient.last_visited_at.isnot(None))
        if date_from:
            query = query.filter(func.date(Patient.last_visited_at) >= date_from)
        if date_to:
            query = query.filter(func.date(Patient.last_visited_at) <= date_to)

    # Order
    query = query.order_by(
        Patient.last_visited_at.desc().nullslast(),
        Patient.created_at.desc(),
    )

    # Total before pagination
    total_count = query.count()

    # Pagination
    offset = (page - 1) * page_size
    patients = query.offset(offset).limit(page_size).all()

    patient_ids = [p.id for p in patients]

    # Batch compute patient_type (+ optional visit flags) to avoid backend N+1
    active_patient_ids: set[UUID] = set()
    next_opd_by_patient_id: dict[UUID, datetime] = {}

    if patient_ids:
        # Always compute active admissions for patient_type (single query), regardless of include.
        # If include=visit_flags, also compute next eligible OPD (single query).
        if "visit_flags" in includes:
            active_patient_ids, next_opd_by_patient_id = _batch_visit_flags_for_page(
                db, patient_ids
            )
        else:
            # only active admission IDs needed for patient_type
            from app.models.admission import Admission, AdmissionStatus

            active_rows = (
                db.query(Admission.patient_id)
                .filter(
                    Admission.patient_id.in_(patient_ids),
                    Admission.status == AdmissionStatus.ACTIVE,
                )
                .distinct()
                .all()
            )
            active_patient_ids = {row[0] for row in active_rows}

    items: list[PatientResponse] = []
    for p in patients:
        patient_dict = PatientResponse.model_validate(p).model_dump()

        has_active_admission = p.id in active_patient_ids
        patient_dict["patient_type"] = (
            PatientType.IPD if has_active_admission else PatientType.OPD
        )

        if "visit_flags" in includes:
            patient_dict["has_active_admission"] = has_active_admission
            # UI rule: don’t show Next OPD when admitted
            patient_dict["next_eligible_opd_appointment_at"] = (
                None if has_active_admission else next_opd_by_patient_id.get(p.id)
            )

        items.append(PatientResponse(**patient_dict))

    return {
        "items": items,
        "total": total_count,
        "page": page,
        "page_size": page_size,
    }


@router.get("/check-duplicates", response_model=DuplicateCheckResponse)
def check_duplicate_patients(
    first_name: str = Query(..., min_length=1, description="First name"),
    last_name: Optional[str] = Query(None, description="Last name"),
    dob: Optional[date] = Query(None, description="Date of birth"),
    phone_primary: str = Query(..., min_length=1, description="Primary phone"),
    national_id_number: Optional[str] = Query(None, description="National ID number"),
    exclude_patient_id: Optional[UUID] = Query(
        None, description="Exclude this patient ID"
    ),
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(get_tenant_context),
) -> DuplicateCheckResponse:
    ensure_search_path(db, ctx.tenant.schema_name)

    normalized_phone = (
        phone_primary.replace(" ", "")
        .replace("-", "")
        .replace("(", "")
        .replace(")", "")
        .strip()
    )
    if not normalized_phone:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="phone_primary cannot be empty after normalization",
        )

    return check_duplicates(
        db=db,
        first_name=first_name.strip(),
        last_name=last_name.strip() if last_name else None,
        dob=dob,
        phone_primary=normalized_phone,
        national_id_number=national_id_number.strip() if national_id_number else None,
        exclude_patient_id=exclude_patient_id,
    )


@router.get("/{patient_id}", response_model=PatientResponse)
def get_patient(
    patient_id: UUID,
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(get_tenant_context),
) -> PatientResponse:
    ensure_search_path(db, ctx.tenant.schema_name)

    patient = db.query(Patient).filter(Patient.id == patient_id).first()
    if not patient:
        raise HTTPException(status_code=404, detail="Patient not found")

    user_roles = get_user_role_names(
        db, ctx.user, tenant_schema_name=ctx.tenant.schema_name
    )
    is_hospital_admin = "HOSPITAL_ADMIN" in user_roles or "SUPER_ADMIN" in user_roles
    is_doctor = "DOCTOR" in user_roles
    is_receptionist = "RECEPTIONIST" in user_roles

    if is_doctor and not is_hospital_admin and not is_receptionist:
        from app.models.admission import Admission
        from app.models.appointment import Appointment

        has_appointment = (
            db.query(Appointment)
            .filter(
                Appointment.patient_id == patient_id,
                Appointment.doctor_user_id == ctx.user.id,
            )
            .first()
        )

        has_admission = (
            db.query(Admission)
            .filter(
                Admission.patient_id == patient_id,
                Admission.primary_doctor_user_id == ctx.user.id,
            )
            .first()
        )

        was_created_by_doctor = patient.created_by_id == ctx.user.id

        if not has_appointment and not has_admission and not was_created_by_doctor:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    "Access to this patient is restricted. You can only view patients assigned to you "
                    "via appointments, admissions, or patients you created."
                ),
            )

    # Compute patient_type (single query)
    from app.models.admission import Admission, AdmissionStatus

    has_active = (
        db.query(Admission.id)
        .filter(
            Admission.patient_id == patient_id,
            Admission.status == AdmissionStatus.ACTIVE,
        )
        .first()
        is not None
    )

    patient_dict = PatientResponse.model_validate(patient).model_dump()
    patient_dict["patient_type"] = PatientType.IPD if has_active else PatientType.OPD
    return PatientResponse(**patient_dict)


@router.patch("/{patient_id}/profile", response_model=PatientResponse)
def update_patient_profile(
    patient_id: UUID,
    payload: ProfileCompleteRequest,
    change_reason: Optional[str] = Query(None, description="Reason for change"),
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(get_tenant_context),
) -> PatientResponse:
    ensure_search_path(db, ctx.tenant.schema_name)

    patient = db.query(Patient).filter(Patient.id == patient_id).first()
    if not patient:
        raise HTTPException(status_code=404, detail="Patient not found")

    try:
        updated_patient = update_patient_profile_service(
            db=db,
            patient_id=patient_id,
            payload=payload,
            updated_by_id=ctx.user.id,
            change_reason=change_reason,
            schema_name=ctx.tenant.schema_name,
        )
        ensure_search_path(db, ctx.tenant.schema_name)

        from app.models.admission import Admission, AdmissionStatus

        has_active = (
            db.query(Admission.id)
            .filter(
                Admission.patient_id == patient_id,
                Admission.status == AdmissionStatus.ACTIVE,
            )
            .first()
            is not None
        )

        patient_dict = PatientResponse.model_validate(updated_patient).model_dump()
        patient_dict["patient_type"] = (
            PatientType.IPD if has_active else PatientType.OPD
        )
        return PatientResponse(**patient_dict)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.patch("/{patient_id}", response_model=PatientResponse)
def update_patient(
    patient_id: UUID,
    payload: PatientUpdate,
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(get_tenant_context),
) -> PatientResponse:
    ensure_search_path(db, ctx.tenant.schema_name)

    patient = db.query(Patient).filter(Patient.id == patient_id).first()
    if not patient:
        raise HTTPException(status_code=404, detail="Patient not found")

    try:
        updated_patient = update_patient_profile_service(
            db=db,
            patient_id=patient_id,
            payload=payload,
            updated_by_id=ctx.user.id,
            schema_name=ctx.tenant.schema_name,
        )
        ensure_search_path(db, ctx.tenant.schema_name)

        from app.models.admission import Admission, AdmissionStatus

        has_active = (
            db.query(Admission.id)
            .filter(
                Admission.patient_id == patient_id,
                Admission.status == AdmissionStatus.ACTIVE,
            )
            .first()
            is not None
        )

        patient_dict = PatientResponse.model_validate(updated_patient).model_dump()
        patient_dict["patient_type"] = (
            PatientType.IPD if has_active else PatientType.OPD
        )
        return PatientResponse(**patient_dict)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post(
    "/{patient_id}/profile-picture",
    response_model=PatientResponse,
    status_code=status.HTTP_200_OK,
)
async def upload_patient_profile_picture(
    patient_id: UUID,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(get_tenant_context),
) -> PatientResponse:
    """
    Upload a profile picture for a patient.

    Supported formats: JPG, JPEG, PNG, WEBP, GIF.
    Maximum file size: 5 megabytes.
    """
    ensure_search_path(db, ctx.tenant.schema_name)

    patient = db.query(Patient).filter(Patient.id == patient_id).first()
    if not patient:
        raise HTTPException(status_code=404, detail="Patient not found")

    allowed_extensions = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
    ext = Path(file.filename or "").suffix.lower()
    if ext not in allowed_extensions:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"File type '{ext}' is not allowed. Supported formats: JPG, JPEG, PNG, WEBP, GIF",
        )

    max_file_size = 5 * 1024 * 1024  # 5MB
    file_bytes = await file.read()
    if len(file_bytes) > max_file_size:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"File size ({len(file_bytes) / (1024 * 1024):.2f}MB) exceeds the maximum of 5MB.",
        )

    if patient.photo_path:
        old_path = resolve_storage_path(patient.photo_path)
        if old_path.exists():
            try:
                os.remove(old_path)
            except Exception:
                pass

    subdir = f"{ctx.tenant.schema_name}/patients/{patient_id}/profile"
    storage_path = save_bytes_to_storage(
        data=file_bytes,
        original_filename=file.filename or "profile.jpg",
        subdir=subdir,
    )

    patient.photo_path = storage_path
    patient.updated_by_id = ctx.user.id
    patient.updated_at = datetime.utcnow()

    try:
        db.commit()
        ensure_search_path(db, ctx.tenant.schema_name)

        patient = db.query(Patient).filter(Patient.id == patient_id).first()
        if not patient:
            raise HTTPException(
                status_code=500, detail="Failed to retrieve updated patient"
            )

        from app.models.admission import Admission, AdmissionStatus

        has_active = (
            db.query(Admission.id)
            .filter(
                Admission.patient_id == patient_id,
                Admission.status == AdmissionStatus.ACTIVE,
            )
            .first()
            is not None
        )

        patient_dict = PatientResponse.model_validate(patient).model_dump()
        patient_dict["patient_type"] = (
            PatientType.IPD if has_active else PatientType.OPD
        )
        return PatientResponse(**patient_dict)
    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to update profile picture: {str(e)}",
        )


@router.get(
    "/{patient_id}/profile-picture",
    response_class=FileResponse,
)
def get_patient_profile_picture(
    patient_id: UUID,
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(get_tenant_context),
):
    ensure_search_path(db, ctx.tenant.schema_name)

    patient = db.query(Patient).filter(Patient.id == patient_id).first()
    if not patient:
        raise HTTPException(status_code=404, detail="Patient not found")

    if not patient.photo_path:
        raise HTTPException(status_code=404, detail="Profile picture not found")

    file_path = resolve_storage_path(patient.photo_path)
    if not file_path.exists():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Profile picture file not found on storage.",
        )

    ext = file_path.suffix.lower()
    media_types = {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".webp": "image/webp",
        ".gif": "image/gif",
    }
    media_type = media_types.get(ext, "image/jpeg")

    return FileResponse(
        path=str(file_path),
        media_type=media_type,
        filename=f"profile{ext}",
    )


@router.get("/{patient_id}/clinical-snapshot")
def get_patient_clinical_snapshot(
    patient_id: UUID,
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(get_tenant_context),
) -> dict:
    """
    Get a lightweight clinical snapshot for a patient.
    Used in prescription form to show allergies, chronic conditions, latest vitals, and encounter info.
    """
    ensure_search_path(db, ctx.tenant.schema_name)

    patient = db.query(Patient).filter(Patient.id == patient_id).first()
    if not patient:
        raise HTTPException(status_code=404, detail="Patient not found")

    from sqlalchemy.orm import joinedload

    from app.models.admission import Admission, AdmissionStatus
    from app.models.appointment import Appointment, AppointmentStatus
    from app.models.department import Department
    from app.models.vital import Vital

    latest_vital = (
        db.query(Vital)
        .filter(Vital.patient_id == patient_id)
        .order_by(Vital.recorded_at.desc())
        .first()
    )

    active_admission = (
        db.query(Admission)
        .options(joinedload(Admission.primary_doctor))
        .filter(
            Admission.patient_id == patient_id,
            Admission.status == AdmissionStatus.ACTIVE,
        )
        .first()
    )

    active_admission_dept_name = None
    if active_admission and active_admission.department_id:
        dept = (
            db.query(Department)
            .filter(Department.id == active_admission.department_id)
            .first()
        )
        active_admission_dept_name = dept.name if dept else None

    now = datetime.now(timezone.utc)
    next_appointment = (
        db.query(Appointment)
        .options(joinedload(Appointment.doctor))
        .filter(
            Appointment.patient_id == patient_id,
            Appointment.scheduled_at >= now,
            Appointment.status == AppointmentStatus.SCHEDULED,
        )
        .order_by(Appointment.scheduled_at.asc())
        .first()
    )

    next_appointment_dept_name = None
    if next_appointment and next_appointment.department_id:
        dept = (
            db.query(Department)
            .filter(Department.id == next_appointment.department_id)
            .first()
        )
        next_appointment_dept_name = dept.name if dept else None

    return {
        "patient_id": str(patient.id),
        "allergies": patient.known_allergies,
        "chronic_conditions": patient.chronic_conditions,
        "latest_vital": {
            "recorded_at": latest_vital.recorded_at.isoformat()
            if latest_vital
            else None,
            "systolic_bp": latest_vital.systolic_bp if latest_vital else None,
            "diastolic_bp": latest_vital.diastolic_bp if latest_vital else None,
            "heart_rate": latest_vital.heart_rate if latest_vital else None,
            "temperature_c": latest_vital.temperature_c if latest_vital else None,
            "weight_kg": latest_vital.weight_kg if latest_vital else None,
            "height_cm": latest_vital.height_cm if latest_vital else None,
        }
        if latest_vital
        else None,
        "active_admission": {
            "id": str(active_admission.id),
            "admit_datetime": active_admission.admit_datetime.isoformat(),
            "department": active_admission_dept_name,
            "primary_doctor_name": (
                f"{active_admission.primary_doctor.first_name} {active_admission.primary_doctor.last_name}".strip()
                if active_admission.primary_doctor
                else None
            ),
        }
        if active_admission
        else None,
        "next_appointment": {
            "id": str(next_appointment.id),
            "scheduled_at": next_appointment.scheduled_at.isoformat(),
            "department": next_appointment_dept_name,
            "doctor_name": (
                f"{next_appointment.doctor.first_name} {next_appointment.doctor.last_name}".strip()
                if next_appointment.doctor
                else None
            ),
        }
        if next_appointment
        else None,
    }
