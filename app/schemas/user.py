from uuid import UUID
from datetime import datetime

from pydantic import BaseModel, EmailStr

from app.models.user import UserStatus, RoleName


class UserBase(BaseModel):
    email: EmailStr
    first_name: str
    last_name: str
    phone: str | None = None
    department: str | None = None
    specialization: str | None = None


class UserCreate(BaseModel):
    tenant_id: UUID | None = None
    email: EmailStr
    first_name: str
    last_name: str
    phone: str | None = None
    password: str
    department: str | None = None
    specialization: str | None = None
    roles: list[RoleName] = []


class UserResponse(UserBase):
    id: UUID
    tenant_id: UUID | None = None
    status: UserStatus
    roles: list[RoleName]
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True