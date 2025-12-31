# app/services/user_service.py
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.security import get_password_hash
from app.models.tenant_global import Tenant
from app.models.tenant_role import TenantRole, TenantUserRole
from app.models.user import RoleName, User, UserStatus
from app.models.user_tenant import UserTenant
from app.schemas.user import UserCreate


def create_user(db: Session, user_in: UserCreate, tenant: Tenant | None = None) -> User:
    """
    Create a user with tenant-scoped roles.

    If tenant is provided, roles are assigned in the tenant schema.
    Also creates a user_tenants entry for multi-tenant support.

    Note: password should always be provided by the caller (endpoint generates temp password).
    """
    hashed_pw = get_password_hash(user_in.password)

    user = User(
        tenant_id=user_in.tenant_id,
        email=user_in.email,
        hashed_password=hashed_pw,
        first_name=user_in.first_name,
        last_name=user_in.last_name,
        phone=user_in.phone,
        status=UserStatus.ACTIVE,
        is_active=True,
        is_deleted=False,
        deleted_at=None,
        must_change_password=True,  # Force password change on first login
        email_verified=False,  # Email not verified until user logs in
        department=user_in.department,
        specialization=user_in.specialization,
    )
    db.add(user)
    db.flush()

    # Create user_tenants entry if tenant_id is provided
    if user_in.tenant_id:
        # Check if user_tenants entry already exists
        existing = (
            db.query(UserTenant)
            .filter(
                UserTenant.user_id == user.id,
                UserTenant.tenant_id == user_in.tenant_id,
            )
            .first()
        )
        if not existing:
            user_tenant = UserTenant(
                user_id=user.id,
                tenant_id=user_in.tenant_id,
                is_default=True,  # First tenant is default
                is_active=True,
            )
            db.add(user_tenant)
            db.flush()

        # Assign roles in tenant schema
        if user_in.roles and tenant:
            conn = db.connection()
            original_path = conn.execute(text("SHOW search_path")).scalar()
            try:
                conn.execute(text(f'SET search_path TO "{tenant.schema_name}", public'))

                # Get tenant roles by name (supports both system and custom roles)
                # user_in.roles is now a list of strings (role names)
                role_names = [str(r) for r in user_in.roles]
                tenant_roles = (
                    db.query(TenantRole)
                    .filter(TenantRole.name.in_(role_names))
                    .filter(TenantRole.is_active == True)  # Only assign active roles
                    .all()
                )

                # Check if all requested roles were found
                found_role_names = {role.name for role in tenant_roles}
                missing_roles = set(role_names) - found_role_names
                if missing_roles:
                    raise ValueError(f"Roles not found or inactive: {', '.join(missing_roles)}")

                # Create TenantUserRole entries
                for role in tenant_roles:
                    user_role = TenantUserRole(
                        user_id=user.id,
                        role_id=role.id,
                    )
                    db.add(user_role)

                db.flush()

                # Restore original search_path
                conn.execute(text(f"SET search_path TO {original_path}"))
            except Exception as e:
                # Rollback transaction first, then restore search_path
                db.rollback()
                try:
                    conn.execute(text(f"SET search_path TO {original_path}"))
                except Exception:
                    pass  # Ignore if transaction is already aborted
                raise

    return user


def create_hospital_admin_for_tenant(
    db: Session,
    tenant: Tenant,
    temp_password: str,
) -> User:
    """
    Create a default HOSPITAL_ADMIN user for a newly registered tenant.
    Uses tenant.contact_email as login email.
    Assigns the "Administrator" department to the admin user.
    """
    from sqlalchemy import text

    from app.models.department import Department

    # Set search_path to tenant schema to find the Administrator department
    conn = db.connection()
    original_path = conn.execute(text("SHOW search_path")).scalar()
    try:
        conn.execute(text(f'SET search_path TO "{tenant.schema_name}", public'))

        # Find the Administrator department (should be created by seed_tenant_defaults)
        admin_dept = db.query(Department).filter(Department.name == "Administrator").first()
        if not admin_dept:
            raise ValueError("Administrator department not found. Tenant seeding may have failed.")

        user_in = UserCreate(
            tenant_id=tenant.id,
            email=tenant.contact_email,
            first_name=tenant.name,  # can be refined later
            last_name="Admin",
            phone=tenant.contact_phone,
            password=temp_password,
            department=admin_dept.name,  # Use department name (User model stores name, not ID)
            roles=[RoleName.HOSPITAL_ADMIN],
        )
        user = create_user(db, user_in, tenant=tenant)

        # Restore search_path
        conn.execute(text(f"SET search_path TO {original_path}"))
        return user
    except Exception as e:
        conn.execute(text(f"SET search_path TO {original_path}"))
        raise


def get_user_by_email_and_tenant(
    db: Session,
    email: str,
    tenant_id: UUID | None,
) -> User | None:
    """
    For now we require email + tenant_id match.
    Future: support SUPER_ADMIN with tenant_id=None.
    Email comparison is case-insensitive.
    """
    from sqlalchemy import func

    query = db.query(User).filter(func.lower(User.email) == func.lower(email))

    if tenant_id is not None:
        query = query.filter(User.tenant_id == tenant_id)

    return query.first()


def get_user_by_email(
    db: Session,
    email: str,
) -> User | None:
    """
    Find a user by email across all tenants.
    Returns the first matching user (for password reset purposes).
    Email comparison is case-insensitive.
    """
    from sqlalchemy import func

    return db.query(User).filter(func.lower(User.email) == func.lower(email)).first()
