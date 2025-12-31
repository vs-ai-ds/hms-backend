# app/schemas/appointment.py
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel

from app.models.appointment import AppointmentStatus
from app.models.prescription import PrescriptionStatus


class AppointmentCreate(BaseModel):
    patient_id: UUID
    department_id: UUID | None = None  # Optional - Backend will auto-set for doctors
    doctor_user_id: UUID | None = None  # Optional - Backend will auto-set for doctors
    scheduled_at: datetime
    notes: str | None = None


class AppointmentResponse(BaseModel):
    id: UUID
    patient_id: UUID
    department_id: UUID | None
    doctor_user_id: UUID
    scheduled_at: datetime
    status: AppointmentStatus
    notes: str | None
    created_at: datetime

    # OPD Lifecycle fields
    checked_in_at: datetime | None = None
    consultation_started_at: datetime | None = None
    completed_at: datetime | None = None
    no_show_at: datetime | None = None
    cancelled_reason: str | None = None
    cancelled_note: str | None = None
    linked_ipd_admission_id: UUID | None = None

    # Computed fields for frontend convenience
    patient_name: str | None = None
    patient_code: str | None = None
    doctor_name: str | None = None
    department: str | None = None
    has_prescription: bool = False  # Derived: prescription exists for this appointment
    prescription_count: int = 0  # Number of prescriptions linked to this appointment
    prescription_status: PrescriptionStatus | None = None  # Latest prescription status if any

    class Config:
        from_attributes = True


class AppointmentListResponse(BaseModel):
    items: list[AppointmentResponse]
    total: int
    page: int
    page_size: int
