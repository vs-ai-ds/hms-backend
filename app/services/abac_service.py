# app/services/abac_service.py
from sqlalchemy.orm import Session

from app.models.user import RoleName, User
from app.services.user_role_service import get_user_role_names


def can_access_patient_department(
    user: User,
    patient_department: str | None,
    db: Session,
    tenant_schema_name: str | None = None,
) -> bool:
    """
    Helper for department-based ABAC checks.

    - HOSPITAL_ADMIN / SUPER_ADMIN: always True.
    - DOCTOR / NURSE: only if user.department == patient_department.
    - Others: False for now.
    """
    role_names = get_user_role_names(db, user, tenant_schema_name=tenant_schema_name)

    if RoleName.SUPER_ADMIN.value in role_names:
        return True
    if RoleName.HOSPITAL_ADMIN.value in role_names:
        return True

    if RoleName.DOCTOR.value in role_names or RoleName.NURSE.value in role_names:
        if user.department and patient_department:
            return user.department == patient_department
        return False

    return False
