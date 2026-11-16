"""Tests for the /api/users admin endpoints (slice 2).

Non-admins get 403; delete refuses with 501 until slice 7 lands its body.
"""

from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from api import main as api_main
from open_notebook.domain.user import User, hash_password, verify_password

AUTH_HEADERS = {"Cookie": "on_session=admin-tok"}


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


def _log_in_as(monkeypatch, user: User) -> None:
    monkeypatch.setattr(
        "open_notebook.domain.user.Session.resolve", AsyncMock(return_value=user)
    )


@pytest.fixture
def client():
    return TestClient(api_main.app)


class TestListUsers:
    def test_non_admin_403(self, monkeypatch, client):
        _log_in_as(monkeypatch, _member())
        assert client.get("/api/users", headers=AUTH_HEADERS).status_code == 403

    def test_admin_lists_users_without_passwords(self, monkeypatch, client):
        _log_in_as(monkeypatch, _admin())
        monkeypatch.setattr(
            User, "get_all", AsyncMock(return_value=[_admin(), _member()])
        )
        response = client.get("/api/users", headers=AUTH_HEADERS)
        assert response.status_code == 200
        body = response.json()
        assert [entry["username"] for entry in body] == ["ada", "bob"]
        for entry in body:
            assert set(entry) == {"id", "username", "role", "created"}
        assert "password_hash" not in response.text


class TestCreateUser:
    def test_admin_creates_user_with_default_role(self, monkeypatch, client):
        _log_in_as(monkeypatch, _admin())
        monkeypatch.setattr(User, "get_by_username", AsyncMock(return_value=None))
        mock_create = AsyncMock(return_value=_member())
        monkeypatch.setattr(User, "create", mock_create)

        response = client.post(
            "/api/users",
            json={"username": "bob", "password": "member-pw"},
            headers=AUTH_HEADERS,
        )
        assert response.status_code == 200
        assert response.json()["username"] == "bob"
        assert response.json()["role"] == "user"
        mock_create.assert_awaited_once_with("bob", "member-pw", role="user")

    def test_duplicate_username_409(self, monkeypatch, client):
        _log_in_as(monkeypatch, _admin())
        monkeypatch.setattr(User, "get_by_username", AsyncMock(return_value=_member()))
        response = client.post(
            "/api/users",
            json={"username": "bob", "password": "member-pw"},
            headers=AUTH_HEADERS,
        )
        assert response.status_code == 409

    def test_non_admin_403(self, monkeypatch, client):
        _log_in_as(monkeypatch, _member())
        response = client.post(
            "/api/users",
            json={"username": "bob", "password": "member-pw"},
            headers=AUTH_HEADERS,
        )
        assert response.status_code == 403


class TestResetUserPassword:
    def test_admin_resets_password(self, monkeypatch, client):
        _log_in_as(monkeypatch, _admin())
        target = _member()
        monkeypatch.setattr(
            "open_notebook.domain.user.User.get", AsyncMock(return_value=target)
        )
        mock_save = AsyncMock()
        monkeypatch.setattr(User, "save", mock_save)

        response = client.put(
            "/api/users/user:member1/password",
            json={"password": "brand-new-pw"},
            headers=AUTH_HEADERS,
        )
        assert response.status_code == 200
        assert verify_password("brand-new-pw", target.password_hash)
        mock_save.assert_awaited_once()

    def test_unknown_user_404(self, monkeypatch, client):
        _log_in_as(monkeypatch, _admin())
        monkeypatch.setattr(
            "open_notebook.domain.user.User.get", AsyncMock(return_value=None)
        )
        response = client.put(
            "/api/users/user:missing/password",
            json={"password": "x"},
            headers=AUTH_HEADERS,
        )
        assert response.status_code == 404

    def test_non_admin_403(self, monkeypatch, client):
        _log_in_as(monkeypatch, _member())
        response = client.put(
            "/api/users/user:member1/password",
            json={"password": "x"},
            headers=AUTH_HEADERS,
        )
        assert response.status_code == 403


class TestDeleteUser:
    def test_delete_501_until_slice_7(self, monkeypatch, client):
        _log_in_as(monkeypatch, _admin())
        response = client.delete("/api/users/user:member1", headers=AUTH_HEADERS)
        assert response.status_code == 501

    def test_delete_non_admin_403(self, monkeypatch, client):
        _log_in_as(monkeypatch, _member())
        response = client.delete("/api/users/user:member1", headers=AUTH_HEADERS)
        assert response.status_code == 403
