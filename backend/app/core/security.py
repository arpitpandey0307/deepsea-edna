"""
Password hashing, JWT tokens and role-based permissions
"""
import uuid
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Dict, Optional, Set

import bcrypt
import jwt

from app.core.config import settings

# bcrypt only uses the first 72 bytes of a password; longer input is rejected at validation
MAX_PASSWORD_BYTES = 72


class UserRole(str, Enum):
    ADMIN = "admin"
    SCIENTIST = "scientist"
    VIEWER = "viewer"
    API_USER = "api_user"


class Permission(str, Enum):
    PREDICT = "predict"
    PREDICT_BATCH = "predict:batch"
    MODEL_VIEW = "model:view"
    MODEL_MANAGE = "model:manage"
    VERSION_VIEW = "version:view"
    VERSION_MANAGE = "version:manage"
    EXPERIMENT_VIEW = "experiment:view"
    EXPERIMENT_MANAGE = "experiment:manage"
    CACHE_VIEW = "cache:view"
    CACHE_MANAGE = "cache:manage"
    MONITORING_VIEW = "monitoring:view"
    USER_MANAGE = "user:manage"
    SYSTEM_ADMIN = "system:admin"


ROLE_DESCRIPTIONS: Dict[UserRole, str] = {
    UserRole.ADMIN: "Full system access, user management and monitoring",
    UserRole.SCIENTIST: "Classify sequences, manage model versions and experiments",
    UserRole.VIEWER: "Classify sequences and view models and experiments",
    UserRole.API_USER: "Programmatic prediction access only",
}

_CLASSIFY = {Permission.PREDICT, Permission.PREDICT_BATCH}
_VIEW_MLOPS = {Permission.MODEL_VIEW, Permission.VERSION_VIEW, Permission.EXPERIMENT_VIEW}

# Single source of truth for authorization (also served to the frontend)
ROLE_PERMISSIONS: Dict[UserRole, Set[Permission]] = {
    UserRole.ADMIN: set(Permission),
    UserRole.SCIENTIST: _CLASSIFY | _VIEW_MLOPS | {
        Permission.MODEL_MANAGE, Permission.VERSION_MANAGE, Permission.EXPERIMENT_MANAGE,
    },
    UserRole.VIEWER: _CLASSIFY | _VIEW_MLOPS,
    UserRole.API_USER: set(_CLASSIFY),
}


def has_permission(role: str, permission: Permission) -> bool:
    try:
        return permission in ROLE_PERMISSIONS[UserRole(role)]
    except ValueError:
        return False


# ==================== Passwords ====================

def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, hashed: Optional[str]) -> bool:
    if not hashed:
        return False  # OAuth-only account
    try:
        return bcrypt.checkpw(password.encode("utf-8"), hashed.encode("utf-8"))
    except ValueError:
        return False


# ==================== JWT ====================

def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _encode(payload: dict, expires: timedelta) -> str:
    now = utcnow()
    payload = {**payload, "iat": now, "exp": now + expires, "jti": uuid.uuid4().hex}
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=settings.ALGORITHM)


def create_access_token(user_id: int, role: str) -> str:
    return _encode(
        {"sub": str(user_id), "role": role, "type": "access"},
        timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES),
    )


def create_refresh_token(user_id: int) -> str:
    return _encode(
        {"sub": str(user_id), "type": "refresh"},
        timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS),
    )


def create_oauth_state(provider: str, nonce: str) -> str:
    return _encode({"provider": provider, "nonce": nonce, "type": "oauth_state"}, timedelta(minutes=10))


def decode_token(token: str, expected_type: str) -> Optional[dict]:
    """Return the payload if the token is valid, unexpired and of the expected type"""
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
    except jwt.PyJWTError:
        return None
    if payload.get("type") != expected_type:
        return None
    return payload
