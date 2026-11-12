"""
Session-based auth middleware for the Open Notebook API.

Multi-user slice 2 (docs/plans/multi-user.md): every /api/* request is
authenticated via the on_session cookie (HttpOnly, set by /api/auth/login
and /api/auth/setup). A valid session sets `current_user` for the request.
On a fresh instance (no user exists yet) every protected endpoint answers
409 {"detail": "setup_required"} so the first admin can run /api/auth/setup.
"""

from typing import Optional

from fastapi import Request
from loguru import logger
from starlette.middleware.base import (
    BaseHTTPMiddleware,
    RequestResponseEndpoint,
)
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp

from open_notebook.domain.user import Session, User
from open_notebook.utils.current_user import current_user

SESSION_COOKIE_NAME = "on_session"

# /api paths the middleware must never block: status reports the setup
# state, and login/setup are how you obtain a session.
API_AUTH_EXEMPT_PATHS = ["/api/auth/status", "/api/auth/login", "/api/auth/setup"]


class SessionAuthMiddleware(BaseHTTPMiddleware):
    """Authenticate /api/* requests with the on_session cookie.

    - Non-/api paths, excluded paths and OPTIONS (CORS preflight) pass through.
    - A valid session sets `current_user` for the duration of the request
      (also for the exempt auth endpoints, so /status can report the user).
    - A missing/invalid session is rejected with 401, or 409
      setup_required while no user exists yet.
    """

    def __init__(
        self, app: ASGIApp, excluded_paths: Optional[list[str]] = None
    ) -> None:
        super().__init__(app)
        self.excluded_paths: list[str] = excluded_paths or [
            "/",
            "/health",
            "/docs",
            "/openapi.json",
            "/redoc",
        ]

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        path = request.url.path

        if (
            path in self.excluded_paths
            or request.method == "OPTIONS"
            or not path.startswith("/api")
        ):
            return await call_next(request)

        user: Optional[User] = None
        raw_token = request.cookies.get(SESSION_COOKIE_NAME)
        if raw_token:
            try:
                user = await Session.resolve(raw_token)
            except Exception as e:
                # A session-table failure must not take down unauthenticated
                # endpoints (e.g. /api/auth/status on a broken DB); treat the
                # cookie as invalid.
                logger.warning(f"Session resolution failed: {e}")
                user = None

        if user is None and path not in API_AUTH_EXEMPT_PATHS:
            try:
                has_users = await User.any_exists()
            except Exception:
                # Fail closed: if we cannot tell whether the instance is
                # set up, require authentication instead of opening it up.
                logger.warning("Could not check for existing users; requiring auth")
                has_users = True
            if not has_users:
                return JSONResponse(
                    status_code=409, content={"detail": "setup_required"}
                )
            return JSONResponse(
                status_code=401, content={"detail": "Authentication required"}
            )

        token = current_user.set(user)
        try:
            return await call_next(request)
        finally:
            current_user.reset(token)
