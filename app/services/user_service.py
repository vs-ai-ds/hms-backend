from uuid import UUID

from sqlalchemy.orm import Session

from app.core.security import get_password_hash
from app.models.tenant_global import Tenant
from app.models.user import User, Role, RoleName, UserStatus
from app.schemas.user import UserCreate


def get_or_create_role(db: Session, role_name: RoleName) -> Role:
    role = db.query(Role).filter(Role.name == role_name.value).first()
    if role:
        return role

    role = Role(
        name=role_name.value,
        description=f"System role {role_name.value}",
        is_system=True,
    )
    db.add(role)
    db.flush()
    return role


def create_user(db: Session, user_in: UserCreate) -> User:
    """
    Create a generic user with given roles (RoleName list).
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
        department=user_in.department,
        specialization=user_in.specialization,
    )
    db.add(user)
    db.flush()

    roles: list[Role] = []
    for role_name in user_in.roles:
        role = get_or_create_role(db, role_name)
        roles.append(role)

    user.roles = roles
    db.flush()
    return user


def create_hospital_admin_for_tenant(
    db: Session,
    tenant: Tenant,
    temp_password: str,
) -> User:
    """
    Create a default HOSPITAL_ADMIN user for a newly registered tenant.
    Uses tenant.contact_email as login email.
    """
    user_in = UserCreate(
        tenant_id=tenant.id,
        email=tenant.contact_email,
        first_name=tenant.name,  # can be refined later
        last_name="Admin",
        phone=tenant.contact_phone,
        password=temp_password,
        roles=[RoleName.HOSPITAL_ADMIN],
    )
    return create_user(db, user_in)


def get_user_by_email_and_tenant(
    db: Session,
    email: str,
    tenant_id: UUID | None,
) -> User | None:
    """
    For now we require email + tenant_id match.
    Future: support SUPER_ADMIN with tenant_id=None.
    """
    query = db.query(User).filter(User.email == email)
   
    if tenant_id is not None:
        query = query.filter(User.tenant_id == tenant_id)
    
    return query.first()