# app/dependencies/authz.py
from typing import Iterable

from fastapi import Depends, HTTPException, status

from app.api.v1.endpoints.auth import get_current_user
from app.models.user import User, RoleName


def require_roles(required_roles: Iterable[RoleName]):
    """
    Dependency factory for role-based access.

    Usage:

    @router.get("/admin")
    def admin_only(user = Depends(require_roles([RoleName.HOSPITAL_ADMIN]))):
        ...

    Returns the current_user if they have at least one required role.
    """

    required = {r.value if isinstance(r, RoleName) else str(r) for r in required_roles}

    def dependency(current_user: User = Depends(get_current_user)) -> User:
        user_roles = {role.name for role in current_user.roles}
        if not user_roles.intersection(required):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Insufficient role permissions.",
            )
        return current_user

    return dependency