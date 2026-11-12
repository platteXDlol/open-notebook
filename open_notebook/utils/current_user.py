"""
Current user context for the authenticated request/command.

Multi-user slice 1 (docs/plans/multi-user.md). Set once per request by the
auth middleware (slice 2) and per background command from the job's
`user_id` (slice 4); domain code reads it via `require_user()` instead of
threading the user through every call.
"""

from contextvars import ContextVar
from typing import Optional

from open_notebook.domain.user import User
from open_notebook.exceptions import AuthenticationError

current_user: ContextVar[Optional[User]] = ContextVar("current_user", default=None)


def require_user() -> User:
    """Return the current user or raise AuthenticationError (mapped to 401)."""
    user = current_user.get()
    if user is None:
        raise AuthenticationError("Authentication required")
    return user
