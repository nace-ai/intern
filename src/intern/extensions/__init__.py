"""Packaged extensions. Two kinds (CONTRIBUTING, "Extensions and capabilities"):

Fixed extensions, chosen for the whole session by a profile's `extensions` list or `--extension`, are in
`REGISTRY`: `tool-summary` (`tool_summary.py`, a run's tool calls counted for eval error analysis). Any
`module:Class` can also be loaded that way, for experiments. The
first pack (write-guard, read-before-edit, loop-detector, tool-output-halflife, decision-triage, output-bounding,
reasoning-policy, quality-monitor) lands after the bare engine has its baseline, one extension per change, each with
its ablation.

Extensions behind a capability are not in the registry: their capability's switch turns them on, and
`config.CAPABILITY_EXTENSIONS` refuses them when named as an extension. Code search (`code_search/`:
`decision_code` and its TUI window) and the workspace (`workspace/`: `index_files`, `decision_files`, `sql`, a
document-aware `read`) by `[capabilities.code_search]` and `[capabilities.workspace]`; memory (`memory/`: recall,
the drive write guard, reminders and the `memory` tool) by `[capabilities.memory]`, loaded with its drive
(docs/memory.md). See docs/capability-settings.md.
"""

from __future__ import annotations

from intern.extensions.tool_summary import ToolSummary
from intern.hooks import Extension

REGISTRY: dict[str, type[Extension]] = {ToolSummary.name: ToolSummary}
