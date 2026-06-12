"""Tests for the Conductor recovery primitives (Batch 4, findings 23–25).

``cb-phase abandon`` (the existing ``(any, conductor) → abandoned`` wildcard
behind a command), ``cb-phase resume`` (the new ``("blocked", "conductor") →
in_progress`` edge, counters preserved), and the watchdog interaction: an
abandoned row drops out of the blocked-owner patrol entirely.

Same deterministic style as ``test_handoff.py``: real SQLite + real FSM;
the store/task resolution seams are monkeypatched at the module level.
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from codeband.cli import handoff
from codeband.state.fsm import InvalidTransitionError, transition
from codeband.state.store import StateStore

TASK = "room-1"


@pytest.fixture
def store(tmp_path) -> StateStore:
    s = StateStore(tmp_path / "state" / "orchestration.db")
    s.create_task(
        task_id=TASK, description="demo", room_id=TASK, owner_id="owner-1",
    )
    return s


@pytest.fixture
def env(monkeypatch, store):
    monkeypatch.setattr(handoff, "_resolve_store", lambda project_dir: store)
    monkeypatch.setattr(
        handoff, "_resolve_task_id",
        lambda project_dir, store, task_arg: (TASK, None),
    )
    return store


def _drive(store, sid, *states_and_roles):
    for new_state, role in states_and_roles:
        transition(sid, TASK, new_state, caller_role=role, store=store)


def _to_blocked(store, sid="st-1", reason="watchdog stall"):
    _drive(
        store, sid,
        ("assigned", "conductor"), ("in_progress", "coder"),
    )
    transition(
        sid, TASK, "blocked", caller_role="watchdog", reason=reason, store=store,
    )


# ─────────────────────────────────────────────────────────────────────────────
# FSM edges
# ─────────────────────────────────────────────────────────────────────────────


def test_conductor_may_resume_blocked_to_in_progress(store):
    _to_blocked(store)
    transition("st-1", TASK, "in_progress", caller_role="conductor", store=store)
    assert store.get_subtask("st-1", TASK).state == "in_progress"


def test_resume_edge_is_conductor_only(store):
    _to_blocked(store)
    for role in ("coder", "mergemaster", "reviewer", "watchdog"):
        with pytest.raises(InvalidTransitionError):
            transition("st-1", TASK, "in_progress", caller_role=role, store=store)


def test_resume_edge_never_revives_terminal_states(store):
    _drive(store, "st-1", ("assigned", "conductor"))
    transition("st-1", TASK, "abandoned", caller_role="conductor", store=store)
    with pytest.raises(InvalidTransitionError):
        transition("st-1", TASK, "in_progress", caller_role="conductor", store=store)


def test_mergemaster_may_block_from_review_passed(store):
    """The rebase-cap escalation can fire at the review_passed gate (the
    SHA-shaped not_eligible routing), so blocked must be a legal mergemaster
    target there — mirroring the merge_pending row."""
    _drive(
        store, "st-1",
        ("assigned", "conductor"), ("in_progress", "coder"),
        ("verify_pending", "coder"), ("review_pending", "coder"),
        ("review_passed", "reviewer"),
    )
    transition("st-1", TASK, "blocked", caller_role="mergemaster", store=store)
    assert store.get_subtask("st-1", TASK).state == "blocked"


# ─────────────────────────────────────────────────────────────────────────────
# cb-phase abandon
# ─────────────────────────────────────────────────────────────────────────────


def test_abandon_command_abandons_from_blocked(env, capsys):
    _to_blocked(env)
    assert handoff.main(["abandon", "st-1", "--task", TASK]) == 0
    assert env.get_subtask("st-1", TASK).state == "abandoned"
    out = capsys.readouterr().out
    assert "ABANDONED: subtask st-1" in out  # the structured-output contract
    assert TASK in out


def test_abandon_command_works_from_any_nonterminal_state(env):
    _drive(env, "st-2", ("assigned", "conductor"), ("in_progress", "coder"))
    assert handoff.main(["abandon", "st-2", "--task", TASK]) == 0
    assert env.get_subtask("st-2", TASK).state == "abandoned"


def test_abandon_command_rejects_terminal_states(env, capsys):
    _to_blocked(env)
    assert handoff.main(["abandon", "st-1"]) == 0
    assert handoff.main(["abandon", "st-1"]) == 1  # already abandoned
    assert "transition rejected" in capsys.readouterr().err


def test_abandon_reason_lands_on_the_transition_log(env):
    import sqlite3

    _to_blocked(env)
    assert handoff.main(
        ["abandon", "st-1", "--reason", "superseded by st-9"],
    ) == 0
    conn = sqlite3.connect(env.db_path)
    try:
        row = conn.execute(
            "SELECT reason FROM transition_log WHERE subtask_id='st-1' "
            "AND to_state='abandoned'",
        ).fetchone()
    finally:
        conn.close()
    assert row == ("superseded by st-9",)


# ─────────────────────────────────────────────────────────────────────────────
# cb-phase resume
# ─────────────────────────────────────────────────────────────────────────────


def test_resume_command_returns_blocked_subtask_to_in_progress(env, capsys):
    _to_blocked(env)
    assert handoff.main(["resume", "st-1", "--task", TASK]) == 0
    assert env.get_subtask("st-1", TASK).state == "in_progress"
    out = capsys.readouterr().out
    assert "RESUMED: subtask st-1" in out  # the structured-output contract


def test_resume_preserves_all_counters(env, capsys):
    """The whole point versus abandon+redispatch: review_round,
    rebase_rounds, and verify_attempts all survive a block/resume cycle."""
    # Earn one of each counter through real FSM walks.
    _drive(
        env, "st-1",
        ("assigned", "conductor"), ("in_progress", "coder"),
        ("verify_pending", "coder"), ("review_pending", "coder"),
        ("review_failed", "reviewer"),                  # review_round → 1
        ("in_progress", "coder"),
        ("verify_pending", "coder"), ("review_pending", "coder"),
        ("review_passed", "reviewer"),
        ("needs_rebase", "mergemaster"),                # rebase_rounds → 1
        ("in_progress", "coder"),
    )
    env.increment_verify_attempts("st-1", TASK)         # verify_attempts → 1
    transition("st-1", TASK, "blocked", caller_role="watchdog", store=env)

    assert handoff.main(["resume", "st-1"]) == 0

    sub = env.get_subtask("st-1", TASK)
    assert sub.state == "in_progress"
    assert sub.review_round == 1
    assert sub.rebase_rounds == 1
    assert sub.verify_attempts == 1
    out = capsys.readouterr().out
    assert "review_round=1" in out
    assert "rebase_rounds=1" in out
    assert "verify_attempts=1" in out


def test_resume_command_rejects_non_blocked_states(env, capsys):
    _drive(env, "st-1", ("assigned", "conductor"), ("in_progress", "coder"))
    assert handoff.main(["resume", "st-1"]) == 1
    assert "transition rejected" in capsys.readouterr().err
    assert env.get_subtask("st-1", TASK).state == "in_progress"


# ─────────────────────────────────────────────────────────────────────────────
# Watchdog interaction: abandon clears the blocked-owner patrol
# ─────────────────────────────────────────────────────────────────────────────


def _mock_rest():
    rest = MagicMock()
    rest.agent_api_messages = MagicMock()
    rest.agent_api_messages.create_agent_chat_message = AsyncMock()
    return rest


def _daemon(store, rest):
    from codeband.agents.watchdog import WatchdogDaemon
    from codeband.config import WatchdogConfig

    return WatchdogDaemon(
        config=WatchdogConfig(),
        rest_client=rest,
        agent_id="agent-wd",
        conductor_id="agent-cond",
        state_store=store,
        owner_id="owner-1",
        owner_handle="Owner",
    )


@pytest.mark.asyncio
async def test_abandon_clears_the_blocked_patrol(env):
    """A blocked-but-not-yet-escalated subtask that the Conductor abandons
    must never escalate afterwards — the patrol reads live state and an
    abandoned row is terminal."""
    _to_blocked(env)
    assert handoff.main(["abandon", "st-1"]) == 0

    rest = _mock_rest()
    await _daemon(env, rest)._check_blocked_subtasks(datetime.now(UTC))

    rest.agent_api_messages.create_agent_chat_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_blocked_patrol_still_fires_without_abandon(env):
    """Control for the test above: the same row, not abandoned, escalates."""
    _to_blocked(env)

    rest = _mock_rest()
    await _daemon(env, rest)._check_blocked_subtasks(datetime.now(UTC))

    rest.agent_api_messages.create_agent_chat_message.assert_awaited_once()
