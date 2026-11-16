"""
Admin-only user management endpoints (multi-user slice 2).

Every route requires a valid session with the admin role (403 otherwise).
User deletion is deferred to slice 7 and answered with 501 until then.
"""

from fastapi import APIRouter, HTTPException

from api.models import PasswordResetRequest, UserCreateRequest, UserResponse
from open_notebook.domain.user import User, hash_password
from open_notebook.exceptions import NotFoundError
from open_notebook.utils.current_user import require_user

router = APIRouter(prefix="/users", tags=["users"])


def _require_admin() -> User:
    user = require_user()
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="Admin privileges required")
    return user


@router.get("")
async def list_users():
    """List all users (no passwords or password hashes)."""
    _require_admin()
    users = await User.get_all(order_by="username")
    return [UserResponse.model_validate(u) for u in users]


@router.post("")
async def create_user(body: UserCreateRequest):
    """Create a user. No self-registration: only admins can create users."""
    _require_admin()
    if await User.get_by_username(body.username) is not None:
        raise HTTPException(
            status_code=409, detail=f"Username '{body.username}' already exists"
        )
    user = await User.create(body.username, body.password, role=body.role)
    return UserResponse.model_validate(user)


@router.put("/{user_id}/password")
async def reset_user_password(user_id: str, body: PasswordResetRequest):
    """Reset another user's password (admin only)."""
    _require_admin()
    user = await User.get(user_id)
    if user is None:
        raise NotFoundError(f"User '{user_id}' not found")
    user.password_hash = hash_password(body.password)
    await user.save()
    return {"message": f"Password reset for {user.username}"}


@router.delete("/{user_id}")
async def delete_user(user_id: str):
    """Deletion (including their content and sessions) lands in slice 7."""
    _require_admin()
    raise HTTPException(
        status_code=501,
        detail="User deletion is not implemented yet (multi-user slice 7)",
    )
