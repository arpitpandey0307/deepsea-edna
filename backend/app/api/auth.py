"""
Authentication and user management endpoints
"""
import logging
import re
from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request, status
from fastapi.security import OAuth2PasswordRequestForm
from pydantic import BaseModel, EmailStr, Field, field_validator
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_token_payload, is_revoked, require_permission
from app.core.config import settings
from app.core.rate_limit import RateLimiter, client_ip
from app.core.security import (
    MAX_PASSWORD_BYTES, ROLE_DESCRIPTIONS, ROLE_PERMISSIONS, Permission, UserRole,
    create_access_token, create_refresh_token, decode_token, hash_password, utcnow, verify_password,
)
from app.db.models import RevokedToken, User
from app.db.session import get_db
from app.services.users import (
    get_user_by_email, get_user_by_login, initial_role, issue_tokens, normalize_email, username_taken,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["Authentication"])
auth_limiter = RateLimiter(settings.LOGIN_RATE_LIMIT_PER_MINUTE)

USERNAME_PATTERN = re.compile(r"^[a-zA-Z0-9_.-]{3,32}$")


# ==================== Schemas ====================

class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class RegisterRequest(BaseModel):
    username: str
    email: EmailStr
    full_name: str = Field(..., min_length=1, max_length=200)
    password: str = Field(..., min_length=8)

    @field_validator("username")
    @classmethod
    def valid_username(cls, v: str) -> str:
        if not USERNAME_PATTERN.match(v):
            raise ValueError("must be 3-32 characters: letters, digits, '_', '.' or '-'")
        return v

    @field_validator("password")
    @classmethod
    def valid_password(cls, v: str) -> str:
        if len(v.encode("utf-8")) > MAX_PASSWORD_BYTES:
            raise ValueError(f"must be at most {MAX_PASSWORD_BYTES} bytes")
        if v.isdigit() or v.isalpha():
            raise ValueError("must contain both letters and numbers or symbols")
        return v


class RefreshRequest(BaseModel):
    refresh_token: str


class LogoutRequest(BaseModel):
    refresh_token: Optional[str] = None


class ProfileUpdate(BaseModel):
    full_name: str = Field(..., min_length=1, max_length=200)


class UserUpdate(BaseModel):
    role: Optional[UserRole] = None
    is_active: Optional[bool] = None


class UserResponse(BaseModel):
    id: int
    username: str
    email: str
    full_name: str
    role: str
    is_active: bool
    disabled: bool
    avatar_url: Optional[str] = None
    providers: List[str] = []
    has_password: bool
    created_at: datetime
    last_login: Optional[datetime] = None


def to_response(user: User) -> UserResponse:
    return UserResponse(
        id=user.id,
        username=user.username,
        email=user.email,
        full_name=user.full_name,
        role=user.role,
        is_active=user.is_active,
        disabled=not user.is_active,
        avatar_url=user.avatar_url,
        providers=sorted({a.provider for a in user.oauth_accounts}),
        has_password=user.hashed_password is not None,
        created_at=user.created_at,
        last_login=user.last_login,
    )


def revoke(db: Session, payload: dict):
    if not is_revoked(db, payload["jti"]):
        db.add(RevokedToken(jti=payload["jti"], expires_at=datetime.fromtimestamp(payload["exp"], tz=timezone.utc)))


def purge_expired_revocations(db: Session):
    db.execute(delete(RevokedToken).where(RevokedToken.expires_at < utcnow()))


# ==================== Sign-in ====================

@router.get("/providers")
def list_providers():
    """Which sign-in methods are enabled (the login page shows matching buttons)"""
    return {
        "password": True,
        "google": bool(settings.GOOGLE_CLIENT_ID and settings.GOOGLE_CLIENT_SECRET),
        "github": bool(settings.GITHUB_CLIENT_ID and settings.GITHUB_CLIENT_SECRET),
    }


@router.post("/register", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
def register(payload: RegisterRequest, request: Request, db: Session = Depends(get_db)):
    """Create an account. New users are viewers unless their email is listed in ADMIN_EMAILS."""
    auth_limiter.check(f"register:{client_ip(request)}")
    email = normalize_email(payload.email)
    if get_user_by_email(db, email):
        raise HTTPException(status_code=409, detail="An account with this email already exists")
    if username_taken(db, payload.username):
        raise HTTPException(status_code=409, detail="This username is taken")

    user = User(
        email=email,
        username=payload.username,
        full_name=payload.full_name.strip(),
        hashed_password=hash_password(payload.password),
        role=initial_role(email),
    )
    db.add(user)
    db.commit()
    logger.info(f"Registered user {user.username} ({user.role})")
    return to_response(user)


@router.post("/token", response_model=TokenResponse)
def login(request: Request, form_data: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)):
    """OAuth2 password login; `username` may be the username or the email address"""
    auth_limiter.check(f"login:{client_ip(request)}")
    user = get_user_by_login(db, form_data.username)
    if not user or not verify_password(form_data.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if not user.is_active:
        raise HTTPException(status_code=403, detail="This account has been disabled")
    return issue_tokens(db, user)


@router.post("/refresh", response_model=TokenResponse)
def refresh(
    refresh_token: Optional[str] = Query(None),
    body: Optional[RefreshRequest] = Body(None),
    db: Session = Depends(get_db),
):
    """Exchange a refresh token for a new token pair (the old refresh token is revoked)"""
    token = refresh_token or (body.refresh_token if body else None)
    payload = decode_token(token or "", expected_type="refresh")
    if payload is None or is_revoked(db, payload["jti"]):
        raise HTTPException(status_code=401, detail="Invalid refresh token")
    user = db.get(User, int(payload["sub"]))
    if user is None or not user.is_active:
        raise HTTPException(status_code=401, detail="Invalid refresh token")

    revoke(db, payload)
    db.commit()
    return {
        "access_token": create_access_token(user.id, user.role),
        "refresh_token": create_refresh_token(user.id),
        "token_type": "bearer",
    }


@router.post("/logout")
def logout(
    body: Optional[LogoutRequest] = Body(None),
    payload: dict = Depends(get_token_payload),
    db: Session = Depends(get_db),
):
    """Revoke the current access token (and the refresh token, if provided)"""
    revoke(db, payload)
    if body and body.refresh_token:
        refresh_payload = decode_token(body.refresh_token, expected_type="refresh")
        if refresh_payload and refresh_payload["sub"] == payload["sub"]:
            revoke(db, refresh_payload)
    purge_expired_revocations(db)
    db.commit()
    return {"message": "Successfully logged out"}


# ==================== Profile ====================

@router.get("/me", response_model=UserResponse)
def get_me(user: User = Depends(get_current_user)):
    return to_response(user)


@router.patch("/me", response_model=UserResponse)
def update_me(update: ProfileUpdate, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    user.full_name = update.full_name.strip()
    db.commit()
    return to_response(user)


@router.get("/permissions")
def get_permissions(_: User = Depends(get_current_user)):
    """Role -> permission matrix"""
    return {
        "roles": [
            {
                "role": role.value,
                "description": ROLE_DESCRIPTIONS[role],
                "permissions": sorted(p.value for p in ROLE_PERMISSIONS[role]),
            }
            for role in UserRole
        ],
        "all_permissions": [p.value for p in Permission],
    }


# ==================== Administration ====================

@router.get("/users")
def list_users(_: User = Depends(require_permission(Permission.USER_MANAGE)), db: Session = Depends(get_db)):
    users = db.scalars(select(User).order_by(User.created_at)).all()
    return {"total": len(users), "users": [to_response(u) for u in users]}


@router.patch("/users/{user_id}", response_model=UserResponse)
def update_user(
    user_id: int,
    update: UserUpdate,
    admin: User = Depends(require_permission(Permission.USER_MANAGE)),
    db: Session = Depends(get_db),
):
    """Change a user's role or enable/disable the account"""
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    if user.id == admin.id:
        raise HTTPException(status_code=400, detail="You cannot change your own role or status")
    if user.email in settings.admin_emails and (
        (update.role and update.role != UserRole.ADMIN) or update.is_active is False
    ):
        raise HTTPException(status_code=400, detail="This admin is configured in ADMIN_EMAILS on the server")

    if update.role is not None:
        user.role = update.role.value
    if update.is_active is not None:
        user.is_active = update.is_active
    db.commit()
    logger.info(f"{admin.username} updated {user.username}: role={user.role} active={user.is_active}")
    return to_response(user)


@router.get("/security-stats")
def security_stats(_: User = Depends(require_permission(Permission.USER_MANAGE)), db: Session = Depends(get_db)):
    by_role = dict(db.execute(select(User.role, func.count()).group_by(User.role)).all())
    total = db.scalar(select(func.count()).select_from(User)) or 0
    active = db.scalar(select(func.count()).select_from(User).where(User.is_active.is_(True))) or 0
    return {
        "total_users": total,
        "active_users": active,
        "disabled_users": total - active,
        "users_by_role": {role.value: by_role.get(role.value, 0) for role in UserRole},
        "revoked_tokens": db.scalar(select(func.count()).select_from(RevokedToken)) or 0,
        "roles_available": [r.value for r in UserRole],
        "permissions_available": [p.value for p in Permission],
    }
