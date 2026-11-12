"""
Tests for multi-user slice 1: user accounts, scrypt password hashing,
and server-side sessions (docs/plans/multi-user.md, slice 1).

No live database: repository functions are mocked at their patch point,
and the migration is asserted as text.
"""

import base64
import hashlib
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from open_notebook.domain.user import (
    Session,
    User,
    hash_password,
    verify_password,
)
from open_notebook.exceptions import AuthenticationError, InvalidInputError
from open_notebook.utils.current_user import current_user, require_user

MIGRATIONS_DIR = (
    Path(__file__).parent.parent / "open_notebook" / "database" / "migrations"
)

USER_ID = "user:abc"
FAKE_USER = User(id=USER_ID, username="ada", password_hash="scrypt$1$1$1$Zm9v$YmFy")


def sha256_hex(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


# ---------------------------------------------------------------------------
# Password hashing
# ---------------------------------------------------------------------------


def test_hash_and_verify_roundtrip():
    stored = hash_password("correct horse battery staple")
    assert verify_password("correct horse battery staple", stored)


def test_verify_rejects_wrong_password():
    stored = hash_password("s3cret")
    assert not verify_password("S3cret", stored)
    assert not verify_password("", stored)


def test_verify_rejects_malformed_stored_hash():
    assert not verify_password("pw", "argon2id$v=19$blah")
    assert not verify_password("pw", "not-a-hash")
    assert not verify_password("pw", "")
    assert not verify_password("pw", "scrypt$1$2$3")  # truncated


def test_hash_password_rejects_empty_password():
    with pytest.raises(InvalidInputError):
        hash_password("")


def test_stored_hash_is_scrypt_with_salt_and_params():
    stored = hash_password("pw")
    parts = stored.split("$")
    assert len(parts) == 6
    assert parts[0] == "scrypt"
    n, r, p = int(parts[1]), int(parts[2]), int(parts[3])
    assert n >= 2**14
    assert r == 8
    assert p == 1
    assert len(base64.b64decode(parts[4])) == 16  # per-user salt


def test_hashes_of_same_password_differ():
    assert hash_password("pw") != hash_password("pw")


# ---------------------------------------------------------------------------
# Migration 27
# ---------------------------------------------------------------------------


def test_migration_27_defines_user_and_session_tables():
    text = (MIGRATIONS_DIR / "27.surrealql").read_text()
    assert "DEFINE TABLE user SCHEMAFULL" in text
    assert "DEFINE TABLE session SCHEMAFULL" in text
    assert re.search(r"DEFINE INDEX \S+ ON user FIELDS username UNIQUE", text)
    assert re.search(r"DEFINE INDEX \S+ ON session FIELDS token_hash UNIQUE", text)
    assert "DEFINE FIELD created ON user" in text
    assert "DEFINE FIELD created ON session" in text


def test_migration_27_down_removes_both_tables():
    text = (MIGRATIONS_DIR / "27_down.surrealql").read_text()
    assert "REMOVE TABLE user" in text
    assert "REMOVE TABLE session" in text


# ---------------------------------------------------------------------------
# Session
# ---------------------------------------------------------------------------


def session_row(expires: datetime) -> dict:
    return {
        "id": "session:1",
        "token_hash": "hash",
        "user": USER_ID,
        "expires": expires.isoformat(),
    }


@pytest.mark.asyncio
async def test_session_create_stores_sha256_token_hash_not_raw_token():
    with patch(
        "open_notebook.domain.user.repo_create",
        new_callable=AsyncMock,
        return_value={},
    ) as mock_create:
        token = await Session.create(FAKE_USER)

    data = mock_create.call_args.args[1]
    assert token and len(token) >= 43  # 256 bits of entropy
    assert data["token_hash"] == sha256_hex(token)
    assert data["token_hash"] != token
    assert data["user"] == USER_ID
    assert isinstance(data["expires"], datetime)
    assert data["expires"] > datetime.now(timezone.utc)


@pytest.mark.asyncio
async def test_session_resolve_returns_user_for_valid_token():
    with (
        patch(
            "open_notebook.domain.user.repo_query",
            new_callable=AsyncMock,
            return_value=[session_row(datetime.now(timezone.utc) + timedelta(days=1))],
        ) as mock_query,
        patch(
            "open_notebook.domain.user.User.get",
            new_callable=AsyncMock,
            return_value=FAKE_USER,
        ) as mock_get,
    ):
        user = await Session.resolve("raw-token")

    assert user is FAKE_USER
    query, variables = mock_query.call_args.args
    assert variables["token_hash"] == sha256_hex("raw-token")
    assert "raw-token" not in query  # raw token never appears in SurrealQL
    mock_get.assert_awaited_once_with(USER_ID)


@pytest.mark.asyncio
async def test_session_resolve_rejects_expired_token():
    expired = datetime.now(timezone.utc) - timedelta(hours=1)
    with patch(
        "open_notebook.domain.user.repo_query",
        new_callable=AsyncMock,
        return_value=[session_row(expired)],
    ) as mock_query:
        assert await Session.resolve("raw-token") is None

    # the expired row is cleaned up
    assert any("DELETE" in call.args[0] for call in mock_query.await_args_list)


@pytest.mark.asyncio
async def test_session_resolve_returns_none_for_unknown_token():
    with patch(
        "open_notebook.domain.user.repo_query",
        new_callable=AsyncMock,
        return_value=[],
    ):
        assert await Session.resolve("never-issued") is None


@pytest.mark.asyncio
async def test_session_revoke_deletes_by_token_hash():
    with patch(
        "open_notebook.domain.user.repo_query", new_callable=AsyncMock
    ) as mock_query:
        await Session.revoke("raw-token")

    query, variables = mock_query.call_args.args
    assert "DELETE FROM session" in query
    assert variables["token_hash"] == sha256_hex("raw-token")


# ---------------------------------------------------------------------------
# User.create
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_user_create_hashes_password():
    created = {
        "id": USER_ID,
        "username": "ada",
        "password_hash": "x",
        "role": "admin",
    }
    with patch(
        "open_notebook.domain.user.repo_create",
        new_callable=AsyncMock,
        return_value=created,
    ) as mock_create:
        user = await User.create("ada", "pw123", role="admin")

    data = mock_create.call_args.args[1]
    assert user.role == "admin"
    assert verify_password("pw123", data["password_hash"])
    assert "pw123" not in data["password_hash"]


@pytest.mark.asyncio
async def test_user_create_rejects_blank_username():
    with pytest.raises(InvalidInputError):
        await User.create("   ", "pw123")


# ---------------------------------------------------------------------------
# current_user
# ---------------------------------------------------------------------------


def test_require_user_raises_when_unset():
    token = current_user.set(None)
    try:
        with pytest.raises(AuthenticationError):
            require_user()
    finally:
        current_user.reset(token)


def test_require_user_returns_the_set_user():
    token = current_user.set(FAKE_USER)
    try:
        assert require_user() is FAKE_USER
    finally:
        current_user.reset(token)
