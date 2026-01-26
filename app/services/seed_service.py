# app/services/seed_service.py
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.models.department import Department
from app.models.permission_definition import PermissionDefinition
from app.models.tenant_role import TenantRole, TenantRolePermission
from app.models.user import RoleName

# Default permission codes as per project doc
DEFAULT_PERMISSIONS = [
    ("dashboard:view", "View dashboard", "dashboard"),
    ("patients:view", "View patients", "patients"),
    ("patients:create", "Create patients", "patients"),
    ("patients:update", "Update patients", "patients"),
    ("appointments:view", "View appointments", "appointments"),
    ("appointments:create", "Create appointments", "appointments"),
    ("appointments:update_status", "Update appointment status", "appointments"),
    ("prescriptions:view", "View prescriptions", "prescriptions"),
    ("prescriptions:create", "Create prescriptions", "prescriptions"),
    ("prescriptions:update_status", "Update prescription status", "prescriptions"),
    ("users:view", "View users", "users"),
    ("users:create", "Create users", "users"),
    ("users:update", "Update users", "users"),
    ("users:deactivate", "Deactivate users", "users"),
    ("users:assign_roles", "Assign roles to users", "users"),
    ("billing:view", "View billing", "billing"),
    ("billing:create", "Create billing", "billing"),
    ("pharmacy:view", "View pharmacy", "pharmacy"),
    ("pharmacy:dispense", "Dispense medicines", "pharmacy"),
    ("lab:view", "View lab tests", "lab"),
    ("lab:order", "Order lab tests", "lab"),
    ("lab:result", "Record lab results", "lab"),
    ("ipd:view", "View IPD", "ipd"),
    ("ipd:admit", "Admit patients", "ipd"),
    ("ipd:discharge", "Discharge patients", "ipd"),
    ("documents:view", "View documents", "documents"),
    ("documents:upload", "Upload documents", "documents"),
    ("sharing:create", "Create sharing requests", "sharing"),
    ("sharing:view", "View sharing requests", "sharing"),
    ("departments:view", "View departments", "departments"),
    ("departments:create", "Create departments", "departments"),
    ("departments:update", "Update departments", "departments"),
    ("departments:delete", "Delete departments", "departments"),
    ("roles:view", "View roles", "roles"),
    ("roles:create", "Create roles", "roles"),
    ("roles:update", "Update roles", "roles"),
    ("roles:assign_permissions", "Assign permissions to roles", "roles"),
    ("stock_items:view", "View stock items", "stock_items"),
    ("stock_items:manage", "Manage stock items", "stock_items"),
    ("settings:view", "View settings", "settings"),
    ("settings:update", "Update settings", "settings"),
]

# Role to permission mappings
ROLE_PERMISSIONS = {
    RoleName.HOSPITAL_ADMIN: [
        "dashboard:view",
        "patients:view",
        "patients:create",
        "patients:update",
        "appointments:view",
        "appointments:create",
        "appointments:update_status",
        "prescriptions:view",
        "prescriptions:create",
        "prescriptions:update_status",
        "users:view",
        "users:create",
        "users:update",
        "users:deactivate",
        "users:assign_roles",
        "billing:view",
        "billing:create",
        "pharmacy:view",
        "pharmacy:dispense",
        "lab:view",
        "lab:order",
        "lab:result",
        "ipd:view",
        "ipd:admit",
        "ipd:discharge",
        "documents:view",
        "documents:upload",
        "sharing:create",
        "sharing:view",
        "departments:view",
        "departments:create",
        "departments:update",
        "departments:delete",
        "roles:view",
        "roles:create",
        "roles:update",
        "roles:assign_permissions",
        "stock_items:view",
        "stock_items:manage",
        "settings:view",
        "settings:update",
    ],
    RoleName.DOCTOR: [
        "dashboard:view",
        "patients:view",
        "patients:create",
        "appointments:view",
        "appointments:create",
        "appointments:update_status",
        "prescriptions:view",
        "prescriptions:create",
        "prescriptions:update_status",
        "ipd:view",
        "ipd:admit",
        "ipd:discharge",
        "lab:view",
        "lab:order",
        "documents:view",
        "documents:upload",
        "sharing:create",
        "sharing:view",
        "stock_items:view",
    ],
    RoleName.NURSE: [
        "dashboard:view",
        "patients:view",
        "appointments:view",
        "ipd:view",
        "ipd:admit",
        "ipd:discharge",
        "stock_items:view",
    ],
    RoleName.PHARMACIST: [
        "dashboard:view",
        "prescriptions:view",
        "prescriptions:update_status",
        "pharmacy:view",
        "pharmacy:dispense",
        "stock_items:view",
        "stock_items:manage",
    ],
    RoleName.RECEPTIONIST: [
        "dashboard:view",
        "patients:create",
        "appointments:view",
        "appointments:create",
    ],
}

# Default departments
# Note: "Administrator" must be first as it's assigned to hospital admin
DEFAULT_DEPARTMENTS = [
    ("Administrator", "Administration Department - System default, cannot be deleted"),
    ("General Medicine", "General Medicine Department"),
    ("Pediatrics", "Pediatrics Department - Child healthcare"),
    ("Cardiology", "Cardiology Department - Heart and cardiovascular care"),
    ("Emergency", "Emergency Department - Urgent care and trauma"),
]


def seed_permission_definitions(db: Session) -> dict[str, PermissionDefinition]:
    """
    Seed permission definitions into public.permission_definitions.
    Returns a dict mapping permission_code -> PermissionDefinition object.
    This should be called once during app initialization, not per tenant.
    """
    permissions_map = {}
    for code, description, category in DEFAULT_PERMISSIONS:
        existing = (
            db.query(PermissionDefinition)
            .filter(PermissionDefinition.code == code)
            .first()
        )
        if existing:
            permissions_map[code] = existing
        else:
            perm = PermissionDefinition(
                code=code, description=description, category=category
            )
            db.add(perm)
            permissions_map[code] = perm
    db.flush()
    return permissions_map


def seed_tenant_roles(
    db: Session, role_permissions_map: dict[RoleName, list[str]]
) -> dict[RoleName, TenantRole]:
    """
    Seed default roles in tenant schema and assign permissions.
    Must be called with tenant schema search_path already set.
    Returns a dict mapping RoleName -> TenantRole object.
    """
    roles_map = {}
    for role_name in [
        RoleName.HOSPITAL_ADMIN,
        RoleName.DOCTOR,
        RoleName.NURSE,
        RoleName.PHARMACIST,
        RoleName.RECEPTIONIST,
    ]:
        existing = (
            db.query(TenantRole).filter(TenantRole.name == role_name.value).first()
        )
        if existing:
            role = existing
        else:
            role = TenantRole(
                name=role_name.value,
                description=f"System default role: {role_name.value}",
                is_system=True,
                system_key=role_name.value,
            )
            db.add(role)
            db.flush()
        roles_map[role_name] = role

        # Clear existing permissions and assign new ones
        # Delete existing role permissions
        db.query(TenantRolePermission).filter(
            TenantRolePermission.role_id == role.id
        ).delete()
        db.flush()

        # Assign permissions to role
        perm_codes = role_permissions_map.get(role_name, [])
        for perm_code in perm_codes:
            role_perm = TenantRolePermission(
                role_id=role.id,
                permission_code=perm_code,
            )
            db.add(role_perm)
        db.flush()

    return roles_map


def seed_departments(db: Session) -> list[Department]:
    """
    Seed default departments into the tenant schema.
    Must be called with tenant schema search_path already set.
    """
    departments = []
    for name, description in DEFAULT_DEPARTMENTS:
        existing = db.query(Department).filter(Department.name == name).first()
        if existing:
            departments.append(existing)
        else:
            # Set is_for_patients=False for Administrator department
            is_for_patients = name != "Administrator"
            dept = Department(
                name=name,
                description=description,
                is_for_patients=is_for_patients,
                is_for_staff=True,  # All departments can be assigned to staff
            )
            db.add(dept)
            departments.append(dept)
    db.flush()
    return departments


def seed_tenant_defaults(db: Session) -> dict:
    """
    Seed default roles, permissions, and departments for a new tenant.
    Must be called with the tenant's schema search_path already set.

    Note: Permission definitions are seeded in public schema separately.
    This function only seeds tenant-scoped roles and departments.
    """
    return ensure_tenant_minimums(db)


def ensure_tenant_minimums(db: Session) -> dict:
    """
    Shared idempotent function to ensure tenant has minimum required data:
    - Default roles with permissions
    - Default departments

    Can be called by tenant registration or demo seeding.
    Must be called with the tenant's schema search_path already set.

    Returns:
        dict with "roles" and "departments" keys
    """
    roles_map = seed_tenant_roles(db, ROLE_PERMISSIONS)
    departments = seed_departments(db)
    return {
        "roles": roles_map,
        "departments": departments,
    }


def update_doctor_role_permissions_for_all_tenants(db: Session) -> dict:
    """
    Update DOCTOR role permissions for all existing tenants.
    Adds missing OPD and IPD permissions to DOCTOR role in all tenant schemas.

    Permissions added:
    - appointments:create
    - appointments:update_status
    - ipd:view
    - ipd:admit
    - ipd:discharge

    This function should be called when updating system role permissions
    for existing tenants (e.g., after adding new permissions to ROLE_PERMISSIONS).

    Returns:
        dict with 'updated_count' (number of tenants updated) and 'errors' (list of errors)
    """
    from app.models.tenant_global import Tenant, TenantStatus

    # Permissions to add to DOCTOR role
    permissions_to_add = [
        "appointments:create",
        "appointments:update_status",
        "ipd:view",
        "ipd:admit",
        "ipd:discharge",
    ]

    all_tenants = db.query(Tenant).filter(Tenant.status != TenantStatus.INACTIVE).all()

    # Get original search_path before making changes
    original_path = db.execute(text("SHOW search_path")).scalar()

    updated_count = 0
    errors = []

    try:
        for tenant in all_tenants:
            try:
                # Set search_path to tenant schema using session execute
                db.execute(text(f'SET search_path TO "{tenant.schema_name}", public'))
                db.commit()  # Commit the search_path change

                # Find DOCTOR role
                doctor_role = (
                    db.query(TenantRole)
                    .filter(
                        TenantRole.name == RoleName.DOCTOR.value,
                        TenantRole.system_key == RoleName.DOCTOR.value,
                    )
                    .first()
                )

                if not doctor_role:
                    errors.append(f"Tenant {tenant.name}: DOCTOR role not found")
                    # Reset search_path before continuing
                    db.execute(text(f"SET search_path TO {original_path}"))
                    db.commit()
                    continue

                # Get existing permissions for this role
                existing_perms = {
                    rp.permission_code
                    for rp in db.query(TenantRolePermission)
                    .filter(TenantRolePermission.role_id == doctor_role.id)
                    .all()
                }

                # Add missing permissions
                added_any = False
                for perm_code in permissions_to_add:
                    if perm_code not in existing_perms:
                        new_perm = TenantRolePermission(
                            role_id=doctor_role.id,
                            permission_code=perm_code,
                        )
                        db.add(new_perm)
                        added_any = True

                if added_any:
                    db.commit()
                    updated_count += 1

                # Reset search_path after processing this tenant
                db.execute(text(f"SET search_path TO {original_path}"))
                db.commit()

            except Exception as e:
                db.rollback()
                errors.append(f"Tenant {tenant.name}: {str(e)}")
                import logging

                logger = logging.getLogger(__name__)
                logger.error(
                    f"Failed to update DOCTOR role for tenant {tenant.name}: {e}"
                )
                # Try to reset search_path even after error
                try:
                    db.execute(text(f"SET search_path TO {original_path}"))
                    db.commit()
                except Exception:
                    pass
                continue
    finally:
        # Ensure search_path is restored
        try:
            db.execute(text(f"SET search_path TO {original_path}"))
            db.commit()
        except Exception:
            pass

    return {
        "updated_count": updated_count,
        "errors": errors,
    }
