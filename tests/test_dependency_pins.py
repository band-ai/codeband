"""Canary tests for critical dependency pins in pyproject.toml.

Pip resolution itself is untestable in-suite, but the pin strings are not:
band-sdk 1.0.0 renamed the ``thenvoi.*`` module namespace to ``band.*`` with
no compatibility shim, so an uncapped pin breaks every fresh install. These
asserts make loosening the cap a deliberate, reviewed act.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

_PYPROJECT = Path(__file__).parent.parent / "pyproject.toml"


def _dependencies() -> list[str]:
    with _PYPROJECT.open("rb") as fh:
        return tomllib.load(fh)["project"]["dependencies"]


def test_band_sdk_pin_is_capped_below_0_3():
    deps = [d for d in _dependencies() if d.startswith("band-sdk")]
    assert deps == ["band-sdk[codex,claude-sdk]>=0.2.8,<0.3"], (
        "band-sdk must stay capped <0.3 until the thenvoi.*→band.* rename "
        f"is migrated; got {deps}"
    )


def test_thenvoi_client_rest_is_declared_and_capped():
    deps = [d for d in _dependencies() if d.startswith("thenvoi-client-rest")]
    assert deps == ["thenvoi-client-rest>=0.0.7,<0.1"], (
        "thenvoi-client-rest is imported directly (thenvoi.client.rest) and "
        f"must be declared with a 0.x cap; got {deps}"
    )
