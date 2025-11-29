# app/schemas/vital.py
from uuid import UUID
from datetime import datetime

from pydantic import BaseModel


class VitalCreate(BaseModel):
    patient_id: UUID
    systolic_bp: float | None = None
    diastolic_bp: float | None = None
    heart_rate: float | None = None
    temperature_c: float | None = None
    respiratory_rate: float | None = None
    spo2: float | None = None
    notes: str | None = None


class VitalResponse(BaseModel):
    id: UUID
    patient_id: UUID
    recorded_by_id: UUID | None
    systolic_bp: float | None
    diastolic_bp: float | None
    heart_rate: float | None
    temperature_c: float | None
    respiratory_rate: float | None
    spo2: float | None
    notes: str | None
    recorded_at: datetime

    class Config:
        from_attributes = True