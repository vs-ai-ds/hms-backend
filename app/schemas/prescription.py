# app/schemas/prescription.py
from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from app.models.prescription import PrescriptionStatus


class PrescriptionItemCreate(BaseModel):
    stock_item_id: UUID | None = None
    medicine_name: str
    dosage: str | None = None
    frequency: str | None = None
    duration: str | None = None
    instructions: str | None = None
    quantity: int | None = None


class PrescriptionCreate(BaseModel):
    patient_id: UUID
    appointment_id: UUID | None = None
    admission_id: UUID | None = None
    doctor_user_id: UUID | None = None  # Optional: for non-doctor users creating prescriptions
    department_id: UUID | None = None  # Optional: for walk-in appointments when user is not a doctor
    chief_complaint: str | None = None
    diagnosis: str | None = None
    items: list[PrescriptionItemCreate]


class PrescriptionUpdate(BaseModel):
    chief_complaint: str | None = None
    diagnosis: str | None = None
    items: list[PrescriptionItemCreate]


class PrescriptionItemResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    stock_item_id: UUID | None = None
    medicine_name: str
    dosage: str | None
    frequency: str | None
    duration: str | None
    instructions: str | None
    quantity: int | None = None


class PrescriptionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    prescription_code: str | None
    patient_id: UUID
    doctor_user_id: UUID
    appointment_id: UUID | None
    admission_id: UUID | None
    status: PrescriptionStatus
    chief_complaint: str | None = None
    diagnosis: str | None = None
    cancelled_reason: str | None = None
    cancelled_at: datetime | None = None
    created_at: datetime
    items: list[PrescriptionItemResponse]

    patient_name: str | None = None
    doctor_name: str | None = None
    visit_type: str | None = None
