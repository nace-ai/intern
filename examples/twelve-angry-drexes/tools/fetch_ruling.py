#!/usr/bin/env python3
"""Build the real-ruling fixture used by the wages case from the public HFforLegal/case-law dataset.

    python3 tools/fetch_ruling.py                    # fetch the row from the datasets-server API
    python3 tools/fetch_ruling.py --from-json FILE   # use a saved API response instead

Writes data/rulings/<name>.json (metadata, full text, verbatim excerpts with offsets) and
cases/files/src/<name>.html (print source for the PDF the demo uploads). Standard library only.
The text is checked against the SHA-256 the dataset publishes for it, and every excerpt is cut
from the text by position, never retyped.
"""
import argparse
import hashlib
import html
import json
import pathlib
import re
import urllib.parse
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent
DATASET, ROW, DOCKET = "HFforLegal/case-law", 158835, "S279397"
NAME = "naranjo_v_spectrum_s279397"
# Sentences to show the jury, found by their opening words and cut through the closing period.
EXCERPTS = [
    ("rule", "Under long established law, an employer cannot incur civil or criminal penalties"),
    ("holding", "We hold that an employer’s objectively reasonable, good faith belief"),
    ("disposition", "The judgment of the Court of Appeal is affirmed."),
]


def fetch_row() -> dict:
    url = "https://datasets-server.huggingface.co/rows?" + urllib.parse.urlencode(
        {"dataset": DATASET, "config": "default", "split": "us", "offset": ROW, "length": 1})
    req = urllib.request.Request(url, headers={"User-Agent": "twelve-angry-drexes/0.1"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read())["rows"][0]["row"]


def sentence(text: str, opening: str) -> tuple[int, str]:
    """Locate a sentence by its opening words (any whitespace between them) and cut it through its closing period."""
    m = re.search(r"\s+".join(re.escape(w) for w in opening.split()), text)
    if not m:
        raise SystemExit(f"excerpt not found in the ruling text: {opening!r}")
    end = text.index(".", m.end() - 1) + 1
    return m.start(), text[m.start():end]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--from-json")
    args = ap.parse_args()
    if args.from_json:
        rows = json.loads(pathlib.Path(args.from_json).read_text())["rows"]
        row = next(r["row"] for r in rows if r["row"].get("docket_number") == DOCKET)
    else:
        row = fetch_row()
    text = row["document"]
    assert row["docket_number"] == DOCKET, row["docket_number"]
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    excerpts = []
    for label, opening in EXCERPTS:
        start, quote = sentence(text, opening)
        excerpts.append({"label": label, "start": start, "text": " ".join(quote.split())})
    out = {
        "name": NAME, "title": row["title"], "court": row["issuer"], "state": row["state"], "docket_number": row["docket_number"],
        "date": row["timestamp"][:10], "short_cite": f"{row['title']} (Cal. May 6, 2024, {row['docket_number']})",
        "sha256": digest, "sha256_matches_dataset": digest == row["hash"], "chars": len(text),
        "source": {"dataset": DATASET, "url": f"https://huggingface.co/datasets/{DATASET}", "row": ROW, "license": "CC BY 4.0",
                   "credit": "Louis Brulé Naudet and Timothy Dolan, HFforLegal case-law, 2024",
                   "note": "A published opinion of the California Supreme Court. Not an official copy."},
        "excerpts": excerpts, "text": text,
    }
    (ROOT / "data" / "rulings").mkdir(parents=True, exist_ok=True)
    (ROOT / "data" / "rulings" / f"{NAME}.json").write_text(json.dumps(out, indent=1, ensure_ascii=False))
    paras = [p for p in re.split(r"\n\s*\n", text) if p.strip()]
    body = "\n".join(f"<p>{html.escape(' '.join(p.split()))}</p>" for p in paras)
    (ROOT / "cases" / "files" / "src" / f"{NAME}.html").write_text(
        '<!doctype html><html lang="en"><head><meta charset="utf-8"><title>' + html.escape(out["short_cite"]) + "</title>"
        '<link rel="stylesheet" href="doc.css"><style>p{text-align:justify}.src{font:9pt/1.35 Helvetica,Arial,sans-serif;color:#555;'
        "border:1px solid #999;padding:6pt 9pt;margin-bottom:14pt}</style></head><body>"
        f'<div class="src">{html.escape(out["short_cite"])}. Text as distributed in the {DATASET} dataset ({out["source"]["license"]}; '
        f'{html.escape(out["source"]["credit"])}). A real published opinion, printed for a software demonstration. Not an official copy.</div>'
        + body + "</body></html>")
    print(f"{NAME}: {len(text)} chars, {len(paras)} paragraphs, sha256 matches dataset: {out['sha256_matches_dataset']}")
    for e in excerpts:
        print(f"  [{e['label']}] @{e['start']}: {e['text'][:150]}")


if __name__ == "__main__":
    main()
