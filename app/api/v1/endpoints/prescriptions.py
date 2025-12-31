# app/api/v1/endpoints/prescriptions.py
from __future__ import annotations

import logging
from datetime import date as date_type
from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import case, func
from sqlalchemy import or_ as sa_or_
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, joinedload

from app.core.database import get_db
from app.core.tenant_context import TenantContext, get_tenant_context
from app.core.tenant_db import ensure_search_path
from app.models.admission import Admission, AdmissionStatus
from app.models.appointment import Appointment, AppointmentStatus
from app.models.department import Department
from app.models.prescription import Prescription, PrescriptionItem, PrescriptionStatus
from app.models.stock import StockItem
from app.models.user import RoleName, User
from app.schemas.prescription import (
    PrescriptionCreate,
    PrescriptionItemResponse,
    PrescriptionResponse,
    PrescriptionUpdate,
)
from app.services.notification_service import send_notification_email
from app.services.prescription_service import (
    PatientNotFoundError,
    PrescriptionNotFoundError,
    create_prescription,
    get_prescription,
)
from app.services.user_role_service import get_user_role_names
from app.utils.email_templates import render_email_template
from app.utils.prescription_pdf import generate_prescription_pdf

router = APIRouter()
logger = logging.getLogger(__name__)


def _ensure_doctor_or_admin(ctx: TenantContext, db: Session) -> None:
    role_names = get_user_role_names(db, ctx.user, tenant_schema_name=ctx.tenant.schema_name)
    if not (
        RoleName.DOCTOR.value in role_names
        or RoleName.HOSPITAL_ADMIN.value in role_names
        or RoleName.SUPER_ADMIN.value in role_names
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only doctors or admins can manage prescriptions.",
        )


def _reload_prescription_with_relations(
    db: Session,
    prescription_id: UUID,
    tenant_schema_name: str,
) -> Prescription:
    """
    Reload prescription with all relationships (patient, doctor, items).
    Does NOT set search_path - caller must ensure it's set.
    """
    prescription = (
        db.query(Prescription)
        .options(
            joinedload(Prescription.patient),
            joinedload(Prescription.doctor),
            joinedload(Prescription.items),
        )
        .filter(Prescription.id == prescription_id)
        .first()
    )
    if not prescription:
        raise HTTPException(status_code=404, detail="Prescription not found")
    return prescription


def _build_response_from_instance(prescription) -> PrescriptionResponse:
    items_resp = []
    for item in getattr(prescription, "items", None) or []:
        items_resp.append(
            PrescriptionItemResponse(
                id=item.id,
                stock_item_id=item.stock_item_id,
                medicine_name=item.medicine_name,
                dosage=item.dosage,
                frequency=item.frequency,
                duration=item.duration,
                instructions=item.instructions,
                quantity=item.quantity,
            )
        )

    patient_name = None
    doctor_name = None
    patient = getattr(prescription, "patient", None)
    doctor = getattr(prescription, "doctor", None)
    if patient:
        patient_name = f"{patient.first_name} {patient.last_name or ''}".strip()
    if doctor:
        doctor_name = f"{doctor.first_name} {doctor.last_name or ''}".strip()

    return PrescriptionResponse(
        id=prescription.id,
        prescription_code=prescription.prescription_code,
        patient_id=prescription.patient_id,
        doctor_user_id=prescription.doctor_user_id,
        appointment_id=prescription.appointment_id,
        admission_id=prescription.admission_id,
        status=prescription.status,
        chief_complaint=prescription.chief_complaint,
        diagnosis=prescription.diagnosis,
        cancelled_reason=getattr(prescription, "cancelled_reason", None),
        cancelled_at=getattr(prescription, "cancelled_at", None),
        created_at=prescription.created_at,
        items=items_resp,
        patient_name=patient_name,
        doctor_name=doctor_name,
        visit_type="OPD" if prescription.appointment_id else "IPD" if prescription.admission_id else None,
    )


@router.post("", response_model=PrescriptionResponse, status_code=status.HTTP_201_CREATED)
def create_prescription_endpoint(
    payload: PrescriptionCreate,
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(get_tenant_context),
) -> PrescriptionResponse:
    ensure_search_path(db, ctx.tenant.schema_name)
    _ensure_doctor_or_admin(ctx, db)

    # Determine doctor_user_id: use payload if provided (for non-doctor users), otherwise use current user
    #current_roles = set(get_user_role_names(db, ctx.user, tenant_schema_name=ctx.tenant.schema_name))
    #current_is_doctor = "DOCTOR" in current_roles

    # If payload provides doctor_user_id, validate it (for non-doctor users creating prescriptions)
    if payload.doctor_user_id:
        doctor_user = db.query(User).filter(User.id == payload.doctor_user_id).first()
        if not doctor_user:
            raise HTTPException(status_code=404, detail="Selected doctor not found")

        doctor_roles = set(get_user_role_names(db, doctor_user, tenant_schema_name=ctx.tenant.schema_name))
        if "DOCTOR" not in doctor_roles:
            doctor_name = (
                f"{doctor_user.first_name} {doctor_user.last_name}".strip() or doctor_user.email or "the selected user"
            )
            raise HTTPException(
                status_code=400,
                detail=f"Selected user ({doctor_name}) does not have the DOCTOR role. Please select a user with the DOCTOR role.",
            )
        doctor_user_id = payload.doctor_user_id
    else:
        # Use current user if no doctor_user_id provided
        doctor_user_id = ctx.user.id

    # OPD validations
    if payload.appointment_id:
        active_admission = (
            db.query(Admission)
            .filter(Admission.patient_id == payload.patient_id, Admission.status == AdmissionStatus.ACTIVE)
            .first()
        )
        if active_admission:
            raise HTTPException(
                status_code=400,
                detail="Cannot create OPD prescription for patient with active admission. Please discharge the patient first.",
            )

        appointment = db.query(Appointment).filter(Appointment.id == payload.appointment_id).first()
        if not appointment:
            raise HTTPException(status_code=404, detail="Appointment not found.")

        if appointment.status in [AppointmentStatus.COMPLETED, AppointmentStatus.CANCELLED, AppointmentStatus.NO_SHOW]:
            raise HTTPException(
                status_code=400,
                detail="Cannot create prescription for appointment with status COMPLETED, CANCELLED, or NO_SHOW.",
            )

        existing_rx = (
            db.query(Prescription)
            .filter(
                Prescription.appointment_id == payload.appointment_id,
                Prescription.status != PrescriptionStatus.CANCELLED,
            )
            .first()
        )
        if existing_rx:
            raise HTTPException(status_code=409, detail="A prescription already exists for this appointment.")

    # IPD validations
    if payload.admission_id:
        admission = db.query(Admission).filter(Admission.id == payload.admission_id).first()
        if not admission:
            raise HTTPException(status_code=404, detail="Admission not found.")
        if admission.patient_id != payload.patient_id:
            raise HTTPException(status_code=400, detail="Admission does not belong to the specified patient.")
        if admission.status != AdmissionStatus.ACTIVE:
            raise HTTPException(status_code=400, detail="Admission is not active.")

    try:
        prescription = create_prescription(
            db=db,
            patient_id=payload.patient_id,
            doctor_user_id=doctor_user_id,
            appointment_id=payload.appointment_id,
            admission_id=payload.admission_id,
            payload=payload,
        )
    except PatientNotFoundError:
        raise HTTPException(status_code=404, detail="Patient not found")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except SQLAlchemyError as e:
        logger.error("SQLAlchemy error creating prescription: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to create prescription.")

    rx_id = prescription.id

    # Build response without extra queries
    try:
        response = _build_response_from_instance(prescription)
    except Exception as e:
        logger.warning("Response build failed (non-fatal). rx=%s err=%s", rx_id, e, exc_info=True)
        response = PrescriptionResponse.model_validate(prescription)

    # Best-effort post actions must never cause a 500
    if prescription.appointment_id:
        try:
            apt = db.query(Appointment).filter(Appointment.id == prescription.appointment_id).first()
            if apt:
                now = datetime.now(timezone.utc)
                if apt.status in [AppointmentStatus.SCHEDULED, AppointmentStatus.CHECKED_IN]:
                    apt.status = AppointmentStatus.IN_CONSULTATION
                    if not getattr(apt, "consultation_started_at", None):
                        apt.consultation_started_at = now
                if not getattr(apt, "checked_in_at", None):
                    apt.checked_in_at = now
                if getattr(apt, "patient", None):
                    apt.patient.last_visited_at = now
                try:
                    db.commit()
                    ensure_search_path(db, ctx.tenant.schema_name)
                except SQLAlchemyError:
                    db.rollback()
        except Exception as e:
            logger.warning("Non-fatal: appointment update failed. rx=%s err=%s", rx_id, e, exc_info=True)

    # No email sent on prescription creation - email is sent only when prescription is issued

    return response


@router.get("", response_model=list[PrescriptionResponse])
def list_prescriptions_endpoint(
    patient_id: UUID | None = Query(None),
    appointment_id: UUID | None = Query(None),
    admission_id: UUID | None = Query(None),
    visit_type: str | None = Query(None),
    department_id: UUID | None = Query(None),
    doctor_user_id: UUID | None = Query(None),
    status: str | None = Query(None),
    date_from: str | None = Query(None),
    date_to: str | None = Query(None),
    order_by: str | None = Query(None, description="Sort by field: 'created_at' (asc/desc)"),
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(get_tenant_context),
) -> list[PrescriptionResponse]:
    ensure_search_path(db, ctx.tenant.schema_name)

    user_roles = get_user_role_names(db, ctx.user, tenant_schema_name=ctx.tenant.schema_name)
    is_doctor = "DOCTOR" in user_roles
    is_admin = "HOSPITAL_ADMIN" in user_roles or "SUPER_ADMIN" in user_roles
    is_pharmacist = "PHARMACIST" in user_roles
    is_receptionist = "RECEPTIONIST" in user_roles

    query = db.query(Prescription).options(
        joinedload(Prescription.patient),
        joinedload(Prescription.doctor),
        joinedload(Prescription.items),
    )

    if is_doctor and not (is_admin or is_pharmacist or is_receptionist):
        query = query.filter(Prescription.doctor_user_id == ctx.user.id)

    if patient_id:
        query = query.filter(Prescription.patient_id == patient_id)
    if appointment_id:
        query = query.filter(Prescription.appointment_id == appointment_id)
    if admission_id:
        query = query.filter(Prescription.admission_id == admission_id)

    if visit_type == "OPD":
        query = query.filter(Prescription.appointment_id.isnot(None))
    elif visit_type == "IPD":
        query = query.filter(Prescription.admission_id.isnot(None))

    if department_id:
        appointment_rx_ids = (
            db.query(Prescription.id)
            .join(Appointment, Prescription.appointment_id == Appointment.id)
            .filter(Appointment.department_id == department_id)
            .subquery()
        )
        admission_rx_ids = (
            db.query(Prescription.id)
            .join(Admission, Prescription.admission_id == Admission.id)
            .filter(Admission.department_id == department_id)
            .subquery()
        )

        query = query.filter(
            sa_or_(
                Prescription.id.in_(db.query(appointment_rx_ids.c.id)),
                Prescription.id.in_(db.query(admission_rx_ids.c.id)),
            )
        )

    if doctor_user_id:
        query = query.filter(Prescription.doctor_user_id == doctor_user_id)

    if status:
        # Support comma-separated statuses (like appointments API)
        parts = [s.strip() for s in status.split(",") if s.strip()]
        if parts:
            try:
                enums = [PrescriptionStatus(s) for s in parts]
                query = query.filter(Prescription.status.in_(enums))
            except ValueError as e:
                logger.warning("Invalid status filter. value=%s err=%s", status, e)
                pass

    if date_from:
        try:
            d_from = date_type.fromisoformat(str(date_from).split("T")[0])
            query = query.filter(func.date(Prescription.created_at) >= d_from)
        except Exception as e:
            logger.warning("Invalid date_from filter. value=%s err=%s", date_from, e)

    if date_to:
        try:
            d_to = date_type.fromisoformat(str(date_to).split("T")[0])
            query = query.filter(func.date(Prescription.created_at) <= d_to)
        except Exception as e:
            logger.warning("Invalid date_to filter. value=%s err=%s", date_to, e)

    # Sort by status priority (DRAFT -> ISSUED -> DISPENSED -> CANCELLED), then created_at DESC
    # This ensures workflow-driven ordering: items needing action appear first
    # If order_by is specified, use that instead (for manual sorting by created_at)
    if order_by == "created_at_asc":
        prescriptions = query.order_by(Prescription.created_at.asc()).all()
    elif order_by == "created_at_desc":
        prescriptions = query.order_by(Prescription.created_at.desc()).all()
    else:
        # Default: sort by status priority, then created_at DESC
        status_priority = case(
            (Prescription.status == PrescriptionStatus.DRAFT, 1),
            (Prescription.status == PrescriptionStatus.ISSUED, 2),
            (Prescription.status == PrescriptionStatus.DISPENSED, 3),
            (Prescription.status == PrescriptionStatus.CANCELLED, 4),
            else_=5,
        )
        prescriptions = query.order_by(status_priority, Prescription.created_at.desc()).all()

    results: list[PrescriptionResponse] = []
    for p in prescriptions:
        results.append(_build_response_from_instance(p))

    return results


@router.get("/{prescription_id}", response_model=PrescriptionResponse)
def get_prescription_endpoint(
    prescription_id: UUID,
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(get_tenant_context),
) -> PrescriptionResponse:
    ensure_search_path(db, ctx.tenant.schema_name)

    try:
        prescription = get_prescription(db=db, prescription_id=prescription_id)
    except PrescriptionNotFoundError:
        raise HTTPException(status_code=404, detail="Prescription not found")

    user_roles = get_user_role_names(db, ctx.user, tenant_schema_name=ctx.tenant.schema_name)
    is_doctor = "DOCTOR" in user_roles
    is_admin = "HOSPITAL_ADMIN" in user_roles or "SUPER_ADMIN" in user_roles
    is_pharmacist = "PHARMACIST" in user_roles
    is_receptionist = "RECEPTIONIST" in user_roles

    if is_doctor and not (is_admin or is_pharmacist or is_receptionist):
        if prescription.doctor_user_id != ctx.user.id:
            raise HTTPException(
                status_code=403, detail="Access restricted. You can only view prescriptions created by you."
            )

    # Make sure response always includes the same computed fields as list()
    try:
        prescription = _reload_prescription_with_relations(db, prescription_id, ctx.tenant.schema_name)
    except HTTPException:
        # Fall back to what service returned if reload fails for any reason
        pass

    return _build_response_from_instance(prescription)


@router.put("/{prescription_id}", response_model=PrescriptionResponse)
def update_prescription_endpoint(
    prescription_id: UUID,
    payload: PrescriptionUpdate,
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(get_tenant_context),
) -> PrescriptionResponse:
    ensure_search_path(db, ctx.tenant.schema_name)
    _ensure_doctor_or_admin(ctx, db)

    prescription = db.query(Prescription).filter(Prescription.id == prescription_id).first()
    if not prescription:
        raise HTTPException(status_code=404, detail="Prescription not found")

    if prescription.status != PrescriptionStatus.DRAFT:
        raise HTTPException(
            status_code=400,
            detail=f"Cannot edit prescription with status {prescription.status.value}. Only DRAFT prescriptions can be edited.",
        )

    role_names = get_user_role_names(db, ctx.user, tenant_schema_name=ctx.tenant.schema_name)
    is_admin = "HOSPITAL_ADMIN" in role_names or "SUPER_ADMIN" in role_names
    is_doctor = "DOCTOR" in role_names

    if is_doctor and not is_admin and prescription.doctor_user_id != ctx.user.id:
        raise HTTPException(
            status_code=403, detail="Access restricted. You can only edit prescriptions created by you."
        )

    try:
        prescription.chief_complaint = payload.chief_complaint
        prescription.diagnosis = payload.diagnosis

        db.query(PrescriptionItem).filter(PrescriptionItem.prescription_id == prescription_id).delete()

        for item_data in payload.items:
            db.add(
                PrescriptionItem(
                    prescription_id=prescription.id,
                    stock_item_id=item_data.stock_item_id,
                    medicine_name=item_data.medicine_name,
                    dosage=item_data.dosage,
                    frequency=item_data.frequency,
                    duration=item_data.duration,
                    instructions=item_data.instructions,
                    quantity=item_data.quantity,
                )
            )

        db.commit()
        ensure_search_path(db, ctx.tenant.schema_name)
    except SQLAlchemyError:
        db.rollback()
        raise HTTPException(status_code=500, detail="Failed to update prescription.")

    # 2) Reload with relations (prevents lazy-load/search_path issues)
    prescription = _reload_prescription_with_relations(db, prescription_id, ctx.tenant.schema_name)

    # 3) Return response
    return _build_response_from_instance(prescription)


@router.patch("/{prescription_id}/dispense", response_model=PrescriptionResponse)
def dispense_prescription(
    prescription_id: UUID,
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(get_tenant_context),
) -> PrescriptionResponse:
    ensure_search_path(db, ctx.tenant.schema_name)

    user_roles = get_user_role_names(db, ctx.user, tenant_schema_name=ctx.tenant.schema_name)
    is_pharmacist = "PHARMACIST" in user_roles
    is_admin = "HOSPITAL_ADMIN" in user_roles or "SUPER_ADMIN" in user_roles

    if not (is_pharmacist or is_admin):
        raise HTTPException(status_code=403, detail="Only pharmacists or admins can dispense prescriptions.")

    prescription = (
        db.query(Prescription)
        .options(joinedload(Prescription.patient), joinedload(Prescription.doctor), joinedload(Prescription.items))
        .filter(Prescription.id == prescription_id)
        .first()
    )
    if not prescription:
        raise HTTPException(status_code=404, detail="Prescription not found")

    if prescription.status != PrescriptionStatus.ISSUED:
        raise HTTPException(
            status_code=400,
            detail=f"Cannot dispense prescription with status {prescription.status.value}. Must be ISSUED.",
        )

    deductions = []
    for item in prescription.items or []:
        if item.stock_item_id and item.quantity:
            stock_item = db.query(StockItem).filter(StockItem.id == item.stock_item_id).first()
            if not stock_item:
                raise HTTPException(status_code=400, detail=f"Stock item not found for medicine '{item.medicine_name}'")
            if (stock_item.current_stock or 0) < item.quantity:
                raise HTTPException(
                    status_code=400,
                    detail=f"Insufficient stock for '{item.medicine_name}'. Available: {stock_item.current_stock}, Required: {item.quantity}",
                )
            deductions.append((stock_item, item.quantity))

    for s, qty in deductions:
        s.current_stock = max(0, (s.current_stock or 0) - qty)

    prescription.status = PrescriptionStatus.DISPENSED

    try:
        db.commit()
        ensure_search_path(db, ctx.tenant.schema_name)
    except SQLAlchemyError:
        db.rollback()
        raise HTTPException(status_code=500, detail="Failed to dispense prescription.")

    # 2) Reload with relations (prevents lazy-load/search_path issues)
    prescription = _reload_prescription_with_relations(db, prescription_id, ctx.tenant.schema_name)

    # 3) Notifications (best-effort) - Enhanced email with details
    patient = prescription.patient
    if patient and getattr(patient, "consent_email", False) and getattr(patient, "email", None):
        try:
            # Get doctor name
            doctor = getattr(prescription, "doctor", None)
            doctor_name = None
            if doctor:
                doctor_name = f"{doctor.first_name} {doctor.last_name or ''}".strip()

            patient_name = f"{patient.first_name} {patient.last_name or ''}".strip()

            # Prepare items data for email
            items_data = []
            for item in getattr(prescription, "items", None) or []:
                items_data.append(
                    {
                        "medicine_name": item.medicine_name or "N/A",
                        "dosage": item.dosage or "-",
                        "frequency": item.frequency or "-",
                        "duration": item.duration or "-",
                        "instructions": item.instructions or "-",
                    }
                )

            # Build detailed email body
            email_body_parts = [
                f"<p>Dear {patient_name},</p>",
                f"<p>Your prescription <strong>{prescription.prescription_code}</strong> has been dispensed at <strong>{ctx.tenant.name}</strong>.</p>",
            ]

            if doctor_name:
                email_body_parts.append(f"<p><strong>Prescribed by:</strong> Dr. {doctor_name}</p>")

            if prescription.chief_complaint:
                email_body_parts.append(f"<p><strong>Chief Complaint:</strong> {prescription.chief_complaint}</p>")

            if prescription.diagnosis:
                email_body_parts.append(f"<p><strong>Diagnosis:</strong> {prescription.diagnosis}</p>")

            if items_data:
                email_body_parts.append("<p><strong>Medicines Dispensed:</strong></p>")
                email_body_parts.append("<ul>")
                for item in items_data:
                    med_info = f"{item['medicine_name']}"
                    if item["dosage"] and item["dosage"] != "-":
                        med_info += f" - {item['dosage']}"
                    if item["frequency"] and item["frequency"] != "-":
                        med_info += f", {item['frequency']}"
                    if item["duration"] and item["duration"] != "-":
                        med_info += f" for {item['duration']}"
                    email_body_parts.append(f"<li>{med_info}</li>")
                email_body_parts.append("</ul>")

            email_body_parts.extend(
                [
                    "<p><strong>Your medicines are ready for collection.</strong></p>",
                    "<p>Please visit the pharmacy at our hospital to collect your medicines.</p>",
                    f"<p>If you have any questions, please contact us at {ctx.tenant.contact_phone or ctx.tenant.contact_email or 'our hospital'}.</p>",
                    f"<p>Thank you for choosing {ctx.tenant.name}.</p>",
                ]
            )

            html = render_email_template(
                title="Prescription Dispensed",
                body_html="".join(email_body_parts),
                hospital_name=ctx.tenant.name,
            )
            send_notification_email(
                db=db,
                to_email=patient.email,
                subject=f"Prescription Dispensed - {ctx.tenant.name}",
                body=html,
                triggered_by=ctx.user,
                reason="prescription_dispensed",
                tenant_schema_name=ctx.tenant.schema_name,
                html=True,
                check_patient_flag=True,
            )
        except Exception:
            logger.exception("Non-fatal: dispense notification failed. rx=%s", prescription_id)

    # 4) Return response
    return _build_response_from_instance(prescription)


@router.patch("/{prescription_id}", response_model=PrescriptionResponse)
def update_prescription_status(
    prescription_id: UUID,
    payload: dict,
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(get_tenant_context),
) -> PrescriptionResponse:
    ensure_search_path(db, ctx.tenant.schema_name)
    prescription = _reload_prescription_with_relations(db, prescription_id, ctx.tenant.schema_name)

    if "status" not in payload:
        raise HTTPException(status_code=400, detail="Status is required")

    try:
        new_status = PrescriptionStatus(payload["status"])
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid status: {payload['status']}")

    role_names = get_user_role_names(db, ctx.user, tenant_schema_name=ctx.tenant.schema_name)
    is_doctor = "DOCTOR" in role_names
    is_admin = "HOSPITAL_ADMIN" in role_names or "SUPER_ADMIN" in role_names
    is_pharmacist = "PHARMACIST" in role_names

    old_status = prescription.status

    allowed = False
    if old_status == PrescriptionStatus.DRAFT:
        if new_status == PrescriptionStatus.ISSUED and (is_doctor or is_admin):
            allowed = True
        elif new_status == PrescriptionStatus.CANCELLED and (is_doctor or is_admin):
            if not payload.get("reason"):
                raise HTTPException(status_code=400, detail="Cancellation reason is required.")
            allowed = True
    elif old_status == PrescriptionStatus.ISSUED:
        if new_status == PrescriptionStatus.DISPENSED and (is_pharmacist or is_admin):
            allowed = True

    if not allowed:
        raise HTTPException(status_code=400, detail=f"Transition {old_status.value} -> {new_status.value} not allowed.")

    # 1) Commit status change
    try:
        prescription.status = new_status
        if new_status == PrescriptionStatus.CANCELLED and payload.get("reason"):
            # Store cancellation reason and timestamp
            prescription.cancelled_reason = payload.get("reason")
            prescription.cancelled_at = datetime.now(timezone.utc)
        db.commit()
        ensure_search_path(db, ctx.tenant.schema_name)
    except SQLAlchemyError:
        db.rollback()
        raise HTTPException(status_code=500, detail="Failed to update prescription status.")

    # 2) Reload with relations (prevents lazy-load/search_path issues)
    prescription = _reload_prescription_with_relations(db, prescription_id, ctx.tenant.schema_name)

    # 3) Side-effects (best-effort)
    if new_status == PrescriptionStatus.ISSUED and prescription.appointment_id:
        try:
            apt = db.query(Appointment).filter(Appointment.id == prescription.appointment_id).first()
            if apt and apt.status in [
                AppointmentStatus.SCHEDULED,
                AppointmentStatus.CHECKED_IN,
                AppointmentStatus.IN_CONSULTATION,
            ]:
                now = datetime.now(timezone.utc)
                apt.status = AppointmentStatus.COMPLETED
                apt.completed_at = now
                if getattr(apt, "patient", None):
                    apt.patient.last_visited_at = now
                try:
                    db.commit()
                    ensure_search_path(db, ctx.tenant.schema_name)
                except SQLAlchemyError:
                    db.rollback()
        except Exception:
            logger.exception("Non-fatal: appointment completion failed. rx=%s", prescription_id)

    # 4) Create followup appointment if requested (best-effort)
    if new_status == PrescriptionStatus.ISSUED and payload.get("create_followup"):
        try:
            followup_scheduled_at = payload.get("followup_scheduled_at")
            followup_department_id = payload.get("followup_department_id")
            followup_doctor_id = payload.get("followup_doctor_id")

            if followup_scheduled_at and followup_department_id and followup_doctor_id:
                # Parse scheduled_at datetime
                try:
                    if isinstance(followup_scheduled_at, str):
                        scheduled_utc = datetime.fromisoformat(followup_scheduled_at.replace("Z", "+00:00"))
                    else:
                        scheduled_utc = followup_scheduled_at

                    # Ensure scheduled_at is timezone-aware UTC
                    if scheduled_utc.tzinfo is None:
                        scheduled_utc = scheduled_utc.replace(tzinfo=timezone.utc)
                    else:
                        scheduled_utc = scheduled_utc.astimezone(timezone.utc)

                    # Validate 15-minute interval (00, 15, 30, 45)
                    from app.utils.datetime_utils import is_valid_15_minute_interval

                    if not is_valid_15_minute_interval(scheduled_utc):
                        logger.warning("Followup appointment time not in 15-minute interval: %s", scheduled_utc)
                        # Don't create followup appointment if time is invalid
                        scheduled_utc = None
                except Exception as e:
                    logger.warning("Failed to parse followup_scheduled_at: %s", e)
                    scheduled_utc = None

                if scheduled_utc:
                    # Validate department and doctor exist
                    department = db.query(Department).filter(Department.id == UUID(followup_department_id)).first()
                    doctor_user = db.query(User).filter(User.id == UUID(followup_doctor_id)).first()

                    if department and doctor_user:
                        # Check if doctor has DOCTOR role
                        doctor_roles = get_user_role_names(db, doctor_user, tenant_schema_name=ctx.tenant.schema_name)
                        if "DOCTOR" in doctor_roles:
                            # Create followup appointment
                            followup_appt = Appointment(
                                patient_id=prescription.patient_id,
                                department_id=UUID(followup_department_id),
                                doctor_user_id=UUID(followup_doctor_id),
                                scheduled_at=scheduled_utc,
                                status=AppointmentStatus.SCHEDULED,
                                notes=f"Follow-up appointment for prescription {prescription.prescription_code}",
                            )
                            try:
                                db.add(followup_appt)
                                db.commit()
                                ensure_search_path(db, ctx.tenant.schema_name)

                                # Reload appointment with relations for notifications
                                followup_appt = (
                                    db.query(Appointment)
                                    .options(
                                        joinedload(Appointment.patient),
                                        joinedload(Appointment.doctor),
                                    )
                                    .filter(Appointment.id == followup_appt.id)
                                    .first()
                                )

                                # Send appointment notification (best-effort)
                                try:
                                    patient = followup_appt.patient if followup_appt else None
                                    if not patient:
                                        logger.warning(
                                            "Followup appointment has no patient. apt=%s",
                                            followup_appt.id if followup_appt else None,
                                        )
                                    elif not getattr(patient, "consent_email", False):
                                        logger.info(
                                            "Patient consent_email is False. patient_id=%s, apt=%s",
                                            patient.id if patient else None,
                                            followup_appt.id if followup_appt else None,
                                        )
                                    elif not getattr(patient, "email", None):
                                        logger.info(
                                            "Patient has no email. patient_id=%s, apt=%s",
                                            patient.id if patient else None,
                                            followup_appt.id if followup_appt else None,
                                        )
                                    else:
                                        html = render_email_template(
                                            title="Follow-up Appointment Scheduled",
                                            body_html=(
                                                f"<p>Dear {patient.first_name} {patient.last_name or ''},</p>"
                                                f"<p>A follow-up appointment has been scheduled for you at <strong>{ctx.tenant.name}</strong>.</p>"
                                                f"<p><strong>Date & Time:</strong> {scheduled_utc.strftime('%B %d, %Y at %I:%M %p')}</p>"
                                                f"<p><strong>Doctor:</strong> Dr. {doctor_user.first_name} {doctor_user.last_name or ''}</p>"
                                                f"<p><strong>Department:</strong> {department.name}</p>"
                                                f"<p>Please arrive on time for your appointment.</p>"
                                            ),
                                            hospital_name=ctx.tenant.name,
                                        )
                                        send_notification_email(
                                            db=db,
                                            to_email=patient.email,
                                            subject=f"Follow-up Appointment Scheduled - {ctx.tenant.name}",
                                            body=html,
                                            triggered_by=ctx.user,
                                            reason="appointment_created",
                                            tenant_schema_name=ctx.tenant.schema_name,
                                            html=True,
                                            check_patient_flag=True,
                                        )
                                        logger.info(
                                            "Followup appointment email sent. patient_id=%s, apt=%s, email=%s",
                                            patient.id,
                                            followup_appt.id if followup_appt else None,
                                            patient.email,
                                        )
                                except Exception as e:
                                    logger.exception(
                                        "Non-fatal: followup appointment email notification failed. apt=%s, error=%s",
                                        followup_appt.id if followup_appt else None,
                                        str(e),
                                    )

                                logger.info(
                                    "Follow-up appointment created: %s for prescription %s",
                                    followup_appt.id,
                                    prescription_id,
                                )
                            except SQLAlchemyError:
                                db.rollback()
                                logger.exception(
                                    "Failed to create followup appointment for prescription %s", prescription_id
                                )
                        else:
                            logger.warning("Followup doctor user %s does not have DOCTOR role", followup_doctor_id)
                    else:
                        logger.warning(
                            "Followup department or doctor not found: dept=%s, doctor=%s",
                            followup_department_id,
                            followup_doctor_id,
                        )
        except Exception:
            logger.exception("Non-fatal: followup appointment creation failed. rx=%s", prescription_id)

    # Best-effort issued email with PDF attachment
    if new_status == PrescriptionStatus.ISSUED:
        try:
            patient = prescription.patient
            if not patient:
                logger.warning("Prescription has no patient. rx=%s", prescription_id)
            elif not getattr(patient, "consent_email", False):
                logger.info(
                    "Patient consent_email is False. patient_id=%s, rx=%s",
                    patient.id if patient else None,
                    prescription_id,
                )
            elif not getattr(patient, "email", None):
                logger.info(
                    "Patient has no email. patient_id=%s, rx=%s", patient.id if patient else None, prescription_id
                )
            else:
                # Prepare prescription data dict for PDF generation
                doctor = getattr(prescription, "doctor", None)
                doctor_name = None
                if doctor:
                    doctor_name = f"{doctor.first_name} {doctor.last_name or ''}".strip()

                patient_name = f"{patient.first_name} {patient.last_name or ''}".strip()
                patient_code = getattr(patient, "patient_code", None) or "N/A"

                # Calculate age if DOB available
                patient_age = None
                if hasattr(patient, "dob") and patient.dob:
                    from datetime import date

                    try:
                        if isinstance(patient.dob, str):
                            dob = datetime.fromisoformat(patient.dob.replace("Z", "+00:00")).date()
                        else:
                            dob = patient.dob
                        today = date.today()
                        patient_age = today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))
                    except Exception:
                        pass

                # Prepare items data
                items_data = []
                for item in getattr(prescription, "items", None) or []:
                    items_data.append(
                        {
                            "medicine_name": item.medicine_name or "N/A",
                            "dosage": item.dosage or "-",
                            "frequency": item.frequency or "-",
                            "duration": item.duration or "-",
                            "instructions": item.instructions or "-",
                        }
                    )

                prescription_data = {
                    "prescription_code": prescription.prescription_code or "N/A",
                    "patient_name": patient_name,
                    "patient_code": patient_code,
                    "patient_age": patient_age,
                    "patient_gender": getattr(patient, "gender", None),
                    "patient_weight": getattr(patient, "weight", None),
                    "doctor_name": doctor_name or "Doctor",
                    "chief_complaint": prescription.chief_complaint,
                    "diagnosis": prescription.diagnosis,
                    "created_at": prescription.created_at.isoformat()
                    if hasattr(prescription.created_at, "isoformat")
                    else str(prescription.created_at),
                    "items": items_data,
                }

                # Generate PDF
                pdf_buffer = generate_prescription_pdf(
                    prescription_data=prescription_data,
                    tenant_name=ctx.tenant.name,
                    tenant_address=ctx.tenant.address,
                    tenant_phone=ctx.tenant.contact_phone,
                    tenant_email=ctx.tenant.contact_email,
                )
                pdf_bytes = pdf_buffer.getvalue()

                # Format appointment date if available
                appointment_date_str = ""
                if prescription.appointment_id and hasattr(prescription, "appointment") and prescription.appointment:
                    apt = prescription.appointment
                    if hasattr(apt, "scheduled_at") and apt.scheduled_at:
                        try:
                            if isinstance(apt.scheduled_at, str):
                                apt_date = datetime.fromisoformat(apt.scheduled_at.replace("Z", "+00:00"))
                            else:
                                apt_date = apt.scheduled_at
                            appointment_date_str = apt_date.strftime("%B %d, %Y at %I:%M %p")
                        except Exception:
                            appointment_date_str = str(apt.scheduled_at) if apt.scheduled_at else ""

                # Build detailed email body
                email_body_parts = [
                    f"<p>Dear {patient_name},</p>",
                    f"<p>Your prescription <strong>{prescription.prescription_code}</strong> has been issued at <strong>{ctx.tenant.name}</strong>.</p>",
                ]

                if appointment_date_str:
                    email_body_parts.append(f"<p><strong>Appointment Date:</strong> {appointment_date_str}</p>")

                if doctor_name:
                    email_body_parts.append(f"<p><strong>Prescribed by:</strong> Dr. {doctor_name}</p>")

                if prescription.chief_complaint:
                    email_body_parts.append(f"<p><strong>Chief Complaint:</strong> {prescription.chief_complaint}</p>")

                if prescription.diagnosis:
                    email_body_parts.append(f"<p><strong>Diagnosis:</strong> {prescription.diagnosis}</p>")

                if items_data:
                    email_body_parts.append("<p><strong>Medicines Prescribed:</strong></p>")
                    email_body_parts.append("<ul>")
                    for item in items_data:
                        med_info = f"{item['medicine_name']}"
                        if item["dosage"] and item["dosage"] != "-":
                            med_info += f" - {item['dosage']}"
                        if item["frequency"] and item["frequency"] != "-":
                            med_info += f", {item['frequency']}"
                        if item["duration"] and item["duration"] != "-":
                            med_info += f" for {item['duration']}"
                        email_body_parts.append(f"<li>{med_info}</li>")
                    email_body_parts.append("</ul>")

                email_body_parts.extend(
                    [
                        "<p>Please find your detailed prescription attached to this email in PDF format.</p>",
                        "<p>You can visit the pharmacy at our hospital to collect your medicines.</p>",
                        f"<p>If you have any questions, please contact us at {ctx.tenant.contact_phone or ctx.tenant.contact_email or 'our hospital'}.</p>",
                        f"<p>Thank you for choosing {ctx.tenant.name}.</p>",
                    ]
                )

                html = render_email_template(
                    title="Prescription Issued",
                    body_html="".join(email_body_parts),
                    hospital_name=ctx.tenant.name,
                )
                send_notification_email(
                    db=db,
                    to_email=patient.email,
                    subject=f"Prescription Issued - {ctx.tenant.name}",
                    body=html,
                    triggered_by=ctx.user,
                    reason="prescription_issued",
                    tenant_schema_name=ctx.tenant.schema_name,
                    html=True,
                    check_patient_flag=True,
                    attachments=[
                        {
                            "filename": f"prescription_{prescription.prescription_code or prescription.id}.pdf",
                            "content": pdf_bytes,
                        }
                    ],
                )
                logger.info(
                    "Prescription issued email sent. patient_id=%s, rx=%s, email=%s",
                    patient.id,
                    prescription_id,
                    patient.email,
                )
        except Exception as e:
            logger.exception("Non-fatal: issue notification failed. rx=%s, error=%s", prescription_id, str(e))

    # 4) Return response
    return _build_response_from_instance(prescription)
