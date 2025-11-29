# app/services/abac_service.py
from app.models.user import User, RoleName


def can_access_patient_department(
    user: User,
    patient_department: str | None,
) -> bool:
    """
    Helper for department-based ABAC checks.

    - HOSPITAL_ADMIN / SUPER_ADMIN: always True.
    - DOCTOR / NURSE: only if user.department == patient_department.
    - Others: False for now.
    """
    role_names = {r.name for r in user.roles}

    if RoleName.SUPER_ADMIN.value in role_names:
        return True
    if RoleName.HOSPITAL_ADMIN.value in role_names:
        return True

    if (
        RoleName.DOCTOR.value in role_names
        or RoleName.NURSE.value in role_names
    ):
        if user.department and patient_department:
            return user.department == patient_department
        return False

    return False