# app/schemas/department.py
import re
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, field_validator


class DepartmentBase(BaseModel):
    name: str
    description: str | None = None
    is_for_staff: bool = True
    is_for_patients: bool = True

    @field_validator("name")
    @classmethod
    def validate_name(cls, v: str) -> str:
        if not v:
            raise ValueError("Department name is required")
        # Trim whitespace first
        v_trimmed = v.strip()
        if len(v_trimmed) < 2:
            raise ValueError("Department name must be at least 2 characters long")
        if len(v_trimmed) > 100:
            raise ValueError("Department name must be at most 100 characters long")
        # Allow alphanumeric, spaces, dash, colon, underscore, and common punctuation
        if not re.match(r"^[A-Za-z0-9\s\-:_.,()]+$", v_trimmed):
            raise ValueError(
                "Department name can only contain alphanumeric characters, spaces, dash (-), colon (:), underscore (_), comma, period, and parentheses."
            )
        return v_trimmed


class DepartmentCreate(DepartmentBase):
    pass


class DepartmentUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    is_for_staff: bool | None = None
    is_for_patients: bool | None = None

    @field_validator("name")
    @classmethod
    def validate_name(cls, v: str | None) -> str | None:
        if v is None:
            return None
        if not v:
            raise ValueError("Department name is required")
        # Trim whitespace first
        v_trimmed = v.strip()
        if len(v_trimmed) < 2:
            raise ValueError("Department name must be at least 2 characters long")
        if len(v_trimmed) > 100:
            raise ValueError("Department name must be at most 100 characters long")
        # Allow alphanumeric, spaces, dash, colon, underscore, and common punctuation
        if not re.match(r"^[A-Za-z0-9\s\-:_.,()]+$", v_trimmed):
            raise ValueError(
                "Department name can only contain alphanumeric characters, spaces, dash (-), colon (:), underscore (_), comma, period, and parentheses."
            )
        return v_trimmed


class DepartmentResponse(DepartmentBase):
    id: UUID
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True
