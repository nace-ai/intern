"""Report diagrams: the agent draws figures for the client report — architecture as understood
from testing, data flows, trust boundaries, connections between components — as plain data
(nodes, edges, optional groups). The report lays the diagram out (longest-path layering with
barycenter ordering, in pure Python — no new dependencies) and draws it as one self-contained
SVG in appendix D. Diagrams are keyed by title: saving again replaces, ``remove`` drops.
"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar

from pydantic import BaseModel, ConfigDict, Field

from intern.engagement.models import Severity
from intern.engagement.store import EngagementStore, slugify
from intern.tools.base import Effect, Tool, ToolContext, ToolOutput, fail

#: Readability limits: a figure must fit one report page with legible text.
DIAGRAM_LIMITS: dict[str, int] = {
    "nodes": 32,
    "edges": 64,
    "groups": 8,
    "label": 48,
    "edge_label": 32,
    "title": 80,
    "caption": 280,
}

#: Figure frame inside a report page; below ``min_scale`` the text is too small to read in print.
FRAME_MAX_W = 854
FRAME_MAX_H = 900
MIN_READABLE_SCALE = 0.62

INK = "#14181F"
INK2 = "#4A5160"
LINE = "#C9CFDA"
#: fill / contour / text per severity tone (finding-linked nodes carry their finding's severity).
TONE: dict[str, tuple[str, str, str]] = {
    "critical": ("#FBE0E6", "#D4183D", "#A80F31"),
    "high": ("#FCE7DD", "#C4320A", "#9E2A08"),
    "medium": ("#FDF0DC", "#B54708", "#8F3C07"),
    "low": ("#EDF0F5", "#5F687A", "#3D4556"),
    "info": ("#FFFFFF", "#8A93A5", "#4A5160"),
}


# -- the diagram as data ---------------------------------------------------------------------------


class DiagramNode(BaseModel):
    id: str
    label: str
    #: Finding id this component is affected by — the figure adds its F-id and severity tone.
    finding: str | None = None
    group: str | None = None


class DiagramEdge(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    from_: str = Field(alias="from")
    to: str
    label: str | None = None


class DiagramGroup(BaseModel):
    id: str
    label: str


class Diagram(BaseModel):
    """One figure: nodes and edges between them, optionally grouped (trust boundaries)."""

    title: str
    caption: str | None = None
    nodes: list[DiagramNode] = Field(default_factory=list)
    edges: list[DiagramEdge] = Field(default_factory=list)
    groups: list[DiagramGroup] = Field(default_factory=list)


def diagram_errors(d: Diagram) -> list[str]:
    """Structural problems with a diagram (unknown references, duplicates, limits). Empty = valid."""
    lim = DIAGRAM_LIMITS
    errs: list[str] = []
    if not d.title.strip():
        errs.append("title is required")
    if len(d.title) > lim["title"]:
        errs.append(f"title is {len(d.title)} chars (max {lim['title']})")
    if d.caption and len(d.caption) > lim["caption"]:
        errs.append(f"caption is {len(d.caption)} chars (max {lim['caption']})")
    if not d.nodes:
        errs.append("at least one node is needed")
    if len(d.nodes) > lim["nodes"]:
        errs.append(f"{len(d.nodes)} nodes (max {lim['nodes']}) — split it into several figures")
    if len(d.edges) > lim["edges"]:
        errs.append(f"{len(d.edges)} edges (max {lim['edges']})")
    if len(d.groups) > lim["groups"]:
        errs.append(f"{len(d.groups)} groups (max {lim['groups']})")
    ids: set[str] = set()
    for n in d.nodes:
        if n.id in ids:
            errs.append(f'duplicate node id "{n.id}"')
        ids.add(n.id)
        if len(n.label) > lim["label"]:
            errs.append(f'node "{n.id}" label is {len(n.label)} chars (max {lim["label"]})')
    group_ids = {g.id for g in d.groups}
    for n in d.nodes:
        if n.group and n.group not in group_ids:
            errs.append(f'node "{n.id}" is in unknown group "{n.group}"')
    for e in d.edges:
        for end in (e.from_, e.to):
            if end not in ids:
                errs.append(f'edge {e.from_}→{e.to}: unknown node "{end}"')
        if e.label and len(e.label) > lim["edge_label"]:
            errs.append(f"edge {e.from_}→{e.to} label is {len(e.label)} chars (max {lim['edge_label']})")
    return errs


# -- text measurement and wrapping (pure Python, no fonts needed) -----------------------------------

_NARROW = re.compile(r"[ijlI.,:;'!|ftr()\[\]{} ]")
_WIDE = re.compile(r"[mwMW@%&]")


def text_width(text: str, size: float, *, bold: bool = False, spacing: float = 0.0) -> float:
    em = sum(
        0.3 if _NARROW.match(ch) else 0.86 if _WIDE.match(ch) else 0.66 if ch.isupper() or ch.isdigit() else 0.56
        for ch in text
    )
    return em * size * (1.06 if bold else 1.0) * 1.04 + spacing * len(text)


def wrap_text(text: str, size: float, max_w: float, max_lines: int, *, bold: bool = False) -> list[str]:
    """Greedy word wrap into at most ``max_lines`` lines no wider than ``max_w`` (last line cut)."""

    def fits(t: str) -> bool:
        return text_width(t, size, bold=bold) <= max_w

    def hard_split(word: str) -> list[str]:
        parts = re.split(r"(?<=[./\-_:@?&=])", word)
        out: list[str] = []
        cur = ""
        for part in parts:
            if cur and not fits(cur + part):
                out.append(cur)
                cur = ""
            while not fits(part):
                if cur:
                    out.append(cur)
                    cur = ""
                cut = 1
                while cut < len(part) and fits(part[: cut + 1]):
                    cut += 1
                out.append(part[:cut])
                part = part[cut:]
            cur += part
        if cur:
            out.append(cur)
        return out

    words = [w for w in re.split(r"\s+", text.strip()) if w]
    pieces: list[str] = []
    for word in words:
        pieces.extend([word] if fits(word) else hard_split(word))
    lines: list[str] = []
    cur = ""
    for piece in pieces:
        nxt = f"{cur} {piece}" if cur else piece
        if fits(nxt):
            cur = nxt
            continue
        if cur:
            lines.append(cur)
        cur = piece
    if cur:
        lines.append(cur)
    if len(lines) <= max_lines:
        return lines
    kept = lines[:max_lines]
    last = f"{kept[-1]} {' '.join(lines[max_lines:])}"
    while len(last) > 1 and not fits(f"{last}…"):
        last = last[:-1]
    return [*kept[:-1], f"{last.rstrip()}…"]


# -- layout ------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class _Size:
    w: float
    h: float
    lines: tuple[str, ...]
    eyebrow: str


@dataclass(frozen=True)
class _Point:
    x: float
    y: float


@dataclass(frozen=True)
class _NodeBox:
    id: str
    x: float
    y: float
    w: float
    h: float
    lines: tuple[str, ...]
    eyebrow: str
    fill: str
    line: str
    text: str


@dataclass(frozen=True)
class _RoutedEdge:
    path: str
    head: tuple[_Point, _Point]
    label_lines: tuple[str, ...] = ()
    label_x: float = 0.0
    label_y: float = 0.0
    label_w: float = 0.0
    label_h: float = 0.0


@dataclass(frozen=True)
class _GroupBox:
    id: str
    label: str
    x: float
    y: float
    w: float
    h: float


@dataclass(frozen=True)
class Layout:
    width: float
    height: float
    scale: float
    nodes: tuple[_NodeBox, ...]
    edges: tuple[_RoutedEdge, ...]
    groups: tuple[_GroupBox, ...]


_PAD_X = 12.0
_PAD_Y = 9.0
_MIN_W = 76.0
_MAX_TEXT_W = 190.0
_NODE_GAP = 28.0
_RANK_GAP = 64.0
_MARGIN = 18.0
_LABEL_SIZE = 12.0
_EYEBROW_SIZE = 8.5


def _measure(label: str, eyebrow: str) -> _Size:
    lines = tuple(wrap_text(label, _LABEL_SIZE, _MAX_TEXT_W, 2, bold=True))
    text_w = max((text_width(line, _LABEL_SIZE, bold=True) for line in lines), default=0.0)
    if eyebrow:
        text_w = max(text_w, text_width(eyebrow, _EYEBROW_SIZE, spacing=0.9))
    h = _PAD_Y * 2 + len(lines) * _LABEL_SIZE * 1.35 + (15.0 if eyebrow else 0.0)
    return _Size(w=max(_MIN_W, text_w + 2 * _PAD_X), h=h, lines=lines, eyebrow=eyebrow)


def _forward_edges(nodes: Sequence[DiagramNode], edges: Sequence[DiagramEdge]) -> set[int]:
    """Indexes of edges that point ``down`` the layering; the rest (same-rank, back, cyclic) route
    around the side. A DFS classifies back edges; same-rank edges are spotted after ranking."""
    adj: dict[str, list[tuple[int, str]]] = {}
    for i, e in enumerate(edges):
        adj.setdefault(e.from_, []).append((i, e.to))
    colour: dict[str, int] = {n.id: 0 for n in nodes}
    back: set[int] = set()
    for root in (n.id for n in nodes):
        if colour[root]:
            continue
        stack: list[tuple[str, int]] = [(root, 0)]
        colour[root] = 1
        while stack:
            node, i = stack[-1]
            outs = adj.get(node, [])
            if i >= len(outs):
                colour[node] = 2
                stack.pop()
                continue
            stack[-1] = (node, i + 1)
            idx, nxt = outs[i]
            if colour.get(nxt, 0) == 1:
                back.add(idx)
            elif not colour.get(nxt, 0):
                colour[nxt] = 1
                stack.append((nxt, 0))
    return back


def _rank(nodes: Sequence[DiagramNode], edges: Sequence[DiagramEdge]) -> dict[str, int]:
    """Longest-path layering over the forward edges (back edges excluded)."""
    back = _forward_edges(nodes, edges)
    forward = [(e.from_, e.to) for i, e in enumerate(edges) if i not in back]
    indeg: dict[str, int] = {n.id: 0 for n in nodes}
    out: dict[str, list[str]] = {n.id: [] for n in nodes}
    for a, b in forward:
        out[a].append(b)
        indeg[b] += 1
    rank_of: dict[str, int] = {n.id: 0 for n in nodes}
    queue = [n.id for n in nodes if indeg[n.id] == 0]
    seen = 0
    while queue:
        node = queue.pop(0)
        seen += 1
        for nxt in out[node]:
            rank_of[nxt] = max(rank_of[nxt], rank_of[node] + 1)
            indeg[nxt] -= 1
            if indeg[nxt] == 0:
                queue.append(nxt)
    if seen < len(nodes):  # a cycle survived the back-edge classification; rank by insertion order
        for e in edges:
            if e.from_ in rank_of and e.to in rank_of:
                rank_of[e.to] = max(rank_of[e.to], rank_of[e.from_] + 1)
    return rank_of


def _order_ranks(
    nodes: Sequence[DiagramNode], edges: Sequence[DiagramEdge], rank_of: Mapping[str, int]
) -> dict[int, list[str]]:
    by_rank: dict[int, list[str]] = {}
    for n in nodes:  # insertion order first, so every later sort has a stable tiebreak
        by_rank.setdefault(rank_of[n.id], []).append(n.id)
    pos = {nid: i for ids in by_rank.values() for i, nid in enumerate(ids)}
    neighbours: dict[str, list[str]] = {n.id: [] for n in nodes}
    for e in edges:
        neighbours.setdefault(e.from_, []).append(e.to)
        neighbours.setdefault(e.to, []).append(e.from_)
    max_rank = max(by_rank) if by_rank else 0
    for sweep in range(4):  # barycenter passes: down the ranks, then up
        downward = sweep % 2 == 0
        ks = range(1, max_rank + 1) if downward else range(max_rank - 1, -1, -1)
        for k in ks:
            ref = k - 1 if downward else k + 1
            ids = by_rank.get(k, [])
            ref_pos = {nid: pos[nid] for nid in by_rank.get(ref, [])}

            def bary(nid: str, ref_pos: dict[str, int] = ref_pos) -> float:
                refs = [ref_pos[x] for x in neighbours.get(nid, []) if x in ref_pos]
                return sum(refs) / len(refs) if refs else float(pos[nid])

            ids.sort(key=lambda nid: (bary(nid), pos[nid]))
            for i, nid in enumerate(ids):
                pos[nid] = i
    return by_rank


def _chevron(tip: _Point, back: _Point, color: str) -> str:
    angle = math.degrees(math.atan2(tip.y - back.y, tip.x - back.x))
    pts = "-9,-5.5 0,0 -9,5.5"
    return (
        f'<polyline points="{pts}" transform="translate({tip.x:.1f} {tip.y:.1f}) '
        f'rotate({angle:.1f})" fill="none" stroke="{color}" stroke-width="1.5" '
        f'stroke-linecap="round" stroke-linejoin="round"/>'
    )


def _path_str(p0: _Point, p1: _Point, p2: _Point, p3: _Point) -> str:
    return f"M {p0.x:.1f} {p0.y:.1f} C {p1.x:.1f} {p1.y:.1f} {p2.x:.1f} {p2.y:.1f} {p3.x:.1f} {p3.y:.1f}"


def layout_diagram(
    d: Diagram, eyebrows: Mapping[str, str] | None = None, tones: Mapping[str, str] | None = None
) -> Layout:
    """Deterministic layered layout: rank by longest path, order ranks by barycenter, route edges
    as smooth curves and size the canvas to its content."""
    eyebrows = eyebrows or {}
    tones = tones or {}
    sizes = {n.id: _measure(n.label, eyebrows.get(n.id, "")) for n in d.nodes}
    rank_of = _rank(d.nodes, d.edges)
    by_rank = _order_ranks(d.nodes, d.edges, rank_of)

    boxes: dict[str, _NodeBox] = {}
    y0 = _MARGIN
    for k in sorted(by_rank):
        ids = by_rank[k]
        band = max(sizes[nid].h for nid in ids)
        x = _MARGIN
        for nid in ids:
            size = sizes[nid]
            fill, line, text = TONE.get(tones.get(nid, "info"), TONE["info"])
            boxes[nid] = _NodeBox(
                nid, x, y0 + (band - size.h) / 2, size.w, size.h, size.lines, size.eyebrow, fill, line, text
            )
            x += size.w + _NODE_GAP
        y0 += band + _RANK_GAP

    @dataclass
    class _Route:
        start: _Point
        c1: _Point
        c2: _Point
        end: _Point
        label_lines: tuple[str, ...]
        label_w: float
        label_h: float

    routes: list[_Route] = []
    for e in d.edges:
        a, b = boxes.get(e.from_), boxes.get(e.to)
        if a is None or b is None:
            continue
        if rank_of[e.to] > rank_of[e.from_]:
            start = _Point(a.x + a.w / 2, a.y + a.h)
            end = _Point(b.x + b.w / 2, b.y)
            gap = max(24.0, min(44.0, (end.y - start.y) / 2))
            c1 = _Point(start.x, start.y + gap)
            c2 = _Point(end.x, end.y - gap)
        else:  # same rank or a back edge: leave and enter on the sides, bulging outward
            right = a.x + a.w / 2 <= b.x + b.w / 2
            start = _Point(a.x + a.w if right else a.x, a.y + a.h / 2)
            end = _Point(b.x + b.w if right else b.x, b.y + b.h / 2)
            bulge = 46.0 if right else -46.0
            c1 = _Point(start.x + bulge, start.y)
            c2 = _Point(end.x + bulge, end.y)
        lines = tuple(wrap_text(e.label, 9.5, 140, 2)) if e.label else ()
        routes.append(
            _Route(
                start,
                c1,
                c2,
                end,
                lines,
                max((text_width(line, 9.5) for line in lines), default=0.0) + 14 if lines else 0.0,
                len(lines) * 12 + 8 if lines else 0.0,
            )
        )

    groups: list[_GroupBox] = []
    for g in d.groups:
        members = [boxes[n.id] for n in d.nodes if n.group == g.id]
        if not members:
            continue
        x0 = min(m.x for m in members) - 16
        y0g = min(m.y for m in members) - 22
        x1 = max(m.x + m.w for m in members) + 16
        y1 = max(m.y + m.h for m in members) + 14
        groups.append(_GroupBox(g.id, g.label, x0, y0g, x1 - x0, y1 - y0g))

    pts = [p for r in routes for p in (r.start, r.c1, r.c2, r.end)]
    xs = [n.x for n in boxes.values()] + [g.x for g in groups] + [p.x for p in pts]
    ys = [n.y for n in boxes.values()] + [g.y for g in groups] + [p.y for p in pts]
    xe = [n.x + n.w for n in boxes.values()] + [g.x + g.w for g in groups] + [p.x for p in pts]
    ye = [n.y + n.h for n in boxes.values()] + [g.y + g.h for g in groups] + [p.y for p in pts]
    if not xs:
        return Layout(0, 0, 1.0, (), (), ())
    dx, dy = _MARGIN - min(xs), _MARGIN - min(ys)
    width = max(xe) + dx + _MARGIN
    height = max(ye) + dy + _MARGIN

    nodes_t = tuple(
        _NodeBox(n.id, n.x + dx, n.y + dy, n.w, n.h, n.lines, n.eyebrow, n.fill, n.line, n.text)
        for n in (boxes[n.id] for n in d.nodes if n.id in boxes)
    )
    edges_t = tuple(
        _RoutedEdge(
            path=_path_str(
                _Point(r.start.x + dx, r.start.y + dy),
                _Point(r.c1.x + dx, r.c1.y + dy),
                _Point(r.c2.x + dx, r.c2.y + dy),
                _Point(r.end.x + dx, r.end.y + dy),
            ),
            head=(
                _Point(r.end.x + dx, r.end.y + dy),
                _Point(r.end.x + dx + (r.end.x - r.c2.x), r.end.y + dy + (r.end.y - r.c2.y)),
            ),
            label_lines=r.label_lines,
            label_x=(r.start.x + 3 * r.c1.x + 3 * r.c2.x + r.end.x) / 8 + dx - r.label_w / 2,
            label_y=(r.start.y + 3 * r.c1.y + 3 * r.c2.y + r.end.y) / 8 + dy - r.label_h / 2,
            label_w=r.label_w,
            label_h=r.label_h,
        )
        for r in routes
    )
    groups_t = tuple(_GroupBox(g.id, g.label, g.x + dx, g.y + dy, g.w, g.h) for g in groups)
    scale = min(1.0, FRAME_MAX_W / width, FRAME_MAX_H / height)
    return Layout(width, height, scale, nodes_t, edges_t, groups_t)


def render_svg(layout: Layout, uid: str = "figure") -> str:
    """The figure as one self-contained SVG: inline styles, no external references, no scripts."""
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" class="figure" width="{layout.width:.0f}" '
        f'height="{layout.height:.0f}" viewBox="0 0 {layout.width:.0f} {layout.height:.0f}" '
        f'role="img" id="{_esc(uid)}">'
    ]
    for g in layout.groups:
        parts.append(
            f'<rect x="{g.x:.1f}" y="{g.y:.1f}" width="{g.w:.1f}" height="{g.h:.1f}" rx="12" '
            f'fill="none" stroke="#98A1B2" stroke-width="1.2" stroke-dasharray="6 5"/>'
        )
        label = wrap_text(g.label, 9, 160, 1)[0] if g.label else ""
        lw = text_width(label, 9, spacing=0.8) + 12
        parts.append(
            f'<rect x="{g.x + 10:.1f}" y="{g.y - 8:.1f}" width="{lw:.1f}" height="16" rx="8" '
            f'fill="#FFFFFF" stroke="#98A1B2" stroke-width="1"/>'
            f'<text x="{g.x + 16:.1f}" y="{g.y + 3.5:.1f}" font-size="9" letter-spacing="0.8" '
            f'fill="#5F687A">{_esc(label.upper())}</text>'
        )
    for e in layout.edges:
        parts.append(f'<path d="{e.path}" fill="none" stroke="{INK2}" stroke-width="1.5" stroke-linecap="round"/>')
    for e in layout.edges:
        if e.label_lines:
            parts.append(
                f'<rect x="{e.label_x:.1f}" y="{e.label_y:.1f}" width="{e.label_w:.1f}" '
                f'height="{e.label_h:.1f}" rx="{e.label_h / 2:.1f}" fill="#FFFFFF" stroke="{LINE}" '
                f'stroke-width="1"/>'
            )
            for i, line in enumerate(e.label_lines):
                parts.append(
                    f'<text x="{e.label_x + e.label_w / 2:.1f}" y="{e.label_y + 13 + i * 12:.1f}" '
                    f'text-anchor="middle" font-size="9.5" fill="{INK2}">{_esc(line)}</text>'
                )
        parts.append(_chevron(e.head[0], e.head[1], INK2))
    for n in layout.nodes:
        parts.append(
            f'<rect x="{n.x:.1f}" y="{n.y:.1f}" width="{n.w:.1f}" height="{n.h:.1f}" rx="9" '
            f'fill="{n.fill}" stroke="{n.line}" stroke-width="1.5"/>'
        )
        cx = n.x + n.w / 2
        if n.eyebrow:
            parts.append(
                f'<text x="{cx:.1f}" y="{n.y + _PAD_Y + 7:.1f}" text-anchor="middle" font-size="8.5" '
                f'letter-spacing="0.9" font-weight="600" fill="{n.line}">{_esc(n.eyebrow.upper())}</text>'
            )
        top = n.y + _PAD_Y + (15.0 if n.eyebrow else 0.0) + _LABEL_SIZE
        for i, line in enumerate(n.lines):
            parts.append(
                f'<text x="{cx:.1f}" y="{top + i * _LABEL_SIZE * 1.35:.1f}" text-anchor="middle" '
                f'font-size="12" font-weight="600" fill="{INK}">{_esc(line)}</text>'
            )
    parts.append("</svg>")
    return "".join(parts)


def _esc(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&#39;")
    )


# -- figures for the report ---------------------------------------------------------------------------


class Figure(BaseModel):
    """A numbered diagram ready for the report: laid out, drawn, its SVG inline."""

    slug: str
    number: int
    title: str
    caption: str | None = None
    svg: str
    scale: float


def build_figures(
    diagrams: Sequence[Diagram], fid_of: Mapping[str, str], sev_of: Mapping[str, Severity]
) -> tuple[list[Figure], list[str]]:
    """Resolve finding references into eyebrows and severity tones, lay out and draw each diagram."""
    warnings: list[str] = []
    figures: list[Figure] = []
    for i, d in enumerate(diagrams):
        errs = diagram_errors(d)
        if errs:
            warnings.append(f'figure "{d.title}": not drawn — {"; ".join(errs)}')
            continue
        eyebrows: dict[str, str] = {}
        tones: dict[str, str] = {}
        for n in d.nodes:
            if not n.finding:
                continue
            fid = fid_of.get(n.finding)
            severity = sev_of.get(n.finding)
            if fid is None or severity is None:
                warnings.append(f'figure "{d.title}": node "{n.id}" references unknown finding {n.finding}')
                continue
            eyebrows[n.id] = f"{fid} · {severity}"
            tones[n.id] = severity
        layout = layout_diagram(d, eyebrows, tones)
        if layout.scale < MIN_READABLE_SCALE:
            warnings.append(
                f'figure "{d.title}": too dense — text prints at {round(layout.scale * 100)}% of '
                f"normal size; split it into smaller figures or drop detail"
            )
        figures.append(
            Figure(
                slug=slugify(d.title)[:40] or f"figure-{i + 1}",
                number=i + 1,
                title=d.title,
                caption=d.caption,
                svg=render_svg(layout, f"fig-{i + 1}"),
                scale=layout.scale,
            )
        )
    return figures, warnings


# -- persistence (keyed by title) ---------------------------------------------------------------------


def diagrams_file(store: EngagementStore) -> Path:
    return store.dir / "diagrams.json"


def load_diagrams(store: EngagementStore) -> list[Diagram]:
    file = diagrams_file(store)
    try:
        data = json.loads(file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    diagrams = data.get("diagrams") if isinstance(data, dict) else None
    if not isinstance(diagrams, list):
        return []
    out: list[Diagram] = []
    for item in diagrams:
        try:
            out.append(Diagram.model_validate(item))
        except ValueError:
            continue
    return out


def save_diagrams(store: EngagementStore, diagrams: Sequence[Diagram]) -> None:
    file = diagrams_file(store)
    file.parent.mkdir(parents=True, exist_ok=True)
    payload = {"diagrams": [d.model_dump(by_alias=True) for d in diagrams]}
    tmp = file.with_name("diagrams.json.tmp")
    tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(file)


def upsert_diagram(diagrams: Sequence[Diagram], d: Diagram) -> list[Diagram]:
    """Replace the diagram with the same title in place, or append — never mutates the input."""
    if any(x.title == d.title for x in diagrams):
        return [d if x.title == d.title else x for x in diagrams]
    return [*diagrams, d]


def remove_diagram(diagrams: Sequence[Diagram], title: str) -> list[Diagram] | None:
    kept = [d for d in diagrams if d.title != title]
    return kept if len(kept) < len(diagrams) else None


# -- the tool ------------------------------------------------------------------------------------------


class DiagramNodeArg(BaseModel):
    id: str
    label: str
    finding: str | None = None
    group: str | None = None


class DiagramEdgeArg(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    from_: str = Field(alias="from")
    to: str
    label: str | None = None


class DiagramGroupArg(BaseModel):
    id: str
    label: str


class DiagramArgs(BaseModel):
    title: str = Field(description="figure title; reusing it replaces that diagram")
    caption: str | None = Field(default=None, description="one or two sentences telling the reader what to see")
    nodes: list[DiagramNodeArg] = Field(default_factory=list)
    edges: list[DiagramEdgeArg] = Field(default_factory=list)
    groups: list[DiagramGroupArg] = Field(default_factory=list)
    remove: bool = Field(default=False, description="drop the diagram with this title")


class ReportDiagramTool(Tool):
    """Draw a figure for the client report; the report lays it out and draws it in its own style."""

    name: ClassVar[str] = "report_diagram"
    description: ClassVar[str] = (
        "Draw a figure for the client report — the target's architecture as you understood it, data "
        "flows, trust boundaries, component connections. You give nodes, edges and optional groups; "
        "the report lays them out and draws them in its own style, numbered with a caption, in the "
        "figures appendix. Reusing a title replaces that diagram; remove=true deletes it. "
        "Set node.finding=<finding id> on a component a confirmed finding affects: it is labelled "
        f"with its F-id and severity tone. Limits: {DIAGRAM_LIMITS['nodes']} nodes, "
        f"{DIAGRAM_LIMITS['edges']} edges, {DIAGRAM_LIMITS['groups']} groups, "
        f"label {DIAGRAM_LIMITS['label']} chars — one idea per figure; split big systems into several."
    )
    Args: ClassVar[type[BaseModel]] = DiagramArgs
    effects: ClassVar[frozenset[Effect]] = frozenset({"write"})

    def __init__(self, store: EngagementStore) -> None:
        self._store = store

    async def run(self, args: DiagramArgs, ctx: ToolContext) -> ToolOutput:
        store = self._store
        current = load_diagrams(store)
        if args.remove:
            kept = remove_diagram(current, args.title)
            if kept is None:
                return fail(
                    f'no diagram "{args.title}" to remove',
                    "Draw it again, or pick the title of a stored diagram.",
                    argument="title",
                )
            save_diagrams(store, kept)
            store.log("report", f"diagram removed: {args.title}")
            return ToolOutput(content=f'diagram "{args.title}" removed')
        diagram = Diagram(
            title=args.title,
            caption=args.caption,
            nodes=[DiagramNode(**n.model_dump()) for n in args.nodes],
            edges=[DiagramEdge(**e.model_dump(by_alias=True)) for e in args.edges],
            groups=[DiagramGroup(**g.model_dump()) for g in args.groups],
        )
        errs = diagram_errors(diagram)
        confirmed = {v.id for v in store.state.vulns if v.status == "confirmed"}
        for n in diagram.nodes:
            if n.finding and n.finding not in confirmed:
                errs.append(f'node "{n.id}": finding {n.finding} is not a confirmed finding')
        if errs:
            return fail(
                f"diagram not saved — {'; '.join(errs)}",
                "Fix the listed problems and call report_diagram again.",
                argument="nodes",
            )
        save_diagrams(store, upsert_diagram(current, diagram))
        store.log("report", f"diagram {diagram.title}: {len(diagram.nodes)} nodes, {len(diagram.edges)} edges")
        from intern.engagement.report_model import build_model  # local import: report_model builds on diagram

        figure = next((f for f in build_model(store).figures if f.title == diagram.title), None)
        scale = figure.scale if figure else 1.0
        legible = scale >= MIN_READABLE_SCALE
        replaced = any(d.title == diagram.title for d in current)
        note = "" if legible else " — ⚠ too dense to read in print: split it or drop detail"
        return ToolOutput(
            content=(
                f'diagram "{diagram.title}" saved ({len(diagram.nodes)} nodes, '
                f"{len(diagram.edges)} edges, prints at {round(scale * 100)}%){note}"
                f"{' — replaced the previous version' if replaced else ''}"
            )
        )


__all__ = [
    "DIAGRAM_LIMITS",
    "MIN_READABLE_SCALE",
    "Diagram",
    "DiagramArgs",
    "DiagramEdge",
    "DiagramGroup",
    "DiagramNode",
    "Figure",
    "ReportDiagramTool",
    "build_figures",
    "diagram_errors",
    "diagrams_file",
    "layout_diagram",
    "load_diagrams",
    "remove_diagram",
    "render_svg",
    "save_diagrams",
    "upsert_diagram",
]
