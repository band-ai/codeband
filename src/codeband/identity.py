"""Durable agent identity resolution — distributed env vs local worktree map.

item-0 stamps the Band ``agent_id`` of the coder that worked a subtask
(``assigned_worker``) and the reviewer that rendered its verdict
(``assigned_reviewer``) onto the subtask row, so the watchdog can @mention the
right agent (``agents/watchdog.py`` uses ``assigned_worker`` directly as a chat
mention id) and rehydration/forensics can attribute work. Identity must be
resolved two different ways because the run modes differ:

* **Distributed mode** (``run_agent``): one OS process IS one Band agent. The
  runner exports ``CODEBAND_AGENT_ID`` (alongside ``CODEBAND_ROLE``) on the
  spawn seam; the ``cb-phase`` subprocess inherits it. :func:`resolve_identity`
  reads it directly — env-first.

* **Local mode** (``run_local``): every role runs as an asyncio task in ONE
  process sharing ONE environment, so there is no single agent to name and
  ``CODEBAND_AGENT_ID`` is deliberately NOT exported (and is actively cleared at
  the seam, so a stale/inherited value cannot hijack resolution). The only
  per-seat signal is the cwd the adapter gives each agent's CLI subprocess:
  coders get ``worktrees/<worker_id>``, reviewers/verifiers get
  ``scratch/<worker_id>`` (see ``workspace/init.py``). The runner writes a
  :data:`LOCATION_MAP_FILENAME` mapping each seat's operating dir to its
  ``agent_id``; :func:`resolve_identity` matches the cwd against it.

Identity is **advisory** — it is a forensic / recovery / mention aid, never a
gate. Every failure mode resolves to ``None`` (do not stamp); nothing here may
raise into an FSM transition.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# The local-mode dir→identity map, written by the runner into the workspace
# ``state/`` dir at ``run_local`` startup and read by the ``cb-phase`` CLI.
LOCATION_MAP_FILENAME = "agent_locations.json"

# Env var carrying the single distributed-agent identity (set by the runner's
# spawn seam only in distributed mode). Its mere presence is the "this process
# is one distributed agent" signal, so the seam clears it in local mode.
AGENT_ID_ENV = "CODEBAND_AGENT_ID"
ROLE_ENV = "CODEBAND_ROLE"


@dataclass(frozen=True)
class ResolvedIdentity:
    """The identity behind a ``cb-phase`` invocation.

    ``agent_id`` is the Band agent_id (the chat-mention key). ``role`` is the
    role name (``coder`` / ``reviewer`` / …) when known — from
    ``$CODEBAND_ROLE`` in distributed mode or the location-map entry in local
    mode; ``None`` only when the env path resolved an id without a role.
    """

    agent_id: str
    role: str | None


def write_location_map(state_dir: Path, entries: list[dict[str, Any]]) -> None:
    """Atomically write the local-mode seat→identity map.

    Each entry is ``{"dir", "worker_id", "agent_id", "role"}``; ``dir`` is
    stored as a resolved absolute path string so the resolver can match a
    spawned subprocess's cwd against it without re-resolving relative paths.
    Overwritten on every ``run_local`` startup, so the map always reflects the
    current run's agent_ids (stale-across-runs is handled by overwrite).

    Best-effort: a write failure logs and returns — a missing map degrades
    local-mode stamping to ``None`` (no stamp), never breaks the swarm.
    """
    state_dir = Path(state_dir)
    payload = {
        "version": 1,
        "entries": [
            {
                "dir": str(Path(e["dir"]).resolve()),
                "worker_id": e["worker_id"],
                "agent_id": e["agent_id"],
                "role": e["role"],
            }
            for e in entries
        ],
    }
    try:
        state_dir.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=state_dir, suffix=".tmp")
        try:
            with open(fd, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2)
            Path(tmp).replace(state_dir / LOCATION_MAP_FILENAME)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise
    except OSError:
        logger.warning(
            "Failed to write agent location map at %s — local-mode identity "
            "stamping will be unavailable this run", state_dir, exc_info=True,
        )


def _is_within(child: Path, parent: Path) -> bool:
    """True when ``child`` is ``parent`` or a descendant of it.

    Uses :meth:`Path.is_relative_to` semantics (path-component boundaries), so
    ``/a/wt-1`` is NOT considered within ``/a/wt-10`` — a plain string-prefix
    check would false-match those sibling worktrees.
    """
    try:
        child.relative_to(parent)
        return True
    except ValueError:
        return False


def _resolve_from_map(cwd: Path, state_dir: Path) -> ResolvedIdentity | None:
    """Resolve identity from the location map by cwd, fail-safe to ``None``.

    Returns the unique seat whose ``dir`` is ``cwd`` or an ancestor of it.
    Zero matches (operator-run, moved worktree, unknown dir) → ``None``.
    More than one match (pathologically nested seat dirs) → ``None`` (refuse to
    guess a wrong identity). A missing / unreadable / malformed map → ``None``.
    """
    map_path = Path(state_dir) / LOCATION_MAP_FILENAME
    try:
        payload = json.loads(map_path.read_text(encoding="utf-8"))
        entries = payload["entries"]
    except (FileNotFoundError, json.JSONDecodeError, KeyError, TypeError, OSError):
        return None

    matches: list[ResolvedIdentity] = []
    for entry in entries:
        try:
            seat_dir = Path(entry["dir"])
            agent_id = entry["agent_id"]
        except (KeyError, TypeError):
            continue
        if _is_within(cwd, seat_dir):
            matches.append(ResolvedIdentity(agent_id=agent_id, role=entry.get("role")))

    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        logger.warning(
            "Ambiguous agent location map: cwd %s is within %d seat dirs — "
            "refusing to guess identity", cwd, len(matches),
        )
    return None


def resolve_identity(*, cwd: Path | str, state_dir: Path | str) -> ResolvedIdentity | None:
    """Resolve the agent identity behind a ``cb-phase`` invocation.

    Env-first: a non-empty ``$CODEBAND_AGENT_ID`` reliably means "this process
    is a single distributed agent" (the runner clears it in local mode), so it
    wins outright. Otherwise fall through to the local location map keyed on the
    invoking cwd. Returns ``None`` when nothing trustworthy resolves — the
    caller must then leave the identity field unstamped.
    """
    env_id = os.environ.get(AGENT_ID_ENV)
    if env_id:
        return ResolvedIdentity(agent_id=env_id, role=os.environ.get(ROLE_ENV))
    return _resolve_from_map(Path(cwd).resolve(), Path(state_dir))
