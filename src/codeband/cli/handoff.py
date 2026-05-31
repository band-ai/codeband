"""``cb-phase`` — the verify-gated handoff CLI (RFC Workstream 3).

This is the enforcement seam. Coding agents (Claude *and* Codex) request a
phase advance by shelling out to ``cb-phase verify``; the effect only happens
if every gate passes, regardless of what the Conductor intended.

    cb-phase verify <subtask_id> --task <task_id> --pr <n> [--worktree <path>]

Gate sequence:

1. ``git -C <worktree> status --porcelain`` must be empty (clean tree).
2. ``gh pr view <n> --json state`` must report ``OPEN``.
3. If ``agents.handoff_verify_command`` is configured, run it in the worktree;
   exit 0 is required.
4. On success, ``fsm.transition(..., "review_pending", caller_role="coder")``.

Any failed gate prints a clear message and exits non-zero. This module imports
**no Band SDK and no asyncio** — it is a fast, pure subprocess callable by both
frameworks.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

from codeband.config import load_config
from codeband.state import StateStore
from codeband.state.fsm import InvalidTransitionError, transition


def _resolve_store(project_dir: Path) -> StateStore:
    """Build the StateStore from the project's codeband.yaml workspace path.

    Mirrors ``kickoff.py`` / ``runner.py``: the DB lives at
    ``{workspace_path}/state/orchestration.db``.
    """
    config = load_config(project_dir)
    workspace_path = Path(config.workspace.path)
    if not workspace_path.is_absolute():
        workspace_path = project_dir / workspace_path
    store = StateStore(workspace_path / "state" / "orchestration.db")
    return store


def _verify_command(project_dir: Path) -> str | None:
    """Return the configured ``agents.handoff_verify_command`` (or ``None``)."""
    config = load_config(project_dir)
    return config.agents.handoff_verify_command


def _git_tree_clean(worktree: Path) -> bool:
    """Return ``True`` if ``git status --porcelain`` is empty in ``worktree``."""
    result = subprocess.run(
        ["git", "-C", str(worktree), "status", "--porcelain"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return False
    return result.stdout.strip() == ""


def _pr_is_open(pr_number: int) -> bool:
    """Return ``True`` if ``gh pr view <n>`` reports state ``OPEN``."""
    result = subprocess.run(
        ["gh", "pr", "view", str(pr_number), "--json", "state"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return False
    try:
        return json.loads(result.stdout).get("state") == "OPEN"
    except (ValueError, AttributeError):
        return False


def _run_verify_command(command: str, cwd: Path) -> int:
    """Run the configured verify command in ``cwd``; return its exit code."""
    result = subprocess.run(command, shell=True, cwd=str(cwd))
    return result.returncode


def _cmd_verify(args: argparse.Namespace) -> int:
    project_dir = Path(args.project_dir).resolve()
    worktree = Path(args.worktree).resolve()

    if not _git_tree_clean(worktree):
        print(
            f"cb-phase: gate failed — working tree at {worktree} is not clean "
            "(commit or stash changes before handoff).",
            file=sys.stderr,
        )
        return 1

    if not _pr_is_open(args.pr):
        print(
            f"cb-phase: gate failed — PR #{args.pr} is not OPEN.",
            file=sys.stderr,
        )
        return 1

    verify_command = _verify_command(project_dir)
    if verify_command:
        code = _run_verify_command(verify_command, worktree)
        if code != 0:
            print(
                f"cb-phase: gate failed — verify command exited {code}: "
                f"{verify_command!r}",
                file=sys.stderr,
            )
            return 1

    store = _resolve_store(project_dir)
    try:
        transition(
            args.subtask_id,
            args.task,
            "review_pending",
            caller_role="coder",
            reason="cb-phase verify",
            store=store,
        )
    except InvalidTransitionError as exc:
        print(f"cb-phase: transition rejected — {exc}", file=sys.stderr)
        return 1

    print(
        f"cb-phase: subtask {args.subtask_id} → review_pending "
        f"(PR #{args.pr}, task {args.task})."
    )
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cb-phase",
        description="Verify-gated phase handoffs for codeband subtasks.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    verify = sub.add_parser(
        "verify",
        help="Gate a subtask into review_pending (clean tree + open PR + verify).",
    )
    verify.add_argument("subtask_id", help="Subtask identifier.")
    verify.add_argument("--task", required=True, help="Task identifier (room_id).")
    verify.add_argument("--pr", type=int, required=True, help="Pull request number.")
    verify.add_argument(
        "--worktree",
        default=".",
        help="Path to the git worktree to check (default: cwd).",
    )
    verify.add_argument(
        "--project-dir",
        default=".",
        help="Project directory containing codeband.yaml (default: cwd).",
    )
    verify.set_defaults(func=_cmd_verify)
    return parser


def main(argv: list[str] | None = None) -> int:
    """Console entry point for ``cb-phase``. Returns a process exit code."""
    parser = _build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
