"""
Regression test for GET /api/config (api/routers/config.py).

An earlier audit flagged check_database_health() as leaking raw exception
text to callers. Reading the current code: the exception
string does get put into check_database_health()'s internal return dict
(`error` key), but get_config() only logs that value server-side
(logger.warning) - it never includes it in the JSON response, which
carries just `dbStatus` ("online"/"offline"). This test locks that
contract in so a future edit can't reintroduce the leak by, e.g., adding
`"error": db_health.get("error")` to the response dict.

Since multi-user slice 2 the endpoint sits behind SessionAuthMiddleware,
so the leak tests authenticate with a resolved session (cookie +
patched Session.resolve).
"""

from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from open_notebook.domain.user import User, hash_password

FAKE_USER = User(
    id="user:abc", username="ada", password_hash=hash_password("ada-pw"), role="admin"
)


@pytest.fixture
def client():
    from api.main import app

    return TestClient(app)


@pytest.fixture
def authenticated_client(client):
    """Client whose on_session cookie resolves to a valid user."""
    with patch(
        "open_notebook.domain.user.Session.resolve",
        new=AsyncMock(return_value=FAKE_USER),
    ):
        yield client, {"Cookie": "on_session=tok"}


SENSITIVE_MESSAGE = "connection refused to internal-db-host.corp.local:8000: password auth failed for user 'root'"


class TestConfigEndpointDoesNotLeakDbErrors:
    def test_db_failure_does_not_include_raw_error_in_response(
        self, authenticated_client
    ):
        client, headers = authenticated_client
        with patch(
            "api.routers.config.repo_query",
            new=AsyncMock(side_effect=RuntimeError(SENSITIVE_MESSAGE)),
        ):
            response = client.get("/api/config", headers=headers)

        assert response.status_code == 200
        body = response.json()
        assert SENSITIVE_MESSAGE not in response.text
        assert "error" not in body
        assert body["dbStatus"] == "offline"
        assert set(body.keys()) == {"version", "latestVersion", "hasUpdate", "dbStatus"}

    def test_db_timeout_does_not_include_raw_error_in_response(
        self, authenticated_client
    ):
        import asyncio

        client, headers = authenticated_client
        with patch(
            "api.routers.config.repo_query",
            new=AsyncMock(side_effect=asyncio.TimeoutError()),
        ):
            response = client.get("/api/config", headers=headers)

        assert response.status_code == 200
        body = response.json()
        assert "error" not in body
        assert body["dbStatus"] == "offline"

    def test_healthy_db_reports_online(self, authenticated_client):
        client, headers = authenticated_client
        with patch(
            "api.routers.config.repo_query",
            new=AsyncMock(return_value=[{"result": 1}]),
        ):
            response = client.get("/api/config", headers=headers)

        assert response.status_code == 200
        assert response.json()["dbStatus"] == "online"


class TestConfigEndpointIsProtected:
    def test_unauthenticated_fresh_instance_gets_setup_required(self, client):
        """On a fresh instance (no user) the middleware answers 409
        setup_required before the handler runs."""
        with patch(
            "open_notebook.domain.user.User.any_exists",
            new=AsyncMock(return_value=False),
        ):
            response = client.get("/api/config")

        assert response.status_code == 409
        assert response.json() == {"detail": "setup_required"}

    def test_unauthenticated_set_up_instance_gets_401(self, client):
        """Once at least one user exists, missing/invalid sessions get 401."""
        with patch(
            "open_notebook.domain.user.User.any_exists",
            new=AsyncMock(return_value=True),
        ):
            response = client.get("/api/config")

        assert response.status_code == 401
