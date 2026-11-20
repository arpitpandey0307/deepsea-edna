"""
User account helpers shared by password and OAuth sign-in
"""
import re
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.security import UserRole, create_access_token, create_refresh_token, utcnow
from app.db.models import User


def normalize_email(email: str) -> str:
    return email.strip().lower()


def get_user_by_login(db: Session, identifier: str) -> Optional[User]:
    """Find a user by username or email (case-insensitive)"""
    ident = identifier.strip().lower()
    return db.scalar(
        select(User).where((func.lower(User.username) == ident) | (User.email == ident))
    )


def get_user_by_email(db: Session, email: str) -> Optional[User]:
    return db.scalar(select(User).where(User.email == normalize_email(email)))


def username_taken(db: Session, username: str) -> bool:
    return db.scalar(select(User.id).where(func.lower(User.username) == username.lower())) is not None


def unique_username(db: Session, base: str) -> str:
    """Derive an available username from an email prefix or provider login"""
    candidate = re.sub(r"[^a-zA-Z0-9_.-]", "", base)[:30] or "user"
    if len(candidate) < 3:
        candidate = f"{candidate}user"
    name, n = candidate, 1
    while username_taken(db, name):
        n += 1
        name = f"{candidate}{n}"
    return name


def initial_role(email: str) -> str:
    return UserRole.ADMIN.value if normalize_email(email) in settings.admin_emails else UserRole.VIEWER.value


def apply_admin_bootstrap(user: User):
    """Accounts listed in ADMIN_EMAILS are always admins"""
    if user.email in settings.admin_emails and user.role != UserRole.ADMIN.value:
        user.role = UserRole.ADMIN.value


def issue_tokens(db: Session, user: User) -> dict:
    """Record the login and return a fresh access/refresh token pair"""
    apply_admin_bootstrap(user)
    user.last_login = utcnow()
    db.commit()
    return {
        "access_token": create_access_token(user.id, user.role),
        "refresh_token": create_refresh_token(user.id),
        "token_type": "bearer",
    }
