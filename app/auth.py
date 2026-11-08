"""Authentication, password hashing, JWT tokens, and role-based access control."""

import datetime
import logging
import os
import secrets
from typing import Annotated, List

import bcrypt
import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlmodel import Session

from .database import SessionDep
from .models import User, CompletedAccess, CancelAccess

# Secrets live only in a git-ignored .env, never in the repository.
PRODUCTION = os.environ.get("APP_ENV") == "production"


def _check_production_secrets() -> None:
    """A server stops with a clear message rather than run with a missing or weak secret."""
    if len(os.environ.get("SECRET_KEY", "")) < 32:
        raise RuntimeError("SECRET_KEY must be set in .env (at least 32 characters): openssl rand -hex 32")
    if not os.environ.get("ADMIN_PASSWORD"):
        raise RuntimeError("ADMIN_PASSWORD must be set in .env on a server")


def _signing_key() -> str:
    if PRODUCTION:
        _check_production_secrets()
    key = os.environ.get("SECRET_KEY", "")
    if key:
        return key
    # Laptop or test run without .env: a random key per process, so no key is ever written in code.
    logging.getLogger(__name__).warning("SECRET_KEY not set; using a random development key (logins reset on restart)")
    return secrets.token_hex(32)


SECRET_KEY = _signing_key()
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE = datetime.timedelta(hours=14)  # Valid for full event day

bearer = HTTPBearer(auto_error=False)


def hash_password(password: str) -> str:
    # Truncate to 72 bytes to adhere to bcrypt specification
    pw_bytes = password.encode("utf-8")[:72]
    return bcrypt.hashpw(pw_bytes, bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    try:
        pw_bytes = password.encode("utf-8")[:72]
        return bcrypt.checkpw(pw_bytes, password_hash.encode("utf-8"))
    except (ValueError, TypeError):
        return False


def create_access_token(user_id: int, username: str, role: str) -> str:
    expire = datetime.datetime.now(datetime.timezone.utc) + ACCESS_TOKEN_EXPIRE
    claims = {
        "sub": str(user_id),
        "username": username,
        "role": role,
        "exp": expire,
    }
    return jwt.encode(claims, SECRET_KEY, algorithm=ALGORITHM)


def get_current_user(
    session: SessionDep,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
) -> User:
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
            headers={"WWW-Authenticate": "Bearer"},
        )
    try:
        claims = jwt.decode(credentials.credentials, SECRET_KEY, algorithms=[ALGORITHM])
        user_id = int(claims["sub"])
    except (jwt.InvalidTokenError, KeyError, ValueError):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    user = session.get(User, user_id)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


def require_role(allowed_roles: List[str]):
    def role_checker(user: CurrentUser) -> User:
        if user.role not in allowed_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Access forbidden: requires one of {allowed_roles}",
            )
        return user

    return role_checker


RequireBarista = Annotated[
    User, Depends(require_role(["barista", "booth_lead", "admin"]))
]
RequireBoothLead = Annotated[User, Depends(require_role(["booth_lead", "admin"]))]
RequireAdmin = Annotated[User, Depends(require_role(["admin"]))]


def can_cancel(session: Session, user: User) -> bool:
    """BRD A15: every role may cancel; a Booth Lead or Admin can switch a barista's right off."""
    if user.role == "admin":
        return True
    setting = session.get(CancelAccess, user.id)
    return user.role in ("barista", "booth_lead") and (setting is None or setting.enabled)


def can_view_completed(session: Session, user: User) -> bool:
    if user.role == "admin":
        return True
    setting = session.get(CompletedAccess, user.id)
    return user.role in ("barista", "booth_lead") and (setting is None or setting.enabled)


def require_completed_access(session: SessionDep, user: RequireBarista) -> User:
    if not can_view_completed(session, user):
        raise HTTPException(403, "Completed orders are disabled for your account")
    return user


RequireCompletedAccess = Annotated[User, Depends(require_completed_access)]

