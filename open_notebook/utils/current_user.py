"""
Current user context for the authenticated request/command.

Multi-user slice 1 (docs/plans/multi-user.md). Set once per request by the
auth middleware (slice 2) and per background command from the job's
`user_id` (slice 4); domain code reads it via `require_user()` instead of
threading the user through every call.
"""

from contextlib import asynccontextmanager
from contextvars import ContextVar
from typing import AsyncIterator, Optional

from open_notebook.domain.user import User
from open_notebook.exceptions import AuthenticationError, NotFoundError

current_user: ContextVar[Optional[User]] = ContextVar("current_user", default=None)


def require_user() -> User:
    """Return the current user or raise AuthenticationError (mapped to 401)."""
    user = current_user.get()
    if user is None:
        raise AuthenticationError("Authentication required")
    return user


@asynccontextmanager
async def run_as(user_id: str) -> AsyncIterator[User]:
    """Bind the job's submitting user to the current context.

    Multi-user slice 4: every background job carries a required `user_id`.
    This resolves it and binds it to `current_user` for the job's duration,
    so domain code (ownership stamping, owner-scoped queries) behaves as if
    the submitting user were present. A missing or unknown user raises
    ValueError — the "permanent failure, do not retry" signal — so the job
    fails before it can write anything.
    """
    if not user_id:
        raise ValueError("Job has no user_id; refusing to run")
    try:
        user = await User.get(user_id)
    except NotFoundError:
        raise ValueError(
            f"Job references unknown user {user_id!r}; refusing to run"
        ) from None
    token = current_user.set(user)
    try:
        yield user
    finally:
        current_user.reset(token)
