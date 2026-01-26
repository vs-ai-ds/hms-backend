# app/schemas/admission.py
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel

from app.models.admission import AdmissionStatus


class AdmissionCreate(BaseModel):
    patient_id: UUID
    department_id: UUID | None = None  # Optional - Backend will auto-set for doctors
    primary_doctor_user_id: UUID | None = (
        None  # Optional - Backend will auto-set for doctors
    )
    admit_datetime: datetime
    notes: str | None = None


class AdmissionDischargeRequest(BaseModel):
    discharge_datetime: datetime
    discharge_summary: str  # Required for discharge


class AdmissionResponse(BaseModel):
    id: UUID
    patient_id: UUID
    department_id: UUID | None
    primary_doctor_user_id: UUID
    admit_datetime: datetime
    discharge_datetime: datetime | None
    discharge_summary: str | None
    status: AdmissionStatus
    notes: str | None
    created_at: datetime
    updated_at: datetime

    # Computed fields for frontend convenience
    patient_name: str | None = None
    patient_code: str | None = None
    doctor_name: str | None = None
    department: str | None = None

    class Config:
        from_attributes = True
