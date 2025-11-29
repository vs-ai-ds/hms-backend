from uuid import UUID
from datetime import datetime

from pydantic import BaseModel, EmailStr

from app.models.tenant_global import TenantStatus


class TenantRegisterRequest(BaseModel):
    name: str
    address: str | None = None
    contact_email: EmailStr
    contact_phone: str | None = None
    license_number: str


class TenantResponse(BaseModel):
    id: UUID
    name: str
    address: str | None
    contact_email: EmailStr
    contact_phone: str | None
    license_number: str
    status: TenantStatus
    schema_name: str
    created_at: datetime
    updated_at: datetime

    # DEV ONLY (for demo/hackathon): fields to show auto-created admin login.
    admin_email: EmailStr | None = None
    admin_temp_password: str | None = None

    class Config:
        from_attributes = True