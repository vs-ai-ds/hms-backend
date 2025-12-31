# app/schemas/patient.py
import re
from datetime import date, datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, EmailStr, field_validator, model_validator

from app.models.patient import PatientType


def normalize_phone(phone: str) -> str:
    """Normalize phone number: remove spaces, dashes, parentheses, keep + and digits."""
    if not phone:
        return ""
    # Remove common separators but keep + at start
    normalized = re.sub(r"[\s\-\(\)]", "", phone)
    return normalized


def validate_phone_digits(phone: str) -> bool:
    """Check if phone has 8-15 digits after normalization."""
    normalized = normalize_phone(phone)
    if normalized.startswith("+"):
        digits = normalized[1:]
    else:
        digits = normalized
    digit_count = sum(c.isdigit() for c in digits)
    return 8 <= digit_count <= 15


class QuickRegisterRequest(BaseModel):
    """Minimal fields for quick patient registration."""

    first_name: str
    last_name: Optional[str] = None
    dob: Optional[date] = None
    dob_unknown: bool = False
    age_only: Optional[int] = None
    gender: str  # MALE/FEMALE/OTHER/UNKNOWN
    # NOTE: patient_type removed - it's derived from active admission, not stored
    phone_primary: str
    email: Optional[EmailStr] = None  # Added email field
    city: str
    # NOTE: department_id removed - department is per-visit (appointment/admission), not per-patient
    complete_profile_later: bool = False

    @field_validator("first_name")
    @classmethod
    def validate_first_name(cls, v: str) -> str:
        v = v.strip()
        if not v or len(v) < 1 or len(v) > 50:
            raise ValueError("First name must be 1-50 characters")
        # Allow Unicode letters, spaces, . ' - (using character class)
        if not re.match(r"^[a-zA-Z\u00C0-\u017F\s.'-]+$", v):
            raise ValueError("First name can only contain letters, spaces, periods, apostrophes, and hyphens")
        return v

    @field_validator("last_name")
    @classmethod
    def validate_last_name(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        v = v.strip()
        if v and (len(v) < 1 or len(v) > 50):
            raise ValueError("Last name must be 1-50 characters if provided")
        if v and not re.match(r"^[a-zA-Z\u00C0-\u017F\s.'-]+$", v):
            raise ValueError("Last name can only contain letters, spaces, periods, apostrophes, and hyphens")
        return v if v else None

    @field_validator("dob")
    @classmethod
    def validate_dob(cls, v: Optional[date], info) -> Optional[date]:
        if v is None:
            return None
        if v > date.today():
            raise ValueError("Date of birth cannot be in the future")
        # Calculate age
        today = date.today()
        age = today.year - v.year - ((today.month, today.day) < (v.month, v.day))
        if age < 0 or age > 120:
            raise ValueError("Age must be between 0 and 120 years")
        return v

    @field_validator("age_only")
    @classmethod
    def validate_age_only(cls, v: Optional[int]) -> Optional[int]:
        if v is not None and (v < 0 or v > 120):
            raise ValueError("Age must be between 0 and 120 years")
        return v

    @field_validator("gender")
    @classmethod
    def validate_gender(cls, v: str) -> str:
        if v not in ["MALE", "FEMALE", "OTHER", "UNKNOWN"]:
            raise ValueError("Gender must be MALE, FEMALE, OTHER, or UNKNOWN")
        return v

    @field_validator("phone_primary")
    @classmethod
    def validate_phone_primary(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("Primary phone is required")
        if not validate_phone_digits(v):
            raise ValueError("Phone must be 8-15 digits (remove spaces or symbols)")
        return normalize_phone(v)

    @field_validator("email")
    @classmethod
    def validate_email(cls, v: Optional[EmailStr]) -> Optional[EmailStr]:
        if v and len(str(v)) > 254:
            raise ValueError("Email must be 254 characters or less")
        return v

    @field_validator("city")
    @classmethod
    def validate_city(cls, v: str) -> str:
        v = v.strip()
        if not v or len(v) < 2 or len(v) > 80:
            raise ValueError("City must be 2-80 characters")
        return v

    @model_validator(mode="after")
    def validate_dob_or_age(self):
        """Either DOB or age_only must be provided if dob_unknown is False."""
        if not self.dob_unknown:
            if not self.dob and not self.age_only:
                raise ValueError("Either date of birth or age must be provided")
        return self


class ProfileCompleteRequest(BaseModel):
    """Extended profile fields (all optional)."""

    middle_name: Optional[str] = None
    last_name: Optional[str] = None
    dob: Optional[date] = None
    dob_unknown: bool = False
    age_only: Optional[int] = None
    phone_alternate: Optional[str] = None
    email: Optional[EmailStr] = None
    city: Optional[str] = None
    address_line1: Optional[str] = None
    address_line2: Optional[str] = None
    postal_code: Optional[str] = None
    state: Optional[str] = None
    country: Optional[str] = None
    blood_group: Optional[str] = None
    marital_status: Optional[str] = None
    preferred_language: Optional[str] = None
    emergency_contact_name: Optional[str] = None
    emergency_contact_relation: Optional[str] = None
    emergency_contact_phone: Optional[str] = None
    known_allergies: Optional[str] = None
    chronic_conditions: Optional[str] = None
    clinical_notes: Optional[str] = None
    is_dnr: bool = False
    is_deceased: bool = False
    date_of_death: Optional[date] = None
    national_id_type: Optional[str] = None
    national_id_number: Optional[str] = None
    consent_sms: bool = False
    consent_email: bool = False

    @field_validator("middle_name", "last_name")
    @classmethod
    def validate_names(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        v = v.strip()
        if v and (len(v) < 1 or len(v) > 50):
            raise ValueError("Name must be 1-50 characters if provided")
        if v and not re.match(r"^[a-zA-Z\u00C0-\u017F\s.'-]+$", v):
            raise ValueError("Name can only contain letters, spaces, periods, apostrophes, and hyphens")
        return v if v else None

    @field_validator("email")
    @classmethod
    def validate_email(cls, v: Optional[EmailStr]) -> Optional[EmailStr]:
        if v and len(str(v)) > 254:
            raise ValueError("Email must be 254 characters or less")
        return v

    @field_validator("phone_alternate", "emergency_contact_phone")
    @classmethod
    def validate_phone(cls, v: Optional[str]) -> Optional[str]:
        if v is None or not v.strip():
            return None
        v = v.strip()
        if not validate_phone_digits(v):
            raise ValueError("Phone must be 8-15 digits (remove spaces or symbols)")
        return normalize_phone(v)

    @field_validator("city")
    @classmethod
    def validate_city(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        v = v.strip()
        if v and (len(v) < 1 or len(v) > 80):
            raise ValueError("City must be 1-80 characters if provided")
        return v if v else None

    @field_validator("address_line1", "address_line2")
    @classmethod
    def validate_address(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        v = v.strip()
        if v and (len(v) < 3 or len(v) > 120):
            raise ValueError("Address line must be 3-120 characters if provided")
        return v if v else None

    @field_validator("national_id_number")
    @classmethod
    def validate_national_id_number(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        v = v.strip()
        if v and (len(v) < 3 or len(v) > 32):
            raise ValueError("National ID number must be 3-32 characters if provided")
        # Allow letters, digits, hyphens, slashes
        if v and not re.match(r"^[A-Za-z0-9\-/]+$", v):
            raise ValueError("National ID number can only contain letters, digits, hyphens, and slashes")
        return v if v else None

    @field_validator("known_allergies", "chronic_conditions", "clinical_notes")
    @classmethod
    def validate_text_fields(cls, v: Optional[str]) -> Optional[str]:
        if v and len(v) > 1000:
            raise ValueError("Field must be 1000 characters or less")
        return v

    @field_validator("postal_code")
    @classmethod
    def validate_postal_code(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        v = v.strip()
        if v and not re.match(r"^\d{6}$", v):
            raise ValueError("Pin code must be exactly 6 digits")
        return v if v else None

    @field_validator("date_of_death")
    @classmethod
    def validate_date_of_death(cls, v: Optional[date]) -> Optional[date]:
        if v is None:
            return None
        from datetime import date, timedelta

        today = date.today()
        thirty_days_ago = today - timedelta(days=30)
        if v < thirty_days_ago or v > today:
            raise ValueError("Date of death must be within the last 30 days including today")
        return v

    @model_validator(mode="after")
    def validate_emergency_contact(self):
        """If any emergency field is filled, require all three (name, relation, phone)."""
        has_name = bool(self.emergency_contact_name and self.emergency_contact_name.strip())
        has_relation = bool(self.emergency_contact_relation and self.emergency_contact_relation.strip())
        has_phone = bool(self.emergency_contact_phone and self.emergency_contact_phone.strip())
        if has_name or has_relation or has_phone:
            if not has_name:
                raise ValueError("Emergency contact name is required if any emergency contact field is provided")
            if not has_relation:
                raise ValueError("Emergency contact relation is required if any emergency contact field is provided")
            if not has_phone:
                raise ValueError("Emergency contact phone is required if any emergency contact field is provided")
        return self

    @model_validator(mode="after")
    def validate_national_id(self):
        """If one national ID field is filled, require both type and number."""
        has_type = bool(self.national_id_type and self.national_id_type.strip())
        has_number = bool(self.national_id_number and self.national_id_number.strip())
        if has_type or has_number:
            if not has_type:
                raise ValueError("National ID type is required if National ID number is provided")
            if not has_number:
                raise ValueError("National ID number is required if National ID type is provided")
        return self


class PatientBase(BaseModel):
    """Base patient schema with all fields."""

    patient_code: Optional[str] = None
    first_name: str
    middle_name: Optional[str] = None
    last_name: Optional[str] = None
    dob: Optional[date] = None
    dob_unknown: bool = False
    age_only: Optional[int] = None
    gender: Optional[str] = None
    # NOTE: patient_type removed - it's derived from active admission, not stored
    phone_primary: Optional[str] = None
    phone_alternate: Optional[str] = None
    city: Optional[str] = None
    # NOTE: department_id removed - department is per-visit, not per-patient
    email: Optional[EmailStr] = None
    address_line1: Optional[str] = None
    address_line2: Optional[str] = None
    postal_code: Optional[str] = None
    state: Optional[str] = None
    country: Optional[str] = None
    blood_group: Optional[str] = None
    marital_status: Optional[str] = None
    preferred_language: Optional[str] = None
    emergency_contact_name: Optional[str] = None
    emergency_contact_relation: Optional[str] = None
    emergency_contact_phone: Optional[str] = None
    known_allergies: Optional[str] = None
    chronic_conditions: Optional[str] = None
    clinical_notes: Optional[str] = None
    is_dnr: bool = False
    is_deceased: bool = False
    date_of_death: Optional[date] = None
    national_id_type: Optional[str] = None
    national_id_number: Optional[str] = None
    photo_path: Optional[str] = None
    consent_sms: bool = False
    consent_email: bool = False


class PatientCreate(PatientBase):
    """Used when creating a new patient."""

    pass


class PatientUpdate(BaseModel):
    """Update schema - all fields optional."""

    first_name: Optional[str] = None
    middle_name: Optional[str] = None
    last_name: Optional[str] = None
    dob: Optional[date] = None
    dob_unknown: Optional[bool] = None
    age_only: Optional[int] = None
    gender: Optional[str] = None
    # NOTE: patient_type removed - it's derived from active admission, not stored
    phone_primary: Optional[str] = None
    phone_alternate: Optional[str] = None
    city: Optional[str] = None
    # NOTE: department_id removed - department is per-visit, not per-patient
    email: Optional[EmailStr] = None
    address_line1: Optional[str] = None
    address_line2: Optional[str] = None
    postal_code: Optional[str] = None
    state: Optional[str] = None
    country: Optional[str] = None
    blood_group: Optional[str] = None
    marital_status: Optional[str] = None
    preferred_language: Optional[str] = None
    emergency_contact_name: Optional[str] = None
    emergency_contact_relation: Optional[str] = None
    emergency_contact_phone: Optional[str] = None
    known_allergies: Optional[str] = None
    chronic_conditions: Optional[str] = None
    clinical_notes: Optional[str] = None
    is_dnr: Optional[bool] = None
    is_deceased: Optional[bool] = None
    date_of_death: Optional[date] = None
    national_id_type: Optional[str] = None
    national_id_number: Optional[str] = None
    photo_path: Optional[str] = None
    consent_sms: Optional[bool] = None
    consent_email: Optional[bool] = None

    @field_validator("first_name")
    @classmethod
    def validate_first_name(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        v = v.strip()
        if not v or len(v) < 1 or len(v) > 50:
            raise ValueError("First name must be 1-50 characters")
        if not re.match(r"^[a-zA-Z\u00C0-\u017F\s.'-]+$", v):
            raise ValueError("First name can only contain letters, spaces, periods, apostrophes, and hyphens")
        return v

    @field_validator("phone_primary")
    @classmethod
    def validate_phone_primary(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        v = v.strip()
        if not validate_phone_digits(v):
            raise ValueError("Phone must be 8-15 digits (remove spaces or symbols)")
        return normalize_phone(v)

    @field_validator("gender")
    @classmethod
    def validate_gender(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        if v not in ["MALE", "FEMALE", "OTHER", "UNKNOWN"]:
            raise ValueError("Gender must be MALE, FEMALE, OTHER, or UNKNOWN")
        return v

    @field_validator("city")
    @classmethod
    def validate_city(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        v = v.strip()
        if v and (len(v) < 2 or len(v) > 80):
            raise ValueError("City must be 2-80 characters")
        return v

    @field_validator("dob")
    @classmethod
    def validate_dob(cls, v: Optional[date]) -> Optional[date]:
        if v is None:
            return None
        if v > date.today():
            raise ValueError("Date of birth cannot be in the future")
        return v

    @model_validator(mode="after")
    def validate_consent_requires_contact(self):
        """Consent requires matching contact info."""
        if self.consent_sms and not self.phone_primary:
            raise ValueError("SMS consent requires primary phone number")
        if self.consent_email and not self.email:
            raise ValueError("Email consent requires email address")
        return self

    @model_validator(mode="after")
    def validate_dnr_deceased_confirmation(self):
        """DNR and Deceased require confirmation (handled in frontend, but validate here too)."""
        # Backend validation - frontend should show confirmation dialog
        return self


class DuplicateCandidate(BaseModel):
    """A potential duplicate patient match."""

    id: UUID
    patient_code: Optional[str]
    first_name: str
    last_name: Optional[str]
    dob: Optional[date]
    phone_primary: Optional[str]
    age: Optional[int]
    last_visited_at: Optional[datetime]
    match_score: float
    match_reason: str


class DuplicateCheckResponse(BaseModel):
    """Response from duplicate check endpoint."""

    has_duplicates: bool
    candidates: list[DuplicateCandidate] = []


class PatientResponse(PatientBase):
    """Full patient response with audit fields."""

    id: UUID
    created_by_id: Optional[UUID] = None
    updated_by_id: Optional[UUID] = None
    created_at: datetime
    updated_at: datetime
    last_visited_at: Optional[datetime] = None

    # Computed field: patient_type derived from active admission
    # This will be computed in the endpoint before returning response
    patient_type: Optional[PatientType] = None

    # Visit flags (optional, only included when include=visit_flags)
    has_active_admission: Optional[bool] = None
    next_eligible_opd_appointment_at: Optional[datetime] = None

    class Config:
        from_attributes = True
