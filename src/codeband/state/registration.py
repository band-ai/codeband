"""Atomic task registration — the single writer of "a task exists".

A task is *registered* when two things agree: a ``tasks`` row in the durable
state store and the ``<project_dir>/.codeband_room`` pointer file naming that
row's room. Historically those were written by separate code paths at
separate times (``send_task`` wrote the row best-effort mid-kickoff and the
pointer only after the task message; the ``/codeband`` peer-seeding path wrote
the pointer and never the row), which produced four observable broken states:

* **H1 — row-without-pointer:** a crash after the row write but before the
  pointer write leaves ``cb-phase`` unable to resolve the task.
* **H2 — pointer-without-row:** a swallowed store failure (or a path that
  never writes the row at all) leaves a pointer that resolves to nothing.
* **H3 — message-before-pointer:** the task message activates agents before
  the pointer exists, so an early ``cb-phase`` call races the write.
* **H4 — ownerless row:** best-effort owner resolution leaves ``owner_id``
  NULL, and the watchdog can never escalate to a human.

:func:`register_task` is the one primitive that closes all four: it validates
the owner up front, applies every DB mutation (supersede + insert/update) in
one transaction, and writes the pointer only after the commit — **row-first**,
because a row without a pointer is the recoverable state (re-running the
registration repairs it), while a pointer without a row is a dead end for
``cb-phase``. Both ``send_task`` and ``cb register-task`` call it; nothing
else may write ``.codeband_room`` or a ``tasks`` row.

This module is deliberately import-clean of any Band/network client — it owns
only the DB (via :class:`~codeband.state.store.StateStore`) and the pointer
file, so peer seeders can call it without Band credentials.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from codeband.state.store import StateStore

# Name of the active-room pointer file, relative to the project dir. The
# single source of truth for "which task is active" as read by cb-phase,
# cb approve/reject, cleanup and doctor.
ROOM_POINTER_NAME = ".codeband_room"


@dataclass
class RegistrationResult:
    """Outcome of one :func:`register_task` call."""

    room_id: str
    # "registered"    — fresh row inserted (no prior valid registration).
    # "re-registered" — a row for this room already existed; owner updated.
    # "superseded"    — a *different* active task was superseded first.
    outcome: str
    superseded_task_id: str | None = None


def _read_pointer(project_dir: Path) -> str | None:
    """Return the current pointer's room id, or ``None`` if absent/empty."""
    pointer = project_dir / ROOM_POINTER_NAME
    try:
        room_id = pointer.read_text(encoding="utf-8").strip()
    except (FileNotFoundError, OSError):
        return None
    return room_id or None


def register_task(
    *,
    room_id: str,
    description: str,
    owner_id: str,
    owner_handle: str | None = None,
    project_dir: Path,
    store: StateStore,
) -> RegistrationResult:
    """Register *room_id* as the active task: tasks row + pointer, row-first.

    ``owner_id`` is required and must be non-empty — a missing owner raises
    :class:`ValueError` before anything is written. One active task at a time
    is enforced here: if the pointer currently names a *different* room with a
    live row, that task is marked ``'superseded'`` in the same transaction
    that registers the new one. Re-registering the same room updates only the
    owner fields (description/status untouched) and rewrites the pointer, so
    the call is safe to retry — including over the half-states the old writers
    could leave behind (row-without-pointer, pointer-without-row).

    The pointer write happens strictly after the DB commit and any failure
    propagates loudly: the resulting row-without-pointer state is exactly what
    a re-run repairs.
    """
    if not owner_id:
        raise ValueError(
            "register_task: owner_id is required and must be non-empty — "
            "every task needs an owner the watchdog can escalate to."
        )
    if not room_id:
        raise ValueError("register_task: room_id is required and must be non-empty.")

    pointer_room = _read_pointer(project_dir)

    # A pointer to a different room only matters if that room has a live row;
    # a dangling pointer (no row) is the invalid H2 state and is simply
    # overwritten by the fresh registration.
    supersede_task_id: str | None = None
    if pointer_room is not None and pointer_room != room_id:
        if store.get_task(pointer_room) is not None:
            supersede_task_id = pointer_room

    # All DB mutations — supersede + insert/update — land in one transaction.
    db_outcome = store.register_task_atomic(
        task_id=room_id,
        description=description,
        room_id=room_id,
        owner_id=owner_id,
        owner_handle=owner_handle,
        supersede_task_id=supersede_task_id,
    )

    # Row-first: the pointer is written only after the commit. A failure here
    # is raised loudly — the row already exists, so re-running register_task
    # for the same room repairs the pointer.
    (project_dir / ROOM_POINTER_NAME).write_text(room_id, encoding="utf-8")

    if supersede_task_id is not None:
        outcome = "superseded"
    elif db_outcome == "updated":
        outcome = "re-registered"
    else:
        outcome = "registered"
    return RegistrationResult(
        room_id=room_id,
        outcome=outcome,
        superseded_task_id=supersede_task_id,
    )
