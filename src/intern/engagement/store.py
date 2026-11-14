"""The engagement store: one folder per target under ``out/``, one state.json persisted after
every mutation, fresh-vs-resume semantics between runs, and history archiving that never
overwrites a delivered report.

Layout (folders are created on first write)::

    out/<slug>/
      state.json     the record (pretty JSON)
      report/        report.pdf/.html/.md, figures/
      evidence/      proof files; raw/ = bytes behind terminal captures; .incoming/ = captures awaiting recording
      tools/         per-tool output (oast, exploits, payloads, js-intel)
      private/       owner-only (mode 0700): browser logins, the OAST key
      work/          the agent's own files; its shell commands run here
      logs/          events.jsonl, summary.json, findings.md, transcripts/
      history/       earlier runs: history/<YYYY-MM-DD_HHMM>/ holding state.json, report/, logs/

Between runs on the same object: hosts, creds, access and access-derived primitives are reused
(durable knowledge); findings, endpoints, coverage, evidence records and pivots reset; the prior
run moves to ``history/`` so a delivered report is never overwritten.
"""

from __future__ import annotations

import contextlib
import json
import random
import re
import shutil
import string
import time
from collections.abc import Callable, Iterable, Mapping
from pathlib import Path
from typing import Literal

from intern.engagement import pivot
from intern.engagement.cvss import score_cvss, severity_drift
from intern.engagement.depth import merge_depth
from intern.engagement.models import (
    Access,
    CoverageMark,
    Cred,
    Endpoint,
    EventLine,
    EvidenceKind,
    EvidenceRecord,
    Host,
    HttpProof,
    PivotMark,
    Primitive,
    ProbeCell,
    Retest,
    RetestInfo,
    Review,
    Service,
    State,
    Vuln,
    VulnStatus,
    now_ms,
)
from intern.engagement.owasp import COVERAGE_NOTE_MAX, na_excuse, normalize_owasp, owasp_title

EVENTS_MAX = 2000
SEVERITY_ORDER: list[str] = ["info", "low", "medium", "high", "critical"]
FINDING_ICONS = {"info": "·", "low": "▽", "medium": "◆", "high": "▲", "critical": "⛔"}
#: What the decision model's call implies for the finding's status.
DREX_STATUS: dict[str, VulnStatus] = {
    "confirmed": "confirmed",
    "dropped": "false_positive",
    "held": "suspected",
    "downgraded": "suspected",
}


class RecordError(ValueError):
    """A state_update the engagement refuses: the message is the tool's error text."""


StateListener = Callable[[State], None]


def slugify(target: str) -> str:
    slug = re.sub(r"[^a-z0-9._-]+", "-", target.strip().lower().removeprefix("https://").removeprefix("http://"))
    return slug.strip("-")[:64] or "target"


def _free_path(path: Path) -> Path:
    if not path.exists():
        return path
    n = 2
    while path.with_name(f"{path.name}-{n}").exists():
        n += 1
    return path.with_name(f"{path.name}-{n}")


def _history_stamp(started: int) -> str:
    try:
        return time.strftime("%Y-%m-%d_%H%M", time.gmtime(started / 1000))
    except (ValueError, OverflowError):
        return time.strftime("%Y-%m-%d_%H%M", time.gmtime())


def _move_into(source: Path, dest: Path) -> None:
    """A merge-move that never overwrites what already stands at the destination."""
    if not source.exists():
        return
    dest.mkdir(parents=True, exist_ok=True)
    for item in source.iterdir():
        target = _free_path(dest / item.name) if (dest / item.name).exists() else dest / item.name
        shutil.move(str(item), str(target))
    with contextlib.suppress(OSError):
        source.rmdir()


def _empty_tree(folder: Path) -> bool:
    """No files but a standing state.json (and no other files anywhere below)."""
    return all(not p.is_file() or p.name == "state.json" for p in folder.rglob("*"))


def original_name(stored: str) -> str:
    match = re.match(r"^\d{10,}-(.+)$", stored)
    return match.group(1) if match else stored


def detect_vuln_ids(text: str, name: str, known_ids: list[str]) -> list[str]:
    """Vuln ids a piece of evidence names, by word boundary or filename prefix (v6_rce.png → v6-…)."""
    haystack = f"{text} {name}".lower()
    found: list[str] = []
    for vuln_id in known_ids:
        if re.search(rf"(^|[^a-z0-9]){re.escape(vuln_id.lower())}([^a-z0-9]|$)", haystack):
            found.append(vuln_id)
    prefix_match = re.match(r"^(v\d+)[_.-]", name, re.IGNORECASE)
    if prefix_match:
        prefix = prefix_match.group(1)
        matches = [v for v in known_ids if v.lower().startswith(f"{prefix.lower()}-")]
        if len(matches) == 1 and matches[0] not in found:
            found.append(matches[0])
    return found


class EngagementStore:
    """One engagement's record. Every mutator updates ``state`` and saves ``state.json``."""

    def __init__(
        self,
        out_root: Path,
        slug: str,
        target: str,
        *,
        scope: Iterable[str] = (),
        objective: str = "",
        resume: bool = False,
        made_with: str | None = None,
    ) -> None:
        self.out_root = out_root
        self.state = State(slug=slug, target=target, scope=list(scope), objective=objective, started=now_ms())
        self.made_with = made_with
        self._seq = 0
        self._created_here = False
        self._fresh_archive: Path | None = None
        self._fresh_replaced: State | None = None
        self.prior_meta: dict[str, object] | None = None
        self._listeners: list[StateListener] = []
        self.open(resume=resume)

    # -- lifecycle -------------------------------------------------------------------------

    @property
    def dir(self) -> Path:
        return self.out_root / self.state.slug

    @property
    def state_file(self) -> Path:
        return self.dir / "state.json"

    @property
    def evidence_dir(self) -> Path:
        return self.dir / "evidence"

    @property
    def private_dir(self) -> Path:
        return self.dir / "private"

    @property
    def work_dir(self) -> Path:
        return self.dir / "work"

    @property
    def logs_dir(self) -> Path:
        return self.dir / "logs"

    @property
    def report_dir(self) -> Path:
        return self.dir / "report"

    def tool_dir(self, tool: str) -> Path:
        return self.dir / "tools" / tool

    def on_change(self, listener: StateListener) -> None:
        """Called after every save; the board and the dashboards read the state this way."""
        self._listeners.append(listener)

    def _notify(self) -> None:
        for listener in self._listeners:
            listener(self.state)

    def save(self) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        tmp = self.state_file.with_name("state.json.tmp")
        tmp.write_text(self.state.model_dump_json(indent=2, by_alias=True, exclude_none=True), encoding="utf-8")
        tmp.replace(self.state_file)
        self._notify()

    def open(self, *, resume: bool) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        self.evidence_dir.mkdir(parents=True, exist_ok=True)
        self.private_dir.mkdir(parents=True, exist_ok=True)
        self.private_dir.chmod(0o700)
        if not self.state_file.exists():
            self.state.made_with = self.made_with
            self._created_here = True
            self.save()
            return
        try:
            self.state = State.model_validate_json(self.state_file.read_text(encoding="utf-8"))
        except ValueError as e:
            raise RecordError(f"state.json is not readable ({e}); move it aside to start a clean board") from e
        if resume:
            return
        # A fresh session over an existing object: archive the prior run, keep durable knowledge.
        if self._has_records(self.state):
            self._fresh_archive = self._archive_run(self.state)
        self._fresh_replaced = self.state.model_copy(deep=True)
        prior_meta = self.latest_prior()
        access_ids = {a.id for a in self.state.access}
        report = self.state.report.model_copy(deep=True)
        report.observations = []
        report.attack_path = []
        report.roadmap = []
        report.priorities = type(report.priorities)()
        self.state = State(
            slug=self.state.slug,
            target=self.state.target,
            scope=self.state.scope,
            objective=self.state.objective,
            started=now_ms(),
            hosts=self.state.hosts,
            creds=self.state.creds,
            access=self.state.access,
            primitives=[p for p in self.state.primitives if p.from_ in access_ids],
            report=report,
            made_with=self.made_with,
        )
        if prior_meta is not None:
            findings = prior_meta["vulns"]
            if isinstance(findings, list):
                confirmed = sum(1 for v in findings if isinstance(v, dict) and v.get("status") == "confirmed")
                self.prior_meta = {
                    "findings": len(findings),
                    "confirmed": confirmed,
                    "when": prior_meta["started"],
                    "file": str(prior_meta["file"]),
                }
        self.log(
            "engagement",
            "engagement re-opened fresh (prior run archived; recon, creds and footholds reused)",
            save=False,
        )
        self.save()

    def _has_records(self, state: State) -> bool:
        return bool(state.events or state.vulns or state.endpoints or state.evidence)

    def _history_entries(self) -> list[tuple[str, Path]]:
        history = self.dir / "history"
        if not history.exists():
            return []
        entries = ((entry.name, entry) for entry in history.iterdir() if entry.is_dir())
        return sorted(entries, key=lambda t: t[0], reverse=True)

    def latest_prior(self) -> dict[str, object] | None:
        """The newest archived run with findings, as {vulns, started, file}; read-only."""
        for _, entry in self._history_entries():
            state_file = entry / "state.json"
            if not state_file.exists():
                continue
            try:
                prior = State.model_validate_json(state_file.read_text(encoding="utf-8"))
            except ValueError:
                continue
            if prior.vulns:
                return {
                    "vulns": [v.model_dump() for v in prior.vulns],
                    "started": prior.started,
                    "file": state_file,
                }
        return None

    def _archive_run(self, run: State) -> Path:
        entry = _free_path(self.dir / "history" / _history_stamp(run.started))
        entry.mkdir(parents=True)
        (entry / "state.json").write_text(
            run.model_dump_json(indent=2, by_alias=True, exclude_none=True), encoding="utf-8"
        )
        _move_into(self.report_dir, entry / "report")
        logs = entry / "logs"
        for name in ("events.jsonl", "summary.json"):
            source = self.logs_dir / name
            if source.exists():
                logs.mkdir(parents=True, exist_ok=True)
                target = _free_path(logs / name) if (logs / name).exists() else logs / name
                shutil.move(str(source), str(target))
        return entry

    def load_prior(self) -> tuple[int, int]:
        """Fold the latest archived run with findings back into the board (dedup by host+title)."""
        prior = self.latest_prior()
        if prior is None:
            return (0, 0)
        try:
            prior_state = State.model_validate_json(Path(str(prior["file"])).read_text(encoding="utf-8"))
        except (ValueError, OSError):
            return (0, 0)
        have = {(v.host, v.title) for v in self.state.vulns}
        added_findings = 0
        for v in prior_state.vulns:
            if (v.host, v.title) not in have:
                self.state.vulns.append(v)
                added_findings += 1
        seen = {f"{e.method.upper()} {e.url}" for e in self.state.endpoints}
        added_endpoints = 0
        for e in prior_state.endpoints:
            key = f"{e.method.upper()} {e.url}"
            if key not in seen:
                self.state.endpoints.append(e)
                seen.add(key)
                added_endpoints += 1
        self.log("engagement", f"resumed prior run: +{added_findings} finding(s), +{added_endpoints} endpoint(s)")
        return (added_findings, added_endpoints)

    def restore_session(self, slug: str) -> Literal["restored", "already", "missing"]:
        """A resumed chat reopens the engagement it worked on; undo a fresh replacement."""
        if slug == self.state.slug and self._fresh_replaced is not None and self._fresh_archive is not None:
            if not self._has_records(self.state):
                self.state = self._fresh_replaced
                _move_into(self._fresh_archive / "report", self.report_dir)
                _move_into(self._fresh_archive / "logs", self.logs_dir)
                state_back = self._fresh_archive / "state.json"
                if state_back.exists():
                    state_back.unlink()
                if _empty_tree(self._fresh_archive):
                    self._fresh_archive.rmdir()
                self.prior_meta = None
                self.save()
                return "restored"
            return "already"
        if slug == self.state.slug:
            return "already"
        prior = self.out_root / slug / "state.json"
        if not prior.exists():
            return "missing"
        try:
            self.state = State.model_validate_json(prior.read_text(encoding="utf-8"))
        except ValueError as e:
            raise RecordError(f"{prior} is not readable ({e})") from e
        self.save()
        return "restored"

    def switch_target(self, target: str, objective: str | None = None, resume: bool = False) -> None:
        slug = slugify(target)
        if slug == self.state.slug:
            self.configure(target=target, scope=[target], objective=objective)
            return
        throwaway: Path | None = None
        if self._created_here and not self._has_records(self.state):
            throwaway = self.dir
        self.state.slug = slug
        self.open(resume=resume)
        self.configure(target=target, scope=[target], objective=objective)
        if throwaway is not None and throwaway != self.dir:
            _move_into(throwaway / "work", self.work_dir)
            _move_into(throwaway / "logs" / "transcripts", self.logs_dir / "transcripts")
            if _empty_tree(throwaway):
                shutil.rmtree(throwaway)
        self.log(
            "engagement",
            f"engagement → {target} "
            f"({'resumed' if resume else 'fresh, reusing recon/creds/footholds'}, dir {self.dir})",
        )

    def start_retest(self) -> tuple[int, int]:
        """Enter retest mode: load the prior findings if the board is empty, mark the run."""
        loaded = (0, 0)
        if not self.state.vulns:
            loaded = self.load_prior()
        if self.state.retest is None:
            self.state.retest = RetestInfo(started=now_ms())
            self.log("retest", "retest started: verifying the client's fixes for the original findings")
            self.save()
        to_verify = sum(1 for v in self.state.vulns if v.status == "confirmed")
        return (to_verify, loaded[0])

    def discard_if_unused(self) -> None:
        """Remove the folder if this process created it and nothing was recorded in it."""
        if self._created_here and not self._has_records(self.state) and _empty_tree(self.dir):
            shutil.rmtree(self.dir, ignore_errors=True)

    # -- record ----------------------------------------------------------------------------

    def log(self, kind: str, text: str, *, save: bool = True) -> None:
        self.state.events.append(EventLine(ts=now_ms(), kind=kind, text=text))
        del self.state.events[:-EVENTS_MAX]
        if save:
            self.save()

    def _id(self, prefix: str) -> str:
        self._seq += 1
        tail = "".join(random.choices(string.ascii_lowercase + string.digits, k=4))
        return f"{prefix}{self._seq}-{tail}"

    def summary(self) -> dict[str, int]:
        return {
            "hosts": len(self.state.hosts),
            "services": sum(len(h.services) for h in self.state.hosts.values()),
            "vulns": len(self.state.vulns),
            "confirmed": sum(1 for v in self.state.vulns if v.status == "confirmed"),
            "suspected": sum(1 for v in self.state.vulns if v.status == "suspected"),
            "false_positive": sum(1 for v in self.state.vulns if v.status == "false_positive"),
            "creds": len(self.state.creds),
            "endpoints": len(self.state.endpoints),
            "access": len(self.state.access),
        }

    def configure(
        self, *, target: str | None = None, scope: list[str] | None = None, objective: str | None = None
    ) -> None:
        if target is not None:
            self.state.target = target
        if scope is not None:
            self.state.scope = scope
        if objective is not None:
            self.state.objective = objective
        self.save()

    def unvalidated(self) -> list[Vuln]:
        return [v for v in self.state.vulns if v.status == "suspected" or (v.confidence or 0) < 0.8]

    def vuln_by_ref(self, ref: str) -> Vuln | None:
        return next((v for v in self.state.vulns if v.id == ref), None)

    # -- mutators --------------------------------------------------------------------------

    def add_host(
        self,
        ip: str,
        *,
        hostname: str | None = None,
        os_name: str | None = None,
        services: list[Service] | None = None,
        reachability: str | None = None,
        source: str | None = None,
    ) -> Host:
        host = self.state.hosts.get(ip)
        if host is None:
            host = Host(ip=ip, source=source)
            self.state.hosts[ip] = host
        if hostname:
            host.hostname = hostname
        if os_name:
            host.os = os_name
        if services:
            merged = {s.port: s for s in host.services}
            for service in services:
                current = merged.get(service.port)
                if current is None:
                    merged[service.port] = service
                else:
                    update = {k: v for k, v in service.model_dump(exclude={"port"}).items() if v is not None}
                    merged[service.port] = current.model_copy(update=update)
            host.services = [merged[port] for port in sorted(merged)]
        if reachability in ("internal", "public"):
            host.reachability = reachability  # type: ignore[assignment]
            host.reach_why = "recorded by agent"
        elif host.reachability in (None, "unknown"):
            verdict = pivot.classify_host(hostname or ip)
            host.reachability = verdict.reach
            host.reach_why = verdict.why
        self.log("host", f"host {ip}{f' ({hostname})' if hostname else ''}: {len(host.services)} service(s)")
        return host

    def _append_finding(self, v: Vuln) -> None:
        port = f":{v.service_port}" if v.service_port else ""
        icon = FINDING_ICONS[v.severity]
        self.log("vuln", f"{icon} [{v.severity.upper()}] {v.title} — {v.host}{port} ({v.status})")
        findings_md = self.logs_dir / "findings.md"
        findings_md.parent.mkdir(parents=True, exist_ok=True)
        line = f"- {icon} **[{v.severity.upper()}]** {v.title} — {v.host}{port}  _({v.status})_\n"
        if v.evidence:
            line += f"  {v.evidence}\n"
        with findings_md.open("a", encoding="utf-8") as fh:
            fh.write(line)

    def _auto_primitive(self, v: Vuln) -> None:
        kind = pivot.infer_primitive(v)
        if kind is None:
            return
        if any(p.from_ == v.id and p.kind == kind for p in self.state.primitives):
            return
        via = v.title + (f" (port {v.service_port})" if v.service_port else "")
        self.state.primitives.append(
            Primitive(id=self._id("x"), kind=kind, host=v.host, via=via, from_=v.id, ts=now_ms())
        )
        self.log("pivot", f"primitive {kind} on {v.host} (from {v.id})")

    def add_vuln(self, fields: Mapping[str, object], *, found_by: str | None = None) -> Vuln:
        """Create or update a finding. Defined values are applied only where present; depth never
        drops, owasp tags union, the id never changes."""
        host = fields.get("host")
        title = fields.get("title")
        existing: Vuln | None = None
        if fields.get("id"):
            existing = self.vuln_by_ref(str(fields["id"]))
            if existing is None:
                raise RecordError(f"no finding {fields['id']} (state_query what=vulns lists ids)")
        elif host and title:
            existing = next((v for v in self.state.vulns if v.host == host and v.title == title), None)
        if existing is None:
            if not (host and title):
                raise RecordError("a new finding needs host and title")
            if str(fields.get("severity", "info")) not in SEVERITY_ORDER:
                raise RecordError(f"severity must be one of {', '.join(SEVERITY_ORDER)}")
            status = str(fields.get("status", "suspected"))
            if status not in ("suspected", "confirmed", "false_positive"):
                raise RecordError("status must be suspected, confirmed or false_positive")
            existing = Vuln(
                id=self._id("v"),
                host=str(host),
                title=str(title),
                severity=str(fields.get("severity", "info")),  # type: ignore[arg-type]
                status=status,  # type: ignore[arg-type]
                found_by=found_by,
            )
            self.state.vulns.append(existing)
            self._append_finding(existing)
        else:
            status = fields.get("status")
            if status is not None:
                if status not in ("suspected", "confirmed", "false_positive"):
                    raise RecordError("status must be suspected, confirmed or false_positive")
                existing.status = status  # type: ignore[assignment]
            if fields.get("severity") is not None and str(fields["severity"]) in SEVERITY_ORDER:
                existing.severity = str(fields["severity"])  # type: ignore[arg-type]
        for name in ("evidence", "cwe", "description", "stop_reason"):
            value = fields.get(name)
            if isinstance(value, str) and value:
                setattr(existing, name, value)
        if fields.get("service_port") is not None:
            existing.service_port = int(fields["service_port"])  # type: ignore[arg-type]
        if fields.get("stop_kind") is not None:
            if str(fields["stop_kind"]) not in ("terminal", "rules", "disproved", "stuck", "time"):
                raise RecordError("stop_kind must be terminal, rules, disproved, stuck or time")
            existing.stop_kind = str(fields["stop_kind"])  # type: ignore[assignment]
        if existing.stop_reason and not existing.stop_kind:
            raise RecordError("stop_reason needs stop_kind (terminal, rules, disproved, stuck, time)")
        for name in ("impact", "likelihood", "remediation", "references"):
            value = fields.get(name)
            if isinstance(value, list) and value:
                setattr(existing, name, [str(x) for x in value])
        raw_tags = fields.get("owasp")
        owasp, unknown = normalize_owasp(raw_tags if isinstance(raw_tags, list) else None)
        if unknown:
            raise RecordError(f"unknown OWASP id(s) {', '.join(unknown)} — use A01..A10, API1..API10, LLM01..LLM10")
        if owasp:
            existing.owasp = list(dict.fromkeys(existing.owasp + owasp))
        vector = fields.get("cvss")
        if isinstance(vector, str) and vector:
            result = score_cvss(vector)
            if isinstance(result, Exception):
                raise RecordError(str(result))
            existing.cvss = result.score
        if fields.get("risk_accepted") is not None:
            existing.risk_accepted = bool(fields["risk_accepted"])
        if any(fields.get(k) for k in ("http_request", "http_response", "http_caption")):
            existing.http = HttpProof(
                request=str(fields.get("http_request") or "") or None,
                response=str(fields.get("http_response") or "") or None,
                caption=str(fields.get("http_caption") or "") or None,
            )
        depth = fields.get("depth")
        if depth is not None and str(depth) not in ("detected", "confirmed", "impact", "chained"):
            raise RecordError("depth must be detected, confirmed, impact or chained")
        existing.depth = merge_depth(existing.depth, str(depth) if depth else None, existing.status)
        self._auto_primitive(existing)
        self.save()
        return existing

    def add_cred(
        self,
        *,
        username: str | None = None,
        secret: str | None = None,
        source: str | None = None,
        valid_for: str | None = None,
        works: bool | None = None,
    ) -> Cred:
        cred = Cred(id=self._id("c"), username=username, secret=secret, source=source, valid_for=valid_for, works=works)
        self.state.creds.append(cred)
        self.log("cred", f"credential {cred.id}{f' ({username})' if username else ''} recorded")
        return cred

    def add_endpoint(
        self,
        *,
        method: str,
        url: str,
        auth: str | None = None,
        status: int | None = None,
        result: str | None = None,
        interesting: bool | None = None,
        owasp: list[str] | None = None,
        title: str | None = None,
        sample: str | None = None,
        params: list[str] | None = None,
        source: str | None = None,
        matrix: list[ProbeCell] | None = None,
    ) -> Endpoint:
        ids, unknown = normalize_owasp(owasp)
        if unknown:
            raise RecordError(f"unknown OWASP id(s) {', '.join(unknown)} — use A01..A10, API1..API10, LLM01..LLM10")
        key = f"{method.upper()} {url}"
        endpoint = next((e for e in self.state.endpoints if f"{e.method.upper()} {e.url}" == key), None)
        if endpoint is None:
            endpoint = Endpoint(
                id=self._id("e"), method=method.upper(), url=url, tested=title or result or "probed", ts=now_ms()
            )
            self.state.endpoints.append(endpoint)
        endpoint.auth = auth or endpoint.auth
        endpoint.status = status if status is not None else endpoint.status
        endpoint.result = result or endpoint.result
        endpoint.interesting = interesting if interesting is not None else endpoint.interesting
        if ids:
            endpoint.owasp = list(dict.fromkeys(endpoint.owasp + ids))
        endpoint.sample = sample or endpoint.sample
        endpoint.params = params or endpoint.params
        endpoint.source = source or endpoint.source
        if matrix:
            endpoint.matrix = matrix
        endpoint.ts = now_ms()
        star = " [★]" if endpoint.interesting else ""
        self.log("endpoint", f"{method.upper()} {url} → {status}{star}")
        return endpoint

    def add_access(
        self,
        *,
        access_type: str,
        target: str,
        via: str | None = None,
        cred_ref: str | None = None,
        level: str | None = None,
        reusable: bool = True,
        notes: str | None = None,
    ) -> Access:
        existing = next(
            (a for a in self.state.access if a.type == access_type and a.target == target and a.via == via), None
        )
        if existing is None:
            existing = Access(
                id=self._id("a"),
                type=access_type,
                target=target,
                via=via,
                cred_ref=cred_ref,
                level=level,
                reusable=reusable,
                notes=notes,
                ts=now_ms(),
            )
            self.state.access.append(existing)
            kind = pivot.access_primitive(access_type)
            if kind:
                via_text = access_type + (f" via {via}" if via else "") + (f" ({level})" if level else "")
                self.state.primitives.append(
                    Primitive(id=self._id("x"), kind=kind, host=target, via=via_text, from_=existing.id, ts=now_ms())
                )
        else:
            existing.level = level or existing.level
            existing.notes = notes or existing.notes
            existing.reusable = reusable
            existing.ts = now_ms()
        self.log("access", f"access: {access_type} on {target}{f' via {via}' if via else ''}")
        return existing

    def recheck_vuln(self, ref: str, *, remediated: bool, notes: str | None = None) -> Vuln:
        v = self.vuln_by_ref(ref)
        if v is None:
            raise RecordError(f"no matching finding {ref} (state_query what=vulns lists ids)")
        v.rechecked_at = now_ms()
        v.remediated = remediated
        v.recheck_note = notes
        self.log("vuln", f"recheck {v.id}: {'REMEDIATED' if remediated else 'still OPEN'}")
        return v

    def retest_vuln(self, ref: str, *, result: str, notes: str) -> Vuln:
        if result not in ("fixed", "not_fixed", "partial"):
            raise RecordError("retest_result must be fixed, not_fixed or partial")
        if not notes.strip():
            raise RecordError("notes are required: what you sent and what came back (and which variations you tried)")
        v = self.vuln_by_ref(ref)
        if v is None:
            raise RecordError(f"no finding {ref} (state_query what=vulns lists ids)")
        v.retest = Retest(result=result, ts=now_ms(), note=notes)  # type: ignore[arg-type]
        v.remediated = result == "fixed"
        v.rechecked_at = now_ms()
        self.log("retest", f"retest {v.id}: {result}")
        return v

    def mark_coverage(
        self, ids: list[str], *, status: Literal["tested", "na"], note: str, by: str | None = None
    ) -> None:
        if status == "na" and na_excuse(note):
            raise RecordError(
                "n/a means the category can't apply to this target — say why (e.g. 'no API surface'). "
                "A category you didn't get to test is untested, not n/a."
            )
        if len(note) > COVERAGE_NOTE_MAX:
            raise RecordError(f"notes are over {COVERAGE_NOTE_MAX} chars — summarise (detail belongs in the report)")
        for category_id in ids:
            prior = next((m for m in self.state.coverage if m.id == category_id), None)
            testers: list[str] = list(prior.testers) if prior else []
            if prior and prior.by:
                testers.append(prior.by)
            if by:
                testers.append(by)
            testers = list(dict.fromkeys(testers))
            mark = CoverageMark(id=category_id, status=status, note=note, ts=now_ms(), by=by, testers=testers)
            if prior is None:
                self.state.coverage.append(mark)
            else:
                self.state.coverage = [mark if m.id == category_id else m for m in self.state.coverage]
        self.log("coverage", f"coverage {', '.join(ids)} = {status}")

    def add_primitive(
        self, *, kind: str, host: str, via: str, from_: str | None = None, notes: str | None = None
    ) -> Primitive:
        if kind not in ("ssrf", "rce", "shell", "tunnel", "file_read", "db_read", "token", "xss"):
            raise RecordError("kind must be one of ssrf, rce, shell, tunnel, file_read, db_read, token, xss")
        if not (kind and host and via):
            raise RecordError("a primitive needs kind, host and via (how to use it, e.g. 'GET /api/fetch?url=')")
        existing = next((p for p in self.state.primitives if p.kind == kind and p.host == host and p.via == via), None)
        if existing is not None:
            return existing
        primitive = Primitive(id=self._id("x"), kind=kind, host=host, via=via, from_=from_, notes=notes, ts=now_ms())
        self.state.primitives.append(primitive)
        self.log("pivot", f"primitive {kind} on {host}: {via}")
        return primitive

    def mark_pivot(self, oid: str, *, status: str, note: str | None = None) -> None:
        if status not in ("success", "failed", "skipped"):
            raise RecordError("pivot_result must be success, failed or skipped")
        valid = {
            o.id
            for o in pivot.opportunities(
                self.state.hosts, self.state.creds, self.state.endpoints, self.state.primitives
            )
        }
        if oid not in valid:
            raise RecordError(f"no pivot opportunity {oid} (state_query what=pivots)")
        record = PivotMark(id=oid, status=status, note=note, ts=now_ms())  # type: ignore[arg-type]
        self.state.pivots = (
            [record if m.id == oid else m for m in self.state.pivots]
            if any(m.id == oid for m in self.state.pivots)
            else [*self.state.pivots, record]
        )
        self.log("pivot", f"pivot {oid}: {status}{f' — {note}' if note else ''}")

    # -- evidence --------------------------------------------------------------------------

    def add_evidence(
        self,
        *,
        description: str,
        file: Path | None = None,
        kind: str = "note",
        vuln_ids: list[str] | None = None,
        url: str | None = None,
        caption: str | None = None,
        move: bool = False,
    ) -> EvidenceRecord | None:
        record: EvidenceRecord | None = None
        if file is not None and file.exists():
            self.evidence_dir.mkdir(parents=True, exist_ok=True)
            stored = self.evidence_dir / f"{now_ms()}-{file.name}"
            if move:
                try:
                    file.replace(stored)
                except OSError:
                    shutil.copy2(file, stored)
                    file.unlink()
            else:
                shutil.copy2(file, stored)
            evidence_kind: EvidenceKind = (
                kind if kind in ("screenshot", "response", "loot", "note") else "note"  # type: ignore[assignment]
            )
            record = EvidenceRecord(
                file=str(stored),
                name=original_name(stored.name),
                kind=evidence_kind,
                description=description,
                vuln_ids=vuln_ids or [],
                url=url,
                caption=caption,
                ts=now_ms(),
            )
            self.state.evidence.append(record)
            self.log("evidence", f"{evidence_kind}: {description} [{stored.name}]")
        return record

    def link_evidence(self, name: str, vuln_ids: list[str]) -> str:
        for record in self.state.evidence:
            if record.name == name or Path(record.file).name == name:
                record.vuln_ids = list(dict.fromkeys([*record.vuln_ids, *vuln_ids]))
                self.save()
                return record.name
        raise RecordError(f"no stored evidence named {name}")

    # -- report ----------------------------------------------------------------------------

    def update_report(self, **fields: object) -> None:
        report = self.state.report
        priorities = fields.pop("priorities", None)
        for name, value in fields.items():
            if value is None or name not in report.model_fields:
                continue
            setattr(report, name, value)
        if isinstance(priorities, dict):
            for horizon in ("immediate", "short_term", "structural"):
                value = priorities.get(horizon)
                if isinstance(value, list):
                    setattr(report.priorities, horizon, [str(x) for x in value])
        self.save()

    # -- decision-model review -------------------------------------------------------------

    def review_queue(self) -> dict[str, list[Vuln] | int]:
        """Findings with a drex call, split into awaiting verification and awaiting the final word."""
        with_call = [v for v in self.state.vulns if v.drex_call]
        to_verify = [v for v in with_call if not any(r.stage == "verifier" for r in v.reviews)]
        to_finalize = [
            v
            for v in with_call
            if any(r.stage == "verifier" for r in v.reviews) and not any(r.stage == "final" for r in v.reviews)
        ]
        return {
            "decisions": len(with_call),
            "verified": len(with_call) - len(to_verify),
            "finalized": sum(1 for v in with_call if any(r.stage == "final" for r in v.reviews)),
            "overturned": sum(
                1 for v in with_call if any(r.stage == "final" and r.verdict == "overturned" for r in v.reviews)
            ),
            "to_verify": to_verify,
            "to_finalize": to_finalize,
        }

    def review_drex_call(
        self,
        ref: str,
        stage: Literal["verifier", "final"],
        *,
        verdict: str,
        status: str | None = None,
        notes: str | None = None,
    ) -> tuple[Vuln, str]:
        """Record a review of the decision model's call. The verifier recommends; the final call
        (the main agent's) applies an overturn to the finding's status."""
        v = self.vuln_by_ref(ref)
        if v is None:
            raise RecordError(f"no finding {ref} (state_query what=vulns lists ids)")
        if not v.drex_call:
            raise RecordError(
                f"drex made no call on {ref} — nothing to review "
                "(only findings drex confirmed, dropped, held or downgraded are reviewed)"
            )
        if not (notes or "").strip():
            raise RecordError("notes are required: what you re-tested live and what you saw")
        if stage == "final" and not any(r.stage == "verifier" for r in v.reviews):
            raise RecordError(
                f"{ref} has no verifier review yet — dispatch the verifier to re-test it first "
                "(state_query what=review lists the queue)"
            )
        implied = DREX_STATUS[v.drex_call]
        overturned = verdict == "overturned"
        current = v.status if stage == "final" else implied
        if overturned:
            if status not in ("confirmed", "suspected", "false_positive"):
                raise RecordError(
                    "an overturned call needs review_status = the status the evidence supports "
                    "(confirmed, suspected or false_positive)"
                )
            if status == current:
                raise RecordError(
                    f"review_status={status} matches what drex's call already implies — that is verdict=upheld"
                )
        v.reviews.append(
            Review(stage=stage, verdict=verdict, status=status if overturned else None, notes=notes or "", ts=now_ms())  # type: ignore[arg-type]
        )
        message = ""
        if stage == "verifier":
            self.log("drex", f"verifier {v.id}: {verdict}{f' → {status}' if overturned and status else ''} — {v.title}")
            message = f"The status stays {v.status} until the main agent's final call."
        elif overturned and status:
            v.status = status  # type: ignore[assignment]
            v.decided_by = "agent"
            self.log("drex", f"final call on {v.id}: drex's call ({v.drex_call}) overturned → {status}")
            message = f"final call on {v.id}: drex's call ({v.drex_call}) overturned → {status}" + (
                " (capture proof and re-check it live like any confirmed finding)" if status == "confirmed" else ""
            )
        else:
            self.log("drex", f"final call on {v.id}: drex's call ({v.drex_call}) upheld — stays {v.status}")
            message = f"final call on {v.id}: drex's call ({v.drex_call}) upheld — stays {v.status}"
        self.save()
        return v, message


def severity_drift_note(v: Vuln) -> str | None:
    if v.cvss and severity_drift(v.severity, v.cvss.band):
        return (
            f"severity {v.severity} is more than one step from the CVSS band ({v.cvss.band}) — "
            "state why in likelihood (environment, compensating controls)"
        )
    return None


def findings_markdown_summary(state: State) -> str:
    """The one-line-per-finding view the dash and the summary show."""
    lines = []
    for v in sorted(
        state.vulns, key=lambda x: SEVERITY_ORDER.index(x.severity) if x.severity in SEVERITY_ORDER else 0, reverse=True
    ):
        port = f":{v.service_port}" if v.service_port else ""
        lines.append(f"{FINDING_ICONS[v.severity]} {v.id} [{v.severity}] {v.title} — {v.host}{port} ({v.status})")
    return "\n".join(lines) or "no findings recorded yet"


def json_dumps(data: object) -> str:
    return json.dumps(data, indent=2, ensure_ascii=False, default=str)


__all__ = [
    "DREX_STATUS",
    "EngagementStore",
    "RecordError",
    "detect_vuln_ids",
    "findings_markdown_summary",
    "json_dumps",
    "original_name",
    "severity_drift_note",
    "slugify",
]

# owasp_title is re-exported for the tools' response text.
_owasp_title = owasp_title
