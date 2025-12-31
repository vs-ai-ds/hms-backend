# app/schemas/document.py
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel


class DocumentBase(BaseModel):
    patient_id: UUID
    file_name: str
    mime_type: str | None = None
    document_type: str | None = None
    storage_path: str


class DocumentCreate(BaseModel):
    patient_id: UUID


class DocumentResponse(DocumentBase):
    id: UUID
    uploaded_by_id: UUID | None = None
    created_at: datetime

    class Config:
        from_attributes = True
