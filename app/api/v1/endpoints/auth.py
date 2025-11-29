from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.database import get_db
from app.core.security import decode_token
from app.models.user import User
from app.schemas.auth import LoginRequest, TokenResponse
from app.schemas.user import UserResponse
from app.services.auth_service import authenticate_user, issue_access_token_for_user

router = APIRouter()

settings = get_settings()

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login")


@router.get("/health", tags=["auth"])
async def auth_health_check() -> dict:
    """
    Simple health check for the auth module.
    """
    return {"status": "auth-ok"}


@router.post("/login", response_model=TokenResponse, tags=["auth"])
def login(
    form_data: OAuth2PasswordRequestForm = Depends(),
    db: Session = Depends(get_db),
) -> TokenResponse:
    """
    OAuth2-style login.

    For now, we expect:
    - username: email
    - password: password
    - tenant_id: optional, passed in `scopes` (hack) or we will refine later.

    To keep it simple for now, we'll assume a single tenant context per user login:
    - For hospital admins/doctor, pass tenant_id in the `scope` field or we'll
      infer later via query param. For hackathon/demo, we can also skip tenant_id
      and just look up by email if unique.
    """

    # Simple interpretation: we ignore tenant_id for now and assume email+tenant is unique enough.
    login_data = LoginRequest(email=form_data.username, password=form_data.password)

    # NOTE: For now, tenant_id=None (SUPER_ADMIN or unique email per tenant).
    # Later we can parse a tenant_id from form_data.scopes or query params.
    try:
        user = authenticate_user(db, login_data, tenant_id=None)
    except Exception as exc:
        print(exc)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc),
        ) from exc

    token = issue_access_token_for_user(user)
    return TokenResponse(access_token=token)


def get_current_user(
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db),
) -> User:
    """
    Dependency to retrieve the current user from a JWT bearer token.
    """
    from app.models.user import User  # local import to avoid cycles

    try:
        payload = decode_token(token)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token",
        )

    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token payload",
        )

    user = db.query(User).filter(User.id == UUID(user_id)).first()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found",
        )

    return user


@router.get("/me", response_model=UserResponse, tags=["auth"])
def read_current_user(
    current_user: User = Depends(get_current_user),
) -> UserResponse:
    """
    Return the current authenticated user.
    """
    roles = [r.name for r in current_user.roles]
    return UserResponse(
        id=current_user.id,
        tenant_id=current_user.tenant_id,
        email=current_user.email,
        first_name=current_user.first_name,
        last_name=current_user.last_name,
        phone=current_user.phone,
        department=current_user.department,
        specialization=current_user.specialization,
        status=current_user.status,
        roles=roles,
        created_at=current_user.created_at,
        updated_at=current_user.updated_at,
    )