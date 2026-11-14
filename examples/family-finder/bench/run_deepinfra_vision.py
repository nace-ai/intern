"""Transcribe census pages with a DeepInfra vision model (OpenAI-compatible API).

Usage: python bench/run_deepinfra_vision.py --model Qwen/Qwen3-VL-235B-A22B-Instruct [--limit N] [--jobs 4] [--width 2400]
Writes results/deepinfra-<short model>/<page_id>.json in the same shape as the other readers.
"""
import argparse, base64, io, json, sys, time, urllib.error, urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from PIL import Image

sys.path.insert(0, str(Path(__file__).parent))
from schema import SCHEMA, INSTRUCTIONS

ROOT = Path(__file__).resolve().parent.parent
URL = "https://api.deepinfra.com/v1/openai/chat/completions"


def key():
    for line in (ROOT / ".env.local").read_text().splitlines():
        if line.startswith("DEEPINFRA_API_KEY="):
            return line.split("=", 1)[1].strip()


def image_b64(path, width):
    im = Image.open(path)
    if im.width > width:
        im = im.resize((width, round(im.height * width / im.width)))
    buf = io.BytesIO()
    im.convert("RGB").save(buf, "JPEG", quality=88)
    return base64.b64encode(buf.getvalue()).decode()


def run_page(k, page, model, width, out_dir):
    out = out_dir / f"{page['page_id']}.json"
    if out.exists():
        return page["page_id"], "cached"
    prompt = (INSTRUCTIONS + "\n\nReturn only a JSON object of the form {\"people\": [...]}, one item per person line, "
              "with exactly these keys per item: line, full_name, relationship, race, sex, age, marital_status, birthplace, occupation. "
              "Field meanings:\n" + json.dumps(SCHEMA["properties"]["people"]["items"]["properties"]))
    body = {"model": model, "temperature": 0, "max_tokens": 32000, "reasoning_effort": "low",
            "messages": [{"role": "user", "content": [
                {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + image_b64(ROOT / "data/pages" / f"{page['page_id']}.jpg", width)}},
                {"type": "text", "text": prompt}]}]}
    t0 = time.monotonic()
    rec = {"page_id": page["page_id"], "system": model, "width": width}
    for attempt in range(4):
        try:
            req = urllib.request.Request(URL, data=json.dumps(body).encode(),
                                         headers={"Authorization": f"Bearer {k}", "Content-Type": "application/json"})
            r = json.load(urllib.request.urlopen(req, timeout=600))
            break
        except urllib.error.HTTPError as e:
            r = {"error": e.code, "body": e.read()[:500].decode(errors="replace")}
            if e.code in (429, 500, 502, 503) and attempt < 3:
                time.sleep(5 * (attempt + 1))
                continue
            break
    rec["wall_ms"] = round((time.monotonic() - t0) * 1000)
    rec["usage"] = r.get("usage")
    rec["cost_usd"] = (r.get("usage") or {}).get("estimated_cost")
    text = ((r.get("choices") or [{}])[0].get("message") or {}).get("content") if "choices" in r else None
    try:
        rec["data"] = json.loads(text[text.find("{"): text.rfind("}") + 1]) if text else None
    except json.JSONDecodeError:
        rec["data"], rec["raw"] = None, text[-2000:]
    if "error" in r:
        rec["error"] = r
    out.write_text(json.dumps(rec, indent=1))
    n = len((rec.get("data") or {}).get("people") or [])
    return page["page_id"], f"{rec['wall_ms']/1000:.0f}s {n} people ${rec['cost_usd']}" + (f" ERR {r.get('error')}" if "error" in r else "")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True)
    ap.add_argument("--limit", type=int)
    ap.add_argument("--jobs", type=int, default=4)
    ap.add_argument("--width", type=int, default=2400)
    a = ap.parse_args()
    k = key()
    pages = json.loads((ROOT / "data/reference/manifest.json").read_text())[: a.limit]
    out_dir = ROOT / "results" / ("deepinfra-" + a.model.split("/")[-1].lower())
    out_dir.mkdir(parents=True, exist_ok=True)
    with ThreadPoolExecutor(a.jobs) as ex:
        for pid, msg in ex.map(lambda p: run_page(k, p, a.model, a.width, out_dir), pages):
            print(pid, msg, flush=True)


if __name__ == "__main__":
    main()
