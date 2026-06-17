"""Tests for durable agent-identity resolution (item-0, Leg 1).

Covers the two resolution modes (distributed env-first vs local worktree map),
the env-hijack guard at the spawn seam, and every fail-safe-to-None edge of the
location map (missing / malformed / unknown cwd / ambiguous / path boundary).
"""

from __future__ import annotations

import json
from pathlib import Path

from codeband.identity import (
    AGENT_ID_ENV,
    LOCATION_MAP_FILENAME,
    ROLE_ENV,
    ResolvedIdentity,
    resolve_identity,
    write_location_map,
)


def _seat(dir_path: Path, worker_id: str, agent_id: str, role: str) -> dict:
    return {"dir": str(dir_path), "worker_id": worker_id, "agent_id": agent_id, "role": role}


# ── distributed mode: env-first ─────────────────────────────────────────────

def test_distributed_env_resolves_agent_id_and_role(monkeypatch, tmp_path):
    monkeypatch.setenv(AGENT_ID_ENV, "agent-coder-7")
    monkeypatch.setenv(ROLE_ENV, "coder")
    resolved = resolve_identity(cwd=tmp_path, state_dir=tmp_path)
    assert resolved == ResolvedIdentity(agent_id="agent-coder-7", role="coder")


def test_distributed_env_without_role_resolves_id_with_none_role(monkeypatch, tmp_path):
    monkeypatch.setenv(AGENT_ID_ENV, "agent-x")
    monkeypatch.delenv(ROLE_ENV, raising=False)
    resolved = resolve_identity(cwd=tmp_path, state_dir=tmp_path)
    assert resolved == ResolvedIdentity(agent_id="agent-x", role=None)


def test_env_wins_over_map(monkeypatch, tmp_path):
    """When both env identity and a matching map entry exist, env wins."""
    wt = tmp_path / "worktrees" / "coder-claude_sdk-0"
    wt.mkdir(parents=True)
    write_location_map(tmp_path, [_seat(wt, "coder-claude_sdk-0", "map-agent", "coder")])
    monkeypatch.setenv(AGENT_ID_ENV, "env-agent")
    monkeypatch.setenv(ROLE_ENV, "coder")
    resolved = resolve_identity(cwd=wt, state_dir=tmp_path)
    assert resolved is not None
    assert resolved.agent_id == "env-agent"


# ── local mode: location map ────────────────────────────────────────────────

def test_local_map_resolves_coder_worktree(monkeypatch, tmp_path):
    monkeypatch.delenv(AGENT_ID_ENV, raising=False)
    wt = tmp_path / "worktrees" / "coder-codex-1"
    wt.mkdir(parents=True)
    write_location_map(tmp_path, [_seat(wt, "coder-codex-1", "agent-codex-1", "coder")])
    resolved = resolve_identity(cwd=wt, state_dir=tmp_path)
    assert resolved == ResolvedIdentity(agent_id="agent-codex-1", role="coder")


def test_local_map_resolves_from_subdirectory(monkeypatch, tmp_path):
    monkeypatch.delenv(AGENT_ID_ENV, raising=False)
    wt = tmp_path / "worktrees" / "coder-claude_sdk-0"
    sub = wt / "src" / "deep"
    sub.mkdir(parents=True)
    write_location_map(tmp_path, [_seat(wt, "coder-claude_sdk-0", "agent-0", "coder")])
    resolved = resolve_identity(cwd=sub, state_dir=tmp_path)
    assert resolved is not None
    assert resolved.agent_id == "agent-0"


def test_local_map_resolves_reviewer_scratch(monkeypatch, tmp_path):
    monkeypatch.delenv(AGENT_ID_ENV, raising=False)
    scratch = tmp_path / "scratch" / "reviewer-codex-0"
    scratch.mkdir(parents=True)
    write_location_map(tmp_path, [_seat(scratch, "reviewer-codex-0", "agent-rev-0", "reviewer")])
    resolved = resolve_identity(cwd=scratch, state_dir=tmp_path)
    assert resolved == ResolvedIdentity(agent_id="agent-rev-0", role="reviewer")


def test_local_map_picks_correct_seat_among_many(monkeypatch, tmp_path):
    monkeypatch.delenv(AGENT_ID_ENV, raising=False)
    coder = tmp_path / "worktrees" / "coder-claude_sdk-0"
    rev = tmp_path / "scratch" / "reviewer-codex-0"
    coder.mkdir(parents=True)
    rev.mkdir(parents=True)
    write_location_map(
        tmp_path,
        [
            _seat(coder, "coder-claude_sdk-0", "agent-coder", "coder"),
            _seat(rev, "reviewer-codex-0", "agent-rev", "reviewer"),
        ],
    )
    assert resolve_identity(cwd=coder, state_dir=tmp_path).agent_id == "agent-coder"
    assert resolve_identity(cwd=rev, state_dir=tmp_path).agent_id == "agent-rev"


# ── local mode: fail-safe edges (all → None) ────────────────────────────────

def test_local_unknown_cwd_returns_none(monkeypatch, tmp_path):
    monkeypatch.delenv(AGENT_ID_ENV, raising=False)
    wt = tmp_path / "worktrees" / "coder-claude_sdk-0"
    wt.mkdir(parents=True)
    write_location_map(tmp_path, [_seat(wt, "coder-claude_sdk-0", "agent-0", "coder")])
    elsewhere = tmp_path / "somewhere" / "else"
    elsewhere.mkdir(parents=True)
    assert resolve_identity(cwd=elsewhere, state_dir=tmp_path) is None


def test_local_missing_map_returns_none(monkeypatch, tmp_path):
    monkeypatch.delenv(AGENT_ID_ENV, raising=False)
    assert resolve_identity(cwd=tmp_path, state_dir=tmp_path) is None


def test_local_malformed_map_returns_none(monkeypatch, tmp_path):
    monkeypatch.delenv(AGENT_ID_ENV, raising=False)
    (tmp_path / LOCATION_MAP_FILENAME).write_text("{not valid json", encoding="utf-8")
    assert resolve_identity(cwd=tmp_path, state_dir=tmp_path) is None


def test_local_path_boundary_no_false_match(monkeypatch, tmp_path):
    """wt-1 must not match a cwd inside wt-10 (string-prefix would false-match)."""
    monkeypatch.delenv(AGENT_ID_ENV, raising=False)
    wt1 = tmp_path / "worktrees" / "coder-claude_sdk-1"
    wt10 = tmp_path / "worktrees" / "coder-claude_sdk-10"
    wt1.mkdir(parents=True)
    wt10.mkdir(parents=True)
    write_location_map(tmp_path, [_seat(wt1, "coder-claude_sdk-1", "agent-1", "coder")])
    # cwd is inside wt-10, which is NOT in the map; wt-1 is a string prefix of
    # wt-10's path but not a path-component ancestor → no match.
    assert resolve_identity(cwd=wt10, state_dir=tmp_path) is None


def test_local_ambiguous_nested_seats_returns_none(monkeypatch, tmp_path):
    """If cwd is within two seat dirs (pathologically nested), refuse to guess."""
    monkeypatch.delenv(AGENT_ID_ENV, raising=False)
    outer = tmp_path / "worktrees" / "coder-claude_sdk-0"
    inner = outer / "nested" / "reviewer-codex-0"
    inner.mkdir(parents=True)
    write_location_map(
        tmp_path,
        [
            _seat(outer, "coder-claude_sdk-0", "agent-outer", "coder"),
            _seat(inner, "reviewer-codex-0", "agent-inner", "reviewer"),
        ],
    )
    # cwd == inner is within BOTH outer and inner → ambiguous → None.
    assert resolve_identity(cwd=inner, state_dir=tmp_path) is None


# ── write_location_map shape ────────────────────────────────────────────────

def test_write_location_map_resolves_dirs_absolute(tmp_path):
    wt = tmp_path / "worktrees" / "coder-claude_sdk-0"
    wt.mkdir(parents=True)
    write_location_map(tmp_path, [_seat(wt, "coder-claude_sdk-0", "agent-0", "coder")])
    payload = json.loads((tmp_path / LOCATION_MAP_FILENAME).read_text())
    assert payload["version"] == 1
    entry = payload["entries"][0]
    assert Path(entry["dir"]).is_absolute()
    assert entry["agent_id"] == "agent-0"
    assert entry["role"] == "coder"


def test_write_location_map_overwrites(tmp_path):
    wt = tmp_path / "worktrees" / "coder-claude_sdk-0"
    wt.mkdir(parents=True)
    write_location_map(tmp_path, [_seat(wt, "coder-claude_sdk-0", "old-agent", "coder")])
    write_location_map(tmp_path, [_seat(wt, "coder-claude_sdk-0", "new-agent", "coder")])
    payload = json.loads((tmp_path / LOCATION_MAP_FILENAME).read_text())
    assert len(payload["entries"]) == 1
    assert payload["entries"][0]["agent_id"] == "new-agent"


# ── spawn-seam env behavior (env-hijack guard) ──────────────────────────────

def test_export_seam_sets_agent_id_when_given(monkeypatch, tmp_path):
    import os

    from codeband.orchestration.runner import _export_project_dir_env

    # Track every key the seam mutates so monkeypatch teardown restores them —
    # the function writes os.environ directly (matches test_attribution.py).
    monkeypatch.setenv("CODEBAND_PROJECT_DIR", "")
    monkeypatch.setenv("CODEBAND_AGENT_SESSION", "")
    monkeypatch.setenv("CODEBAND_ROLE", "")
    monkeypatch.delenv(AGENT_ID_ENV, raising=False)
    _export_project_dir_env(tmp_path, role="coder", agent_id="agent-coder-3")
    assert os.environ[AGENT_ID_ENV] == "agent-coder-3"


def test_export_seam_clears_stale_agent_id_when_not_given(monkeypatch, tmp_path):
    """Local-mode call (no agent_id) must clear an inherited value so it cannot
    hijack local resolution — the env-hijack guard."""
    import os

    from codeband.orchestration.runner import _export_project_dir_env

    monkeypatch.setenv("CODEBAND_PROJECT_DIR", "")
    monkeypatch.setenv("CODEBAND_AGENT_SESSION", "")
    monkeypatch.setenv(AGENT_ID_ENV, "stale-from-prior-run")
    _export_project_dir_env(tmp_path)
    assert AGENT_ID_ENV not in os.environ
