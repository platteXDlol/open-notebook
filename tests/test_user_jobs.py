"""Slice 4: background jobs are submitted as the user who submitted them.

Covers the job-ownership rules from docs/plans/multi-user.md slice 4:

- Every CommandInput subclass has a required ``user_id`` — a queued job
  without a user (e.g. pre-migration legacy jobs) fails validation
  before any write.
- Every submit site passes the authenticated user's id
  (``require_user().id``).
- Every @command enters ``run_as(input_data.user_id)`` as its first
  action: a job referencing an unknown user fails before side effects,
  and records written by a job carry the job user (e.g. a podcast
  episode is owned by the user who requested generation).

Unit tests patch repository functions — no live database required.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from conftest import TEST_ADMIN_USER, authed_client
from pydantic import ValidationError
from surrealdb import RecordID

from commands.embedding_commands import (
    CreateInsightInput,
    EmbedInsightInput,
    EmbedNoteInput,
    EmbedSourceInput,
    RebuildEmbeddingsInput,
    create_insight_command,
    embed_insight_command,
    embed_note_command,
    embed_source_command,
    rebuild_embeddings_command,
)
from commands.podcast_commands import (
    PodcastGenerationInput,
    generate_podcast_command,
)
from commands.source_commands import (
    RunTransformationInput,
    SourceProcessingInput,
    process_source_command,
    run_transformation_command,
)
from open_notebook.exceptions import NotFoundError
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
# 1. Command inputs require a submitting user
# ---------------------------------------------------------------------------


def _source_processing_kwargs(source_id="source:1"):
    return dict(
        source_id=source_id,
        content_state={},
        notebook_ids=[],
        transformations=[],
        embed=False,
    )


INPUT_CASES = [
    ("SourceProcessingInput", SourceProcessingInput, _source_processing_kwargs()),
    (
        "RunTransformationInput",
        RunTransformationInput,
        dict(source_id="source:1", transformation_id="transformation:1"),
    ),
    ("RebuildEmbeddingsInput", RebuildEmbeddingsInput, dict(mode="all")),
    (
        "CreateInsightInput",
        CreateInsightInput,
        dict(source_id="source:1", insight_type="summary", content="insight"),
    ),
    ("EmbedNoteInput", EmbedNoteInput, dict(note_id="note:1")),
    ("EmbedInsightInput", EmbedInsightInput, dict(insight_id="source_insight:1")),
    ("EmbedSourceInput", EmbedSourceInput, dict(source_id="source:1")),
    (
        "PodcastGenerationInput",
        PodcastGenerationInput,
        dict(episode_profile="podcast", episode_name="Ep", content="content"),
    ),
]


@pytest.mark.parametrize(
    ("label", "input_cls", "kwargs"),
    INPUT_CASES,
    ids=[label for label, _, _ in INPUT_CASES],
)
def test_command_input_requires_user_id(label, input_cls, kwargs):
    """A job without a submitting user cannot be constructed — legacy
    queued jobs die at parse time, before any write."""
    with pytest.raises(ValidationError):
        input_cls(**kwargs)
    assert input_cls(**kwargs, user_id="user:tests").user_id == "user:tests"


# ---------------------------------------------------------------------------
# 2. run_as
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_as_binds_and_restores_user():
    from open_notebook.utils.current_user import run_as

    user_get = AsyncMock(return_value=TEST_ADMIN_USER)
    with patch("open_notebook.domain.user.User.get", new=user_get):
        async with run_as("user:tests") as user:
            assert current_user.get() is TEST_ADMIN_USER
            assert user is TEST_ADMIN_USER
    user_get.assert_awaited_once_with("user:tests")
    assert current_user.get() is None


@pytest.mark.asyncio
async def test_run_as_rejects_missing_user_id():
    from open_notebook.utils.current_user import run_as

    with pytest.raises(ValueError, match="no user_id"):
        async with run_as(""):
            pass  # pragma: no cover - unreachable: run_as raises in __aenter__


@pytest.mark.asyncio
async def test_run_as_rejects_unknown_user():
    from open_notebook.utils.current_user import run_as

    with patch(
        "open_notebook.domain.user.User.get",
        new=AsyncMock(side_effect=NotFoundError("user not found")),
    ):
        with pytest.raises(ValueError, match="unknown user"):
            async with run_as("user:ghost"):
                pass  # pragma: no cover - unreachable
    assert current_user.get() is None


# ---------------------------------------------------------------------------
# 3. Unknown user: every job fails before side effects
# ---------------------------------------------------------------------------


COMMAND_CASES = [
    (
        "process_source",
        process_source_command,
        SourceProcessingInput,
        _source_processing_kwargs("source:ghost"),
    ),
    (
        "run_transformation",
        run_transformation_command,
        RunTransformationInput,
        dict(source_id="source:ghost", transformation_id="transformation:ghost"),
    ),
    (
        "rebuild_embeddings",
        rebuild_embeddings_command,
        RebuildEmbeddingsInput,
        dict(mode="all"),
    ),
    (
        "create_insight",
        create_insight_command,
        CreateInsightInput,
        dict(source_id="source:ghost", insight_type="summary", content="x"),
    ),
    ("embed_note", embed_note_command, EmbedNoteInput, dict(note_id="note:ghost")),
    (
        "embed_insight",
        embed_insight_command,
        EmbedInsightInput,
        dict(insight_id="source_insight:ghost"),
    ),
    (
        "embed_source",
        embed_source_command,
        EmbedSourceInput,
        dict(source_id="source:ghost"),
    ),
    (
        "generate_podcast",
        generate_podcast_command,
        PodcastGenerationInput,
        dict(episode_profile="podcast", episode_name="Ep", content="c"),
    ),
]


@pytest.mark.parametrize(
    ("label", "command", "input_cls", "kwargs"),
    COMMAND_CASES,
    ids=[label for label, _, _, _ in COMMAND_CASES],
)
@pytest.mark.asyncio
async def test_command_with_unknown_user_fails_before_side_effects(
    label, command, input_cls, kwargs
):
    """run_as must be the first action in every job: an unknown user
    fails before the command touches the database or other side effects."""
    user_get = AsyncMock(side_effect=NotFoundError("user not found"))
    with patch("open_notebook.domain.user.User.get", new=user_get):
        with pytest.raises(ValueError, match="unknown user"):
            await command(input_cls(**kwargs, user_id="user:ghost"))
    user_get.assert_awaited_once_with("user:ghost")


# ---------------------------------------------------------------------------
# 4. A's job writes records owned by A
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_embed_source_job_by_a_writes_records_owned_by_a():
    class FakeSource:
        full_text = "word " * 300
        asset = None
        owner = TEST_ADMIN_USER.id

    inserted: dict = {}

    async def fake_insert(table, records):
        inserted.setdefault("records", []).extend(
            records if isinstance(records, list) else [records]
        )

    user_get = AsyncMock(return_value=TEST_ADMIN_USER)
    with (
        patch("open_notebook.domain.user.User.get", new=user_get),
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
            new=AsyncMock(return_value=[[0.1], [0.2]]),
        ),
        patch("commands.embedding_commands.repo_query", new=_RecordingQuery()),
        patch("commands.embedding_commands.repo_insert", new=fake_insert),
    ):
        result = await embed_source_command(
            EmbedSourceInput(source_id="source:abc", user_id="user:tests")
        )

    assert result.success is True
    user_get.assert_awaited_once_with("user:tests")
    assert inserted.get("records"), "job inserted no embedding records"
    for record in inserted["records"]:
        assert record.get("owner") == RecordID.parse(str(TEST_ADMIN_USER.id))


@pytest.mark.asyncio
async def test_podcast_job_by_a_stamps_episode_owned_by_a(tmp_path):
    class FakeEpisodeProfile:
        name = "podcast"
        speaker_config = "speaker_profile:1"
        outline_llm = "model:1"
        transcript_llm = "model:2"
        default_briefing = "briefing"

        def model_dump(self):
            return {
                "name": self.name,
                "speaker_config": self.speaker_config,
                "outline_llm": self.outline_llm,
                "transcript_llm": self.transcript_llm,
            }

        async def resolve_outline_config(self):
            return ("provider", "outline-model", {})

        async def resolve_transcript_config(self):
            return ("provider", "transcript-model", {})

    class FakeSpeakerProfile:
        name = "sp"
        voice_model = "model:3"

        def model_dump(self):
            return {"name": self.name, "voice_model": self.voice_model}

        async def resolve_tts_config(self):
            return ("provider", "tts-model", {})

    created = {}

    async def fake_repo_create(table, data):
        created["data"] = data
        return [{"id": "episode:1"}]

    async def fake_repo_update(table, record_id, data):
        return [{"id": "episode:1"}]

    async def fake_profile_query(query, params=None):
        if "episode_profile" in query:
            return [
                {
                    "id": "episode_profile:1",
                    "name": "podcast",
                    "speaker_config": "speaker_profile:1",
                    "outline_llm": "model:1",
                    "transcript_llm": "model:2",
                }
            ]
        return [
            {
                "id": "speaker_profile:1",
                "name": "sp",
                "voice_model": "model:3",
                # podcast-creator's SpeakerConfig validates every profile:
                # tts fields are injected by the command, speakers must
                # already carry name/voice_id/backstory/personality.
                "speakers": [
                    {
                        "name": "sp-a",
                        "voice_id": "voice-1",
                        "backstory": "backstory",
                        "personality": "personality",
                    }
                ],
            }
        ]

    user_get = AsyncMock(return_value=TEST_ADMIN_USER)
    with (
        patch("open_notebook.domain.user.User.get", new=user_get),
        patch(
            "commands.podcast_commands.EpisodeProfile.get_by_name",
            new=AsyncMock(return_value=FakeEpisodeProfile()),
        ),
        patch(
            "commands.podcast_commands.SpeakerProfile.resolve",
            new=AsyncMock(return_value=FakeSpeakerProfile()),
        ),
        patch("commands.podcast_commands.repo_query", new=fake_profile_query),
        patch(
            "commands.podcast_commands._resolve_model_config",
            new=AsyncMock(return_value=("provider", "model", {})),
        ),
        patch("open_notebook.domain.base.repo_create", new=fake_repo_create),
        patch("open_notebook.domain.base.repo_update", new=fake_repo_update),
        patch(
            "commands.podcast_commands.build_episode_output_dir",
            return_value=("ep-test", tmp_path / "ep-test"),
        ),
        patch(
            "commands.podcast_commands.create_podcast",
            new=AsyncMock(
                return_value={
                    "final_output_file_path": None,
                    "transcript": None,
                    "outline": None,
                }
            ),
        ),
    ):
        result = await generate_podcast_command(
            PodcastGenerationInput(
                episode_profile="podcast",
                episode_name="Ep 1",
                content="content",
                user_id="user:tests",
            )
        )

    assert result.success is True
    user_get.assert_awaited_once_with("user:tests")
    assert created.get("data"), "episode was not created"
    # The episode is owned by the user who requested generation — stamped
    # from the bound user by run_as, not taken from an explicit owner field.
    assert created["data"].get("owner") == RecordID.parse(str(TEST_ADMIN_USER.id))


# ---------------------------------------------------------------------------
# 5. Submit sites pass the authenticated user
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_command_service_injects_user_id():
    from api.command_service import CommandService

    submitted = {}

    def fake_submit(app_name, command_name, command_args, **kwargs):
        submitted["args"] = command_args
        return "job:1"

    token = _set_user(TEST_ADMIN_USER)
    try:
        with patch("api.command_service.submit_command", side_effect=fake_submit):
            job_id = await CommandService.submit_command_job(
                "open_notebook", "embed_source", {"source_id": "source:1"}
            )
    finally:
        _reset_user(token)

    assert job_id == "job:1"
    assert submitted["args"]["user_id"] == TEST_ADMIN_USER.id
    assert submitted["args"]["source_id"] == "source:1"


@pytest.mark.asyncio
async def test_create_source_insight_endpoint_submits_with_user_id(two_user_session):
    from api.main import app

    submitted = {}

    def fake_submit(app_name, command_name, command_args, **kwargs):
        submitted["args"] = command_args
        return "job:1"

    fake_source = MagicMock(
        id="source:1", full_text="some text content", owner=TEST_ADMIN_USER.id
    )
    with (
        patch(
            "api.routers.sources.Source.get",
            new=AsyncMock(return_value=fake_source),
        ),
        patch(
            "api.routers.sources.Transformation.get",
            new=AsyncMock(
                return_value=MagicMock(id="transformation:1", model_id="model:1")
            ),
        ),
        patch("api.routers.sources.submit_command", side_effect=fake_submit),
    ):
        response = authed_client(app).post(
            "/api/sources/source:1/insights",
            json={"transformation_id": "transformation:1"},
        )

    assert response.status_code == 202
    assert submitted["args"]["user_id"] == TEST_ADMIN_USER.id
    assert submitted["args"]["source_id"] == "source:1"
    assert submitted["args"]["transformation_id"] == "transformation:1"


@pytest.mark.asyncio
async def test_vectorize_submits_job_with_user_id():
    from open_notebook.domain.notebook import Source

    submitted = {}

    def fake_submit(app_name, command_name, command_args, **kwargs):
        submitted["args"] = command_args
        return "job:1"

    token = _set_user(TEST_ADMIN_USER)
    try:
        with patch(
            "open_notebook.domain.notebook.submit_command", side_effect=fake_submit
        ):
            source = Source(title="t", full_text="some text")
            source.id = "source:1"
            await source.vectorize()
    finally:
        _reset_user(token)

    assert submitted["args"] == {
        "source_id": "source:1",
        "user_id": TEST_ADMIN_USER.id,
    }


@pytest.mark.asyncio
async def test_add_insight_submits_job_with_user_id():
    from open_notebook.domain.notebook import Source

    submitted = {}

    def fake_submit(app_name, command_name, command_args, **kwargs):
        submitted["args"] = command_args
        return "job:1"

    token = _set_user(TEST_ADMIN_USER)
    try:
        with patch(
            "open_notebook.domain.notebook.submit_command", side_effect=fake_submit
        ):
            source = Source(title="t", full_text="some text")
            source.id = "source:1"
            await source.add_insight("summary", "insight text")
    finally:
        _reset_user(token)

    assert submitted["args"] == {
        "source_id": "source:1",
        "insight_type": "summary",
        "content": "insight text",
        "user_id": TEST_ADMIN_USER.id,
    }


@pytest.mark.asyncio
async def test_note_save_submits_embed_job_with_user_id():
    from open_notebook.domain.notebook import Note

    submitted = {}

    def fake_submit(app_name, command_name, command_args, **kwargs):
        submitted["args"] = command_args
        return "job:1"

    token = _set_user(TEST_ADMIN_USER)
    try:
        with (
            patch(
                "open_notebook.domain.base.repo_update",
                new=AsyncMock(return_value=[{"id": "note:1"}]),
            ),
            patch(
                "open_notebook.domain.notebook.submit_command",
                side_effect=fake_submit,
            ),
        ):
            note = Note(title="n", content="hello world")
            note.id = "note:1"
            await note.save()
    finally:
        _reset_user(token)

    assert submitted["args"] == {
        "note_id": "note:1",
        "user_id": TEST_ADMIN_USER.id,
    }
