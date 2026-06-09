"""Tests for the atomic task-registration primitive (initiator-as-owner, part 1).

Covers the ``register_task`` contract (row-first, required owner, supersede
semantics, repair of the historical half-states), the ``send_task`` reorder
(owner required; registration strictly before the task message), and the
``cb register-task`` CLI wrapper. LLM-free: real sqlite + tmp dirs, mocked
Band clients only where ``send_task`` needs them.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from click.testing import CliRunner

from codeband.cli import cli as cb_cli
from codeband.state import StateStore
from codeband.state.registration import register_task


@pytest.fixture
def store(tmp_path: Path) -> StateStore:
    """A StateStore backed by an isolated DB under tmp_path."""
    return StateStore(tmp_path / "state" / "orchestration.db")


def _pointer(project_dir: Path) -> Path:
    return project_dir / ".codeband_room"


def _task_row_count(db_path: Path) -> int:
    conn = sqlite3.connect(db_path)
    try:
        (count,) = conn.execute("SELECT COUNT(*) FROM tasks").fetchone()
    finally:
        conn.close()
    return count


# ---------------------------------------------------------------------------
# register_task — the primitive's contract
# ---------------------------------------------------------------------------

class TestRegisterTask:
    def test_fresh_registration_writes_row_and_pointer(
        self, tmp_path: Path, store: StateStore
    ) -> None:
        result = register_task(
            room_id="room-1",
            description="do the thing",
            owner_id="owner-7",
            owner_handle="yoni/claude-abc",
            project_dir=tmp_path,
            store=store,
        )

        assert result.outcome == "registered"
        assert result.superseded_task_id is None
        task = store.get_task("room-1")
        assert task is not None
        assert task.description == "do the thing"
        assert task.status == "active"
        assert task.owner_id == "owner-7"
        assert task.owner_handle == "yoni/claude-abc"
        assert _pointer(tmp_path).read_text(encoding="utf-8").strip() == "room-1"

    @pytest.mark.parametrize("bad_owner", ["", None])
    def test_missing_owner_raises_and_writes_nothing(
        self, tmp_path: Path, store: StateStore, bad_owner
    ) -> None:
        with pytest.raises(ValueError, match="owner_id"):
            register_task(
                room_id="room-1",
                description="do the thing",
                owner_id=bad_owner,
                project_dir=tmp_path,
                store=store,
            )

        assert store.get_task("room-1") is None
        assert _task_row_count(store.db_path) == 0
        assert not _pointer(tmp_path).exists()

    def test_reregister_same_room_updates_owner_only(
        self, tmp_path: Path, store: StateStore
    ) -> None:
        register_task(
            room_id="room-1",
            description="original description",
            owner_id="owner-a",
            owner_handle="handle-a",
            project_dir=tmp_path,
            store=store,
        )
        result = register_task(
            room_id="room-1",
            description="DIFFERENT description must be ignored",
            owner_id="owner-b",
            owner_handle="handle-b",
            project_dir=tmp_path,
            store=store,
        )

        assert result.outcome == "re-registered"
        assert _task_row_count(store.db_path) == 1
        task = store.get_task("room-1")
        assert task is not None
        assert task.owner_id == "owner-b"
        assert task.owner_handle == "handle-b"
        # Description and status are deliberately untouched on re-registration.
        assert task.description == "original description"
        assert task.status == "active"
        assert _pointer(tmp_path).read_text(encoding="utf-8").strip() == "room-1"

    def test_new_room_supersedes_active_task(
        self, tmp_path: Path, store: StateStore
    ) -> None:
        register_task(
            room_id="room-old",
            description="old task",
            owner_id="owner-a",
            project_dir=tmp_path,
            store=store,
        )
        result = register_task(
            room_id="room-new",
            description="new task",
            owner_id="owner-b",
            project_dir=tmp_path,
            store=store,
        )

        assert result.outcome == "superseded"
        assert result.superseded_task_id == "room-old"
        old = store.get_task("room-old")
        new = store.get_task("room-new")
        assert old is not None and old.status == "superseded"
        assert new is not None and new.status == "active"
        assert _pointer(tmp_path).read_text(encoding="utf-8").strip() == "room-new"

    def test_pointer_without_row_is_overwritten_cleanly(
        self, tmp_path: Path, store: StateStore
    ) -> None:
        # The /codeband broken state (H2): a pointer that resolves to no row.
        _pointer(tmp_path).write_text("ghost-room", encoding="utf-8")

        result = register_task(
            room_id="room-1",
            description="real task",
            owner_id="owner-7",
            project_dir=tmp_path,
            store=store,
        )

        # Nothing to supersede — the dangling pointer was invalid state.
        assert result.outcome == "registered"
        assert result.superseded_task_id is None
        assert store.get_task("ghost-room") is None
        assert store.get_task("room-1") is not None
        assert _pointer(tmp_path).read_text(encoding="utf-8").strip() == "room-1"

    def test_row_without_pointer_restores_pointer(
        self, tmp_path: Path, store: StateStore
    ) -> None:
        # H1: the row exists but the pointer write never happened.
        register_task(
            room_id="room-1",
            description="task",
            owner_id="owner-a",
            project_dir=tmp_path,
            store=store,
        )
        _pointer(tmp_path).unlink()

        result = register_task(
            room_id="room-1",
            description="task",
            owner_id="owner-b",
            project_dir=tmp_path,
            store=store,
        )

        assert result.outcome == "re-registered"
        assert _pointer(tmp_path).read_text(encoding="utf-8").strip() == "room-1"
        task = store.get_task("room-1")
        assert task is not None
        assert task.owner_id == "owner-b"
        assert _task_row_count(store.db_path) == 1


# ---------------------------------------------------------------------------
# send_task — owner required, registration strictly before the task message
# ---------------------------------------------------------------------------

@dataclass
class FakeIdentity:
    id: str
    name: str


@dataclass
class FakeIdentityResponse:
    data: FakeIdentity


@dataclass
class FakeRoom:
    id: str


@dataclass
class FakeRoomResponse:
    data: FakeRoom


def _make_human_client(room_id: str) -> AsyncMock:
    human_client = AsyncMock()
    human_client.human_api_chats.create_my_chat_room.return_value = FakeRoomResponse(
        data=FakeRoom(id=room_id)
    )
    human_client.human_api_profile.get_my_profile.return_value = FakeIdentityResponse(
        data=FakeIdentity(id="owner-1", name="Initiator")
    )
    return human_client


def _make_client_factory(human_client: AsyncMock):
    """AsyncRestClient replacement: human key → human client, else conductor."""
    conductor_client = AsyncMock()
    conductor_client.agent_api_identity.get_agent_me.return_value = FakeIdentityResponse(
        data=FakeIdentity(id="cond-0", name="Conductor")
    )

    def factory(api_key, base_url=None):
        if api_key == "human-key":
            return human_client
        return conductor_client

    return factory


async def _run_send_task(human_client, sample_config, tmp_path: Path) -> None:
    import os

    import thenvoi_rest

    from codeband.orchestration import kickoff

    factory = _make_client_factory(human_client)
    with patch.dict(os.environ, {"BAND_API_KEY": "human-key"}):
        original = thenvoi_rest.AsyncRestClient
        thenvoi_rest.AsyncRestClient = factory
        try:
            await kickoff.send_task(sample_config, tmp_path, "implement feature X")
        finally:
            thenvoi_rest.AsyncRestClient = original


class TestSendTaskRegistration:
    @pytest.mark.asyncio
    async def test_owner_resolution_failure_aborts_before_message(
        self, sample_config, sample_agent_config, tmp_path: Path
    ) -> None:
        sample_agent_config.to_yaml(tmp_path / "agent_config.yaml")
        human_client = _make_human_client("room-123")
        human_client.human_api_profile.get_my_profile.side_effect = RuntimeError(
            "profile endpoint down"
        )

        with pytest.raises(RuntimeError, match="initiator"):
            await _run_send_task(human_client, sample_config, tmp_path)

        # Aborted loudly before any participant add or message post …
        human_client.human_api_participants.add_my_chat_participant.assert_not_called()
        human_client.human_api_messages.send_my_chat_message.assert_not_called()
        # … and before anything was registered.
        assert not _pointer(tmp_path).exists()
        db_path = tmp_path / "workspace" / "state" / "orchestration.db"
        assert not db_path.exists() or _task_row_count(db_path) == 0

    @pytest.mark.asyncio
    async def test_registration_ordered_before_message_post(
        self, sample_config, sample_agent_config, tmp_path: Path, monkeypatch
    ) -> None:
        sample_agent_config.to_yaml(tmp_path / "agent_config.yaml")
        human_client = _make_human_client("room-123")

        events: list[str] = []

        from codeband.state import registration as registration_module

        real_register_task = registration_module.register_task

        def recording_register_task(**kwargs):
            events.append("register")
            return real_register_task(**kwargs)

        monkeypatch.setattr(
            registration_module, "register_task", recording_register_task
        )

        async def recording_send_message(room_id, message):
            events.append("message")
            # The pointer and the tasks row must already exist when the task
            # message (the agent-activation edge) is posted.
            assert _pointer(tmp_path).read_text(encoding="utf-8").strip() == "room-123"
            db_path = tmp_path / "workspace" / "state" / "orchestration.db"
            task = StateStore(db_path).get_task("room-123")
            assert task is not None
            assert task.owner_id == "owner-1"

        human_client.human_api_messages.send_my_chat_message = AsyncMock(
            side_effect=recording_send_message
        )

        await _run_send_task(human_client, sample_config, tmp_path)

        assert events == ["register", "message"]


# ---------------------------------------------------------------------------
# cb register-task — thin CLI wrapper
# ---------------------------------------------------------------------------

class TestRegisterTaskCli:
    def test_success_exits_zero_and_registers(self, sample_config, tmp_path: Path) -> None:
        sample_config.to_yaml(tmp_path / "codeband.yaml")

        runner = CliRunner()
        result = runner.invoke(cb_cli, [
            "register-task",
            "--room", "room-cli",
            "--owner", "owner-9",
            "--owner-handle", "yoni/peer",
            "--description", "seeded by a peer",
            "--dir", str(tmp_path),
        ])

        assert result.exit_code == 0, result.output
        assert "Registered task room-cli" in result.output
        assert _pointer(tmp_path).read_text(encoding="utf-8").strip() == "room-cli"
        task = StateStore(
            tmp_path / "workspace" / "state" / "orchestration.db"
        ).get_task("room-cli")
        assert task is not None
        assert task.owner_id == "owner-9"
        assert task.owner_handle == "yoni/peer"
        assert task.status == "active"

    def test_missing_owner_exits_nonzero_writes_nothing(
        self, sample_config, tmp_path: Path
    ) -> None:
        sample_config.to_yaml(tmp_path / "codeband.yaml")

        runner = CliRunner()
        result = runner.invoke(cb_cli, [
            "register-task",
            "--room", "room-cli",
            "--description", "seeded by a peer",
            "--dir", str(tmp_path),
        ])

        assert result.exit_code != 0
        assert not _pointer(tmp_path).exists()
        assert not (tmp_path / "workspace" / "state" / "orchestration.db").exists()
