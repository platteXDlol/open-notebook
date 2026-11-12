"""
Authentication router: setup, login, logout, session user, password change.

Multi-user slice 2 (docs/plans/multi-user.md). Sessions are random tokens
stored hashed server-side (slice 1) and carried in the HttpOnly on_session
cookie. Login is rate limited to 10 failed attempts per username + client
IP within 5 minutes (in-memory; fine for a single-process API).
"""

import time
from collections import defaultdict, deque
from typing import Deque, Dict, Tuple

from fastapi import APIRouter, HTTPException, Request, Response
from loguru import logger
from pydantic import BaseModel

from api.auth import SESSION_COOKIE_NAME
from api.models import AuthRequest, PasswordChangeRequest, UserResponse
from open_notebook.domain.user import (
    SESSION_TTL,
    Session,
    User,
    hash_password,
    verify_password,
)
from open_notebook.exceptions import AuthenticationError, RateLimitError
from open_notebook.utils.current_user import current_user, require_user

router = APIRouter(prefix="/auth", tags=["auth"])

# Login rate limit: at most _LOGIN_MAX_FAILURES failures per
# (username, client IP) within _LOGIN_WINDOW_SECONDS.
_LOGIN_MAX_FAILURES = 10
_LOGIN_WINDOW_SECONDS = 300
_login_failures: Dict[Tuple[str, str], Deque[float]] = defaultdict(deque)


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _check_login_rate_limit(key: Tuple[str, str]) -> None:
    """Raise RateLimitError if `key` is within the failure window."""
    now = time.monotonic()
    events = _login_failures[key]
    while events and now - events[0] > _LOGIN_WINDOW_SECONDS:
        events.popleft()
    if len(events) >= _LOGIN_MAX_FAILURES:
        raise RateLimitError("Too many failed login attempts. Try again later.")


def _record_login_failure(key: Tuple[str, str]) -> None:
    _login_failures[key].append(time.monotonic())


def _clear_login_failures(key: Tuple[str, str]) -> None:
    _login_failures.pop(key, None)


def _session_cookie_max_age() -> int:
    return int(SESSION_TTL.total_seconds())


def _set_session_cookie(request: Request, response: Response, token: str) -> None:
    response.set_cookie(
        SESSION_COOKIE_NAME,
        token,
        max_age=_session_cookie_max_age(),
        path="/",
        httponly=True,
        samesite="lax",
        secure=request.url.scheme == "https",
    )


class _LogoutMessage(BaseModel):
    message: str


@router.get("/status")
async def get_auth_status() -> dict:
    """Report whether the instance still needs setup and the session user."""
    try:
        setup_required = not await User.any_exists()
    except Exception:
        # A DB failure must not masquerade as "not set up": setup is only
        # allowed on a verified-empty instance.
        setup_required = False
    user = current_user.get()
    return {
        "setup_required": setup_required,
        "user": UserResponse.model_validate(user) if user else None,
    }


@router.post("/setup")
async def setup_first_admin(
    body: AuthRequest, request: Request, response: Response
) -> UserResponse:
    """Create the first admin account. Only available while no user exists."""
    if await User.any_exists():
        raise HTTPException(status_code=409, detail="Setup already completed")
    user = await User.create(body.username, body.password, role="admin")
    token = await Session.create(user)
    _set_session_cookie(request, response, token)
    logger.info(f"First admin account '{user.username}' created via setup")
    return UserResponse.model_validate(user)


@router.post("/login")
async def login(
    body: AuthRequest, request: Request, response: Response
) -> UserResponse:
    """Exchange username + password for a session cookie."""
    key = (body.username.lower(), _client_ip(request))
    _check_login_rate_limit(key)
    user = await User.get_by_username(body.username)
    if user is None or not verify_password(body.password, user.password_hash):
        _record_login_failure(key)
        raise AuthenticationError("Invalid username or password")
    _clear_login_failures(key)
    token = await Session.create(user)
    _set_session_cookie(request, response, token)
    return UserResponse.model_validate(user)


@router.post("/logout")
async def logout(request: Request, response: Response) -> _LogoutMessage:
    """Revoke the session behind the on_session cookie."""
    require_user()
    raw_token = request.cookies.get(SESSION_COOKIE_NAME)
    if raw_token:
        await Session.revoke(raw_token)
    response.delete_cookie(
        SESSION_COOKIE_NAME,
        path="/",
        httponly=True,
        samesite="lax",
        secure=request.url.scheme == "https",
    )
    return _LogoutMessage(message="Logged out")


@router.get("/me")
async def get_current_user() -> UserResponse:
    """Return the authenticated user."""
    return UserResponse.model_validate(require_user())


@router.put("/me/password")
async def change_my_password(body: PasswordChangeRequest) -> _LogoutMessage:
    """Change the password of the session user."""
    user = require_user()
    if not verify_password(body.current_password, user.password_hash):
        raise AuthenticationError("Current password is incorrect")
    user.password_hash = hash_password(body.new_password)
    await user.save()
    return _LogoutMessage(message="Password changed")
