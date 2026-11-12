"""
User accounts, password hashing, and server-side sessions.

Multi-user slice 1 (docs/plans/multi-user.md). Passwords are hashed with
scrypt (stdlib, per-user salt); sessions are random tokens whose sha256
hash is stored server-side (the raw token goes into a cookie in slice 2).
"""

import base64
import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, ClassVar, Dict, Optional

from loguru import logger
from pydantic import field_validator

from open_notebook.database.repository import repo_create, repo_query
from open_notebook.domain.base import ObjectModel
from open_notebook.exceptions import (
    DatabaseOperationError,
    InvalidInputError,
    NotFoundError,
)

# scrypt parameters (OWASP minimum range). The stored hash is
# self-describing, so raising these later won't break existing hashes.
SCRYPT_N = 2**14
SCRYPT_R = 8
SCRYPT_P = 1
SCRYPT_KEY_LEN = 64
SESSION_TTL = timedelta(days=30)


def _sha256_token(raw_token: str) -> str:
    """Hash a session token before it ever touches the database."""
    return hashlib.sha256(raw_token.encode()).hexdigest()


def hash_password(password: str) -> str:
    """Hash a password with scrypt.

    Returns a self-describing string: scrypt$n$r$p$<b64 salt>$<b64 digest>.
    """
    if not password:
        raise InvalidInputError("Password must not be empty")
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(
        password.encode(),
        salt=salt,
        n=SCRYPT_N,
        r=SCRYPT_R,
        p=SCRYPT_P,
        dklen=SCRYPT_KEY_LEN,
        maxmem=SCRYPT_N * SCRYPT_R * 128 * 4,  # 4x the 128*n*r working memory
    )
    return "$".join(
        [
            "scrypt",
            str(SCRYPT_N),
            str(SCRYPT_R),
            str(SCRYPT_P),
            base64.b64encode(salt).decode(),
            base64.b64encode(digest).decode(),
        ]
    )


def verify_password(password: str, stored: str) -> bool:
    """Check a password against a stored scrypt hash. Never raises."""
    parts = stored.split("$")
    if len(parts) != 6 or parts[0] != "scrypt":
        return False
    try:
        n, r, p = int(parts[1]), int(parts[2]), int(parts[3])
        salt = base64.b64decode(parts[4], validate=True)
        expected = base64.b64decode(parts[5], validate=True)
    except (ValueError, TypeError):
        return False
    # Sanity bounds so a corrupted row cannot force a huge allocation.
    if not 1 <= n <= 2**20 or not 1 <= r <= 256 or not 1 <= p <= 64:
        return False
    try:
        digest = hashlib.scrypt(
            password.encode(),
            salt=salt,
            n=n,
            r=r,
            p=p,
            dklen=len(expected),
            maxmem=n * r * 128 * 4,
        )
    except Exception:
        return False
    return secrets.compare_digest(digest, expected)


class User(ObjectModel):
    table_name: ClassVar[str] = "user"
    username: str
    password_hash: str
    role: str = "user"
    auth_provider: str = "local"

    @classmethod
    async def create(cls, username: str, password: str, role: str = "user") -> "User":
        """Create a user with a scrypt-hashed password."""
        username = username.strip()
        if not username:
            raise InvalidInputError("Username must not be empty")
        try:
            record = await repo_create(
                "user",
                {
                    "username": username,
                    "password_hash": hash_password(password),
                    "role": role,
                },
            )
        except Exception as e:
            logger.exception(f"Error creating user {username}: {str(e)}")
            raise DatabaseOperationError(f"Failed to create user: {e}") from e
        return cls(**record)


class Session(ObjectModel):
    table_name: ClassVar[str] = "session"
    token_hash: str
    user: str
    expires: datetime

    @field_validator("expires", mode="before")
    @classmethod
    def parse_expires(cls, value):
        if isinstance(value, str):
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        return value

    @classmethod
    async def create(cls, user: User) -> str:
        """Create a session for `user`; returns the raw token (cookie value)."""
        token = secrets.token_urlsafe(32)  # 256 bits of entropy
        data: Dict[str, Any] = {
            "token_hash": _sha256_token(token),
            "user": user.id,
            "expires": datetime.now(timezone.utc) + SESSION_TTL,
        }
        try:
            await repo_create("session", data)
        except Exception as e:
            raise DatabaseOperationError(f"Failed to create session: {e}") from e
        return token

    @classmethod
    async def resolve(cls, raw_token: str) -> Optional[User]:
        """Return the user for a raw token, or None if unknown/expired."""
        if not raw_token:
            return None
        rows = await repo_query(
            "SELECT * FROM session WHERE token_hash = $token_hash",
            {"token_hash": _sha256_token(raw_token)},
        )
        if not rows:
            return None
        row = rows[0]
        expires = row["expires"]
        if isinstance(expires, str):
            expires = datetime.fromisoformat(expires.replace("Z", "+00:00"))
        if expires <= datetime.now(timezone.utc):
            await cls.revoke(raw_token)
            return None
        try:
            return await User.get(row["user"])
        except NotFoundError:
            # The user was deleted out from under the session.
            await cls.revoke(raw_token)
            return None

    @classmethod
    async def revoke(cls, raw_token: str) -> None:
        """Delete the session for a raw token (no-op if unknown)."""
        if not raw_token:
            return
        await repo_query(
            "DELETE FROM session WHERE token_hash = $token_hash",
            {"token_hash": _sha256_token(raw_token)},
        )
