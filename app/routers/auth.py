"""Authentication router."""

import threading
import time
import uuid
from collections import defaultdict, deque

from fastapi import APIRouter, HTTPException, Request, status
from sqlmodel import select

from ..auth import CurrentUser, create_access_token, verify_password, can_view_completed, can_cancel
from ..database import SessionDep
from ..models import User, AuditLog
from ..schemas import LoginRequest, TokenResponse, UserResponse

router = APIRouter(prefix="/api/auth", tags=["auth"])

# 5 wrong passwords for one login from one address in 5 minutes -> refused for 5 minutes.
# Keyed by login AND address, so nobody can lock the booth's tablets out from elsewhere.
MAX_FAILURES, WINDOW_SECONDS = 5, 300
_failures: dict[tuple[str, str], deque] = defaultdict(deque)
_failures_lock = threading.Lock()


def client_address(request: Request) -> str:
    # Caddy replaces any client-sent X-Forwarded-For with the real address; the app port is not public.
    forwarded = request.headers.get("x-forwarded-for", "")
    return forwarded.split(",")[-1].strip() or (request.client.host if request.client else "unknown")


def _recent(key, now):
    attempts = _failures[key]
    while attempts and now - attempts[0] > WINDOW_SECONDS:
        attempts.popleft()
    return attempts


def reset_login_limits() -> None:
    with _failures_lock:
        _failures.clear()


@router.post("/login", response_model=TokenResponse)
def login(login_data: LoginRequest, session: SessionDep, request: Request) -> TokenResponse:
    key = (login_data.username.strip().lower()[:50], client_address(request)[:64])
    now = time.monotonic()
    with _failures_lock:
        if len(_recent(key, now)) >= MAX_FAILURES:
            raise HTTPException(429, "Too many wrong passwords. Wait 5 minutes, or ask the Booth Lead.")
    user = session.exec(
        select(User).where(User.username == login_data.username)
    ).first()
    if not user or not verify_password(login_data.password, user.password_hash):
        with _failures_lock:
            if len(_failures) > 5000:  # keep memory bounded: drop expired entries
                for stale in [k for k in _failures if not _recent(k, now)]:
                    del _failures[stale]
            attempts = _recent(key, now)
            attempts.append(now)
            locked = len(attempts) == MAX_FAILURES
        if locked:
            session.add(AuditLog(id=str(uuid.uuid4()), staff_username=key[0][:50], action="LOGIN_LOCKED",
                                 details=f"{MAX_FAILURES} wrong passwords from {key[1]}; refused for 5 minutes"))
            session.commit()
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    with _failures_lock:
        _failures.pop(key, None)
    token = create_access_token(
        user_id=user.id, username=user.username, role=user.role
    )
    session.add(AuditLog(id=str(uuid.uuid4()), staff_username=user.username,
                         action="LOGIN", details="Staff signed in"))
    session.commit()
    return TokenResponse(
        access_token=token,
        token_type="bearer",
        role=user.role,
        username=user.username,
        can_view_completed=can_view_completed(session, user),
        can_cancel=can_cancel(session, user),
    )


@router.get("/me", response_model=UserResponse)
def get_me(user: CurrentUser, session: SessionDep) -> UserResponse:
    return UserResponse(id=user.id, username=user.username, role=user.role,
                        can_view_completed=can_view_completed(session, user),
                        can_cancel=can_cancel(session, user))


@router.post("/logout")
def logout(user: CurrentUser, session: SessionDep):
    session.add(AuditLog(id=str(uuid.uuid4()), staff_username=user.username,
                         action="LOGOUT", details="Staff signed out"))
    session.commit()
    return {"status": "signed_out"}

