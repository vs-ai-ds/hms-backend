# app/schemas/appointment.py
from uuid import UUID
from datetime import datetime

from pydantic import BaseModel

from app.models.appointment import AppointmentStatus


class AppointmentCreate(BaseModel):
    patient_id: UUID
    scheduled_at: datetime
    doctor_id: UUID | None = None
    notes: str | None = None


class AppointmentResponse(BaseModel):
    id: UUID
    patient_id: UUID
    doctor_id: UUID | None
    scheduled_at: datetime
    status: AppointmentStatus
    notes: str | None

    class Config:
        from_attributes = True