# app/api/v1/endpoints/roles.py
from __future__ import annotations

from typing import Iterable
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.tenant_context import TenantContext, get_tenant_context
from app.core.tenant_db import ensure_search_path
from app.dependencies.authz import require_permission
from app.models.tenant_role import TenantRole, TenantRolePermission, TenantUserRole
from app.models.user import User
from app.schemas.role import RoleCreate, RoleResponse, RoleUpdate

router = APIRouter()


def _fetch_permission_definitions(db: Session, codes: Iterable[str]) -> dict[str, dict]:
    """
    Fetch permission definitions from public.permission_definitions in one query.

    Using schema-qualified SQL avoids any reliance on search_path or ORM mapping
    for PermissionDefinition (and avoids the temptation to SET search_path TO public).
    """
    codes_list = [c for c in (codes or []) if c]
    if not codes_list:
        return {}

    rows = (
        db.execute(
            text(
                """
                SELECT code, description, category
                FROM public.permission_definitions
                WHERE code = ANY(:codes)
                """
            ),
            {"codes": codes_list},
        )
        .mappings()
        .all()
    )

    out: dict[str, dict] = {}
    for r in rows:
        out[r["code"]] = {
            "code": r["code"],
            "name": r["description"],
            "category": r["category"],
        }
    return out


def _validate_permission_codes(db: Session, permission_codes: list[str]) -> None:
    """
    Validate permission codes exist in public.permission_definitions.
    """
    if not permission_codes:
        return

    found = _fetch_permission_definitions(db, permission_codes)
    invalid = sorted(set(permission_codes) - set(found.keys()))
    if invalid:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid permission codes: {', '.join(invalid)}",
        )


def _role_to_response(db: Session, role: TenantRole) -> RoleResponse:
    """
    Convert TenantRole ORM object to RoleResponse with permission details.
    """
    # role.permissions is lazy="joined" in the model.
    perm_codes = [rp.permission_code for rp in (role.permissions or [])]
    perm_defs = _fetch_permission_definitions(db, perm_codes)

    permissions = []
    for code in perm_codes:
        # Keep stable order based on role_permissions rows
        if code in perm_defs:
            permissions.append(perm_defs[code])

    return RoleResponse.model_validate(
        {
            "id": role.id,
            "name": role.name,
            "description": role.description,
            "is_system": role.is_system,
            "system_key": role.system_key,
            "is_active": getattr(role, "is_active", True),
            "permissions": permissions,
            "created_at": role.created_at,
            "updated_at": role.updated_at,
        }
    )


@router.get("", response_model=list[RoleResponse], tags=["roles"])
def list_roles(
    current_user: User = Depends(require_permission("roles:view")),
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(get_tenant_context),
) -> list[RoleResponse]:
    """
    List all roles for the current tenant.
    """
    ensure_search_path(db, ctx.tenant.schema_name)

    roles = db.query(TenantRole).order_by(TenantRole.name).all()

    # One public lookup for all permission codes across all roles.
    all_codes: list[str] = []
    for r in roles:
        for rp in r.permissions or []:
            all_codes.append(rp.permission_code)

    perm_defs = _fetch_permission_definitions(db, all_codes)

    result: list[RoleResponse] = []
    for role in roles:
        perm_codes = [rp.permission_code for rp in (role.permissions or [])]
        permissions = [perm_defs[c] for c in perm_codes if c in perm_defs]

        result.append(
            RoleResponse.model_validate(
                {
                    "id": role.id,
                    "name": role.name,
                    "description": role.description,
                    "is_system": role.is_system,
                    "system_key": role.system_key,
                    "is_active": getattr(role, "is_active", True),
                    "permissions": permissions,
                    "created_at": role.created_at,
                    "updated_at": role.updated_at,
                }
            )
        )

    return result


@router.post(
    "",
    response_model=RoleResponse,
    status_code=status.HTTP_201_CREATED,
    tags=["roles"],
)
def create_role(
    payload: RoleCreate,
    current_user: User = Depends(require_permission("roles:create")),
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(get_tenant_context),
) -> RoleResponse:
    """
    Create a new role for the current tenant.
    If template_role_id is provided, permissions will be copied from that role.
    """
    ensure_search_path(db, ctx.tenant.schema_name)

    existing = db.query(TenantRole).filter(TenantRole.name == payload.name).first()
    if existing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Role with this name already exists.",
        )

    # If template_role_id is provided, copy permissions from that role
    permission_codes = list(payload.permission_codes) if payload.permission_codes else []
    if payload.template_role_id:
        template_role = db.query(TenantRole).filter(TenantRole.id == payload.template_role_id).first()
        if not template_role:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Template role not found.")

        template_codes = [rp.permission_code for rp in (template_role.permissions or [])]
        permission_codes = template_codes

        # Merge with any additional permissions provided
        if payload.permission_codes:
            permission_codes = list(set(permission_codes + list(payload.permission_codes)))

    # Validate permission codes exist in public.permission_definitions
    _validate_permission_codes(db, permission_codes)

    role = TenantRole(
        name=payload.name,
        description=payload.description,
        is_system=False,
        system_key=None,
    )
    db.add(role)
    db.flush()  # ensures role.id is available

    # Assign permissions
    for code in permission_codes:
        db.add(TenantRolePermission(role_id=role.id, permission_code=code))

    db.commit()

    # Important: ensure tenant search_path is still correct before refresh/query work
    ensure_search_path(db, ctx.tenant.schema_name)

    # Reload with joined permissions to build response reliably
    role = db.query(TenantRole).filter(TenantRole.id == role.id).first()
    if not role:
        raise HTTPException(status_code=500, detail="Role created but could not be reloaded.")

    return _role_to_response(db, role)


@router.patch(
    "/{role_id}",
    response_model=RoleResponse,
    tags=["roles"],
)
def update_role(
    role_id: UUID,
    payload: RoleUpdate,
    current_user: User = Depends(require_permission("roles:update")),
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(get_tenant_context),
) -> RoleResponse:
    """
    Update a role.
    """
    ensure_search_path(db, ctx.tenant.schema_name)

    role = db.query(TenantRole).filter(TenantRole.id == role_id).first()
    if not role:
        raise HTTPException(status_code=404, detail="Role not found")

    # Prevent modification of system roles
    if role.is_system:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "Cannot modify system roles. System roles cannot be renamed, deleted, "
                "or have their permissions changed. To customize, create a new role based on this template."
            ),
        )

    if payload.name and payload.name != role.name:
        existing = db.query(TenantRole).filter(TenantRole.name == payload.name, TenantRole.id != role_id).first()
        if existing:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Role with this name already exists.",
            )
        role.name = payload.name

    if payload.description is not None:
        role.description = payload.description

    # Update permissions if provided
    if payload.permission_codes is not None:
        new_codes = list(payload.permission_codes) if payload.permission_codes else []
        _validate_permission_codes(db, new_codes)

        # Delete existing permissions
        db.query(TenantRolePermission).filter(TenantRolePermission.role_id == role.id).delete()

        # Add new permissions
        for code in new_codes:
            db.add(TenantRolePermission(role_id=role.id, permission_code=code))

    db.commit()

    ensure_search_path(db, ctx.tenant.schema_name)

    role = db.query(TenantRole).filter(TenantRole.id == role_id).first()
    if not role:
        raise HTTPException(status_code=500, detail="Role updated but could not be reloaded.")

    return _role_to_response(db, role)


@router.get(
    "/permissions",
    response_model=list[dict],
    tags=["roles"],
)
def list_available_permissions(
    current_user: User = Depends(require_permission("roles:view")),
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(get_tenant_context),
) -> list[dict]:
    """
    List all available permission codes from public.permission_definitions.

    Note: We do not SET search_path TO public. We read the public table explicitly.
    """
    # We still ensure tenant path is set; no harm, and avoids surprises if anything else
    # in the request/session expects tenant search_path.
    ensure_search_path(db, ctx.tenant.schema_name)

    rows = (
        db.execute(
            text(
                """
                SELECT code, description, category
                FROM public.permission_definitions
                ORDER BY 
                    CASE category
                        WHEN 'dashboard' THEN 1
                        WHEN 'patients' THEN 2
                        WHEN 'appointments' THEN 3
                        WHEN 'ipd' THEN 4
                        WHEN 'prescriptions' THEN 5
                        WHEN 'pharmacy' THEN 6
                        WHEN 'lab' THEN 7
                        WHEN 'documents' THEN 8
                        WHEN 'sharing' THEN 9
                        WHEN 'users' THEN 10
                        WHEN 'departments' THEN 11
                        WHEN 'roles' THEN 12
                        WHEN 'stock_items' THEN 13
                        WHEN 'billing' THEN 14
                        WHEN 'settings' THEN 15
                        ELSE 99
                    END,
                    code
                """
            )
        )
        .mappings()
        .all()
    )

    return [{"code": r["code"], "name": r["description"], "category": r["category"]} for r in rows]


@router.patch(
    "/{role_id}/toggle-active",
    response_model=RoleResponse,
    tags=["roles"],
)
def toggle_role_active(
    role_id: UUID,
    current_user: User = Depends(require_permission("roles:update")),
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(get_tenant_context),
) -> RoleResponse:
    """
    Enable or disable a custom role (non-system roles only).
    """
    ensure_search_path(db, ctx.tenant.schema_name)

    role = db.query(TenantRole).filter(TenantRole.id == role_id).first()
    if not role:
        raise HTTPException(status_code=404, detail="Role not found")

    # Prevent modification of system roles
    if role.is_system:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Cannot enable/disable system roles.",
        )

    role.is_active = not role.is_active
    db.commit()

    ensure_search_path(db, ctx.tenant.schema_name)

    role = db.query(TenantRole).filter(TenantRole.id == role_id).first()
    if not role:
        raise HTTPException(status_code=500, detail="Role updated but could not be reloaded.")

    return _role_to_response(db, role)


@router.delete(
    "/{role_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    tags=["roles"],
)
def delete_role(
    role_id: UUID,
    current_user: User = Depends(require_permission("roles:delete")),
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(get_tenant_context),
) -> None:
    """
    Delete a role.
    System roles cannot be deleted.
    """
    ensure_search_path(db, ctx.tenant.schema_name)

    role = db.query(TenantRole).filter(TenantRole.id == role_id).first()
    if not role:
        raise HTTPException(status_code=404, detail="Role not found")

    # Prevent deletion of system roles
    if role.is_system:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Cannot delete system roles. System roles are locked and cannot be removed. To customize, create a new role based on this template.",
        )

    user_count = db.query(TenantUserRole).filter(TenantUserRole.role_id == role_id).count()
    if user_count > 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Cannot delete role. It is assigned to {user_count} user(s). Please remove the role from all users first.",
        )

    # Delete role (permissions will be cascade deleted)
    db.delete(role)
    db.commit()
