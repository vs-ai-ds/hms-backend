# app/utils/prescription_pdf.py
"""
Utility to generate PDF from prescription data using reportlab.
Matches the format of PrescriptionPrintView component.
"""

from datetime import datetime
from io import BytesIO
from typing import Optional

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle


def generate_prescription_pdf(
    prescription_data: dict,
    tenant_name: str = "Hospital",
    tenant_address: Optional[str] = None,
    tenant_phone: Optional[str] = None,
    tenant_email: Optional[str] = None,
) -> BytesIO:
    """
    Generate a PDF from prescription data.
    Returns a BytesIO buffer containing the PDF.
    """
    buffer = BytesIO()

    # Use A4 size (210mm x 297mm) to match frontend print view
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        rightMargin=20 * mm,
        leftMargin=20 * mm,
        topMargin=15 * mm,
        bottomMargin=10 * mm,
    )

    # Container for the 'Flowable' objects
    elements = []

    # Styles
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "CustomTitle",
        parent=styles["Heading1"],
        fontSize=22,
        textColor=colors.black,
        spaceAfter=12,
        fontName="Helvetica-Bold",
    )

    heading_style = ParagraphStyle(
        "CustomHeading",
        parent=styles["Heading2"],
        fontSize=14,
        textColor=colors.black,
        spaceAfter=6,
        fontName="Helvetica-Bold",
    )

    normal_style = ParagraphStyle(
        "CustomNormal",
        parent=styles["Normal"],
        fontSize=10,
        textColor=colors.black,
        spaceAfter=6,
    )

    small_style = ParagraphStyle(
        "CustomSmall",
        parent=styles["Normal"],
        fontSize=8,
        textColor=colors.black,
        spaceAfter=4,
    )

    # Header Section
    doctor_name = prescription_data.get("doctor_name", "Doctor Name")
    if doctor_name and not doctor_name.startswith("Dr."):
        doctor_name = f"Dr. {doctor_name}"

    elements.append(Paragraph(doctor_name, title_style))

    # Tenant info in header
    tenant_info_parts = []
    if tenant_address:
        tenant_info_parts.append(tenant_address)
    if tenant_phone:
        tenant_info_parts.append(tenant_phone)
    if tenant_email:
        tenant_info_parts.append(tenant_email)

    if tenant_info_parts:
        elements.append(Paragraph(" • ".join(tenant_info_parts), small_style))

    elements.append(Spacer(1, 5 * mm))

    # Patient Information Section
    patient_name = f"{prescription_data.get('patient_name', 'N/A')}"
    patient_code = prescription_data.get("patient_code", "N/A")
    prescription_code = prescription_data.get("prescription_code", "N/A")

    patient_data = [
        ["Patient Name:", patient_name],
        ["Patient Code:", patient_code],
        ["Prescription Code:", prescription_code],
    ]

    # Add age, gender, weight if available
    if prescription_data.get("patient_age"):
        patient_data.append(["Age:", f"{prescription_data['patient_age']} years"])
    if prescription_data.get("patient_gender"):
        patient_data.append(["Gender:", prescription_data["patient_gender"]])
    if prescription_data.get("patient_weight"):
        patient_data.append(["Weight:", f"{prescription_data['patient_weight']} kg"])

    patient_table = Table(patient_data, colWidths=[50 * mm, 120 * mm])
    patient_table.setStyle(
        TableStyle(
            [
                ("FONTNAME", (0, 0), (-1, -1), "Helvetica"),
                ("FONTSIZE", (0, 0), (-1, -1), 10),
                ("TEXTCOLOR", (0, 0), (0, -1), colors.black),
                ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
                ("ALIGN", (0, 0), (-1, -1), "LEFT"),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )
    elements.append(patient_table)
    elements.append(Spacer(1, 5 * mm))

    # Date
    prescription_date = prescription_data.get("created_at")
    if prescription_date:
        if isinstance(prescription_date, str):
            try:
                date_obj = datetime.fromisoformat(
                    prescription_date.replace("Z", "+00:00")
                )
                date_str = date_obj.strftime("%d/%m/%Y")
            except:
                date_str = (
                    prescription_date[:10]
                    if len(prescription_date) >= 10
                    else prescription_date
                )
        else:
            date_str = prescription_date.strftime("%d/%m/%Y")
        elements.append(Paragraph(f"Date: {date_str}", normal_style))
        elements.append(Spacer(1, 3 * mm))

    # Chief Complaint
    if prescription_data.get("chief_complaint"):
        elements.append(Paragraph("Chief Complaint:", heading_style))
        elements.append(Paragraph(prescription_data["chief_complaint"], normal_style))
        elements.append(Spacer(1, 3 * mm))

    # Diagnosis
    if prescription_data.get("diagnosis"):
        elements.append(Paragraph("Diagnosis:", heading_style))
        elements.append(Paragraph(prescription_data["diagnosis"], normal_style))
        elements.append(Spacer(1, 3 * mm))

    # Medicines/Items
    items = prescription_data.get("items", [])
    if items:
        elements.append(Paragraph("Medicines:", heading_style))
        elements.append(Spacer(1, 2 * mm))

        # Table header
        medicine_data = [
            ["Medicine", "Dosage", "Frequency", "Duration", "Instructions"]
        ]

        for item in items:
            medicine_name = item.get("medicine_name", "N/A")
            dosage = item.get("dosage") or "-"
            frequency = item.get("frequency") or "-"
            duration = item.get("duration") or "-"
            instructions = item.get("instructions") or "-"

            medicine_data.append(
                [
                    medicine_name,
                    dosage,
                    frequency,
                    duration,
                    instructions,
                ]
            )

        medicine_table = Table(
            medicine_data, colWidths=[60 * mm, 30 * mm, 30 * mm, 30 * mm, 40 * mm]
        )
        medicine_table.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), colors.grey),
                    ("TEXTCOLOR", (0, 0), (-1, 0), colors.whitesmoke),
                    ("ALIGN", (0, 0), (-1, -1), "LEFT"),
                    ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                    ("FONTSIZE", (0, 0), (-1, 0), 10),
                    ("BOTTOMPADDING", (0, 0), (-1, 0), 8),
                    ("BACKGROUND", (0, 1), (-1, -1), colors.beige),
                    ("TEXTCOLOR", (0, 1), (-1, -1), colors.black),
                    ("FONTNAME", (0, 1), (-1, -1), "Helvetica"),
                    ("FONTSIZE", (0, 1), (-1, -1), 9),
                    ("GRID", (0, 0), (-1, -1), 1, colors.black),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("TOPPADDING", (0, 1), (-1, -1), 4),
                    ("BOTTOMPADDING", (0, 1), (-1, -1), 4),
                ]
            )
        )
        elements.append(medicine_table)
        elements.append(Spacer(1, 5 * mm))

    # Footer
    elements.append(Spacer(1, 10 * mm))
    elements.append(Paragraph("Signature: _________________", normal_style))
    elements.append(Spacer(1, 2 * mm))
    elements.append(Paragraph(doctor_name, normal_style))

    # Build PDF
    doc.build(elements)
    buffer.seek(0)
    return buffer
