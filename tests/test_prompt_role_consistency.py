"""Tests for prompt consistency around conductor/planning responsibilities."""

from pathlib import Path


def test_conductor_prompt_keeps_technical_work_out_of_role():
    prompt = Path("src/codeband/prompts/conductor.md").read_text(encoding="utf-8")

    assert "You are a coordinator, not an implementer or debugger." in prompt
    assert "Do **not** analyze code, debug failing tests, design implementations, or propose patches yourself." in prompt
    assert "provide a fix" not in prompt


def test_plan_review_trigger_is_planner_message_not_conductor_relay():
    planner = Path("src/codeband/prompts/planner.md").read_text(encoding="utf-8")
    reviewer = Path("src/codeband/prompts/plan_reviewer.md").read_text(encoding="utf-8")

    assert "a concrete **Plan Reviewer** from the Worker Pool Roster" in planner
    assert "@Plan-Reviewer-Codex-0" in planner
    assert "This is the primary delivery mechanism and is what starts plan review." in planner
    assert "When the Conductor sends you a plan for review" not in reviewer
    assert "When the Planner sends a plan message that @mentions both you and the Conductor" in reviewer


def test_mergemaster_conflict_reports_require_verification_artifacts():
    """Mergemaster must demand `gh pr view --json mergeable,mergeStateStatus`,
    `git diff --name-only --diff-filter=U`, and verbatim git stderr in every
    conflict report; the Conductor must verify before forwarding to the Coder.

    This pins the guardrail introduced after a Mergemaster hallucinated a
    merge conflict with a non-existent PR. Removing any of these phrases from
    the prompts re-opens that bug.
    """
    mergemaster = Path("src/codeband/prompts/mergemaster.md").read_text(encoding="utf-8")
    conductor = Path("src/codeband/prompts/conductor.md").read_text(encoding="utf-8")

    assert "git diff --name-only --diff-filter=U" in mergemaster
    assert "gh pr view <pr-number> --json mergeable,mergeStateStatus" in mergemaster
    assert "Last lines of `git merge` stderr:" in mergemaster
    # Cross-check: when gh says MERGEABLE/CLEAN, do not declare a conflict.
    assert 'gh pr view' in mergemaster and '"mergeable": "MERGEABLE"' in mergemaster

    # Conductor must refuse to forward an evidence-less conflict to the Coder.
    assert "Verify before forwarding to the Coder" in conductor
    assert "gh pr view --json mergeable,mergeStateStatus" in conductor


def test_planner_forbids_implementation_code_in_plans():
    """The Planner must describe WHAT to build, not HOW to implement it."""
    planner = Path("src/codeband/prompts/planner.md").read_text(encoding="utf-8")
    plan_reviewer = Path(
        "src/codeband/prompts/plan_reviewer.md",
    ).read_text(encoding="utf-8")

    assert "Plans describe WHAT, not HOW" in planner
    assert "Do **not** include in the plan" in planner
    assert "Function or method bodies" in planner

    # Plan Reviewer flags implementation code as a blocking issue.
    assert "Plan vs. Implementation Boundary" in plan_reviewer
    assert "[Blocking]" in plan_reviewer
    assert "Function or method bodies the Coder is supposed to write" in plan_reviewer


def test_coder_dispatches_review_directly_to_opposite_framework_reviewer():
    """The Coder picks the Code Reviewer; the Conductor does not relay.

    Pins the direct-dispatch invariant: at PR completion, the Coder
    @-mentions an opposite-framework Reviewer alongside the Conductor.
    The Conductor stays silent at first dispatch.
    """
    coder = Path("src/codeband/prompts/coder.md").read_text(encoding="utf-8")
    code_reviewer = Path(
        "src/codeband/prompts/code_reviewer.md",
    ).read_text(encoding="utf-8")
    conductor = Path("src/codeband/prompts/conductor.md").read_text(encoding="utf-8")

    # Coder side: mention BOTH the Reviewer and the Conductor; pick from the roster.
    assert "@mentioning **only @Conductor**" not in coder, (
        "Coder prompt still routes through the Conductor — the relay was "
        "supposed to be removed in Bug 4."
    )
    assert (
        "@mentioning **both an opposite-framework Code Reviewer and @Conductor**"
        in coder
    )
    assert "Pick the reviewer from the Worker Pool Roster" in coder

    # Code Reviewer side: expects direct dispatch from the Coder.
    assert "A Coder @mentions you directly at PR completion" in code_reviewer

    # Conductor side: stays silent at first dispatch; still relays re-reviews.
    assert "Coder's @mention to the Reviewer is the dispatch" in conductor
    assert "Re-review **is** routed via you" in conductor
