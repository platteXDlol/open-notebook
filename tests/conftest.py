"""
Pytest configuration file.

This file ensures that the project root is in the Python path,
allowing tests to import from the api and open_notebook modules.
"""

import sys
from pathlib import Path

import pytest

# Load environment variables from .env file
# This must be done BEFORE any imports that depend on environment variables
from dotenv import load_dotenv

# Load .env file from project root
dotenv_path = Path(__file__).parent.parent / ".env"
if dotenv_path.exists():
    load_dotenv(dotenv_path)
    print(f"Loaded environment variables from {dotenv_path}")
else:
    print(f"Warning: .env file not found at {dotenv_path}")

# Add the project root to the Python path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

# ---------------------------------------------------------------------------
# Shared auth helpers (multi-user slice 2)
#
# Since slice 2 every /api/* request requires an on_session cookie. Unit
# tests run without a live database, so the legacy API tests authenticate
# with a fake session instead:
#
#     @pytest.fixture
#     def client(api_session_test):
#         from api.main import app
#         return authed_client(app)
#
# Tests that assert unauthenticated behaviour (401 / 409 setup_required) must
# NOT use api_session_test and must not send TEST_AUTH_COOKIE.
# ---------------------------------------------------------------------------
from unittest.mock import patch

from fastapi.testclient import TestClient

from open_notebook.domain.user import User

# Static hash that is never verified: these tests patch the domain layer.
TEST_ADMIN_USER = User(
    id="user:tests",
    username="test-admin",
    password_hash="scrypt$16384$8$1$aGVhZGxlc3M$bm90LXZlcmlmaWVk",
    role="admin",
)
TEST_AUTH_COOKIE = {"Cookie": "on_session=unit-test-session"}


def authed_client(app, **kwargs) -> TestClient:
    """TestClient that always sends a valid on_session cookie."""
    return TestClient(app, headers=TEST_AUTH_COOKIE, **kwargs)


@pytest.fixture
def api_session_test():
    """Make session resolution and the setup gate deterministic (no DB needed)."""

    async def _resolve(raw_token):
        return TEST_ADMIN_USER

    async def _any_exists():
        return True

    with (
        patch("open_notebook.domain.user.Session.resolve", new=_resolve),
        patch("open_notebook.domain.user.User.any_exists", new=_any_exists),
    ):
        yield
