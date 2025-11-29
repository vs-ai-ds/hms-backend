# app/schemas/prescription.py
from uuid import UUID
from datetime import datetime

from pydantic import BaseModel


class PrescriptionItemCreate(BaseModel):
    medicine_name: str
    dosage: str | None = None       # e.g. "500mg"
    frequency: str | None = None    # e.g. "every 8 hours"
    duration: str | None = None     # e.g. "5 days"
    instructions: str | None = None # e.g. "after food"


class PrescriptionCreate(BaseModel):
    patient_id: UUID
    appointment_id: UUID | None = None
    items: list[PrescriptionItemCreate]


class PrescriptionItemResponse(BaseModel):
    id: UUID
    medicine_name: str
    dosage: str | None
    frequency: str | None
    duration: str | None
    instructions: str | None

    class Config:
        from_attributes = True


class PrescriptionResponse(BaseModel):
    id: UUID
    patient_id: UUID
    doctor_id: UUID
    appointment_id: UUID | None
    created_at: datetime
    items: list[PrescriptionItemResponse]

    class Config:
        from_attributes = True