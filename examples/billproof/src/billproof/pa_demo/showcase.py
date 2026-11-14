"""Showcase case selection: 5 validation rows, one per demo beat.

All policies are hosted by Prime Therapeutics state Medicaid sites (public,
non-.gov — Nace browser runs block .gov). Each criteria block fits on one
PDF page; `source_pdf` lives in data/pa_demo/_src/ (fetched once, gitignored-
friendly: small, cite-able, and the dataset's provenance URL is kept).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from billproof.bench.pa.cases import PaCase, load_cases
from billproof.config import DATA_DIR

PA_DEMO_DIR = DATA_DIR / "pa_demo"
SRC_DIR = PA_DEMO_DIR / "_src"

SHOWCASE: dict[str, dict[str, Any]] = {
    "PA-D1": {
        "row_id": "pa1-mi-medicaid-pharmacy-criteria-312870052900fea6-documented",
        "title": "Radicava (edaravone) for ALS — approve",
        "drug": "RADICAVA (edaravone) oral suspension",
        "beat": "approve",
        "source_pdf": "MIRx_clinical_criteria.pdf",
        "expected_pages": [357],
        "note": "3 AND criteria, all documented -> criteria_met -> approval letter.",
    },
    "PA-D2": {
        "row_id": "pa1-mi-medicaid-pharmacy-criteria-4371b79aa0047c1e-two_unmet",
        "title": "Mytesi (crofelemer) — criteria unmet",
        "drug": "MYTESI (crofelemer)",
        "beat": "deny",
        "source_pdf": "MIRx_clinical_criteria.pdf",
        "expected_pages": [315],
        "note": "2 AND criteria both explicitly ruled out -> criteria_unmet -> appeal letter.",
    },
    "PA-D3": {
        "row_id": "pa1-ar-medicaid-pa-criteria-b1945e2bfca19b1a-reduction_overall_number_migraine_days_reduction__missing",
        "title": "Vyepti renewal — missing documentation",
        "drug": "VYEPTI (eptinezumab-jjmr) — renewal authorization",
        "beat": "incomplete",
        "source_pdf": "PAD_Criteria.pdf",
        "expected_pages": [351],
        "note": "2 AND renewal criteria, one documented, one missing -> incomplete -> records request.",
    },
    "PA-D4": {
        "row_id": "pa1-mi-medicaid-pharmacy-criteria-269b8c4ab598b72e-wrong_payer",
        "title": "Lupkynis (voclosporin) — wrong payer",
        "drug": "LUPKYNIS (voclosporin)",
        "beat": "not_applicable",
        "source_pdf": "MIRx_clinical_criteria.pdf",
        "expected_pages": [249],
        "note": "All 4 AND criteria documented, but the request names another payer -> not_applicable.",
    },
    "PA-D5": {
        "row_id": "pa1-ar-medicaid-pa-criteria-148819a05a549d16-one_met_rest_missing",
        "title": "Sildenafil (Revatio) — OR, one alternative met",
        "drug": "SILDENAFIL (Revatio)",
        "beat": "or_met",
        "source_pdf": "PACriteria.pdf",
        "expected_pages": [552],
        "note": "3 OR alternatives, one met + two undocumented -> criteria_met via OR logic.",
    },
}

_ROWS: dict[str, PaCase] | None = None


def load_row(row_id: str) -> PaCase:
    global _ROWS
    if _ROWS is None:
        _ROWS = {
            c.row_id: c
            for c in load_cases("validation", stages=None)
            if c.row_id in {v["row_id"] for v in SHOWCASE.values()}
        }
    return _ROWS[row_id]


def load_showcase() -> list[dict[str, Any]]:
    """[{slug, case, spec}] in stable order."""
    out = []
    for slug, spec in SHOWCASE.items():
        out.append({"slug": slug, "case": load_row(spec["row_id"]), "spec": spec})
    return out


def case_dir(slug: str) -> Path:
    return PA_DEMO_DIR / slug
