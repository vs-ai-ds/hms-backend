# app/api/v1/endpoints/patients.py
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.tenant_context import TenantContext, get_tenant_context
from app.models.patient import Patient
from app.schemas.patient import PatientCreate, PatientResponse, PatientUpdate

router = APIRouter()


@router.post(
    "/",
    response_model=PatientResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_patient(
    payload: PatientCreate,
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(get_tenant_context),
) -> PatientResponse:
    """
    Create a patient in the current tenant schema.

    ABAC: department is set at patient creation; later queries can filter by this.
    """
    patient = Patient(
        first_name=payload.first_name,
        last_name=payload.last_name,
        dob=payload.dob,
        gender=payload.gender,
        blood_group=payload.blood_group,
        phone=payload.phone,
        email=payload.email,
        address=payload.address,
        emergency_contact=payload.emergency_contact,
        department=payload.department,
        patient_type=payload.patient_type,
        created_by_id=ctx.user.id,
    )
    db.add(patient)
    db.commit()
    db.refresh(patient)

    return PatientResponse.model_validate(patient)


@router.get("/", response_model=list[PatientResponse])
def list_patients(
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(get_tenant_context),
) -> list[PatientResponse]:
    """
    List patients for the current tenant.

    ABAC rule:
    - If current_user has a department and is DOCTOR or NURSE:
        only see patients in that department.
    - HOSPITAL_ADMIN (and above) can see all patients.
    """
    query = db.query(Patient)

    user_roles = {role.name for role in ctx.user.roles}
    user_department = ctx.user.department

    if user_department and ( "DOCTOR" in user_roles or "NURSE" in user_roles ):
        query = query.filter(Patient.department == user_department)

    patients = query.order_by(Patient.created_at.desc()).all()
    return [PatientResponse.model_validate(p) for p in patients]


@router.get("/{patient_id}", response_model=PatientResponse)
def get_patient(
    patient_id: UUID,
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(get_tenant_context),
) -> PatientResponse:
    """
    Fetch a single patient.

    ABAC rule:
    - If DOCTOR/NURSE with a department:
        patient must belong to same department.
    - HOSPITAL_ADMIN and above can view any patient in tenant.
    """
    patient = db.query(Patient).filter(Patient.id == patient_id).first()
    if not patient:
        raise HTTPException(status_code=404, detail="Patient not found")

    user_roles = {role.name for role in ctx.user.roles}
    user_department = ctx.user.department

    if user_department and ( "DOCTOR" in user_roles or "NURSE" in user_roles ):
        if patient.department != user_department:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Access to this patient is restricted by department.",
            )

    return PatientResponse.model_validate(patient)


@router.patch("/{patient_id}", response_model=PatientResponse)
def update_patient(
    patient_id: UUID,
    payload: PatientUpdate,
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(get_tenant_context),
) -> PatientResponse:
    """
    Update a patient.

    Policy (can be refined later):
    - HOSPITAL_ADMIN: can update any patient.
    - DOCTOR/NURSE: only if same department, for now.
    """
    patient = db.query(Patient).filter(Patient.id == patient_id).first()
    if not patient:
        raise HTTPException(status_code=404, detail="Patient not found")

    user_roles = {role.name for role in ctx.user.roles}
    user_department = ctx.user.department

    is_admin = "HOSPITAL_ADMIN" in user_roles or "SUPER_ADMIN" in user_roles
    is_clinical = "DOCTOR" in user_roles or "NURSE" in user_roles

    if is_clinical and user_department and patient.department != user_department and not is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You can only update patients in your department.",
        )

    data = payload.model_dump(exclude_unset=True)
    for field, value in data.items():
        setattr(patient, field, value)

    db.commit()
    db.refresh(patient)
    return PatientResponse.model_validate(patient)