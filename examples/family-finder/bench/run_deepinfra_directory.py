"""Read Internet Archive directory pages with a DeepInfra vision model; the model fetches each page by URL.

Usage:
  python bench/run_deepinfra_directory.py <ia_identifier> [--model M] [--jobs 24] [--start-frac 0.06] [--end-frac 0.95]
  python bench/run_deepinfra_directory.py --sample   # the 20 benchmark pages of the 1928 directory
Writes results/directory-deepinfra/<short model>/<identifier>/<page>.json; skips pages already done.
"""
import argparse, json, sys, time, urllib.error, urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from directory_schema import SCHEMA, INSTRUCTIONS

ROOT = Path(__file__).resolve().parent.parent
URL = "https://api.deepinfra.com/v1/openai/chat/completions"
PROMPT = (INSTRUCTIONS + " If the page has no resident listing (title page, advertisements, street guide, "
          "classified business section), return {\"entries\": []}.\n\nReturn only a JSON object {\"entries\": [...]} with exactly "
          "these keys per item: surname, given_names, spouse, colored_mark, widow_of, occupation, employer, residence_code, address. "
          "Field meanings:\n" + json.dumps(SCHEMA["properties"]["entries"]["items"]["properties"]))


def key():
    for line in (ROOT / ".env.local").read_text().splitlines():
        if line.startswith("DEEPINFRA_API_KEY="):
            return line.split("=", 1)[1].strip()


def page_count(ident):
    meta = json.load(urllib.request.urlopen(f"https://archive.org/metadata/{ident}/metadata/imagecount", timeout=60))
    return int(meta["result"])


def run_page(k, model, ident, n, out_dir):
    out = out_dir / f"n{n:04d}.json"
    if out.exists():
        return n, "cached"
    img = f"https://archive.org/download/{ident}/page/n{n}_w1800.jpg"
    body = {"model": model, "temperature": 0, "max_tokens": 16000, "reasoning_effort": "low",
            "messages": [{"role": "user", "content": [{"type": "image_url", "image_url": {"url": img}},
                                                      {"type": "text", "text": PROMPT}]}]}
    t0 = time.monotonic()
    r = {}
    for attempt in range(4):
        try:
            req = urllib.request.Request(URL, data=json.dumps(body).encode(),
                                         headers={"Authorization": f"Bearer {k}", "Content-Type": "application/json"})
            r = json.load(urllib.request.urlopen(req, timeout=600))
            break
        except urllib.error.HTTPError as e:
            r = {"error": e.code, "body": e.read()[:300].decode(errors="replace")}
            if e.code in (429, 500, 502, 503, 504) and attempt < 3:
                time.sleep(5 * (attempt + 1))
                continue
            break
        except Exception as e:  # network hiccup
            r = {"error": str(e)}
            time.sleep(5)
    text = (((r.get("choices") or [{}])[0].get("message") or {}).get("content")) if "choices" in r else None
    data = None
    if text:
        try:
            data = json.loads(text[text.find("{"): text.rfind("}") + 1])
        except json.JSONDecodeError:
            data = None
    if data is None:
        return n, f"FAILED {str(r.get('error') or 'unparsable')[:80]}"  # not saved, so a re-run retries it
    rec = {"identifier": ident, "page": n, "image_url": img, "system": model, "wall_ms": round((time.monotonic() - t0) * 1000),
           "usage": r.get("usage"), "cost_usd": (r.get("usage") or {}).get("estimated_cost"), "data": data}
    out.write_text(json.dumps(rec))
    return n, f"{rec['wall_ms']/1000:.0f}s {len(data.get('entries') or [])} entries"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("identifier", nargs="?")
    ap.add_argument("--model", default="nvidia/NVIDIA-Nemotron-3.5-Super-VL")
    ap.add_argument("--jobs", type=int, default=24)
    ap.add_argument("--start-frac", type=float, default=0.06)
    ap.add_argument("--end-frac", type=float, default=0.95)
    ap.add_argument("--sample", action="store_true")
    a = ap.parse_args()
    k = key()
    short = a.model.split("/")[-1].lower()
    if a.sample:
        ident = "hillswilmingtonn17hill"
        pages = [m["pdf_index"] for m in json.loads((ROOT / "data/directory/manifest.json").read_text())]
    else:
        ident = a.identifier
        total = page_count(ident)
        pages = list(range(int(total * a.start_frac), int(total * a.end_frac)))
    out_dir = ROOT / "results/directory-deepinfra" / short / ident
    out_dir.mkdir(parents=True, exist_ok=True)
    print(f"{ident}: {len(pages)} pages with {a.model}", flush=True)
    with ThreadPoolExecutor(a.jobs) as ex:
        for n, msg in ex.map(lambda p: run_page(k, a.model, ident, p, out_dir), pages):
            print(ident, n, msg, flush=True)


if __name__ == "__main__":
    main()
