"""Guards the migration off the dead ``thenvoi`` namespaces.

Two renames landed in band-sdk and have to stay done:

* band-sdk 1.0.0 renamed the SDK module namespace ``thenvoi`` -> ``band`` and
  builds its MCP tool names as ``band_<verb>`` (band/runtime/tools.py:
  ``prefixed_name = f"band_{name}"``). There is no ``thenvoi_*`` tool alias, so
  a prompt naming ``thenvoi_send_message`` tells agents to call a tool that
  does not exist.
* band-sdk 2.x replaced the ``thenvoi_rest`` REST client with
  ``band-client-rest`` (module ``band_rest``), re-exported as
  ``band.client.rest``. ``thenvoi_rest`` is not installed by any supported
  version, so importing it raises ``ModuleNotFoundError``.

Neither name may appear anywhere in the package any more.
"""

from __future__ import annotations

import re
from pathlib import Path

import codeband

_PKG = Path(codeband.__file__).parent

# Any ``thenvoi`` reference at all: the dead SDK namespace (``thenvoi.adapters``),
# the dead REST client (``thenvoi_rest``), and stale ``thenvoi_<verb>`` tool names.
_DEAD_NAMESPACE = re.compile(r"\bthenvoi")
# Dead MCP tool-name prefix used in prompts.
_DEAD_TOOL_PREFIX = re.compile(r"\bthenvoi_")


def test_no_python_source_imports_dead_thenvoi_namespace():
    offenders: list[str] = []
    for path in _PKG.rglob("*.py"):
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if _DEAD_NAMESPACE.search(line):
                offenders.append(f"{path.relative_to(_PKG)}:{lineno}: {line.strip()}")
    assert not offenders, "Dead 'thenvoi' namespace still referenced:\n" + "\n".join(
        offenders
    )


def test_no_prompt_references_dead_thenvoi_tool_prefix():
    offenders: list[str] = []
    for path in (_PKG / "prompts").rglob("*.md"):
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if _DEAD_TOOL_PREFIX.search(line):
                offenders.append(f"{path.relative_to(_PKG)}:{lineno}: {line.strip()}")
    assert not offenders, "Dead 'thenvoi_' MCP tool prefix still in prompts:\n" + "\n".join(
        offenders
    )
