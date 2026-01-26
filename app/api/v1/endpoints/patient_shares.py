# app/api/v1/endpoints/patient_shares.py
import logging
from datetime import datetime, timezone
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.tenant_context import TenantContext, get_tenant_context
from app.core.tenant_db import ensure_search_path
from app.dependencies.authz import require_permission
from app.models.patient import Patient
from app.models.patient_share import PatientShare, PatientShareLink, ShareStatus
from app.models.tenant_global import Tenant
from app.models.user import User
from app.schemas.patient_share import (
    PatientShareCreate,
    PatientShareResponse,
    SharedPatientSummary,
    TenantOption,
)
from app.services.patient_share_service import (
    create_patient_share,
    get_shared_patient_summary,
    log_share_access,
    revoke_share,
)

router = APIRouter()
logger = logging.getLogger(__name__)


@router.get("/tenants", response_model=list[TenantOption], tags=["patient-shares"])
def list_tenants_for_sharing(
    search: Optional[str] = Query(None, description="Search by tenant name"),
    current_user: User = Depends(require_permission("sharing:create")),
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(get_tenant_context),
) -> list[TenantOption]:
    """
    List tenants available for patient sharing (excludes current tenant).
    Requires sharing:create permission.
    """
    query = db.query(Tenant).filter(
        Tenant.id != ctx.tenant.id,
        Tenant.status == "ACTIVE",
    )

    if search:
        search_term = f"%{search.strip()}%"
        query = query.filter(
            Tenant.name.ilike(search_term) | Tenant.contact_email.ilike(search_term)
        )

    tenants = query.order_by(Tenant.name.asc()).limit(50).all()
    return [
        TenantOption(id=t.id, name=t.name, contact_email=t.contact_email)
        for t in tenants
    ]


@router.post(
    "",
    response_model=PatientShareResponse,
    status_code=status.HTTP_201_CREATED,
    tags=["patient-shares"],
)
def create_patient_share_endpoint(
    patient_id: UUID = Query(..., description="Patient ID to share"),
    payload: PatientShareCreate = ...,
    current_user: User = Depends(require_permission("sharing:create")),
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(get_tenant_context),
) -> PatientShareResponse:
    """
    Create a patient share.
    Requires sharing:create permission and patient consent confirmation.
    """
    if not payload.consent_confirmed:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Patient consent must be confirmed to share data.",
        )

    # Validate target tenant - always required
    if not payload.target_tenant_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Target hospital is required.",
        )

    target_tenant = (
        db.query(Tenant).filter(Tenant.id == payload.target_tenant_id).first()
    )
    if not target_tenant:
        raise HTTPException(status_code=404, detail="Target tenant not found")

    # Ensure target tenant is active
    if target_tenant.status != "ACTIVE":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Target hospital must be active to receive shared patient records.",
        )

    # Get patient info for email and response
    conn = db.connection()
    original_path = conn.execute(text("SHOW search_path")).scalar()
    patient_name = "Patient"
    patient_code = None
    try:
        conn.execute(text(f'SET search_path TO "{ctx.tenant.schema_name}", public'))
        patient = db.query(Patient).filter(Patient.id == patient_id).first()
        if patient:
            patient_name = f"{patient.first_name} {patient.last_name or ''}".strip()
            patient_code = patient.patient_code
    except Exception as e:
        logger.warning(f"Failed to fetch patient info: {e}", exc_info=True)
    finally:
        try:
            conn.execute(text(f"SET search_path TO {original_path}"))
        except Exception:
            pass

    try:
        share = create_patient_share(
            db=db,
            source_tenant_id=ctx.tenant.id,
            patient_id=patient_id,
            target_tenant_id=payload.target_tenant_id,
            share_mode=payload.share_mode,
            validity_days=payload.validity_days,
            created_by_user_id=ctx.user.id,
            note=payload.note,
        )

        # Send detailed notification emails
        from app.services.notification_service import send_notification_email
        from app.utils.email_templates import render_email_template
        from app.core.config import get_settings

        settings = get_settings()

        share_mode_text = (
            "Read-only Link"
            if payload.share_mode.value == "READ_ONLY_LINK"
            else "Write-enabled (Create Record)"
        )
        expires_text = (
            f"Expires: {share.expires_at.strftime('%Y-%m-%d %H:%M:%S UTC')}"
            if share.expires_at
            else "Never expires"
        )
        created_by_name = (
            f"{ctx.user.first_name} {ctx.user.last_name or ''}".strip()
            or ctx.user.email
        )

        # Email body content
        email_body_html = f"""
        <p>Dear {{recipient_name}},</p>
        
        <p>A patient record has been shared. Details are provided below:</p>
        
        <div style="background-color: #f8f9fa; padding: 15px; border-radius: 5px; margin: 20px 0;">
            <h3 style="color: #2c3e50; margin-top: 0;">Share Details</h3>
            <table style="width: 100%; border-collapse: collapse;">
                <tr>
                    <td style="padding: 8px; font-weight: bold; width: 40%;">Source Hospital:</td>
                    <td style="padding: 8px;">{ctx.tenant.name}</td>
                </tr>
                <tr>
                    <td style="padding: 8px; font-weight: bold;">Target Hospital:</td>
                    <td style="padding: 8px;">{target_tenant.name}</td>
                </tr>
                <tr>
                    <td style="padding: 8px; font-weight: bold;">Patient Name:</td>
                    <td style="padding: 8px;">{patient_name}</td>
                </tr>
                {f'<tr><td style="padding: 8px; font-weight: bold;">Patient Code:</td><td style="padding: 8px;">{patient_code}</td></tr>' if patient_code else ""}
                <tr>
                    <td style="padding: 8px; font-weight: bold;">Shared By:</td>
                    <td style="padding: 8px;">{created_by_name}</td>
                </tr>
                <tr>
                    <td style="padding: 8px; font-weight: bold;">Share Type:</td>
                    <td style="padding: 8px;">{share_mode_text}</td>
                </tr>
                <tr>
                    <td style="padding: 8px; font-weight: bold;">Shared Date & Time:</td>
                    <td style="padding: 8px;">{share.created_at.strftime("%Y-%m-%d %H:%M:%S UTC")}</td>
                </tr>
                <tr>
                    <td style="padding: 8px; font-weight: bold;">Validity:</td>
                    <td style="padding: 8px;">{expires_text}</td>
                </tr>
                {f'<tr><td style="padding: 8px; font-weight: bold;">Note:</td><td style="padding: 8px;">{share.note}</td></tr>' if share.note else ""}
            </table>
        </div>
        
        <p style="margin-top: 20px;">
            {{action_message}}
        </p>
        """

        # Send email to target hospital
        if target_tenant and target_tenant.contact_email:
            try:
                hospital_email_body = email_body_html.replace(
                    "{recipient_name}", target_tenant.name
                ).replace(
                    "{action_message}",
                    'Please log in to your HMS account to view the shared patient record in the "Shared Patients" section.',
                )
                hospital_email_html = render_email_template(
                    title="Patient Record Shared",
                    body_html=hospital_email_body,
                    hospital_name=target_tenant.name,
                )

                send_notification_email(
                    db=db,
                    to_email=target_tenant.contact_email,
                    subject=f"Patient Record Shared - {ctx.tenant.name}",
                    body=hospital_email_html,
                    triggered_by=ctx.user,
                    reason="patient_share_created",
                    tenant_schema_name=target_tenant.schema_name,
                    html=True,
                )
            except Exception as e:
                logger.warning(
                    f"Failed to send share notification email to hospital: {e}",
                    exc_info=True,
                )

        # Send email to patient if email exists and consent given
        patient_email = None
        try:
            conn = db.connection()
            original_path = conn.execute(text("SHOW search_path")).scalar()
            conn.execute(text(f'SET search_path TO "{ctx.tenant.schema_name}", public'))
            patient = db.query(Patient).filter(Patient.id == patient_id).first()
            if patient and patient.email and getattr(patient, "consent_email", False):
                patient_email = patient.email
        except Exception as e:
            logger.warning(f"Failed to fetch patient email: {e}", exc_info=True)
        finally:
            try:
                conn.execute(text(f"SET search_path TO {original_path}"))
            except Exception:
                pass

        if patient_email:
            try:
                patient_email_body = email_body_html.replace(
                    "{recipient_name}", patient_name
                ).replace(
                    "{action_message}",
                    f"Your medical record has been shared with <strong>{target_tenant.name}</strong> for continuity of care. "
                    f"If you have any concerns, please contact {ctx.tenant.name}.",
                )
                patient_email_html = render_email_template(
                    title="Your Medical Record Has Been Shared",
                    body_html=patient_email_body,
                    hospital_name=ctx.tenant.name,
                )

                send_notification_email(
                    db=db,
                    to_email=patient_email,
                    subject=f"Your Medical Record Shared - {ctx.tenant.name}",
                    body=patient_email_html,
                    triggered_by=ctx.user,
                    reason="patient_share_created",
                    tenant_schema_name=ctx.tenant.schema_name,
                    html=True,
                    check_patient_flag=True,  # Respect SEND_EMAIL_TO_PATIENTS setting
                )
            except Exception as e:
                logger.warning(
                    f"Failed to send share notification email to patient: {e}",
                    exc_info=True,
                )

        # Build response
        response_dict = PatientShareResponse.model_validate(share).model_dump()
        source_tenant = (
            db.query(Tenant).filter(Tenant.id == share.source_tenant_id).first()
        )
        if source_tenant:
            response_dict["source_tenant_name"] = source_tenant.name
        if share.target_tenant_id:
            target_tenant = (
                db.query(Tenant).filter(Tenant.id == share.target_tenant_id).first()
            )
            if target_tenant:
                response_dict["target_tenant_name"] = target_tenant.name

        # Add created by user name
        created_by_user = (
            db.query(User).filter(User.id == share.created_by_user_id).first()
        )
        if created_by_user:
            response_dict["created_by_user_name"] = (
                f"{created_by_user.first_name} {created_by_user.last_name or ''}".strip()
                or created_by_user.email
            )

        # Add patient name and code
        response_dict["patient_name"] = patient_name
        response_dict["patient_code"] = patient_code

        return PatientShareResponse(**response_dict)

    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to create patient share: {str(e)}",
        )


@router.get(
    "/shared/{token}",
    response_model=SharedPatientSummary,
    tags=["patient-shares"],
)
def get_shared_patient_by_token(
    token: str,
    request: Request,
    db: Session = Depends(get_db),
) -> SharedPatientSummary:
    """
    Get shared patient summary by token (read-only link).
    No authentication required, but token must be valid and not expired/revoked.
    """
    share = db.query(PatientShare).filter(PatientShare.token == token).first()
    if not share:
        raise HTTPException(status_code=404, detail="Share not found")

    if share.status != ShareStatus.ACTIVE:
        raise HTTPException(status_code=403, detail="Share is not active")

    if share.expires_at:
        now = datetime.now(timezone.utc)
        if share.expires_at < now:
            share.status = ShareStatus.EXPIRED
            db.commit()
            raise HTTPException(status_code=403, detail="Share has expired")

    try:
        # Log access
        client_ip = request.client.host if request.client else None
        user_agent = request.headers.get("user-agent")
        log_share_access(
            db=db,
            share_id=share.id,
            ip_address=client_ip,
            user_agent=user_agent,
        )

        summary = get_shared_patient_summary(db=db, share_id=share.id, token=token)
        return summary

    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get(
    "/{share_id}/patient-data",
    response_model=SharedPatientSummary,
    tags=["patient-shares"],
)
def get_shared_patient_data(
    share_id: UUID,
    current_user: User = Depends(require_permission("sharing:view")),
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(get_tenant_context),
) -> SharedPatientSummary:
    """
    Get shared patient data for authenticated users.
    Available for both READ_ONLY_LINK and CREATE_RECORD modes.
    Requires sharing:view permission and share must be active and not revoked.
    """
    share = db.query(PatientShare).filter(PatientShare.id == share_id).first()
    if not share:
        raise HTTPException(status_code=404, detail="Share not found")

    # Check if user's tenant is either source or target
    if (
        share.source_tenant_id != ctx.tenant.id
        and share.target_tenant_id != ctx.tenant.id
    ):
        raise HTTPException(
            status_code=403,
            detail="You can only view shares for your hospital.",
        )

    # Check if share is active and not revoked
    if share.status != ShareStatus.ACTIVE:
        raise HTTPException(status_code=403, detail="Share is not active")

    if share.expires_at:
        now = datetime.now(timezone.utc)
        if share.expires_at < now:
            share.status = ShareStatus.EXPIRED
            db.commit()
            raise HTTPException(status_code=403, detail="Share has expired")

    try:
        summary = get_shared_patient_summary(db=db, share_id=share.id, token=None)
        return summary
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post(
    "/{share_id}/import",
    response_model=PatientShareResponse,
    tags=["patient-shares"],
)
def import_patient_share_endpoint(
    share_id: UUID,
    current_user: User = Depends(require_permission("sharing:view")),
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(get_tenant_context),
) -> PatientShareResponse:
    """
    Import a shared patient record (for CREATE_RECORD mode).
    Only target tenant can import. Creates patient record if not already imported.
    """
    from app.models.patient_share import PatientShareLink
    from app.utils.id_generators import generate_patient_code

    share = db.query(PatientShare).filter(PatientShare.id == share_id).first()
    if not share:
        raise HTTPException(status_code=404, detail="Share not found")

    if share.target_tenant_id != ctx.tenant.id:
        raise HTTPException(
            status_code=403,
            detail="You can only import shares received by your hospital.",
        )

    if share.share_mode.value != "CREATE_RECORD":
        raise HTTPException(
            status_code=400,
            detail="Only CREATE_RECORD mode shares can be imported.",
        )

    if share.status != ShareStatus.ACTIVE:
        raise HTTPException(
            status_code=400,
            detail=f"Cannot import share with status {share.status.value}.",
        )

    # Check if already imported
    existing_link = (
        db.query(PatientShareLink)
        .filter(
            PatientShareLink.share_id == share_id,
            PatientShareLink.target_tenant_id == ctx.tenant.id,
        )
        .first()
    )

    if existing_link:
        # Already imported, return existing
        response_dict = PatientShareResponse.model_validate(share).model_dump()
        response_dict["target_patient_id"] = existing_link.target_patient_id
        return PatientShareResponse(**response_dict)

    # Import only patient data (not visit history - appointments/prescriptions/admissions)
    # Visit history is from source hospital and should not be imported
    conn = db.connection()
    original_path = conn.execute(text("SHOW search_path")).scalar()

    try:
        # Get source patient
        source_tenant = (
            db.query(Tenant).filter(Tenant.id == share.source_tenant_id).first()
        )
        if not source_tenant:
            raise ValueError("Source tenant not found")

        conn.execute(text(f'SET search_path TO "{source_tenant.schema_name}", public'))
        source_patient = (
            db.query(Patient).filter(Patient.id == share.patient_id).first()
        )
        if not source_patient:
            raise ValueError("Source patient not found")

        # Switch to target tenant and create patient
        conn.execute(text(f'SET search_path TO "{ctx.tenant.schema_name}", public'))

        target_patient = Patient(
            first_name=source_patient.first_name,
            last_name=source_patient.last_name,
            middle_name=source_patient.middle_name,
            dob=source_patient.dob,
            gender=source_patient.gender,
            phone_primary=source_patient.phone_primary,
            phone_alternate=source_patient.phone_alternate,
            email=source_patient.email,
            city=source_patient.city,
            state=source_patient.state,
            country=source_patient.country,
            postal_code=source_patient.postal_code,
            address_line1=source_patient.address_line1,
            address_line2=source_patient.address_line2,
            known_allergies=source_patient.known_allergies,
            chronic_conditions=source_patient.chronic_conditions,
            clinical_notes=getattr(source_patient, "clinical_notes", None),
            emergency_contact_name=source_patient.emergency_contact_name,
            emergency_contact_relation=source_patient.emergency_contact_relation,
            emergency_contact_phone=source_patient.emergency_contact_phone,
            national_id_type=source_patient.national_id_type,
            national_id_number=source_patient.national_id_number,
            marital_status=source_patient.marital_status,
            preferred_language=source_patient.preferred_language,
            blood_group=getattr(source_patient, "blood_group", None),
            is_dnr=getattr(source_patient, "is_dnr", False),
            is_deceased=getattr(source_patient, "is_deceased", False),
            date_of_death=getattr(source_patient, "date_of_death", None),
        )
        target_patient.patient_code = generate_patient_code(db, ctx.tenant.id)
        db.add(target_patient)
        db.flush()  # Get ID without committing
        target_patient_id = target_patient.id

        # Import vitals (they are not associated with appointments/prescriptions/departments/doctors)
        from app.models.vital import Vital
        from app.services.patient_share_service import get_shared_patient_summary

        summary = get_shared_patient_summary(db=db, share_id=share_id, token=None)
        for vital_data in summary.vitals:
            if vital_data.get("recorded_at"):
                try:
                    # Parse ISO datetime string
                    recorded_at_str = vital_data["recorded_at"]
                    if recorded_at_str:
                        try:
                            recorded_at = datetime.fromisoformat(
                                recorded_at_str.replace("Z", "+00:00")
                            )
                        except (ValueError, AttributeError):
                            # Fallback to simple parsing
                            from datetime import date as date_type

                            recorded_at = datetime.combine(
                                date_type.fromisoformat(recorded_at_str.split("T")[0]),
                                datetime.min.time(),
                            )
                    else:
                        continue

                    vital = Vital(
                        patient_id=target_patient_id,
                        recorded_at=recorded_at,
                        systolic_bp=vital_data.get("systolic_bp"),
                        diastolic_bp=vital_data.get("diastolic_bp"),
                        heart_rate=vital_data.get("heart_rate"),
                        temperature_c=vital_data.get("temperature_c"),
                        respiratory_rate=vital_data.get("respiratory_rate"),
                        spo2=vital_data.get("spo2"),
                        weight_kg=vital_data.get("weight_kg"),
                        height_cm=vital_data.get("height_cm"),
                        notes=vital_data.get("notes"),
                    )
                    db.add(vital)
                except Exception as e:
                    logger.warning(f"Failed to import vital: {e}", exc_info=True)

        db.flush()

        # Create share link
        link = PatientShareLink(
            share_id=share.id,
            source_patient_id=share.patient_id,
            source_tenant_id=share.source_tenant_id,
            target_patient_id=target_patient_id,
            target_tenant_id=ctx.tenant.id,
        )
        db.add(link)
        db.commit()
        ensure_search_path(db, ctx.tenant.schema_name)

        response_dict = PatientShareResponse.model_validate(share).model_dump()
        response_dict["target_patient_id"] = target_patient_id
        return PatientShareResponse(**response_dict)

    except Exception as e:
        db.rollback()
        logger.error(
            f"Failed to import patient for share {share_id}: {e}", exc_info=True
        )
        raise HTTPException(
            status_code=500, detail=f"Failed to import patient: {str(e)}"
        )
    finally:
        try:
            conn.execute(text(f"SET search_path TO {original_path}"))
        except Exception:
            pass


@router.post(
    "/{share_id}/revoke",
    response_model=PatientShareResponse,
    tags=["patient-shares"],
)
def revoke_patient_share_endpoint(
    share_id: UUID,
    current_user: User = Depends(require_permission("sharing:create")),
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(get_tenant_context),
) -> PatientShareResponse:
    """
    Revoke a patient share.
    Only the source tenant can revoke shares.
    """
    share = db.query(PatientShare).filter(PatientShare.id == share_id).first()
    if not share:
        raise HTTPException(status_code=404, detail="Share not found")

    if share.source_tenant_id != ctx.tenant.id:
        raise HTTPException(
            status_code=403,
            detail="You can only revoke shares created by your hospital.",
        )

    try:
        revoked_share = revoke_share(
            db=db,
            share_id=share_id,
            revoked_by_user_id=ctx.user.id,
        )
        return PatientShareResponse.model_validate(revoked_share)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get(
    "",
    response_model=list[PatientShareResponse],
    tags=["patient-shares"],
)
def list_patient_shares(
    patient_id: Optional[UUID] = Query(None, description="Filter by patient ID"),
    current_user: User = Depends(require_permission("sharing:view")),
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(get_tenant_context),
) -> list[PatientShareResponse]:
    """
    List patient shares for the current tenant.
    Shows shares created by this tenant (source) and shares received (target).
    """
    query = db.query(PatientShare).filter(
        (PatientShare.source_tenant_id == ctx.tenant.id)
        | (PatientShare.target_tenant_id == ctx.tenant.id)
    )

    if patient_id:
        query = query.filter(PatientShare.patient_id == patient_id)

    shares = query.order_by(PatientShare.created_at.desc()).limit(100).all()

    results = []
    conn = db.connection()
    original_path = conn.execute(text("SHOW search_path")).scalar()

    for share in shares:
        share_dict = PatientShareResponse.model_validate(share).model_dump()

        # Get tenant names
        source_tenant = (
            db.query(Tenant).filter(Tenant.id == share.source_tenant_id).first()
        )
        if source_tenant:
            share_dict["source_tenant_name"] = source_tenant.name
        if share.target_tenant_id:
            target_tenant = (
                db.query(Tenant).filter(Tenant.id == share.target_tenant_id).first()
            )
            if target_tenant:
                share_dict["target_tenant_name"] = target_tenant.name

            # Get target patient ID from PatientShareLink if CREATE_RECORD mode
            if share.share_mode.value == "CREATE_RECORD":
                link = (
                    db.query(PatientShareLink)
                    .filter(
                        PatientShareLink.share_id == share.id,
                        PatientShareLink.target_tenant_id == share.target_tenant_id,
                    )
                    .first()
                )
                if link:
                    share_dict["target_patient_id"] = link.target_patient_id

        # Get created by user name
        created_by_user = (
            db.query(User).filter(User.id == share.created_by_user_id).first()
        )
        if created_by_user:
            share_dict["created_by_user_name"] = (
                f"{created_by_user.first_name} {created_by_user.last_name or ''}".strip()
                or created_by_user.email
            )

        # Get patient name and code from source tenant
        try:
            source_tenant = (
                db.query(Tenant).filter(Tenant.id == share.source_tenant_id).first()
            )
            if source_tenant:
                conn.execute(
                    text(f'SET search_path TO "{source_tenant.schema_name}", public')
                )
                patient = (
                    db.query(Patient).filter(Patient.id == share.patient_id).first()
                )
                if patient:
                    share_dict["patient_name"] = (
                        f"{patient.first_name} {patient.last_name or ''}".strip()
                    )
                    share_dict["patient_code"] = patient.patient_code
        except Exception as e:
            logger.warning(
                f"Failed to fetch patient info for share {share.id}: {e}", exc_info=True
            )

        results.append(PatientShareResponse(**share_dict))

    # Restore search_path
    try:
        conn.execute(text(f"SET search_path TO {original_path}"))
    except Exception:
        pass

    return results
