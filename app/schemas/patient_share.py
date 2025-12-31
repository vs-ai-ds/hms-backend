# app/schemas/patient_share.py
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field

from app.models.patient_share import ShareMode, ShareStatus


class PatientShareCreate(BaseModel):
    target_tenant_id: UUID = Field(
        ...,
        description="Target tenant ID (required - must be an active hospital)",
    )
    share_mode: ShareMode = Field(..., description="Share mode: READ_ONLY_LINK or CREATE_RECORD")
    validity_days: int = Field(1, ge=1, le=30, description="Validity in days (1-30)")
    note: str | None = Field(None, max_length=500, description="Optional note")
    consent_confirmed: bool = Field(..., description="User confirmed patient consent")


class PatientShareResponse(BaseModel):
    id: UUID
    source_tenant_id: UUID
    target_tenant_id: UUID | None
    patient_id: UUID
    target_patient_id: UUID | None = None  # Patient ID in target tenant (for CREATE_RECORD mode)
    share_mode: ShareMode
    token: str
    expires_at: datetime | None
    status: ShareStatus
    created_at: datetime
    revoked_at: datetime | None = None
    note: str | None = None
    source_tenant_name: str | None = None
    target_tenant_name: str | None = None
    created_by_user_name: str | None = None
    patient_name: str | None = None
    patient_code: str | None = None

    class Config:
        from_attributes = True


class SharedPatientSummary(BaseModel):
    """Summary data exposed in shared patient view - includes all shareable records"""

    first_name: str
    last_name: str | None
    middle_name: str | None = None
    patient_code: str | None
    dob: str | None
    gender: str | None
    blood_group: str | None
    phone_primary: str | None
    phone_alternate: str | None = None
    email: str | None
    city: str | None
    state: str | None = None
    country: str | None = None
    postal_code: str | None = None
    address_line1: str | None = None
    address_line2: str | None = None
    known_allergies: str | None
    chronic_conditions: str | None
    clinical_notes: str | None = None
    emergency_contact_name: str | None = None
    emergency_contact_relation: str | None = None
    emergency_contact_phone: str | None = None
    national_id_type: str | None = None
    national_id_number: str | None = None
    marital_status: str | None = None
    preferred_language: str | None = None
    is_dnr: bool = False
    is_deceased: bool = False
    date_of_death: str | None = None
    
    # Related records - full data for import
    vitals: list[dict] = Field(default_factory=list)  # All vitals
    appointments: list[dict] = Field(default_factory=list)  # All appointments
    prescriptions: list[dict] = Field(default_factory=list)  # All prescriptions with items
    admissions: list[dict] = Field(default_factory=list)  # All admissions
    
    # Legacy fields for backward compatibility
    last_visits: list[dict] = Field(default_factory=list)
    last_prescriptions: list[dict] = Field(default_factory=list)
    recent_vitals: list[dict] = Field(default_factory=list)


class TenantOption(BaseModel):
    """Tenant option for dropdown"""

    id: UUID
    name: str
    contact_email: str

    class Config:
        from_attributes = True
