# app/schemas/notification.py
from uuid import UUID
from datetime import datetime

from pydantic import BaseModel, EmailStr

from app.models.notification import NotificationChannel, NotificationStatus


class NotificationCreate(BaseModel):
    channel: NotificationChannel
    recipient: str
    subject: str | None = None
    message: str


class NotificationResponse(BaseModel):
    id: UUID
    channel: NotificationChannel
    recipient: str
    subject: str | None
    message: str
    status: NotificationStatus
    error_message: str | None
    triggered_by_id: UUID | None
    created_at: datetime

    class Config:
        from_attributes = True