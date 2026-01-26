import re
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, EmailStr, field_validator

from app.models.user import RoleName, UserStatus


class PermissionResponse(BaseModel):
    code: str


class RoleResponse(BaseModel):
    name: str
    permissions: list[PermissionResponse]


class UserBase(BaseModel):
    email: EmailStr
    first_name: str
    last_name: str
    phone: str | None = None
    department: str | None = (
        None  # Optional in response (for backward compatibility with existing users)
    )
    specialization: str | None = None


def validate_phone_digits(phone: str) -> bool:
    """Validate phone has 8-15 digits."""
    digits = re.sub(r"[^\d]", "", phone)
    return 8 <= len(digits) <= 15


class UserCreate(BaseModel):
    tenant_id: UUID | None = None
    email: EmailStr  # Required - email is mandatory for staff users
    first_name: str
    last_name: str
    phone: str | None = None
    password: str | None = (
        None  # Temporary password (will be generated if not provided)
    )
    department: str  # Made mandatory
    specialization: str | None = None
    roles: list[
        str
    ] = []  # Changed to list[str] to support both system and custom roles

    @field_validator("first_name")
    @classmethod
    def validate_first_name(cls, v: str) -> str:
        v = v.strip()
        if not v or len(v) < 1 or len(v) > 50:
            raise ValueError("First name must be 1-50 characters")
        # Allow Unicode letters, spaces, . ' - (using character class)
        if not re.match(r"^[a-zA-Z\u00C0-\u017F\s.'-]+$", v):
            raise ValueError(
                "First name can only contain letters, spaces, periods, apostrophes, and hyphens"
            )
        return v

    @field_validator("last_name")
    @classmethod
    def validate_last_name(cls, v: str) -> str:
        v = v.strip()
        if not v or len(v) < 1 or len(v) > 50:
            raise ValueError("Last name must be 1-50 characters")
        # Allow Unicode letters, spaces, . ' - (using character class)
        if not re.match(r"^[a-zA-Z\u00C0-\u017F\s.'-]+$", v):
            raise ValueError(
                "Last name can only contain letters, spaces, periods, apostrophes, and hyphens"
            )
        return v

    @field_validator("phone")
    @classmethod
    def validate_phone(cls, v: str | None) -> str | None:
        if v is None:
            return None
        v = v.strip()
        if not v:
            return None
        # Validate phone format and digits
        phone_regex = re.compile(r"^[0-9+\-\s]{5,15}$")
        if not phone_regex.match(v):
            raise ValueError(
                "Phone must be 5-15 characters and contain only digits, spaces, + or -"
            )
        if not validate_phone_digits(v):
            raise ValueError("Phone must be 8-15 digits (remove spaces or symbols)")
        return v

    @field_validator("department")
    @classmethod
    def validate_department(cls, v: str) -> str:
        v = v.strip()
        if not v or len(v) < 1:
            raise ValueError("Department is required")
        return v

    @field_validator("specialization")
    @classmethod
    def validate_specialization(cls, v: str | None) -> str | None:
        if v is None:
            return None
        v = v.strip()
        return v if v else None

    @field_validator("roles", mode="before")
    @classmethod
    def convert_roles_to_strings(cls, v):
        """Convert roles to strings (supports both RoleName enum and string names)."""
        if not v:
            return []
        if isinstance(v, list):
            result = []
            for role in v:
                if isinstance(role, RoleName):
                    result.append(role.value)
                elif isinstance(role, str):
                    result.append(role)
                else:
                    result.append(str(role))
            return result
        return v


class UserResponse(UserBase):
    id: UUID
    tenant_id: UUID | None = None
    status: UserStatus
    is_active: bool = True
    is_deleted: bool = False
    must_change_password: bool = False
    email_verified: bool = (
        False  # True when user has successfully logged in (email access confirmed)
    )
    roles: list[RoleResponse]  # Changed from list[RoleName] to list[RoleResponse]
    tenant_name: str | None = None  # Hospital name for frontend display
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True
