"""Slice 3: per-user isolation (ownership core).

Covers the ownership rules from docs/plans/multi-user.md slice 3:

- Owned tables (notebook, source, note, chat_session, source_embedding,
  source_insight, episode, artifact) are scoped to the current user; foreign
  rows are invisible (get() -> NotFoundError -> 404) and save() stamps the
  creating user as owner.
- Global-or-user tables (model, credential, transformation, episode_profile,
  speaker_profile) are visible when owner = me OR owner = NONE.
- Search (fn::text_search, fn::vector_search, resolve_notebook_scope)
  receives the current user's owner.
- Jobs inherit the parent owner (embed_source records, create_insight,
  rebuild collection, podcast generation job).

Unit tests patch repository functions — no live database is needed.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from conftest import TEST_ADMIN_USER, TEST_USER_B

from open_notebook.utils.current_user import current_user


def _set_user(user):
    return current_user.set(user)


def _reset_user(token):
    current_user.reset(token)


class _RecordingQuery:
    """Async stand-in for repo_query that records (query, params) calls."""

    def __init__(self, rows=None):
        self.rows = rows or []
        self.calls = []

    async def __call__(self, query, params=None):
        self.calls.append((query, params))
        return self.rows


# ---------------------------------------------------------------------------
# Domain core: owned tables
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_owned_get_all_scoped_to_current_user():
    from open_notebook.domain.notebook import Notebook

    fake = _RecordingQuery()
    token = _set_user(TEST_ADMIN_USER)
    try:
        with patch("open_notebook.domain.base.repo_query", new=fake):
            await Notebook.get_all()
    finally:
        _reset_user(token)
    query, params = fake.calls[0]
    assert "owner = $owner" in query
    assert params == {"owner": TEST_ADMIN_USER.id}


@pytest.mark.asyncio
async def test_owned_get_foreign_row_raises_not_found():
    from open_notebook.domain.notebook import Notebook
    from open_notebook.exceptions import NotFoundError

    fake = _RecordingQuery()  # empty result: row exists but not for this user
    token = _set_user(TEST_USER_B)
    try:
        with patch("open_notebook.domain.base.repo_query", new=fake):
            with pytest.raises(NotFoundError):
                await Notebook.get("notebook:1")
    finally:
        _reset_user(token)
    query, params = fake.calls[0]
    assert "owner = $owner" in query
    assert params["owner"] == TEST_USER_B.id


@pytest.mark.asyncio
async def test_owned_queries_unscoped_without_user():
    from open_notebook.domain.notebook import Notebook
    from open_notebook.exceptions import NotFoundError

    fake = _RecordingQuery()
    with patch("open_notebook.domain.base.repo_query", new=fake):
        await Notebook.get_all()
        with pytest.raises(NotFoundError):
            await Notebook.get("notebook:1")
    for query, params in fake.calls:
        assert "$owner" not in query
        assert "owner" not in (params or {})


@pytest.mark.asyncio
async def test_save_stamps_owner_on_create():
    from open_notebook.domain.notebook import Notebook

    token = _set_user(TEST_ADMIN_USER)
    try:
        with patch(
            "open_notebook.domain.base.repo_create", new_callable=AsyncMock
        ) as create:
            create.return_value = [
                {
                    "id": "notebook:new",
                    "name": "New notebook",
                    "description": "",
                    "owner": TEST_ADMIN_USER.id,
                }
            ]
            notebook = Notebook(name="New notebook", description="")
            await notebook.save()
            table, data = create.call_args.args
            assert table == "notebook"
            assert data["owner"] == TEST_ADMIN_USER.id
    finally:
        _reset_user(token)


@pytest.mark.asyncio
async def test_save_keeps_explicit_owner():
    """Workers pass the parent owner explicitly; it must not be overwritten."""
    from open_notebook.domain.notebook import Notebook

    token = _set_user(TEST_ADMIN_USER)
    try:
        with patch(
            "open_notebook.domain.base.repo_update", new_callable=AsyncMock
        ) as update:
            update.return_value = [{"id": "notebook:x"}]
            notebook = Notebook.model_construct(
                id="notebook:x",
                name="N",
                description="",
                owner="user:worker-owner",
            )
            await notebook.save()
            _, _, data = update.call_args.args
            assert data["owner"] == "user:worker-owner"
    finally:
        _reset_user(token)


# ---------------------------------------------------------------------------
# Global-or-user tables: owner = me OR owner = NONE
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_global_or_user_tables_allow_global_rows():
    from open_notebook.ai.models import Model

    fake = _RecordingQuery(
        rows=[{"id": "model:1", "name": "M", "provider": "openai", "type": "chat"}]
    )
    token = _set_user(TEST_ADMIN_USER)
    try:
        with patch("open_notebook.domain.base.repo_query", new=fake):
            await Model.get_all()
            await Model.get("model:1")
    finally:
        _reset_user(token)
    for query, params in fake.calls:
        assert "owner = $owner OR owner = NONE" in query
        assert params["owner"] == TEST_ADMIN_USER.id


@pytest.mark.asyncio
async def test_credential_queries_allow_global_rows():
    from open_notebook.domain.credential import Credential

    fake = _RecordingQuery()
    token = _set_user(TEST_ADMIN_USER)
    try:
        with patch("open_notebook.domain.credential.repo_query", new=fake):
            await Credential.get_all()
            await Credential.get_by_provider("openai")
    finally:
        _reset_user(token)
    for query, params in fake.calls:
        assert "owner = $owner OR owner = NONE" in query
        assert params["owner"] == TEST_ADMIN_USER.id


@pytest.mark.asyncio
async def test_model_get_models_by_type_allows_global_rows():
    from open_notebook.ai.models import Model

    fake = _RecordingQuery()
    token = _set_user(TEST_ADMIN_USER)
    try:
        with patch("open_notebook.ai.models.repo_query", new=fake):
            await Model.get_models_by_type("chat")
    finally:
        _reset_user(token)
    query, params = fake.calls[0]
    assert "owner = $owner OR owner = NONE" in query
    assert params["owner"] == TEST_ADMIN_USER.id


# ---------------------------------------------------------------------------
# Search: $owner is passed to the SurrealDB functions
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_text_search_receives_owner():
    from open_notebook.domain.notebook import text_search

    fake = _RecordingQuery()
    token = _set_user(TEST_ADMIN_USER)
    try:
        with patch("open_notebook.domain.notebook.repo_query", new=fake):
            await text_search("hello", 5)
    finally:
        _reset_user(token)
    query, params = fake.calls[0]
    assert "fn::text_search" in query
    assert "$owner" in query
    assert params["owner"] == TEST_ADMIN_USER.id


@pytest.mark.asyncio
async def test_text_search_owner_none_without_user():
    from open_notebook.domain.notebook import text_search

    fake = _RecordingQuery()
    with patch("open_notebook.domain.notebook.repo_query", new=fake):
        await text_search("hello", 5)
    query, params = fake.calls[0]
    assert "$owner" in query
    assert params["owner"] is None


@pytest.mark.asyncio
async def test_vector_search_receives_owner():
    from open_notebook.domain.notebook import vector_search

    fake = _RecordingQuery()
    token = _set_user(TEST_ADMIN_USER)
    try:
        with (
            patch(
                "open_notebook.utils.embedding.generate_embedding",
                new=AsyncMock(return_value=[0.1, 0.2]),
            ),
            patch("open_notebook.domain.notebook.repo_query", new=fake),
        ):
            await vector_search("hello", 5)
    finally:
        _reset_user(token)
    query, params = fake.calls[0]
    assert "fn::vector_search" in query
    assert "$owner" in query
    assert params["owner"] == TEST_ADMIN_USER.id


@pytest.mark.asyncio
async def test_resolve_notebook_scope_filters_foreign_notebooks():
    from open_notebook.domain.notebook import resolve_notebook_scope
    from open_notebook.exceptions import NotFoundError

    fake = _RecordingQuery()  # empty: notebook:1 is not this user's
    token = _set_user(TEST_USER_B)
    try:
        with patch("open_notebook.domain.notebook.repo_query", new=fake):
            with pytest.raises(NotFoundError, match="Notebook\\(s\\) not found"):
                await resolve_notebook_scope(["notebook:1"])
    finally:
        _reset_user(token)
    query, params = fake.calls[0]
    assert "owner = $owner" in query
    assert params["owner"] == TEST_USER_B.id


# ---------------------------------------------------------------------------
# Jobs inherit the parent owner
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_embed_source_records_inherit_owner():
    from commands.embedding_commands import EmbedSourceInput, embed_source_command

    class FakeSource:
        full_text = "some text " * 200
        asset = None
        owner = "user:owner-x"

    inserted = {}

    async def fake_repo_query(query, params=None):
        return []

    async def fake_repo_insert(table, records):
        assert table == "source_embedding"
        inserted["records"] = records

    with (
        patch(
            "commands.embedding_commands.Source.get",
            new=AsyncMock(return_value=FakeSource()),
        ),
        patch(
            "commands.embedding_commands.chunk_text",
            return_value=["chunk 0", "chunk 1"],
        ),
        patch(
            "commands.embedding_commands.generate_embeddings",
            new=AsyncMock(return_value=[[0.1]] * 2),
        ),
        patch("commands.embedding_commands.repo_query", new=fake_repo_query),
        patch("commands.embedding_commands.repo_insert", new=fake_repo_insert),
    ):
        result = await embed_source_command(EmbedSourceInput(source_id="source:abc"))

    assert result.success is True
    assert [r.get("owner") for r in inserted["records"]] == [
        "user:owner-x",
        "user:owner-x",
    ]


@pytest.mark.asyncio
async def test_create_insight_inherits_owner():
    from commands.embedding_commands import (
        CreateInsightInput,
        create_insight_command,
    )

    class FakeSource:
        owner = "user:owner-x"

    fake = _RecordingQuery(rows=[{"id": "source_insight:1"}])
    with (
        patch(
            "commands.embedding_commands.Source.get",
            new=AsyncMock(return_value=FakeSource()),
        ),
        patch("commands.embedding_commands.repo_query", new=fake),
        patch("commands.embedding_commands.submit_command"),
    ):
        result = await create_insight_command(
            CreateInsightInput(
                source_id="source:abc", insight_type="summary", content="insight"
            )
        )

    assert result.success is True
    query, params = fake.calls[0]
    assert '"owner": $owner' in query
    assert params["owner"] == "user:owner-x"


@pytest.mark.asyncio
async def test_collect_items_scoped_to_current_user():
    from commands.embedding_commands import collect_items_for_rebuild

    fake = _RecordingQuery()
    token = _set_user(TEST_ADMIN_USER)
    try:
        with patch("commands.embedding_commands.repo_query", new=fake):
            await collect_items_for_rebuild("all", True, True, True)
    finally:
        _reset_user(token)
    assert len(fake.calls) == 3
    for query, params in fake.calls:
        assert "owner = $owner" in query
        assert params["owner"] == TEST_ADMIN_USER.id


@pytest.mark.asyncio
async def test_collect_items_unscoped_without_user():
    from commands.embedding_commands import collect_items_for_rebuild

    fake = _RecordingQuery()
    with patch("commands.embedding_commands.repo_query", new=fake):
        await collect_items_for_rebuild("all", True, True, True)
    for query, params in fake.calls:
        assert "$owner" not in query
        assert "owner" not in (params or {})


@pytest.mark.asyncio
async def test_podcast_generation_job_inherits_owner():
    from api.podcast_service import PodcastService

    submitted = {}

    def fake_submit(app, command, args, **kwargs):
        submitted["args"] = args
        return "job:1"

    token = _set_user(TEST_ADMIN_USER)
    try:
        with (
            patch("api.podcast_service.EpisodeProfile") as ep,
            patch("api.podcast_service.SpeakerProfile") as sp,
            patch("api.podcast_service.submit_command", side_effect=fake_submit),
        ):
            ep.get_by_name = AsyncMock(return_value=MagicMock(id="episode_profile:1"))
            sp.resolve = AsyncMock(return_value=MagicMock(id="speaker_profile:1"))
            job_id = await PodcastService.submit_generation_job(
                "podcast", "interview", "Ep 1", content="content"
            )
    finally:
        _reset_user(token)
    assert job_id == "job:1"
    assert submitted["args"]["owner"] == TEST_ADMIN_USER.id


# ---------------------------------------------------------------------------
# API level: another user's rows are 404, not 200-with-their-data
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_user_b_cannot_fetch_admin_notebook(two_user_session):
    from conftest import authed_client, user_b_client

    from api.main import app

    admin_row = {
        "id": "notebook:1",
        "name": "Admin notebook",
        "description": "",
        "archived": False,
        "created": "2026-01-01T00:00:00Z",
        "updated": "2026-01-01T00:00:00Z",
        "source_count": 0,
        "note_count": 0,
    }
    calls = []

    async def fake_query(query, params=None):
        calls.append((query, params or {}))
        # "The database": only rows matching the requested owner exist.
        if (params or {}).get("owner") == TEST_USER_B.id:
            return []
        return [dict(admin_row)]

    with patch("api.routers.notebooks.repo_query", new=fake_query):
        admin = authed_client(app)
        assert admin.get("/api/notebooks/notebook:1").status_code == 200
        user_b = user_b_client(app)
        assert user_b.get("/api/notebooks/notebook:1").status_code == 404

    fetch_calls = [c for c in calls if "FROM $notebook_id" in c[0]]
    assert len(fetch_calls) == 2
    for query, params in fetch_calls:
        assert "owner = $owner" in query
    assert fetch_calls[0][1]["owner"] == TEST_ADMIN_USER.id
    assert fetch_calls[1][1]["owner"] == TEST_USER_B.id


@pytest.mark.asyncio
async def test_user_b_notebook_list_scoped(two_user_session):
    from conftest import user_b_client

    from api.main import app

    fake = _RecordingQuery()
    with patch("api.routers.notebooks.repo_query", new=fake):
        response = user_b_client(app).get("/api/notebooks")
    assert response.status_code == 200
    query, params = fake.calls[0]
    assert "owner = $owner" in query
    assert params["owner"] == TEST_USER_B.id
