"""Write the extra synthetic documents a case workspace holds besides the packet PDFs.

    .venv/bin/python scripts/search_corpus.py

SYN-D08 (showcase SC-05, emergency CT denied CO-50) gets a 3-page plan
Evidence of Coverage excerpt and the ED record the denial notice mentions.
All synthetic: fictional payer, provider and patient.
"""
from __future__ import annotations

import sys
from pathlib import Path

from reportlab.lib.pagesizes import letter
from reportlab.lib.units import inch
from reportlab.pdfgen import canvas

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from billproof.search.corpus import CORPUS_DIR  # noqa: E402

W, H = letter

COVERAGE = [
    ("HARBOR SYNTHETIC HEALTH · EVIDENCE OF COVERAGE (EXCERPT)", [
        ("Cedar Choice PPO · coverage year 2026 · member SYN-DEMO-08", None),
        ("SECTION 1. DEFINITIONS", "h"),
        ("1.4 Emergency medical condition. A medical condition with acute symptoms of sufficient severity, "
         "including severe pain, that a prudent layperson with an average knowledge of health and medicine "
         "could reasonably expect, without immediate medical attention, to place the health of the person in "
         "serious jeopardy or cause serious impairment of bodily functions.", None),
        ("1.7 Medically necessary. Services that a treating clinician orders to diagnose or treat an illness, "
         "injury or its symptoms, consistent with generally accepted standards of medical practice, and not "
         "mainly for the convenience of the member or the clinician.", None),
        ("1.9 Emergency services. A medical screening examination in an emergency department and the further "
         "examination, imaging and treatment needed to stabilize the member.", None),
    ]),
    ("HARBOR SYNTHETIC HEALTH · EVIDENCE OF COVERAGE (EXCERPT)", [
        ("SECTION 5. COVERED SERVICES", "h"),
        ("5.1 Office visits. Covered at the copay shown in the Summary of Benefits.", None),
        ("5.2 Emergency services. The plan covers emergency services for an emergency medical condition, "
         "including diagnostic imaging ordered by the treating emergency clinician, at the in-network cost-sharing "
         "level. The plan decides whether a visit was an emergency from the symptoms present when the member "
         "sought care, not from the final diagnosis. A claim for emergency services will not be denied solely "
         "because the final diagnosis was not an emergency.", None),
        ("5.3 Advanced imaging. CT, MRI and PET scans performed outside an emergency department need prior "
         "authorization. Prior authorization is not required for imaging performed during an emergency "
         "department visit.", None),
        ("5.4 Laboratory services. Covered when ordered by a treating clinician.", None),
    ]),
    ("HARBOR SYNTHETIC HEALTH · EVIDENCE OF COVERAGE (EXCERPT)", [
        ("SECTION 8. APPEALS", "h"),
        ("8.1 Internal appeal. You may file an internal appeal within 180 days of the date of the notice of "
         "adverse benefit determination. Send it in writing to the Grievances & Appeals Department, "
         "PO Box 5550139, Fairview, CA 94102, or by fax to (800) 555-0140.", None),
        ("8.2 What the plan must send you. On request and free of charge, the plan will send the clinical "
         "criteria and the plan provision it relied on for the denial.", None),
        ("8.3 Decision time. The plan decides a post-service appeal within 30 days of receiving it.", None),
        ("8.4 External review. If the internal appeal is denied, you may request an independent external "
         "review at no cost to you.", None),
    ]),
]

ED_RECORD = [
    ("CEDAR VALE MEDICAL CENTER · EMERGENCY DEPARTMENT RECORD", [
        ("Patient PT-D08 · Account AC-DEMO-08 · Date of service 2026-10-15 · Arrival 02:40", None),
        ("Treating clinician: SYN-CLINICIAN-01 (emergency medicine)", None),
        ("CHIEF COMPLAINT", "h"),
        ("Sudden severe right lower abdominal pain for 6 hours, pain 8 of 10, with nausea and one episode of vomiting.", None),
        ("EXAMINATION", "h"),
        ("Temperature 38.6 C. Heart rate 112. Right lower quadrant tenderness with guarding and rebound.", None),
        ("LABS", "h"),
        ("White blood cell count 15.2 (high).", None),
        ("ORDERS AND REASON", "h"),
        ("CT abdomen and pelvis with contrast (74177) ordered at 03:25 to evaluate for acute appendicitis. "
         "Reason: acute abdominal pain with fever and raised white count.", None),
        ("RESULT AND DISPOSITION", "h"),
        ("CT: normal appendix; enlarged mesenteric lymph nodes. Final diagnosis: mesenteric adenitis. "
         "Discharged home at 07:10 with follow-up.", None),
    ]),
]


def _wrap(text: str, width: int = 95) -> list[str]:
    out, line = [], ""
    for word in text.split():
        if len(line) + len(word) + 1 > width:
            out.append(line)
            line = word
        else:
            line = f"{line} {word}".strip()
    return out + ([line] if line else [])


def _write(path: Path, pages: list[tuple[str, list[tuple[str, str | None]]]], footer: str) -> None:
    c = canvas.Canvas(str(path), pagesize=letter)
    for n, (title, paras) in enumerate(pages, 1):
        y = H - 0.9 * inch
        c.setFont("Helvetica-Bold", 12)
        c.drawString(0.8 * inch, y, title)
        y -= 0.4 * inch
        for text, kind in paras:
            c.setFont("Helvetica-Bold" if kind == "h" else "Helvetica", 10)
            for row in _wrap(text):
                c.drawString(0.8 * inch, y, row)
                y -= 14
            y -= 8
        c.setFont("Helvetica", 8)
        c.drawString(0.8 * inch, 0.6 * inch, f"{footer} · Page {n} of {len(pages)}")
        c.showPage()
    c.save()


def main() -> None:
    CORPUS_DIR.mkdir(parents=True, exist_ok=True)
    _write(CORPUS_DIR / "SYN-D08_coverage.pdf", COVERAGE, "Synthetic plan document · Cedar Choice PPO")
    _write(CORPUS_DIR / "SYN-D08_ed_record.pdf", ED_RECORD, "Synthetic ED record · Cedar Vale Medical Center")
    print("wrote", sorted(p.name for p in CORPUS_DIR.glob("*.pdf")))


if __name__ == "__main__":
    main()
