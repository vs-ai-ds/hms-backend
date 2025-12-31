# app/schemas/vital.py
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, field_validator


class VitalCreate(BaseModel):
    patient_id: UUID
    appointment_id: UUID | None = None
    admission_id: UUID | None = None
    systolic_bp: float | None = None
    diastolic_bp: float | None = None
    heart_rate: float | None = None
    temperature_c: float | None = None
    respiratory_rate: float | None = None
    spo2: float | None = None
    weight_kg: float | None = None
    height_cm: float | None = None
    notes: str | None = None
    recorded_at: datetime | None = None

    @field_validator("systolic_bp", "diastolic_bp")
    @classmethod
    def validate_bp(cls, v: float | None) -> float | None:
        if v is not None and (v < 0 or v > 300):
            raise ValueError("Blood pressure must be between 0 and 300")
        return v

    @field_validator("heart_rate")
    @classmethod
    def validate_heart_rate(cls, v: float | None) -> float | None:
        if v is not None and (v < 0 or v > 300):
            raise ValueError("Heart rate must be between 0 and 300")
        return v

    @field_validator("temperature_c")
    @classmethod
    def validate_temperature(cls, v: float | None) -> float | None:
        if v is not None and (v < 30 or v > 45):
            raise ValueError("Temperature must be between 30 and 45 degrees Celsius")
        return v

    @field_validator("spo2")
    @classmethod
    def validate_spo2(cls, v: float | None) -> float | None:
        if v is not None and (v < 0 or v > 100):
            raise ValueError("SpO2 must be between 0 and 100")
        return v


class VitalResponse(BaseModel):
    id: UUID
    patient_id: UUID
    appointment_id: UUID | None
    admission_id: UUID | None
    recorded_by_id: UUID | None
    systolic_bp: float | None
    diastolic_bp: float | None
    heart_rate: float | None
    temperature_c: float | None
    respiratory_rate: float | None
    spo2: float | None
    weight_kg: float | None
    height_cm: float | None
    notes: str | None
    recorded_at: datetime

    class Config:
        from_attributes = True
