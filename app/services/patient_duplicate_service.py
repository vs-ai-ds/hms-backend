# app/services/patient_duplicate_service.py
from datetime import date
from typing import Optional
from uuid import UUID

from sqlalchemy import and_, or_, text
from sqlalchemy.orm import Session

from app.models.patient import Patient
from app.schemas.patient import DuplicateCandidate


def calculate_age(dob: Optional[date]) -> Optional[int]:
    """Calculate age from date of birth."""
    if not dob:
        return None
    today = date.today()
    age = today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))
    return age


def normalize_phone_for_match(phone: Optional[str]) -> Optional[str]:
    """Normalize phone for duplicate matching (digits only)."""
    if not phone:
        return None
    # Remove all non-digits
    digits = "".join(c for c in phone if c.isdigit())
    return digits if digits else None


def find_duplicate_candidates(
    db: Session,
    *,
    first_name: str,
    last_name: Optional[str],
    dob: Optional[date],
    phone_primary: str,
    national_id_number: Optional[str] = None,
    exclude_patient_id: Optional[UUID] = None,
) -> list[DuplicateCandidate]:
    """
    Find potential duplicate patients based on:
    1. Same phone number
    2. Same first_name + last_name + DOB
    3. Same national_id_number

    Returns candidates sorted by match score (highest first).
    """
    candidates: list[DuplicateCandidate] = []
    normalized_phone = normalize_phone_for_match(phone_primary)

    # Build query
    query = db.query(Patient)

    # Exclude current patient if updating
    if exclude_patient_id:
        query = query.filter(Patient.id != exclude_patient_id)

    # Match conditions
    conditions = []

    # 1. Phone match (highest priority) - use LIKE for partial match on normalized digits
    if normalized_phone and len(normalized_phone) >= 8:
        # Match if last 8+ digits are the same (handles different formats)
        # PostgreSQL regexp_replace: regexp_replace(string, pattern, replacement, flags)
        # The 'g' flag must be a literal SQL string, not a bound parameter
        phone_search_pattern = f"%{normalized_phone[-8:]}%"
        # Use text() with column reference - SQLAlchemy will handle schema qualification
        # Don't use .columns() to avoid the scalar subquery warning
        conditions.append(
            text(
                "regexp_replace(COALESCE(patients.phone_primary, ''), '[^0-9]', '', 'g') LIKE :phone_pattern"
            ).bindparams(phone_pattern=phone_search_pattern)
        )

    # 2. Name + DOB match
    name_dob_conditions = [Patient.first_name.ilike(first_name.strip())]
    if last_name:
        name_dob_conditions.append(Patient.last_name.ilike(last_name.strip()))
    if dob:
        name_dob_conditions.append(Patient.dob == dob)
    if len(name_dob_conditions) >= 2:  # At least first_name + one more
        conditions.append(and_(*name_dob_conditions))

    # 3. National ID match
    if national_id_number:
        conditions.append(Patient.national_id_number == national_id_number.strip())

    if not conditions:
        return []

    # Execute query with OR of all conditions
    matches = query.filter(or_(*conditions)).all()

    # Score and format candidates
    for patient in matches:
        match_score = 0.0
        match_reasons = []

        # Phone match (weight: 0.5)
        if (
            normalized_phone
            and normalize_phone_for_match(patient.phone_primary) == normalized_phone
        ):
            match_score += 0.5
            match_reasons.append("Same phone number")

        # Name + DOB match (weight: 0.4)
        name_match = patient.first_name.strip().lower() == first_name.strip().lower()
        if name_match:
            if last_name and patient.last_name:
                last_match = (
                    patient.last_name.strip().lower() == last_name.strip().lower()
                )
                if last_match:
                    match_score += 0.2
                    match_reasons.append("Same first and last name")
                else:
                    match_score += 0.1
                    match_reasons.append("Same first name")
            else:
                match_score += 0.1
                match_reasons.append("Same first name")

            if dob and patient.dob == dob:
                match_score += 0.2
                match_reasons.append("Same date of birth")

        # National ID match (weight: 0.3)
        if national_id_number and patient.national_id_number:
            if patient.national_id_number.strip() == national_id_number.strip():
                match_score += 0.3
                match_reasons.append("Same national ID")

        # Only include if score > 0.3 (threshold)
        if match_score >= 0.3:
            age = calculate_age(patient.dob)
            candidates.append(
                DuplicateCandidate(
                    id=patient.id,
                    patient_code=patient.patient_code,
                    first_name=patient.first_name,
                    last_name=patient.last_name,
                    dob=patient.dob,
                    phone_primary=patient.phone_primary,
                    age=age,
                    last_visited_at=patient.last_visited_at,
                    match_score=match_score,
                    match_reason="; ".join(match_reasons),
                )
            )

    # Sort by score (highest first)
    candidates.sort(key=lambda x: x.match_score, reverse=True)

    return candidates
