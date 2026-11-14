"""The report tools. ``report_content`` writes the client-facing narrative (cover synopsis,
executive summary, observations, priorities, attack path, roadmap, engagement details) into the
engagement record — every field optional, a call only changes the fields it carries, lists
replace wholesale. ``report`` regenerates every edition from the record and answers with the
paths written plus the warn-only gap list (never an error): what the report still lacks.
"""

from __future__ import annotations

from typing import ClassVar

from pydantic import BaseModel, ConfigDict, Field

from intern.engagement.models import AttackStep, Observation, RoadmapItem, Tester
from intern.engagement.report_model import (
    ATTACK_STAGES,
    ITEM_LIMITS,
    NARRATIVE_LIMITS,
    ROADMAP_PHASES,
    build_model,
    content_gaps,
)
from intern.engagement.report_render import FIGURES_DIR, write_report
from intern.engagement.store import EngagementStore
from intern.tools.base import Effect, Tool, ToolContext, ToolOutput, fail

#: Client-readability limits spelled out for the model in one place.
LIMIT_HELP = (
    f"synopsis {NARRATIVE_LIMITS['synopsis']}, headline {NARRATIVE_LIMITS['headline']}, "
    f"summary {NARRATIVE_LIMITS['summary']}, worked_well {NARRATIVE_LIMITS['worked_well']}, "
    f"coverage_notes {NARRATIVE_LIMITS['coverage_notes']}, limitations {NARRATIVE_LIMITS['limitations']}, "
    f"observation title/detail {ITEM_LIMITS['observation_title']}/{ITEM_LIMITS['observation_detail']}, "
    f"priority item {ITEM_LIMITS['priority']}, attack-path narrative {ITEM_LIMITS['attack_narrative']}, "
    f"roadmap action {ITEM_LIMITS['roadmap_action']}."
)


class ObservationArg(BaseModel):
    title: str
    detail: str
    findings: list[str] = Field(default_factory=list, description="finding ids the report maps to F-01…")


class PrioritiesArg(BaseModel):
    immediate: list[str] | None = None
    short_term: list[str] | None = None
    structural: list[str] | None = None


class AttackStepArg(BaseModel):
    stage: str = Field(description=f"one of {', '.join(ATTACK_STAGES)}")
    narrative: str = Field(description="what you did at this stage and what it unlocked")
    findings: list[str] = Field(default_factory=list)


class RoadmapItemArg(BaseModel):
    phase: str = Field(description=f"one of {', '.join(ROADMAP_PHASES)}")
    action: str
    closes: list[str] = Field(default_factory=list)
    owner: str | None = None
    effort: int | None = Field(default=None, ge=1, le=3)


class TesterArg(BaseModel):
    name: str
    role: str | None = None


class ReportContentArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    client: str | None = None
    engagement_id: str | None = None
    version: str | None = None
    synopsis: str | None = None
    headline: str | None = None
    summary: str | None = None
    observations: list[ObservationArg] | None = None
    priorities: PrioritiesArg | None = None
    worked_well: str | None = None
    coverage_notes: str | None = None
    attack_path: list[AttackStepArg] | None = None
    roadmap: list[RoadmapItemArg] | None = None
    testers: list[TesterArg] | None = None
    source_ips: list[str] | None = Field(
        default=None, description="public addresses the testing traffic came from (from the operator)"
    )
    limitations: str | None = None


class ReportContentTool(Tool):
    """Write the client-facing narrative the report template needs."""

    name: ClassVar[str] = "report_content"
    description: ClassVar[str] = (
        "Write the client-facing narrative of the report. Call before `report`, after findings are "
        "validated; every field is optional and a call only changes the fields it carries (lists "
        "replace wholesale). Metadata: client, engagement_id, version. Cover: synopsis (one "
        "paragraph: scope, perspective, depth of validation). Executive summary: headline "
        "(one-sentence conclusion), summary (2–3 sentences for a non-technical reader), "
        "observations[{title, detail, findings}], priorities{immediate, short_term, structural}, "
        "worked_well. Attack path: attack_path[{stage, narrative, findings}] — only stages you "
        "actually reached. Roadmap: roadmap[{phase, action, closes, owner?, effort? 1-3}]. "
        "Engagement details: testers[{name, role}] and source_ips[] (from the operator — never "
        f"invent them), limitations (what restricted the test, what was assumed). Length limits "
        f"(rejected if exceeded): {LIMIT_HELP} In report text put code in backticks."
    )
    Args: ClassVar[type[BaseModel]] = ReportContentArgs
    effects: ClassVar[frozenset[Effect]] = frozenset({"write"})

    def __init__(self, store: EngagementStore) -> None:
        self._store = store

    async def run(self, args: ReportContentArgs, ctx: ToolContext) -> ToolOutput:
        store = self._store
        given = {name: value for name, value in args.model_dump().items() if value is not None}
        if not given:
            return fail(
                "report_content was not run: pass at least one field",
                "Carry the fields to change; a call only changes the fields it has.",
            )
        too_long = _length_errors(args)
        if too_long:
            return fail(
                f"too long for the client report — summarise and resend: {'; '.join(too_long)}. "
                "Keep detail in the findings and evidence, not the narrative.",
                "Shorten the named fields and call report_content again.",
                argument=too_long[0].split(" is ")[0],
            )
        bad_stage = next((s.stage for s in args.attack_path or [] if s.stage not in ATTACK_STAGES), None)
        if bad_stage:
            return fail(
                f"unknown attack-path stage {bad_stage!r}",
                f"Use one of {', '.join(ATTACK_STAGES)} — only stages you actually reached.",
                argument="attack_path",
            )
        bad_phase = next((r.phase for r in args.roadmap or [] if r.phase not in ROADMAP_PHASES), None)
        if bad_phase:
            return fail(
                f"unknown roadmap phase {bad_phase!r}",
                f"Use one of {', '.join(ROADMAP_PHASES)}.",
                argument="roadmap",
            )
        referenced = [
            *(fid for o in args.observations or [] for fid in o.findings),
            *(fid for s in args.attack_path or [] for fid in s.findings),
            *(fid for r in args.roadmap or [] for fid in r.closes),
        ]
        known = {v.id for v in store.state.vulns}
        unknown = sorted({fid for fid in referenced if fid not in known})
        if unknown:
            return fail(
                f"unknown finding id(s) {', '.join(unknown)}",
                "Use the ids from state_query what=vulns — the report maps them to F-01, F-02 …",
                argument="findings",
            )
        patch: dict[str, object] = {name: value for name, value in given.items() if name != "priorities"}
        if args.observations is not None:
            patch["observations"] = [
                Observation(title=o.title, detail=o.detail, findings=o.findings) for o in args.observations
            ]
        if args.attack_path is not None:
            patch["attack_path"] = [
                AttackStep(stage=s.stage, narrative=s.narrative, findings=s.findings)  # type: ignore[arg-type]
                for s in args.attack_path
            ]
        if args.roadmap is not None:
            patch["roadmap"] = [
                RoadmapItem(
                    phase=r.phase,  # type: ignore[arg-type]
                    action=r.action,
                    closes=r.closes,
                    owner=r.owner,
                    effort=r.effort,
                )
                for r in args.roadmap
            ]
        if args.testers is not None:
            patch["testers"] = [Tester(name=t.name, role=t.role) for t in args.testers]
        if args.priorities is not None:
            patch["priorities"] = {
                key: value for key, value in args.priorities.model_dump().items() if value is not None
            }
        store.update_report(**patch)
        fields = ", ".join(sorted(given))
        store.log("report", f"content: {fields}")
        return ToolOutput(content=f"report content updated: {fields}")


def _length_errors(args: ReportContentArgs) -> list[str]:
    errs: list[str] = []

    def check(label: str, value: str | None, max_len: int) -> None:
        if value is not None and len(value) > max_len:
            errs.append(f"{label} is {len(value)} chars (max {max_len})")

    for name, max_len in NARRATIVE_LIMITS.items():
        check(name, getattr(args, name), max_len)
    for i, o in enumerate(args.observations or []):
        check(f"observations[{i}].title", o.title, ITEM_LIMITS["observation_title"])
        check(f"observations[{i}].detail", o.detail, ITEM_LIMITS["observation_detail"])
    if args.priorities is not None:
        for key in ("immediate", "short_term", "structural"):
            items = getattr(args.priorities, key) or []
            for i, item in enumerate(items):
                check(f"priorities.{key}[{i}]", item, ITEM_LIMITS["priority"])
    for i, step in enumerate(args.attack_path or []):
        check(f"attack_path[{i}].narrative", step.narrative, ITEM_LIMITS["attack_narrative"])
    for i, item in enumerate(args.roadmap or []):
        check(f"roadmap[{i}].action", item.action, ITEM_LIMITS["roadmap_action"])
    return errs


class ReportArgs(BaseModel):
    """`report` takes no arguments: it regenerates from the engagement record."""

    model_config = ConfigDict(extra="forbid")


class ReportTool(Tool):
    """Regenerate every report edition from the record; gaps come back as warnings, not errors."""

    name: ClassVar[str] = "report"
    description: ClassVar[str] = (
        "Regenerate the client report from the engagement record: report.md, report.html "
        "(self-contained), report.pdf (needs the browser extra) and figures/, all under the "
        "engagement's report/ folder. Takes no arguments. The answer lists the paths written and "
        "every gap the report still has (missing narrative, findings without evidence, unlinked "
        "artifacts, coverage gaps) as warnings — write them with report_content / state_update and "
        "call report again."
    )
    Args: ClassVar[type[BaseModel]] = ReportArgs
    effects: ClassVar[frozenset[Effect]] = frozenset({"write"})

    def __init__(self, store: EngagementStore) -> None:
        self._store = store

    async def run(self, args: ReportArgs, ctx: ToolContext) -> ToolOutput:
        store = self._store
        try:
            files = await write_report(store)
        except Exception as error:  # a report that cannot be written is reported, never raised
            model = build_model(store)
            return ToolOutput(
                content=(f"report generation failed: {error}\n" + "\n".join(f"⚠ {gap}" for gap in content_gaps(model)))
            )
        lines = [
            f"report written ({files.model.confirmed_count} confirmed findings):",
            f"- {files.md}",
            f"- {files.html}",
        ]
        if files.pdf is not None:
            lines.append(f"- {files.pdf}")
        if files.figure_count:
            lines.append(f"- {files.figures} ({files.figure_count} figure(s) in {FIGURES_DIR}/)")
        if files.pdf_skip:
            lines.append(f"⚠ report.pdf not produced: {files.pdf_skip} — report.html and report.md are complete")
        gaps = content_gaps(files.model)
        if gaps:
            lines.append("")
            lines.append("gaps (fix with report_content / state_update / evidence, then re-run report):")
            lines.extend(f"⚠ {gap}" for gap in gaps)
        else:
            lines.append("")
            lines.append("no gaps: the report is complete")
        return ToolOutput(content="\n".join(lines))


__all__ = ["ReportContentArgs", "ReportContentTool", "ReportTool"]
