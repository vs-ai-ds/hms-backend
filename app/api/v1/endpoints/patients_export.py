# app/api/v1/endpoints/patients_export.py
import csv
from datetime import date
from io import StringIO
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.tenant_context import TenantContext, get_tenant_context
from app.models.patient import Patient
from app.services.user_role_service import get_user_role_names

router = APIRouter()


@router.get("/export/csv")
def export_patients_csv(
    search: Optional[str] = Query(None),
    department_id: Optional[UUID] = Query(None),
    doctor_user_id: Optional[UUID] = Query(None),
    patient_type: Optional[str] = Query(None),
    visit_type: Optional[str] = Query(None),
    date_from: Optional[date] = Query(None),
    date_to: Optional[date] = Query(None),
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(get_tenant_context),
):
    """
    Export patients to CSV.
    """
    # Build query (same logic as list_patients)
    query = db.query(Patient)

    # Apply ABAC filters
    user_roles = get_user_role_names(db, ctx.user, tenant_schema_name=ctx.tenant.schema_name)
    user_department = ctx.user.department
    is_hospital_admin = "HOSPITAL_ADMIN" in user_roles

    # ABAC: Filter by department via appointments/admissions (department is per-visit, not per-patient)
    if user_department and not is_hospital_admin and ("DOCTOR" in user_roles or "NURSE" in user_roles):
        from sqlalchemy import or_ as sa_or_

        from app.models.admission import Admission
        from app.models.appointment import Appointment
        from app.models.department import Department

        dept = db.query(Department).filter(Department.name == user_department).first()
        if dept:
            # Filter patients with appointments or admissions in this department
            appointment_patient_ids = (
                db.query(Appointment.patient_id).filter(Appointment.department_id == dept.id).distinct().subquery()
            )
            admission_patient_ids = (
                db.query(Admission.patient_id).filter(Admission.department_id == dept.id).distinct().subquery()
            )
            query = query.filter(
                sa_or_(
                    Patient.id.in_(db.query(appointment_patient_ids.c.patient_id)),
                    Patient.id.in_(db.query(admission_patient_ids.c.patient_id)),
                )
            )

    # Apply search
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

    # Apply filters
    if department_id:
        # Filter by department via appointments/admissions
        from sqlalchemy import or_ as sa_or_

        from app.models.admission import Admission
        from app.models.appointment import Appointment

        appointment_patient_ids = (
            db.query(Appointment.patient_id).filter(Appointment.department_id == department_id).distinct().subquery()
        )
        admission_patient_ids = (
            db.query(Admission.patient_id).filter(Admission.department_id == department_id).distinct().subquery()
        )
        query = query.filter(
            sa_or_(
                Patient.id.in_(db.query(appointment_patient_ids.c.patient_id)),
                Patient.id.in_(db.query(admission_patient_ids.c.patient_id)),
            )
        )
    if doctor_user_id:
        from app.models.appointment import Appointment

        patient_ids_with_appointments = (
            db.query(Appointment.patient_id).filter(Appointment.doctor_user_id == doctor_user_id).distinct().subquery()
        )
        query = query.filter(Patient.id.in_(db.query(patient_ids_with_appointments.c.patient_id)))
    if patient_type:
        query = query.filter(Patient.patient_type == patient_type)
    if date_from:
        query = query.filter(func.date(Patient.last_visited_at) >= date_from)
    if date_to:
        query = query.filter(func.date(Patient.last_visited_at) <= date_to)

    patients = query.order_by(Patient.created_at.desc()).all()

    # Generate CSV
    output = StringIO()
    writer = csv.writer(output)

    # Write header with upcoming appointment info
    writer.writerow(
        [
            "Patient Code",
            "First Name",
            "Last Name",
            "DOB",
            "Gender",
            "Phone",
            "Email",
            "City",
            "Patient Type",
            "Last Visited",
            "Upcoming Appointment Date",
            "Upcoming Appointment Doctor",
            "Upcoming Appointment Department",
            "Created At",
        ]
    )

    # Write data
    from datetime import datetime, timezone

    from sqlalchemy.orm import joinedload

    from app.models.appointment import Appointment, AppointmentStatus
    from app.services.patient_type_service import get_patient_type

    for patient in patients:
        # Get derived patient type
        patient_type = get_patient_type(db, patient.id)

        # Get upcoming appointment (next scheduled appointment)
        upcoming_appt = (
            db.query(Appointment)
            .options(joinedload(Appointment.doctor), joinedload(Appointment.department))
            .filter(
                Appointment.patient_id == patient.id,
                Appointment.status == AppointmentStatus.SCHEDULED,
                Appointment.scheduled_at >= datetime.now(timezone.utc),
            )
            .order_by(Appointment.scheduled_at.asc())
            .first()
        )

        upcoming_date = (
            upcoming_appt.scheduled_at.strftime("%Y-%m-%d %H:%M")
            if upcoming_appt and upcoming_appt.scheduled_at
            else ""
        )
        upcoming_doctor = (
            f"{upcoming_appt.doctor.first_name} {upcoming_appt.doctor.last_name}".strip()
            if upcoming_appt and upcoming_appt.doctor
            else ""
        )
        upcoming_dept = upcoming_appt.department.name if upcoming_appt and upcoming_appt.department else ""

        writer.writerow(
            [
                patient.patient_code or "",
                patient.first_name,
                patient.last_name or "",
                patient.dob.isoformat() if patient.dob else "",
                patient.gender or "",
                patient.phone_primary or "",
                patient.email or "",
                patient.city or "",
                patient_type.value,
                patient.last_visited_at.isoformat() if patient.last_visited_at else "",
                upcoming_date,
                upcoming_doctor,
                upcoming_dept,
                patient.created_at.isoformat() if patient.created_at else "",
            ]
        )

    output.seek(0)

    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename=patients_{ctx.tenant.name.replace(' ', '_')}.csv"},
    )


@router.get("/export/pdf")
def export_patients_pdf(
    search: Optional[str] = Query(None),
    department_id: Optional[UUID] = Query(None),
    doctor_user_id: Optional[UUID] = Query(None),
    patient_type: Optional[str] = Query(None),
    visit_type: Optional[str] = Query(None),
    date_from: Optional[date] = Query(None),
    date_to: Optional[date] = Query(None),
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(get_tenant_context),
):
    """
    Export patients to PDF.
    """
    # Build query (same logic as list_patients)
    query = db.query(Patient)

    # Apply ABAC filters
    user_roles = get_user_role_names(db, ctx.user, tenant_schema_name=ctx.tenant.schema_name)
    user_department = ctx.user.department
    is_hospital_admin = "HOSPITAL_ADMIN" in user_roles

    # ABAC: Filter by department via appointments/admissions (department is per-visit, not per-patient)
    if user_department and not is_hospital_admin and ("DOCTOR" in user_roles or "NURSE" in user_roles):
        from sqlalchemy import or_ as sa_or_

        from app.models.admission import Admission
        from app.models.appointment import Appointment
        from app.models.department import Department

        dept = db.query(Department).filter(Department.name == user_department).first()
        if dept:
            # Filter patients with appointments or admissions in this department
            appointment_patient_ids = (
                db.query(Appointment.patient_id).filter(Appointment.department_id == dept.id).distinct().subquery()
            )
            admission_patient_ids = (
                db.query(Admission.patient_id).filter(Admission.department_id == dept.id).distinct().subquery()
            )
            query = query.filter(
                sa_or_(
                    Patient.id.in_(db.query(appointment_patient_ids.c.patient_id)),
                    Patient.id.in_(db.query(admission_patient_ids.c.patient_id)),
                )
            )

    # Apply search and filters (same as CSV)
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
    if department_id:
        # Filter by department via appointments/admissions
        from sqlalchemy import or_ as sa_or_

        from app.models.admission import Admission
        from app.models.appointment import Appointment

        appointment_patient_ids = (
            db.query(Appointment.patient_id).filter(Appointment.department_id == department_id).distinct().subquery()
        )
        admission_patient_ids = (
            db.query(Admission.patient_id).filter(Admission.department_id == department_id).distinct().subquery()
        )
        query = query.filter(
            sa_or_(
                Patient.id.in_(db.query(appointment_patient_ids.c.patient_id)),
                Patient.id.in_(db.query(admission_patient_ids.c.patient_id)),
            )
        )
    if doctor_user_id:
        from app.models.appointment import Appointment

        patient_ids_with_appointments = (
            db.query(Appointment.patient_id).filter(Appointment.doctor_user_id == doctor_user_id).distinct().subquery()
        )
        query = query.filter(Patient.id.in_(db.query(patient_ids_with_appointments.c.patient_id)))
    if patient_type:
        query = query.filter(Patient.patient_type == patient_type)
    if visit_type:
        # Filter by visit type - patients with OPD appointments or IPD admissions
        from app.models.admission import Admission
        from app.models.appointment import Appointment

        if visit_type.upper() == "OPD":
            appointment_patient_ids = db.query(Appointment.patient_id).distinct().subquery()
            query = query.filter(Patient.id.in_(db.query(appointment_patient_ids.c.patient_id)))
        elif visit_type.upper() == "IPD":
            admission_patient_ids = db.query(Admission.patient_id).distinct().subquery()
            query = query.filter(Patient.id.in_(db.query(admission_patient_ids.c.patient_id)))
    if date_from:
        query = query.filter(func.date(Patient.last_visited_at) >= date_from)
    if date_to:
        query = query.filter(func.date(Patient.last_visited_at) <= date_to)

    patients = query.order_by(Patient.created_at.desc()).all()

    # Generate PDF
    from io import BytesIO

    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=letter)
    elements = []

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "CustomTitle",
        parent=styles["Heading1"],
        fontSize=16,
        textColor=colors.HexColor("#0950AC"),
        spaceAfter=30,
    )

    # Title
    elements.append(Paragraph(f"Patients Report - {ctx.tenant.name}", title_style))
    elements.append(Spacer(1, 0.2 * inch))

    # Table data - limited columns for PDF width
    data = [["Code", "Name", "Phone", "Type", "Upcoming Appt", "Created"]]

    from datetime import datetime, timezone

    from app.models.appointment import Appointment, AppointmentStatus
    from app.services.patient_type_service import get_patient_type

    for patient in patients:
        # Get derived patient type
        patient_type = get_patient_type(db, patient.id)

        # Get upcoming appointment
        from sqlalchemy.orm import joinedload

        upcoming_appt = (
            db.query(Appointment)
            .options(joinedload(Appointment.doctor), joinedload(Appointment.department))
            .filter(
                Appointment.patient_id == patient.id,
                Appointment.status == AppointmentStatus.SCHEDULED,
                Appointment.scheduled_at >= datetime.now(timezone.utc),
            )
            .order_by(Appointment.scheduled_at.asc())
            .first()
        )

        upcoming_str = (
            upcoming_appt.scheduled_at.strftime("%Y-%m-%d") if upcoming_appt and upcoming_appt.scheduled_at else ""
        )

        name = f"{patient.first_name} {patient.last_name or ''}".strip()
        data.append(
            [
                patient.patient_code or "",
                name,
                patient.phone_primary or "",
                patient_type.value,
                upcoming_str,
                patient.created_at.strftime("%Y-%m-%d") if patient.created_at else "",
            ]
        )

    # Create table - adjust column widths for new columns
    table = Table(data, colWidths=[1 * inch, 2 * inch, 1 * inch, 0.7 * inch, 1 * inch, 0.8 * inch])
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#0950AC")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.whitesmoke),
                ("ALIGN", (0, 0), (-1, -1), "LEFT"),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, 0), 10),
                ("BOTTOMPADDING", (0, 0), (-1, 0), 12),
                ("BACKGROUND", (0, 1), (-1, -1), colors.beige),
                ("GRID", (0, 0), (-1, -1), 1, colors.black),
                ("FONTSIZE", (0, 1), (-1, -1), 9),
            ]
        )
    )

    elements.append(table)
    doc.build(elements)

    buffer.seek(0)

    return StreamingResponse(
        buffer,
        media_type="application/pdf",
        headers={"Content-Disposition": f"attachment; filename=patients_{ctx.tenant.name.replace(' ', '_')}.pdf"},
    )
