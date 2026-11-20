"""
Shared API dependencies: database session, current user, permission checks
"""
from typing import Callable

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session

from app.core.security import Permission, decode_token, has_permission
from app.db.models import RevokedToken, User
from app.db.session import get_db

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/token")

CREDENTIALS_ERROR = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Invalid or expired credentials",
    headers={"WWW-Authenticate": "Bearer"},
)


def is_revoked(db: Session, jti: str) -> bool:
    return db.get(RevokedToken, jti) is not None


def get_token_payload(token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)) -> dict:
    payload = decode_token(token, expected_type="access")
    if payload is None or is_revoked(db, payload["jti"]):
        raise CREDENTIALS_ERROR
    return payload


def get_current_user(payload: dict = Depends(get_token_payload), db: Session = Depends(get_db)) -> User:
    """The authenticated, active user (checked on every request, so disabling takes effect at once)"""
    user = db.get(User, int(payload["sub"]))
    if user is None or not user.is_active:
        raise CREDENTIALS_ERROR
    return user


def require_permission(permission: Permission) -> Callable[..., User]:
    def checker(user: User = Depends(get_current_user)) -> User:
        if not has_permission(user.role, permission):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Insufficient permissions (requires {permission.value})",
            )
        return user

    return checker
