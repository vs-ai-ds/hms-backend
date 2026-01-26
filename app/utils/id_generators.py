# app/utils/id_generators.py
from uuid import UUID

from sqlalchemy.orm import Session

from app.models.patient import Patient
from app.models.tenant_global import Tenant


def generate_patient_code(db: Session, tenant_id: UUID) -> str:
    """
    Generate a unique patient code in format: {tenantId}-P-{sequential}

    Where:
    - {tenantId} = first 8 characters of tenant UUID (hex)
    - P = literal "P" for Patient
    - {sequential} = sequential number (zero-padded to 5 digits)

    Example: a1b2c3d4-P-00001, a1b2c3d4-P-00002, etc.
    """
    # Get tenant to ensure it exists
    tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()
    if not tenant:
        raise ValueError("Tenant not found")

    # Use first 8 characters of tenant UUID
    tenant_prefix = str(tenant.id).replace("-", "")[:8]
    prefix = f"{tenant_prefix}-P-"

    # Query for existing codes with this prefix
    existing_codes = (
        db.query(Patient.patient_code)
        .filter(Patient.patient_code.like(f"{prefix}%"))
        .all()
    )

    # Extract sequence numbers
    max_seq = 0
    for (code,) in existing_codes:
        if code and code.startswith(prefix):
            try:
                seq_str = code[len(prefix) :]
                seq_num = int(seq_str)
                max_seq = max(max_seq, seq_num)
            except ValueError:
                continue

    # Increment and format
    next_seq = max_seq + 1
    return f"{prefix}{next_seq:05d}"


def generate_prescription_code(db: Session, tenant_id: UUID) -> str:
    """
    Generate a unique prescription code in format: {tenantId}-RX-{sequential}

    Where:
    - {tenantId} = first 8 characters of tenant UUID (hex)
    - RX = literal "RX" for Prescription
    - {sequential} = sequential number (zero-padded to 5 digits)

    Example: a1b2c3d4-RX-00001, a1b2c3d4-RX-00002, etc.
    """
    from app.models.prescription import Prescription

    # Get tenant to ensure it exists
    tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()
    if not tenant:
        raise ValueError("Tenant not found")

    # Use first 8 characters of tenant UUID
    tenant_prefix = str(tenant.id).replace("-", "")[:8]
    prefix = f"{tenant_prefix}-RX-"

    # Query for existing codes with this prefix
    existing_codes = (
        db.query(Prescription.prescription_code)
        .filter(Prescription.prescription_code.like(f"{prefix}%"))
        .all()
    )

    # Extract sequence numbers
    max_seq = 0
    for (code,) in existing_codes:
        if code and code.startswith(prefix):
            try:
                seq_str = code[len(prefix) :]
                seq_num = int(seq_str)
                max_seq = max(max_seq, seq_num)
            except ValueError:
                continue

    # Increment and format
    next_seq = max_seq + 1
    return f"{prefix}{next_seq:05d}"
