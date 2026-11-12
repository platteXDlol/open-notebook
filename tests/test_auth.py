"""Tests for the session auth middleware and /api/auth endpoints (slice 2).

The middleware authenticates /api/* requests via the `on_session` cookie and
sets `current_user`. Fresh instances (no user exists) get 409
`{"detail": "setup_required"}` on every protected path until /api/auth/setup
has created the first admin.
"""

from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from api import main as api_main
from api.routers import auth as auth_router
from open_notebook.domain.user import User, hash_password, verify_password

AUTH_HEADERS = {"Cookie": "on_session=tok"}
SESSION_COOKIE = "on_session"


def _admin() -> User:
    return User(
        id="user:admin1",
        username="ada",
        password_hash=hash_password("admin-pw"),
        role="admin",
    )


def _member() -> User:
    return User(
        id="user:member1",
        username="bob",
        password_hash=hash_password("member-pw"),
        role="user",
    )


@pytest.fixture
def client():
    return TestClient(api_main.app)


@pytest.fixture(autouse=True)
def _clear_login_failures():
    """Rate limit state is process-global; keep tests isolated."""
    auth_router._login_failures.clear()


class TestSessionAuthMiddleware:
    def test_api_401_without_cookie_when_users_exist(self, monkeypatch, client):
        monkeypatch.setattr(User, "any_exists", AsyncMock(return_value=True))
        assert client.get("/api/notebooks").status_code == 401

    def test_api_409_setup_required_when_no_users(self, monkeypatch, client):
        monkeypatch.setattr(User, "any_exists", AsyncMock(return_value=False))
        response = client.get("/api/notebooks")
        assert response.status_code == 409
        assert response.json() == {"detail": "setup_required"}

    def test_api_fails_closed_when_user_check_errors(self, monkeypatch, client):
        monkeypatch.setattr(
            User, "any_exists", AsyncMock(side_effect=RuntimeError("db down"))
        )
        assert client.get("/api/notebooks").status_code == 401

    def test_invalid_cookie_401(self, monkeypatch, client):
        monkeypatch.setattr(
            "open_notebook.domain.user.Session.resolve", AsyncMock(return_value=None)
        )
        monkeypatch.setattr(User, "any_exists", AsyncMock(return_value=True))
        assert client.get("/api/notebooks", headers=AUTH_HEADERS).status_code == 401

    def test_valid_cookie_passes_and_sets_current_user(self, monkeypatch, client):
        monkeypatch.setattr(
            "open_notebook.domain.user.Session.resolve",
            AsyncMock(return_value=_admin()),
        )
        response = client.get("/api/auth/me", headers=AUTH_HEADERS)
        assert response.status_code == 200
        body = response.json()
        assert body["username"] == "ada"
        assert body["role"] == "admin"

    def test_non_api_paths_need_no_session(self, client):
        assert client.get("/health").status_code == 200


class TestAuthStatus:
    def test_status_reports_setup_required_without_cookie(self, monkeypatch, client):
        monkeypatch.setattr(User, "any_exists", AsyncMock(return_value=False))
        body = client.get("/api/auth/status").json()
        assert body == {"setup_required": True, "user": None}

    def test_status_reports_user_when_authenticated(self, monkeypatch, client):
        monkeypatch.setattr(
            "open_notebook.domain.user.Session.resolve",
            AsyncMock(return_value=_member()),
        )
        monkeypatch.setattr(User, "any_exists", AsyncMock(return_value=True))
        body = client.get("/api/auth/status", headers=AUTH_HEADERS).json()
        assert body["setup_required"] is False
        assert body["user"]["username"] == "bob"
        assert body["user"]["role"] == "user"
        assert "password_hash" not in body["user"]


class TestAuthSetup:
    def test_setup_creates_admin_and_logs_in(self, monkeypatch, client):
        monkeypatch.setattr(User, "any_exists", AsyncMock(return_value=False))
        mock_create = AsyncMock(return_value=_admin())
        monkeypatch.setattr(User, "create", mock_create)
        mock_session = AsyncMock(return_value="tok-setup")
        monkeypatch.setattr("open_notebook.domain.user.Session.create", mock_session)

        response = client.post(
            "/api/auth/setup", json={"username": "ada", "password": "setup-pw"}
        )
        assert response.status_code == 200
        assert response.json()["username"] == "ada"
        assert response.json()["role"] == "admin"
        mock_create.assert_awaited_once_with("ada", "setup-pw", role="admin")
        mock_session.assert_awaited_once()

        cookie = response.headers["set-cookie"]
        assert cookie.startswith(f"{SESSION_COOKIE}=tok-setup")
        for flag in ("Max-Age=2592000", "Path=/", "SameSite=Lax", "HttpOnly"):
            assert flag.lower() in cookie.lower()

    def test_setup_refused_when_users_exist(self, monkeypatch, client):
        monkeypatch.setattr(User, "any_exists", AsyncMock(return_value=True))
        response = client.post(
            "/api/auth/setup", json={"username": "ada", "password": "setup-pw"}
        )
        assert response.status_code == 409


class TestAuthLogin:
    def test_login_success_sets_session_cookie(self, monkeypatch, client):
        user = _member()
        monkeypatch.setattr(User, "get_by_username", AsyncMock(return_value=user))
        mock_session = AsyncMock(return_value="tok-login")
        monkeypatch.setattr("open_notebook.domain.user.Session.create", mock_session)

        response = client.post(
            "/api/auth/login", json={"username": "bob", "password": "member-pw"}
        )
        assert response.status_code == 200
        assert response.json()["username"] == "bob"
        assert response.headers["set-cookie"].startswith(f"{SESSION_COOKIE}=tok-login")
        mock_session.assert_awaited_once_with(user)

    def test_login_wrong_password_401(self, monkeypatch, client):
        monkeypatch.setattr(User, "get_by_username", AsyncMock(return_value=_member()))
        response = client.post(
            "/api/auth/login", json={"username": "bob", "password": "nope"}
        )
        assert response.status_code == 401

    def test_login_unknown_user_401(self, monkeypatch, client):
        monkeypatch.setattr(User, "get_by_username", AsyncMock(return_value=None))
        response = client.post(
            "/api/auth/login", json={"username": "ghost", "password": "x"}
        )
        assert response.status_code == 401


class TestLoginRateLimit:
    def test_eleventh_failure_in_window_is_429(self, monkeypatch, client):
        monkeypatch.setattr(User, "get_by_username", AsyncMock(return_value=None))
        for _ in range(10):
            response = client.post(
                "/api/auth/login", json={"username": "ada", "password": "x"}
            )
            assert response.status_code == 401
        response = client.post(
            "/api/auth/login", json={"username": "ada", "password": "x"}
        )
        assert response.status_code == 429

    def test_limit_is_per_username(self, monkeypatch, client):
        monkeypatch.setattr(User, "get_by_username", AsyncMock(return_value=None))
        for _ in range(10):
            client.post("/api/auth/login", json={"username": "ada", "password": "x"})
        response = client.post(
            "/api/auth/login", json={"username": "bob", "password": "x"}
        )
        assert response.status_code == 401

    def test_successful_login_clears_failures(self, monkeypatch, client):
        user = _member()
        monkeypatch.setattr(User, "get_by_username", AsyncMock(return_value=user))
        monkeypatch.setattr(
            "open_notebook.domain.user.Session.create", AsyncMock(return_value="tok")
        )
        for _ in range(5):
            client.post(
                "/api/auth/login", json={"username": "bob", "password": "wrong"}
            )
        response = client.post(
            "/api/auth/login", json={"username": "bob", "password": "member-pw"}
        )
        assert response.status_code == 200
        for _ in range(5):
            client.post(
                "/api/auth/login", json={"username": "bob", "password": "wrong"}
            )
        response = client.post(
            "/api/auth/login", json={"username": "bob", "password": "member-pw"}
        )
        assert response.status_code == 200


class TestAuthLogout:
    def test_logout_revokes_session_and_clears_cookie(self, monkeypatch, client):
        monkeypatch.setattr(
            "open_notebook.domain.user.Session.resolve",
            AsyncMock(return_value=_member()),
        )
        mock_revoke = AsyncMock()
        monkeypatch.setattr("open_notebook.domain.user.Session.revoke", mock_revoke)

        response = client.post("/api/auth/logout", headers=AUTH_HEADERS)
        assert response.status_code == 200
        mock_revoke.assert_awaited_once_with("tok")
        cookie = response.headers["set-cookie"]
        assert cookie.startswith(f"{SESSION_COOKIE}=")
        assert "Max-Age=0" in cookie

    def test_logout_without_cookie_401(self, monkeypatch, client):
        monkeypatch.setattr(User, "any_exists", AsyncMock(return_value=True))
        assert client.post("/api/auth/logout").status_code == 401


class TestAuthMe:
    def test_me_without_cookie_401(self, monkeypatch, client):
        monkeypatch.setattr(User, "any_exists", AsyncMock(return_value=True))
        assert client.get("/api/auth/me").status_code == 401


class TestChangePassword:
    def test_change_own_password(self, monkeypatch, client):
        user = _member()
        monkeypatch.setattr(
            "open_notebook.domain.user.Session.resolve", AsyncMock(return_value=user)
        )
        mock_save = AsyncMock()
        monkeypatch.setattr(User, "save", mock_save)

        response = client.put(
            "/api/auth/me/password",
            json={"current_password": "member-pw", "new_password": "new-pw-123"},
            headers=AUTH_HEADERS,
        )
        assert response.status_code == 200
        assert verify_password("new-pw-123", user.password_hash)
        mock_save.assert_awaited_once()

    def test_change_own_password_wrong_current_401(self, monkeypatch, client):
        user = _member()
        monkeypatch.setattr(
            "open_notebook.domain.user.Session.resolve", AsyncMock(return_value=user)
        )
        mock_save = AsyncMock()
        monkeypatch.setattr(User, "save", mock_save)

        response = client.put(
            "/api/auth/me/password",
            json={"current_password": "wrong", "new_password": "new-pw-123"},
            headers=AUTH_HEADERS,
        )
        assert response.status_code == 401
        mock_save.assert_not_awaited()
