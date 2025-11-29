# app/schemas/patient.py
from uuid import UUID
from datetime import datetime, date

from pydantic import BaseModel, EmailStr

from app.models.patient import PatientType


class PatientBase(BaseModel):
    first_name: str
    last_name: str | None = None
    dob: date | None = None
    gender: str | None = None
    blood_group: str | None = None
    phone: str | None = None
    email: EmailStr | None = None
    address: str | None = None
    emergency_contact: str | None = None
    department: str | None = None
    patient_type: PatientType


class PatientCreate(PatientBase):
    """
    Used when creating a new patient.
    """
    pass


class PatientUpdate(BaseModel):
    first_name: str | None = None
    last_name: str | None = None
    dob: date | None = None
    gender: str | None = None
    blood_group: str | None = None
    phone: str | None = None
    email: EmailStr | None = None
    address: str | None = None
    emergency_contact: str | None = None
    department: str | None = None
    patient_type: PatientType | None = None


class PatientResponse(PatientBase):
    id: UUID
    created_by_id: UUID | None = None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True