# app/schemas/sharing.py
from uuid import UUID
from datetime import datetime

from pydantic import BaseModel

from app.models.sharing import SharingStatus


class SharingRequestCreate(BaseModel):
    to_tenant_id: UUID
    patient_global_id: str
    reason: str | None = None


class SharingRequestResponse(BaseModel):
    id: UUID
    from_tenant_id: UUID
    to_tenant_id: UUID
    patient_global_id: str
    reason: str | None
    status: SharingStatus
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True