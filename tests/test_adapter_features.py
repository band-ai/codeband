"""Every adapter must declare its features explicitly.

band-sdk moved these knobs twice: first from the ``enable_execution_reporting``
/ ``enable_memory_tools`` constructor flags to ``features=AdapterFeatures(...)``,
then (2.0.0) to flat ``emit=`` / ``capabilities=`` kwargs, with
``Emit.EXECUTION`` renamed to ``Emit.TOOL_CALLS``.

The trap is 3.0.0's defaulting rule: ``emit`` defaults to everything the
adapter supports, but ``capabilities`` defaults to *none* because it is opt-in.
So an adapter built without an explicit ``capabilities=`` silently loses the
memory tools, while every prompt still instructs the agent to call
``band_store_memory`` / ``band_list_memories``. That failure is invisible at
construction time, so these tests pin the resolved feature set for both
frameworks — Codex included, since covering only Claude is what let the Codex
runners drift in the first place.
"""

from __future__ import annotations

import warnings
from pathlib import Path

import pytest

from band.core.types import Capability, Emit


def _claude_runner_cases(workspace: str):
    from codeband.agents.code_reviewer import ClaudeCodeReviewerRunner
    from codeband.agents.conductor import ClaudeConductorRunner
    from codeband.agents.mergemaster import ClaudeMergemasterRunner
    from codeband.agents.plan_reviewer import ClaudePlanReviewerRunner
    from codeband.agents.planner import ClaudePlannerRunner
    from codeband.agents.player_claude import ClaudePlayerRunner

    ws = {"workspace": workspace}
    return [
        (ClaudePlannerRunner, ws),
        (ClaudeConductorRunner, {}),
        (ClaudePlayerRunner, ws),
        (ClaudeCodeReviewerRunner, ws),
        (ClaudePlanReviewerRunner, ws),
        (ClaudeMergemasterRunner, ws),
    ]


@pytest.mark.parametrize(
    "runner_cls_index", range(6), ids=lambda i: f"case{i}"
)
def test_claude_runner_uses_features_api_without_deprecation(
    runner_cls_index: int, tmp_path: Path
):
    cls, kwargs = _claude_runner_cases(str(tmp_path))[runner_cls_index]

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        runner = cls(**kwargs)

    deprecated = [
        str(w.message)
        for w in caught
        if "enable_execution_reporting" in str(w.message)
        or "enable_memory_tools" in str(w.message)
    ]
    assert not deprecated, f"{cls.__name__} still uses deprecated kwargs: {deprecated}"

    features = runner.adapter.features
    assert set(features.emit) == {Emit.TOOL_CALLS, Emit.THOUGHTS}, cls.__name__
    assert set(features.capabilities) == {Capability.MEMORY}, cls.__name__


def _codex_runner_cases(workspace: str):
    from codeband.agents.code_reviewer import CodexCodeReviewerRunner
    from codeband.agents.conductor import CodexConductorRunner
    from codeband.agents.mergemaster import CodexMergemasterRunner
    from codeband.agents.plan_reviewer import CodexPlanReviewerRunner
    from codeband.agents.planner import CodexPlannerRunner
    from codeband.agents.player_codex import CodexPlayerRunner

    ws = {"workspace": workspace}
    return [
        (CodexPlannerRunner, ws),
        (CodexConductorRunner, {}),
        (CodexPlayerRunner, ws),
        (CodexCodeReviewerRunner, ws),
        (CodexPlanReviewerRunner, ws),
        (CodexMergemasterRunner, ws),
    ]


@pytest.mark.parametrize("runner_cls_index", range(6), ids=lambda i: f"case{i}")
def test_codex_runner_declares_memory_capability(runner_cls_index: int, tmp_path: Path):
    """Codex runners must opt into the memory capability their prompts rely on."""
    cls, kwargs = _codex_runner_cases(str(tmp_path))[runner_cls_index]

    features = cls(**kwargs).adapter.features

    assert set(features.capabilities) == {Capability.MEMORY}, cls.__name__
    # Never the SDK's "everything supported" default — USAGE in particular is
    # noise the runner does not consume.
    assert Emit.USAGE not in features.emit, cls.__name__
    assert Emit.TOOL_CALLS in features.emit, cls.__name__
