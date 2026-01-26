# app/api/v1/endpoints/departments.py
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.tenant_context import TenantContext, get_tenant_context
from app.core.tenant_db import ensure_search_path
from app.dependencies.authz import require_permission
from app.models.department import Department
from app.models.user import User
from app.schemas.department import (
    DepartmentCreate,
    DepartmentResponse,
    DepartmentUpdate,
)

router = APIRouter()


@router.get("", response_model=list[dict], tags=["departments"])
def list_departments(
    for_staff: bool | None = None,
    for_patients: bool | None = None,
    current_user: User = Depends(require_permission("departments:view")),
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(get_tenant_context),
) -> list[dict]:
    """
    List all departments for the current tenant with user counts.

    Optional filters:
    - for_staff: If True, only return departments available for staff
    - for_patients: If True, only return departments available for patients
    """
    ensure_search_path(db, ctx.tenant.schema_name)
    from app.models.user import User as UserModel

    query = db.query(Department)

    # Apply filters
    if for_staff is not None:
        query = query.filter(Department.is_for_staff == for_staff)
    if for_patients is not None:
        query = query.filter(Department.is_for_patients == for_patients)

    departments = query.order_by(Department.name).all()
    result = []
    for d in departments:
        try:
            dept_dict = DepartmentResponse.model_validate(d).model_dump()
            # Count users assigned to this department
            user_count = (
                db.query(UserModel).filter(UserModel.department == d.name).count()
            )
            # Count patients with appointments or admissions in this department
            # (Department is per-visit, not per-patient)
            from app.models.admission import Admission
            from app.models.appointment import Appointment

            # Get unique patient IDs from appointments
            appointment_patient_ids = [
                row[0]
                for row in db.query(Appointment.patient_id)
                .filter(Appointment.department_id == d.id)
                .filter(Appointment.patient_id.isnot(None))
                .distinct()
                .all()
            ]

            # Get unique patient IDs from admissions
            admission_patient_ids = [
                row[0]
                for row in db.query(Admission.patient_id)
                .filter(Admission.department_id == d.id)
                .filter(Admission.patient_id.isnot(None))
                .distinct()
                .all()
            ]

            # Count unique patients (union of both sets)
            all_patient_ids = set(appointment_patient_ids + admission_patient_ids)
            patient_count = len(all_patient_ids)
            dept_dict["user_count"] = user_count
            dept_dict["patient_count"] = patient_count
            dept_dict["is_system"] = (
                d.name == "Administrator"
            )  # Mark Administrator as system department
            result.append(dept_dict)
        except Exception as e:
            # Skip departments with invalid data (e.g., whitespace-only names)
            # Log the error for debugging
            import logging

            logger = logging.getLogger(__name__)
            logger.warning(f"Skipping department {d.id} due to validation error: {e}")
            continue
    return result


@router.post(
    "",
    response_model=DepartmentResponse,
    status_code=status.HTTP_201_CREATED,
    tags=["departments"],
)
def create_department(
    payload: DepartmentCreate,
    current_user: User = Depends(require_permission("departments:create")),
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(get_tenant_context),
) -> DepartmentResponse:
    """
    Create a new department for the current tenant.
    """
    ensure_search_path(db, ctx.tenant.schema_name)
    # Check if department with same name already exists
    existing = db.query(Department).filter(Department.name == payload.name).first()
    if existing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Department with this name already exists.",
        )

    department = Department(
        name=payload.name,
        description=payload.description,
        is_for_staff=payload.is_for_staff,
        is_for_patients=payload.is_for_patients,
    )
    db.add(department)
    db.flush()  # Get the ID without committing
    department_id = department.id

    db.commit()
    ensure_search_path(db, ctx.tenant.schema_name)

    # Re-query to ensure we get the department with all fields (created_at, updated_at)
    department = db.query(Department).filter(Department.id == department_id).first()
    if not department:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve created department",
        )

    return DepartmentResponse.model_validate(department)


@router.patch(
    "/{department_id}",
    response_model=DepartmentResponse,
    tags=["departments"],
)
def update_department(
    department_id: UUID,
    payload: DepartmentUpdate,
    current_user: User = Depends(require_permission("departments:update")),
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(get_tenant_context),
) -> DepartmentResponse:
    """
    Update a department.
    """
    ensure_search_path(db, ctx.tenant.schema_name)
    department = db.query(Department).filter(Department.id == department_id).first()
    if not department:
        raise HTTPException(status_code=404, detail="Department not found")

    if payload.name and payload.name != department.name:
        # Check if another department with this name exists
        existing = (
            db.query(Department)
            .filter(
                Department.name == payload.name,
                Department.id != department_id,
            )
            .first()
        )
        if existing:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Department with this name already exists.",
            )
        department.name = payload.name

    if payload.description is not None:
        department.description = payload.description

    if payload.is_for_staff is not None:
        department.is_for_staff = payload.is_for_staff

    if payload.is_for_patients is not None:
        department.is_for_patients = payload.is_for_patients

    db.commit()
    ensure_search_path(db, ctx.tenant.schema_name)

    # Re-query to ensure we get the department with all fields (created_at, updated_at)
    updated_department = (
        db.query(Department).filter(Department.id == department_id).first()
    )
    if not updated_department:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve updated department",
        )

    return DepartmentResponse.model_validate(updated_department)


@router.delete(
    "/{department_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    tags=["departments"],
)
def delete_department(
    department_id: UUID,
    current_user: User = Depends(require_permission("departments:delete")),
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(get_tenant_context),
) -> None:
    """
    Delete a department.
    Cannot delete "Administrator" department or departments with assigned users.
    """
    ensure_search_path(db, ctx.tenant.schema_name)
    department = db.query(Department).filter(Department.id == department_id).first()
    if not department:
        raise HTTPException(status_code=404, detail="Department not found")

    # Prevent deletion of "Administrator" department
    if department.name == "Administrator":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Cannot delete the Administrator department. This is a system default department.",
        )

    # Check if any users are assigned to this department
    from app.models.user import User

    user_count = db.query(User).filter(User.department == department.name).count()
    if user_count > 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Cannot delete department. {user_count} user(s) are assigned to this department. Please reassign users to another department first.",
        )

    # Check if any appointments or admissions exist for this department
    # (Department is per-visit, not per-patient)
    from app.models.admission import Admission
    from app.models.appointment import Appointment

    appointment_count = (
        db.query(Appointment).filter(Appointment.department_id == department_id).count()
    )
    admission_count = (
        db.query(Admission).filter(Admission.department_id == department_id).count()
    )
    if appointment_count > 0 or admission_count > 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Cannot delete department. {appointment_count} appointment(s) and {admission_count} admission(s) are linked to this department. Please reassign them to another department first.",
        )

    db.delete(department)
    db.commit()
