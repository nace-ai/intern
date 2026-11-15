#!/usr/bin/env python3
"""Check every hyperlink the project shows and write the result.

    python3 tools/link_audit.py [--browser] [--out DIR]

Checked:
  statute   the page of every statute in data/statutes: the address the screens, Slack and MCP replies use
  site      the public site's pages, and each case's courtroom address
  doc       every http link in README.md, DEMO.md, PRD.md and docs/ARCHITECTURE.md
  address   an address inside code text in those files (an API endpoint, a command): listed, not requested,
            because it is not a link and an endpoint answers a plain GET with 404 or 405
Written: DIR/link-audit-<stamp>.jsonl (one row per link) and DIR/link-audit-<stamp>.md (counts, and every link that is
not ok). DIR defaults to runs/. Exit code 1 when any link is dead or opens the wrong page.

Results: ok; dead; wrong page (a LawWiki page whose title does not name the section, or a script-drawn page without
the section heading); not checkable (the site refuses automated clients, as nysenate.gov does); status only (a
script-drawn page answered 200 but was not opened in a browser). --browser opens the script-drawn pages
(statutes.capitol.texas.gov answers 200 for any address) in headless Chrome through tools/browser_check.mjs, about
7 seconds a page (Node 22+ and Google Chrome). One request at a time to each site, four a second at most.
"""
from __future__ import annotations

import concurrent.futures as cf
import json
import re
import subprocess
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from engine import cases, config, lawwiki, linkcheck, net  # noqa: E402

SCRIPT_DRAWN = {"statutes.capitol.texas.gov"}   # hosts that answer 200 for any address
DOCS = ["README.md", "DEMO.md", "PRD.md", "docs/ARCHITECTURE.md"]
PAGES = ["", "01-slack-intake.html", "02-live-courtroom.html", "03-trace-ledger.html", "deck.html", "walkthrough.mp4"]
URL_RE = re.compile(r"https?://[^\s<>\"'`)\]]+")
CODE_RE = re.compile(r"```.*?```|`[^`\n]*`", re.S)   # fenced blocks and inline code
LOCAL = ("127.0.0.1", "localhost", "example.com", "x.test")


def host(url: str) -> str:
    return url.split("/")[2].lower()


def gather() -> list[dict]:
    """Every link to check: {kind, url, where, ...}."""
    shown = defaultdict(list)
    for c in cases.all_cases():
        for s in c["statutes"]:
            shown[s["key"]].append(c["id"])
    rows = []
    for f in sorted(config.STATUTES_DIR.glob("*_*.json")):
        if f.name.startswith("db_"):
            continue
        d = json.loads(f.read_text(encoding="utf-8"))
        key = f"{d['jurisdiction']}/{d['code']}/{d['section']}"
        rows.append({"kind": "statute", "url": lawwiki.url_for(d), "where": key, "section": d["section"], "cases": shown.get(key, [])})
    base = config.public_url()
    rows += [{"kind": "site", "url": f"{base}/{p}", "where": p or "walkthrough"} for p in PAGES]
    rows += [{"kind": "site", "url": f"{base}/{config.COURTROOM_PAGE}#case={c['id']}", "where": "courtroom " + c["id"], "case": c["id"]} for c in cases.all_cases()]
    seen = set()
    for name in DOCS:
        path = ROOT / name
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8")
        for kind, part in (("doc", CODE_RE.sub(" ", text)), ("address", " ".join(CODE_RE.findall(text)))):
            for url in URL_RE.findall(part):
                url = url.rstrip(".,;:*")
                if (name, url) not in seen and not any(h in host(url) for h in LOCAL):
                    seen.add((name, url))
                    rows.append({"kind": kind, "url": url, "where": name})
    return rows


def check_all(rows: list[dict]) -> None:
    """Add state, status, title and why to each row: one request at a time to each site."""
    by_host = defaultdict(list)
    for url in dict.fromkeys(r["url"] for r in rows if r["kind"] != "address"):
        by_host[host(url)].append(url)

    def one_site(urls: list[str]) -> dict:
        out = {}
        for url in urls:
            out[url] = linkcheck.check(url)
            time.sleep(0.25)
        return out
    found = {}
    with cf.ThreadPoolExecutor(max(1, len(by_host))) as pool:
        for part in pool.map(one_site, by_host.values()):
            found.update(part)
    for r in rows:
        r.update({k: found[r["url"]][k] for k in ("state", "status", "title", "why")} if r["url"] in found and r["kind"] != "address"
                 else {"state": "skipped", "status": None, "title": "", "why": "an address inside code text, not a link"})


def judge(rows: list[dict], browser: bool) -> None:
    """Set `result` on each row (see the module text)."""
    try:
        _, script = net.send("GET", f"{config.public_url()}/tad-sim.js", tries=1)
        site_cases = set(re.findall(r'id: "([a-z0-9-]+)", short:', script.decode("utf-8", "replace")))
    except net.NetError:
        site_cases = set()
    drawn = [r for r in rows if r["state"] == "ok" and host(r["url"]) in SCRIPT_DRAWN]
    seen = {}
    if browser and drawn:
        urls = list(dict.fromkeys(r["url"] for r in drawn))
        print(f"opening {len(urls)} script-drawn pages in a browser, about {len(urls) * 7 // 60 + 1} minutes", flush=True)
        done = subprocess.run(["node", str(ROOT / "tools" / "browser_check.mjs"), *urls], capture_output=True, text=True)
        for line in done.stdout.splitlines():
            try:
                row = json.loads(line)
                seen[row["url"]] = row
            except ValueError:
                pass
    for r in rows:
        if r["state"] == "skipped":
            r["result"] = "not a link"
        elif r["state"] == "dead":
            r["result"] = "dead"
        elif r["state"] == "unchecked":
            r["result"] = "not checkable"
        elif host(r["url"]) in SCRIPT_DRAWN:
            b = seen.get(r["url"])
            r["result"] = "status only" if b is None else "ok" if b["ok"] else "wrong page"
            if b is not None:
                r["browser"] = b["found"]
        elif r["kind"] == "statute" and "lawwiki.ai" in host(r["url"]) and r["section"].lower() not in r["title"].lower():
            r["result"] = "wrong page"
            r["why"] = "the page title does not name the section"
        elif r.get("case") and r["case"] not in site_cases:
            r["result"] = "wrong page"
            r["why"] = "the site's simulator has no such case"
        else:
            r["result"] = "ok"


def report(rows: list[dict], stamp: str, browser: bool) -> str:
    kinds = sorted({r["kind"] for r in rows})
    names = ["ok", "status only", "not checkable", "wrong page", "dead", "not a link"]
    out = [f"# Link audit {stamp}", "",
           f"{len(rows)} links. Statute pages: LawWiki where it publishes the jurisdiction ({', '.join(sorted(lawwiki.PUBLISHED))}), "
           "else the official source. Script-drawn pages were " + ("opened in a browser." if browser else "not opened in a browser (run with --browser)."), "",
           "| Kind | " + " | ".join(names) + " | Total |", "|---|" + "---|" * (len(names) + 1)]
    for k in kinds:
        n = Counter(r["result"] for r in rows if r["kind"] == k)
        out.append(f"| {k} | " + " | ".join(str(n[x]) for x in names) + f" | {sum(n.values())} |")
    hosts = Counter((host(r["url"]), r["result"]) for r in rows if r["kind"] == "statute")
    out += ["", "## Statute pages by site", "", "| Site | Result | Links |", "|---|---|---|"]
    out += [f"| {h} | {res} | {n} |" for (h, res), n in sorted(hosts.items())]
    rest = [r for r in rows if r["result"] not in ("ok", "not a link")]
    out += ["", f"## Links that are not ok ({len(rest)})", ""]
    for r in rest:
        out += [f"result: {r['result']}", f"kind: {r['kind']}", f"where: {r['where']}", f"url: {r['url']}", f"status: {r['status']}", f"why: {r['why']}", ""]
    if not rest:
        out.append("None.")
    return "\n".join(out) + "\n"


def main() -> int:
    args = sys.argv[1:]
    if "-h" in args or "--help" in args:
        print(__doc__)
        return 2
    browser = "--browser" in args
    out_dir = Path(args[args.index("--out") + 1]) if "--out" in args else config.RUNS_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    rows = gather()
    check_all(rows)
    judge(rows, browser)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    base = out_dir / f"link-audit-{stamp}"
    base.with_suffix(".jsonl").write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
    base.with_suffix(".md").write_text(report(rows, stamp, browser), encoding="utf-8")
    n = Counter(r["result"] for r in rows)
    print(f"{len(rows)} links: " + ", ".join(f"{v} {k}" for k, v in n.most_common()))
    for r in rows:
        if r["result"] in ("dead", "wrong page"):
            print(f"  {r['result']}: {r['url']} ({r['where']}; {r['why']})")
    print(f"rows: {base.with_suffix('.jsonl')}\nsummary: {base.with_suffix('.md')}")
    return 1 if n["dead"] or n["wrong page"] else 0


if __name__ == "__main__":
    sys.exit(main())
