# app/schemas/role.py
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel


class RoleBase(BaseModel):
    name: str
    description: str | None = None


class RoleCreate(RoleBase):
    permission_codes: list[str] = []
    template_role_id: UUID | None = (
        None  # Optional: create role based on existing role template
    )


class RoleUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    permission_codes: list[str] | None = None


class PermissionResponse(BaseModel):
    code: str
    name: str | None = None
    category: str | None = None


class RoleResponse(RoleBase):
    id: UUID
    is_system: bool
    system_key: str | None = None
    is_active: bool = True
    permissions: list[PermissionResponse] = []
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True
