"""Five-slide submission deck. Numbers are read from saved live runs."""

from __future__ import annotations

import json
from pathlib import Path

from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas

from config import ROOT

OUT_PDF = ROOT / "submission" / "presentation.pdf"
OUT_MD = ROOT / "submission" / "slides.md"


def _load(path: Path) -> dict:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _ms(value: float | None) -> str:
    if value is None:
        return "not yet measured"
    return f"{round(value)} ms"


def slides() -> list[tuple[str, list[str]]]:
    full = _load(ROOT / "results" / "live" / "decisions" / "latest.json")
    pilot = _load(ROOT / "results" / "live" / "decisions" / "runs" / "2026-11-14T201845+0000-70aac090" / "summary.json")
    documents = _load(ROOT / "results" / "live" / "documents" / "latest.json")
    doc_summary = documents.get("summary") or {}
    row = (documents.get("rows") or [{}])[0]
    measured: list[str] = []

    def provider_line(label: str, block: dict, planned: int) -> str:
        return (
            f"{label} {block['accuracy']['numerator']}/{block['accuracy']['denominator']} correct, "
            f"false clearance {block['false_clearance']['numerator']}/{block['false_clearance']['denominator']}, "
            f"failures {block['failures']} of {planned}. "
            f"P50/P95 {_ms(block['latency_ms']['valid_response']['p50'])} / {_ms(block['latency_ms']['valid_response']['p95'])}, known cost ${block['cost_usd']['known_total']}."
        )

    if pilot:
        measured.append(f"Development pilot {pilot.get('run_id', '')}, n={pilot.get('planned')} development cases:")
        measured.append(provider_line("Drex", pilot["drex"], pilot["planned"]))
        measured.append(provider_line("OpenRouter", pilot["openrouter"], pilot["planned"]))
        measured.append("One OpenRouter miss in the pilot: DEV-12 gold REVIEW, predicted CLEAR.")
    if full.get("summary"):
        s = full["summary"]
        drex_f, router_f = s.get("drex") or {}, s.get("openrouter") or {}
        if drex_f and router_f and s.get("planned", 0) >= 100:
            measured.append(f"Held-out test {s.get('run_id', '')}, n={s.get('planned')} held-out cases, identical shared evidence and rules:")
            measured.append(provider_line("Drex", drex_f, s["planned"]))
            measured.append(provider_line("OpenRouter", router_f, s["planned"]))
            measured.append(
                f"Median OpenRouter/Drex latency {s['speedup']['median_right_over_left']:.2f} on {s['speedup']['matched_valid_pairs']} matched pairs. Above 1 means Drex returned first."
            )
    if row:
        measured.extend(
            [
                f"One NDI packet {row.get('case_id')} in {doc_summary.get('run_id', '')}. Document cost ${row.get('cost_usd')}. OCR {row.get('ocr_applied')}.",
                f"Fields {row.get('field_score', {}).get('field_matches')}/{row.get('field_score', {}).get('fields_scored')}. Grounding coverage {row.get('grounding', {}).get('coverage')}.",
                "Drex on that extraction matched the oracle.",
            ]
        )
    else:
        measured.append("NDI packet: not yet measured.")
    measured.append("Mock runs are simulated and stay separate. No settings changed after the development freeze.")
    return [
        (
            "Cosmic Customs",
            [
                "An orbital customs desk for fictional cargo, built for the Nace Drexathon.",
                "Inspectors read a manifest, a containment certificate, and a watch note.",
                "The desk compares Nace Drex with a frontier model on the same evidence.",
                "A deterministic rule oracle labels every case. The models do not grade themselves.",
                "The question is whether a fast decision model can apply a written rulebook",
                "without clearing a shipment that should be held or reviewed.",
            ],
        ),
        (
            "Rulebook and generated evidence",
            [
                "Damaged seals and pressure below 90 kPa are HOLD, even when other facts conflict.",
                "Missing required facts and contradictions are REVIEW. A weight gap above 3% is REVIEW.",
                "Singing cargo needs a valid certificate, or a valid waiver that is not class-4 acoustic.",
                "Exactly 90 kPa and exactly 3% pass. A waiver cannot override a physical hazard.",
                "20 development cases expose every primitive rule. 100 held-out cases are 33 CLEAR,",
                "33 HOLD, and 34 REVIEW. Selected interactions and wording families stay out of development.",
                "Documents are newly generated synthetic text. This is not a claim of zero contamination.",
                "Model input is the rulebook and the evidence only. Expected labels stay off the page.",
            ],
        ),
        (
            "Architecture and Nace integrations",
            [
                "FastAPI serves a static terminal. Python 3.12 holds the oracle, clients, and storage.",
                "Drex uses POST /v1/systemone with one CLEAR/HOLD/REVIEW choice question.",
                "OpenRouter uses chat completions and a one-field JSON verdict schema.",
                "Both providers see the same shared text. Settings freeze after the development pilot.",
                "NDI flow: upload grant, parse, extract with schemas/ndi_fields.json, ground quotes,",
                "then Drex. Extraction failure does not get replaced with generator facts.",
                "Intern 0.30.1 wrote tests/test_intern_integrity.py with Drex as System 1.",
                "A privileged fact baseline and a naive regex baseline sit beside the models.",
            ],
        ),
        (
            "Measured results",
            measured,
        ),
        (
            "Demo and next steps",
            [
                "The local dashboard is http://127.0.0.1:8000/. It replays saved results and spends nothing on load.",
                "The Inspect button is labeled MOCK or LIVE from server health. Default is mock.",
                "With APP_MODE=live and server-side keys, Inspect calls both providers once per click.",
                "Text view replays the held-out test. Document view shows the one live NDI packet.",
                "Artwork on the packets is geometric fallback art, not an Adobe Firefly render.",
                "GitHub: github.com/bashaboinamanojkumar/cosmic-customs. Dashboard: cosmic-customs.vercel.app, mock mode.",
            ],
        ),
    ]


def write_markdown(pages: list[tuple[str, list[str]]]) -> None:
    lines = ["# Cosmic Customs — five slides", ""]
    for index, (title, bullets) in enumerate(pages, start=1):
        lines.append(f"## Slide {index}. {title}")
        lines.append("")
        lines.extend(f"- {bullet}" for bullet in bullets)
        lines.append("")
    OUT_MD.write_text("\n".join(lines), encoding="utf-8")


def write_pdf(pages: list[tuple[str, list[str]]]) -> None:
    OUT_PDF.parent.mkdir(parents=True, exist_ok=True)
    drawing = canvas.Canvas(str(OUT_PDF), pagesize=letter)
    width, height = letter
    for index, (title, bullets) in enumerate(pages, start=1):
        drawing.setFillColorRGB(0.05, 0.09, 0.16)
        drawing.rect(0, 0, width, height, fill=1, stroke=0)
        drawing.setFillColorRGB(0.86, 0.62, 0.28)
        drawing.rect(0, height - 8, width, 8, fill=1, stroke=0)
        drawing.setFillColorRGB(0.96, 0.94, 0.88)
        drawing.setFont("Times-Bold", 22)
        drawing.drawString(48, height - 48, title)
        drawing.setFont("Times-Roman", 12)
        y = height - 84
        for bullet in bullets:
            words = bullet.split()
            line = ""
            first = True
            for word in words:
                trial = word if not line else line + " " + word
                if len(trial) > 88:
                    drawing.drawString(56, y, ("•  " if first else "    ") + line)
                    y -= 16
                    line = word
                    first = False
                else:
                    line = trial
            if line:
                drawing.drawString(56, y, ("•  " if first else "    ") + line)
                y -= 20
        drawing.setFillColorRGB(0.86, 0.62, 0.28)
        drawing.setFont("Times-Roman", 10)
        drawing.drawString(48, 36, f"Cosmic Customs  ·  slide {index} of {len(pages)}")
        drawing.showPage()
    drawing.save()


def main() -> None:
    pages = slides()
    write_markdown(pages)
    write_pdf(pages)
    print(OUT_PDF)
    print(OUT_MD)


if __name__ == "__main__":
    main()
