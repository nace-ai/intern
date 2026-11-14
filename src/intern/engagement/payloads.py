"""PayloadsAllTheThings, bundled (assets/payloads/patt.json.txt): one category per vulnerability
class (its Markdown pages + Intruder/fuzzing lists), plus the Methodology pages. Lookup is
forgiving (aliases like sqli, ssti, lfi), pages are read by section so the agent pulls only what
it needs, and lists are written to disk for ffuf / Burp Intruder.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar, Literal

from pydantic import BaseModel, Field

from intern.tools.base import Effect, Tool, ToolContext, ToolOutput, fail

if TYPE_CHECKING:
    from intern.engagement.store import EngagementStore

#: Sections are read in parts of this many characters, split on line boundaries.
PART = 9000
METHODOLOGY = "Methodology and Resources"


@dataclass(frozen=True)
class PFile:
    path: str
    kind: str  # "page" | "list"
    text: str


@dataclass(frozen=True)
class Bundle:
    source: str
    commit: str
    date: str
    license: str
    files: tuple[PFile, ...]


@dataclass(frozen=True)
class Category:
    name: str
    pages: tuple[PFile, ...]
    lists: tuple[PFile, ...]


@dataclass(frozen=True)
class Heading:
    level: int
    text: str
    line: int


@dataclass(frozen=True)
class Hit:
    page: str
    heading: str
    line: str


_BUNDLE: Bundle | None = None


def payload_bundle() -> Bundle:
    """The bundled library, parsed once."""
    global _BUNDLE
    if _BUNDLE is None:
        raw = Path(__file__).parent / "assets" / "payloads" / "patt.json.txt"
        data = json.loads(raw.read_text(encoding="utf-8"))
        files = tuple(
            PFile(path=str(f["path"]), kind=str(f["kind"]), text=str(f["text"]))
            for f in data["files"]  # type: ignore[index]
        )
        _BUNDLE = Bundle(
            source=str(data["source"]),
            commit=str(data["commit"]),
            date=str(data["date"]),
            license=str(data["license"]),
            files=files,
        )
    return _BUNDLE


def categories(bundle: Bundle | None = None) -> list[Category]:
    """Every category (unique top-level path segment) with its pages and wordlists."""
    by: dict[str, list[PFile]] = {}
    for f in (bundle or payload_bundle()).files:
        by.setdefault(f.path.split("/", 1)[0], []).append(f)
    out = []
    for name, fs in sorted(by.items()):
        out.append(Category(name, tuple(f for f in fs if f.kind == "page"), tuple(f for f in fs if f.kind == "list")))
    return out


#: Short aliases → category (or "Category/Page") names.
ALIASES: dict[str, str] = {
    "sqli": "SQL Injection",
    "sql": "SQL Injection",
    "nosqli": "NoSQL Injection",
    "nosql": "NoSQL Injection",
    "xss": "XSS Injection",
    "ssrf": "Server Side Request Forgery",
    "ssti": "Server Side Template Injection",
    "template": "Server Side Template Injection",
    "ssi": "Server Side Include Injection",
    "xxe": "XXE Injection",
    "lfi": "File Inclusion",
    "rfi": "File Inclusion",
    "traversal": "Directory Traversal",
    "path traversal": "Directory Traversal",
    "rce": "Command Injection",
    "cmdi": "Command Injection",
    "command injection": "Command Injection",
    "idor": "Insecure Direct Object References",
    "jwt": "JSON Web Token",
    "csrf": "Cross-Site Request Forgery",
    "cors": "CORS Misconfiguration",
    "deserialization": "Insecure Deserialization",
    "upload": "Upload Insecure Files",
    "file upload": "Upload Insecure Files",
    "redirect": "Open Redirect",
    "smuggling": "Request Smuggling",
    "crlf": "CRLF Injection",
    "ldap": "LDAP Injection",
    "xpath": "XPATH Injection",
    "xslt": "XSLT Injection",
    "graphql": "GraphQL Injection",
    "oauth": "OAuth Misconfiguration",
    "saml": "SAML Injection",
    "prototype pollution": "Prototype Pollution",
    "race": "Race Condition",
    "mass assignment": "Mass Assignment",
    "cache deception": "Web Cache Deception",
    "websocket": "Web Sockets",
    "websockets": "Web Sockets",
    "account takeover": "Account Takeover",
    "ato": "Account Takeover",
    "type juggling": "Type Juggling",
    "api keys": "API Key Leaks",
    "secrets": "API Key Leaks",
    "git": "Insecure Source Code Management",
    "dependency confusion": "Dependency Confusion",
    "reverse shell": f"{METHODOLOGY}/Reverse Shell Cheatsheet",
    "privesc": f"{METHODOLOGY}/Linux - Privilege Escalation",
    "linux privesc": f"{METHODOLOGY}/Linux - Privilege Escalation",
    "windows privesc": f"{METHODOLOGY}/Windows - Privilege Escalation",
    "pivoting": f"{METHODOLOGY}/Network Pivoting Techniques",
    "aws": f"{METHODOLOGY}/Cloud - AWS Pentest",
    "azure": f"{METHODOLOGY}/Cloud - Azure Pentest",
    "kubernetes": f"{METHODOLOGY}/Container - Kubernetes Pentest",
    "k8s": f"{METHODOLOGY}/Container - Kubernetes Pentest",
    "docker": f"{METHODOLOGY}/Container - Docker Pentest",
    "active directory": f"{METHODOLOGY}/Active Directory Attack",
    "ad": f"{METHODOLOGY}/Active Directory Attack",
    "hash cracking": f"{METHODOLOGY}/Hash Cracking",
    "mssql": f"{METHODOLOGY}/MSSQL Server - Cheatsheet",
}


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()


def resolve(ref: str, bundle: Bundle | None = None) -> tuple[Category, PFile | None] | None:
    """A category (and optionally one of its pages) from a name, alias or "Category/Page" reference."""
    cats = categories(bundle)
    full = ALIASES.get(_norm(ref), ref)
    cat_ref, _, page_ref = full.partition("/")
    n = _norm(cat_ref)
    category = next((c for c in cats if _norm(c.name) == n), None)
    if category is None:
        category = next((c for c in cats if _norm(c.name).startswith(n)), None)
    if category is None:
        category = next((c for c in cats if n in _norm(c.name)), None)
    if category is None:
        return None
    if not page_ref:
        return category, None
    pn = _norm(page_ref)
    page = next((p for p in category.pages if _norm(page_name(p)) == pn), None)
    if page is None:
        page = next((p for p in category.pages if pn in _norm(page_name(p))), None)
    return category, page


def page_name(p: PFile) -> str:
    return re.sub(r"\.md$", "", "/".join(p.path.split("/")[1:]))


def list_name(p: PFile) -> str:
    return p.path.split("/")[-1]


def main_page(c: Category) -> PFile | None:
    """The page shown by default: the category README, else its only page."""
    readme = next((p for p in c.pages if re.search(r"(^|/)README\.md$", p.path, re.IGNORECASE)), None)
    return readme if readme is not None else (c.pages[0] if len(c.pages) == 1 else None)


_FENCE = re.compile(r"^\s*```")
_HEADING = re.compile(r"^(#{1,4})\s+(.+?)\s*#*\s*$")
_LINK = re.compile(r"\[([^\]]+)\]\([^)]*\)")


def headings(text: str) -> list[Heading]:
    """Markdown headings (level ≤ 4) outside fenced code blocks, links stripped to their text."""
    out: list[Heading] = []
    fence = False
    for i, line in enumerate(text.split("\n")):
        if _FENCE.match(line):
            fence = not fence
        if fence:
            continue
        m = _HEADING.match(line)
        if m:
            out.append(Heading(level=len(m.group(1)), text=_LINK.sub(r"\1", m.group(2)), line=i))
    return out


def section(text: str, query: str) -> tuple[Heading, str] | None:
    """The text under a heading, until the next heading of the same or a higher level."""
    hs = headings(text)
    q = _norm(query)
    h = next((x for x in hs if _norm(x.text) == q), None)
    if h is None:
        h = next((x for x in hs if q in _norm(x.text)), None)
    if h is None:
        return None
    nxt = next((x for x in hs if x.line > h.line and x.level <= h.level), None)
    lines = text.split("\n")
    end = nxt.line if nxt is not None else len(lines)
    return h, "\n".join(lines[h.line : end]).strip()


def search(
    query: str, *, category: Category | None = None, max_hits: int = 30, bundle: Bundle | None = None
) -> list[Hit]:
    """Lines mentioning the query, with the page and the heading they sit under."""
    q = query.lower()
    if category is not None:
        pages = category.pages
    else:
        pages = tuple(f for f in (bundle or payload_bundle()).files if f.kind == "page")
    hits: list[Hit] = []
    for p in pages:
        hs = headings(p.text)
        for i, line in enumerate(p.text.split("\n")):
            if len(hits) >= max_hits or q not in line.lower():
                continue
            h = next((x for x in reversed(hs) if x.line <= i), None)
            hits.append(
                Hit(page=re.sub(r"\.md$", "", p.path), heading=h.text if h is not None else "", line=line.strip()[:220])
            )
    return hits


def paginate(body: str, part: int, size: int = PART) -> tuple[str, int]:
    """Split long text into parts on line boundaries; part N of the total count."""
    chunks: list[str] = []
    current = ""
    for line in body.split("\n"):
        if current and len(current) + len(line) + 1 > size:
            chunks.append(current)
            current = ""
        current = current + "\n" + line if current else line
    if current:
        chunks.append(current)
    i = min(max(1, part), len(chunks) or 1)
    return (chunks[i - 1] if chunks else ""), len(chunks)


def _credit() -> str:
    b = payload_bundle()
    return f"— PayloadsAllTheThings (MIT, {b.commit[:7]} {b.date})"


class PayloadsArgs(BaseModel):
    action: Literal["list", "show", "search", "wordlist"]
    category: str | None = None
    section: str | None = None
    part: int = Field(default=1, ge=1)
    query: str | None = None
    name: str | None = Field(
        default=None, description="wordlist: file name (or part of it) from the category's wordlists"
    )


class PayloadsTool(Tool):
    """PayloadsAllTheThings inside the harness, read section by section."""

    name = "payloads"
    description = (
        "PayloadsAllTheThings (bundled, offline): payloads, filter/WAF bypasses, engine-specific "
        "techniques and exploitation notes for each vulnerability class — SQL/NoSQL injection, XSS, "
        "SSRF, SSTI, XXE, deserialization, file inclusion/traversal, upload, JWT, OAuth/SAML, "
        "request smuggling, cache deception, prototype pollution, race conditions … plus "
        "Methodology pages (cloud, containers, privilege escalation, pivoting, reverse shells). "
        "action=list: every category. action=show category=<name|alias> (sqli, xss, ssti, lfi, "
        "ssrf, rce, jwt …): its pages, wordlists and section headings; add section=<heading> to "
        "read that part (part=N for long sections); category='SQL Injection/MySQL Injection' "
        "opens a sub-page. action=search query=<text> [category]: find a technique or payload "
        "across the library. action=wordlist category=… name=<file>: write an Intruder/fuzzing "
        "list to the engagement folder for ffuf, Burp Intruder or a script. Use it when a basic "
        "payload is filtered, to fingerprint an engine, or to find the next technique — "
        "non-destructive payloads first (the rules of engagement still apply)."
    )
    Args = PayloadsArgs
    effects: ClassVar[frozenset[Effect]] = frozenset({"read", "write"})

    def __init__(self, store: EngagementStore) -> None:
        self._store = store

    async def run(self, args: PayloadsArgs, ctx: ToolContext) -> ToolOutput:
        if args.action == "list":
            lines = "\n".join(f"- {_category_line(c)}" for c in categories())
            head = "PayloadsAllTheThings categories (show one with action=show category=<name>):"
            return ToolOutput(content=f"{head}\n{lines}\n{_credit()}")
        if args.action == "search":
            return self._search(args)
        if not args.category:
            return fail(
                f"{args.action} needs category (action=list shows them)",
                "Call payloads again with category=<name or alias> (action=list shows them all).",
            )
        resolved = resolve(args.category)
        if resolved is None:
            return fail(
                f"no category '{args.category}' — action=list shows them",
                "Call payloads with action=list to see the categories.",
            )
        category, page = resolved
        if args.action == "wordlist":
            return self._wordlist(args, category)
        return self._show(args, category, page)

    def _search(self, args: PayloadsArgs) -> ToolOutput:
        if not (args.query or "").strip():
            return fail("search needs query", "Call payloads again with query=<text> to search for.")
        category = None
        if args.category:
            resolved = resolve(args.category)
            if resolved is None:
                return fail(
                    f"no category '{args.category}' (action=list)",
                    "Call payloads with action=list to see the categories.",
                )
            category = resolved[0]
        hits = search(args.query or "", category=category)
        scope = f" in {category.name}" if category is not None else ""
        if not hits:
            return ToolOutput(content=f'nothing matches "{args.query}"{scope}')
        lines = "\n".join(f"- {h.page} › {h.heading}: {h.line}" for h in hits)
        return ToolOutput(
            content=f"{lines}\n(read a section with action=show category=<page> section=<heading>)\n{_credit()}"
        )

    def _wordlist(self, args: PayloadsArgs, category: Category) -> ToolOutput:
        want = (args.name or "").lower()
        list_ = next((x for x in category.lists if list_name(x).lower() == want), None)
        if list_ is None:
            list_ = next((x for x in category.lists if bool(want) and want in list_name(x).lower()), None)
        if list_ is None:
            available = ", ".join(list_name(x) for x in category.lists) or "none"
            return fail(
                f"no wordlist '{args.name or ''}' in {category.name} — available: {available}",
                "Call payloads again with name=<file> from the category's wordlists.",
            )
        out_dir = self._store.tool_dir("payloads")
        out_dir.mkdir(parents=True, exist_ok=True)
        file = out_dir / list_name(list_)
        file.write_text(list_.text, encoding="utf-8")
        entries = sum(1 for line in list_.text.split("\n") if line.strip())
        self._store.log("payloads", f"wordlist {list_name(list_)} ({entries} entries) → {file}")
        return ToolOutput(content=f"wrote {entries} entries to {file}\n{_credit()}")

    def _show(self, args: PayloadsArgs, category: Category, page: PFile | None) -> ToolOutput:
        page = page or main_page(category)
        if page is None:
            pages = "\n".join(f"- {page_name(x)}" for x in category.pages)
            head = f'# {category.name}\nPages (open one with category="{category.name}/<page>"):'
            return ToolOutput(content=f"{head}\n{pages}\n{_credit()}")
        if args.section:
            found = section(page.text, args.section)
            if found is None:
                return fail(
                    f"no section '{args.section}' in {page.path} — show it without section to see the headings",
                    "Call payloads again with a section heading listed by action=show.",
                )
            heading, body = found
            text, parts = paginate(body, args.part)
            shown = min(max(1, args.part), parts or 1)
            more = f"\n\n[part {shown}/{parts} — part=N for more]" if parts > 1 else ""
            self._store.log("payloads", f"{page.path} › {heading.text}")
            return ToolOutput(content=f"{text}{more}\n{_credit()}")
        toc = [h for h in headings(page.text) if h.level <= 3][:90]
        toc_lines = "\n".join(f"{'  ' * (h.level - 1)}- {h.text}" for h in toc)
        others = ", ".join(page_name(x) for x in category.pages if x != page)
        wordlists = ", ".join(f"{list_name(x)} ({_entries(x)})" for x in category.lists)
        header = f"# {category.name}{f' / {page_name(page)}' if page is not main_page(category) else ''}"
        lines = [header]
        if others:
            lines.append(f'Other pages: {others} (category="{category.name}/<page>")')
        if wordlists:
            lines.append(f"Wordlists: {wordlists} (action=wordlist name=<file>)")
        lines.append("Sections (read one with section=<heading>):")
        lines.append(toc_lines)
        lines.append(_credit())
        return ToolOutput(content="\n".join(line for line in lines if line))


def _entries(list_: PFile) -> int:
    return sum(1 for line in list_.text.split("\n") if line.strip())


def _category_line(c: Category) -> str:
    pages = f"{len(c.pages)} page{'s' if len(c.pages) != 1 else ''}"
    wordlists = f", {len(c.lists)} wordlist{'s' if len(c.lists) != 1 else ''}" if c.lists else ""
    return f"{c.name} ({pages}{wordlists})"
