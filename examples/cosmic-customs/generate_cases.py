"""Generate the synthetic development and held-out customs cases."""

from __future__ import annotations

import argparse
import hashlib
import json
import random
from decimal import Decimal
from pathlib import Path
from typing import Any

from config import PROMPT_VERSION, RULEBOOK_VERSION
from prompts import assert_model_input_allowlist, prompt_identity, shared_input_for_case
from rules import decide
from schemas import Case, Facts

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"

DEV_SEED = 20261114
TEST_SEED = 88141120
ID_SEED = 424242
LAYOUT_SEED = 171717
SHOWCASE_SEED = 909090
FREEZE_TIME = "2026-11-14T20:05:00+00:00"

DEV_FAMILIES = ("ledger", "telegraph", "customs_form")
TEST_FAMILIES = ("quartermaster", "signal_log", "archive_card")
PRIMITIVES = (
    "clear_ordinary",
    "clear_exact_pressure_90",
    "clear_exact_weight_3pct",
    "clear_class4_alone",
    "clear_waiver_exception",
    "clear_expired_certificate_nonsinging",
    "clear_missing_certificate_nonsinging",
    "clear_corrected_suspicion",
    "hold_damaged_seal",
    "hold_low_pressure",
    "review_conflict",
    "review_missing_required",
    "review_weight_mismatch",
    "review_singing_without_certificate",
    "review_class4_waiver_exclusion",
    "review_unknown_certificate",
)
HELDOUT = (
    "hold_low_pressure_overrides_missing",
    "hold_damaged_seal_overrides_conflict",
    "hold_damaged_seal_overrides_waiver",
    "review_weight_overrides_waiver",
    "review_conflict_overrides_waiver",
)
INTENDED = {
    "clear_ordinary": "CLEAR",
    "clear_exact_pressure_90": "CLEAR",
    "clear_exact_weight_3pct": "CLEAR",
    "clear_class4_alone": "CLEAR",
    "clear_waiver_exception": "CLEAR",
    "clear_expired_certificate_nonsinging": "CLEAR",
    "clear_missing_certificate_nonsinging": "CLEAR",
    "clear_corrected_suspicion": "CLEAR",
    "hold_damaged_seal": "HOLD",
    "hold_low_pressure": "HOLD",
    "hold_low_pressure_overrides_missing": "HOLD",
    "hold_damaged_seal_overrides_conflict": "HOLD",
    "hold_damaged_seal_overrides_waiver": "HOLD",
    "review_conflict": "REVIEW",
    "review_missing_required": "REVIEW",
    "review_weight_mismatch": "REVIEW",
    "review_singing_without_certificate": "REVIEW",
    "review_class4_waiver_exclusion": "REVIEW",
    "review_unknown_certificate": "REVIEW",
    "review_weight_overrides_waiver": "REVIEW",
    "review_conflict_overrides_waiver": "REVIEW",
}

SPECIES = ("Lumen Moth", "Glass Newt", "Choir Eel", "Velvet Spore", "Amber Krill", "Quiet Fern", "Paper Squid", "Cinder Bee")
PORTS = ("Vesper Dock", "Ring Seven", "Kite Yard", "North Cask", "Blue Gantries", "Orchard Lock")
OFFICERS = ("I. Pell", "S. Quill", "M. Oster", "R. Voss", "A. Keene", "N. Brant")

PHRASES: dict[str, dict[str, str]] = {
    "ledger": {
        "singing_true": "Organism flag: the cargo is singing or telepathic.",
        "singing_false": "Organism flag: the cargo is not singing and is not telepathic.",
        "hazard_other": "Hazard class recorded as ordinary, not acoustic class 4.",
        "hazard_class4": "Hazard class recorded as acoustic class 4.",
        "seal_intact": "Containment seal condition: intact.",
        "seal_damaged": "Containment seal condition: damaged.",
        "pressure": "Seal pressure gauge reads {value} kPa.",
        "manifest_weight": "Manifest gross weight: {value} kg.",
        "cert_weight": "Certificate gross weight: {value} kg.",
        "cert_valid": "Containment certificate status: valid.",
        "cert_expired": "Containment certificate status: expired.",
        "cert_missing": "Containment certificate status: not on file.",
        "cert_unknown": "Containment certificate status was not read and remains unknown.",
        "waiver_valid": "Emergency waiver status: valid.",
        "waiver_expired": "Emergency waiver status: expired.",
        "waiver_missing": "Emergency waiver status: not on file.",
        "conflict_hazard_other": "A second clerk recorded this lot as acoustic class 4.",
        "conflict_hazard_class4": "A second clerk recorded this lot as ordinary, not acoustic class 4.",
        "conflict_weight": "A duplicate slip lists a gross weight that does not agree with the manifest line.",
        "corrected": "An earlier watch suspected a ruptured seal. That suspicion was corrected after the gauge was reread. The seal is intact.",
        "header_manifest": "CARGO MANIFEST",
        "header_certificate": "CONTAINMENT CERTIFICATE",
        "header_note": "INSPECTOR NOTE",
    },
    "telegraph": {
        "singing_true": "CARGO SINGS OR THINKS: yes.",
        "singing_false": "CARGO SINGS OR THINKS: no, neither singing nor telepathic.",
        "hazard_other": "HAZARD: ordinary. Not acoustic class 4.",
        "hazard_class4": "HAZARD: acoustic class 4.",
        "seal_intact": "SEAL: intact.",
        "seal_damaged": "SEAL: damaged.",
        "pressure": "PRESSURE: {value} kPa.",
        "manifest_weight": "GROSS ON MANIFEST: {value} kg.",
        "cert_weight": "GROSS ON CERTIFICATE: {value} kg.",
        "cert_valid": "CERTIFICATE: valid.",
        "cert_expired": "CERTIFICATE: expired.",
        "cert_missing": "CERTIFICATE: not on file.",
        "cert_unknown": "CERTIFICATE: unread, status unknown.",
        "waiver_valid": "WAIVER: valid.",
        "waiver_expired": "WAIVER: expired.",
        "waiver_missing": "WAIVER: not on file.",
        "conflict_hazard_other": "LATER TICK: clerk marks acoustic class 4.",
        "conflict_hazard_class4": "LATER TICK: clerk marks ordinary, not acoustic class 4.",
        "conflict_weight": "LATER TICK: the gross figures do not agree.",
        "corrected": "FIRST GUESS: ruptured seal. CORRECTION: that suspicion was withdrawn. SEAL NOW: intact.",
        "header_manifest": "MANIFEST BURST",
        "header_certificate": "CERTIFICATE BURST",
        "header_note": "WATCH BURST",
    },
    "customs_form": {
        "singing_true": "Bio-signal box: a cantor or mind-link is present.",
        "singing_false": "Bio-signal box: neither a cantor nor a mind-link is present.",
        "hazard_other": "Hazard box: ordinary freight, outside acoustic class 4.",
        "hazard_class4": "Hazard box: acoustic class 4.",
        "seal_intact": "Seal box: intact.",
        "seal_damaged": "Seal box: damaged.",
        "pressure": "Pressure box: {value} kPa.",
        "manifest_weight": "Declared gross mass: {value} kg.",
        "cert_weight": "Certified gross mass: {value} kg.",
        "cert_valid": "Certificate box: valid.",
        "cert_expired": "Certificate box: expired.",
        "cert_missing": "Certificate box: not on file.",
        "cert_unknown": "Certificate box left blank. The status is unknown.",
        "waiver_valid": "Waiver box: valid.",
        "waiver_expired": "Waiver box: expired.",
        "waiver_missing": "Waiver box: not on file.",
        "conflict_hazard_other": "A margin note calls the freight acoustic class 4.",
        "conflict_hazard_class4": "A margin note calls the freight ordinary, outside acoustic class 4.",
        "conflict_weight": "A margin note says the declared masses do not agree.",
        "corrected": "The first pencil note guessed at a ruptured seal. The note was corrected. The seal box remains intact.",
        "header_manifest": "FORM C-MANIFEST",
        "header_certificate": "FORM C-CERTIFICATE",
        "header_note": "FORM C-NOTE",
    },
    "quartermaster": {
        "singing_true": "The quartermaster heard song or a telepathic hail from this cask.",
        "singing_false": "The quartermaster heard neither song nor a telepathic hail.",
        "hazard_other": "Stowage class is general goods, outside acoustic class 4.",
        "hazard_class4": "Stowage class is acoustic class 4.",
        "seal_intact": "The cask seal is intact.",
        "seal_damaged": "The cask seal is damaged.",
        "pressure": "The gauge on the cask stands at {value} kPa.",
        "manifest_weight": "Tally weight of the cask is {value} kg.",
        "cert_weight": "Paper weight of the cask is {value} kg.",
        "cert_valid": "The containment paper is valid.",
        "cert_expired": "The containment paper is expired.",
        "cert_missing": "The containment paper is not on file.",
        "cert_unknown": "The containment paper was not in the pouch, and its status is unknown.",
        "waiver_valid": "The emergency chit is valid.",
        "waiver_expired": "The emergency chit is expired.",
        "waiver_missing": "The emergency chit is not on file.",
        "conflict_hazard_other": "A second tally calls the same cask acoustic class 4.",
        "conflict_hazard_class4": "A second tally calls the same cask general goods, outside acoustic class 4.",
        "conflict_weight": "A second tally says the cask weights do not agree.",
        "corrected": "Someone first wrote that the seal looked ruptured. The line was corrected. The cask seal is intact.",
        "header_manifest": "QUARTERMASTER TALLY",
        "header_certificate": "QUARTERMASTER PAPER",
        "header_note": "QUARTERMASTER WATCH",
    },
    "signal_log": {
        "singing_true": "Signal: life aboard is singing or telepathic.",
        "singing_false": "Signal: life aboard is not singing and is not telepathic.",
        "hazard_other": "Signal: hazard channel reads ordinary, not acoustic class 4.",
        "hazard_class4": "Signal: hazard channel reads acoustic class 4.",
        "seal_intact": "Signal: seal channel reads intact.",
        "seal_damaged": "Signal: seal channel reads damaged.",
        "pressure": "Signal: pressure channel reads {value} kPa.",
        "manifest_weight": "Signal: manifest mass channel reads {value} kg.",
        "cert_weight": "Signal: certificate mass channel reads {value} kg.",
        "cert_valid": "Signal: certificate channel reads valid.",
        "cert_expired": "Signal: certificate channel reads expired.",
        "cert_missing": "Signal: certificate channel reads not on file.",
        "cert_unknown": "Signal: certificate channel was not received. Status unknown.",
        "waiver_valid": "Signal: waiver channel reads valid.",
        "waiver_expired": "Signal: waiver channel reads expired.",
        "waiver_missing": "Signal: waiver channel reads not on file.",
        "conflict_hazard_other": "A repeated signal instead reads acoustic class 4.",
        "conflict_hazard_class4": "A repeated signal instead reads ordinary, not acoustic class 4.",
        "conflict_weight": "A repeated signal says the mass channels do not agree.",
        "corrected": "An old signal guessed the seal was ruptured. A correction voids that guess. The seal channel reads intact.",
        "header_manifest": "SIGNAL LOG / MANIFEST",
        "header_certificate": "SIGNAL LOG / CERTIFICATE",
        "header_note": "SIGNAL LOG / WATCH",
    },
    "archive_card": {
        "singing_true": "Archive card: this specimen sings or reaches by thought.",
        "singing_false": "Archive card: this specimen does not sing and does not reach by thought.",
        "hazard_other": "Archive card: hazard is ordinary, not acoustic class 4.",
        "hazard_class4": "Archive card: hazard is acoustic class 4.",
        "seal_intact": "Archive card: the seal is intact.",
        "seal_damaged": "Archive card: the seal is damaged.",
        "pressure": "Archive card: recorded pressure is {value} kPa.",
        "manifest_weight": "Archive card: manifest gross is {value} kg.",
        "cert_weight": "Archive card: certificate gross is {value} kg.",
        "cert_valid": "Archive card: the certificate is valid.",
        "cert_expired": "Archive card: the certificate is expired.",
        "cert_missing": "Archive card: the certificate is not on file.",
        "cert_unknown": "Archive card: certificate status was never entered and is unknown.",
        "waiver_valid": "Archive card: the emergency waiver is valid.",
        "waiver_expired": "Archive card: the emergency waiver is expired.",
        "waiver_missing": "Archive card: the emergency waiver is not on file.",
        "conflict_hazard_other": "A pasted duplicate calls the hazard acoustic class 4.",
        "conflict_hazard_class4": "A pasted duplicate calls the hazard ordinary, not acoustic class 4.",
        "conflict_weight": "A pasted duplicate says the gross figures do not agree.",
        "corrected": "A faded suspicion said the seal was ruptured. The archivist corrected it. The seal is intact.",
        "header_manifest": "ARCHIVE CARD A",
        "header_certificate": "ARCHIVE CARD B",
        "header_note": "ARCHIVE CARD C",
    },
}


def num(value: Decimal) -> str:
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text


def say(family: str, key: str, value: str = "") -> str:
    return PHRASES[family][key].format(value=value)


def build_facts(signature: str, rng: random.Random, boundary: bool) -> Facts:
    singing: bool | None = False
    hazard: str | None = "other"
    cert: str | None = "valid"
    waiver: str | None = None
    seal: str | None = "intact"
    pressure: Decimal | None = Decimal(rng.randrange(96, 130))
    manifest: Decimal | None = Decimal(rng.randrange(500, 1600))
    cert_weight: Decimal | None = manifest
    conflicts: list[str] = []

    if signature == "clear_exact_pressure_90":
        pressure = Decimal("90")
    elif signature == "clear_exact_weight_3pct":
        manifest = Decimal("1000") if boundary else Decimal(rng.choice((200, 250, 400, 500)))
        cert_weight = manifest * Decimal("1.03")
    elif signature == "clear_class4_alone":
        hazard = "class4_acoustic"
    elif signature == "clear_waiver_exception":
        singing = True
        cert = "missing"
        waiver = "valid"
        cert_weight = None
    elif signature == "clear_expired_certificate_nonsinging":
        cert = "expired"
    elif signature == "clear_missing_certificate_nonsinging":
        cert = "missing"
        cert_weight = None
    elif signature == "clear_corrected_suspicion":
        pressure = Decimal("101")
    elif signature == "hold_damaged_seal":
        seal = "damaged"
    elif signature == "hold_low_pressure":
        pressure = Decimal("89.999") if boundary else Decimal(rng.randrange(30, 88))
    elif signature == "review_conflict":
        conflicts = ["hazard_class"]
    elif signature == "review_missing_required":
        hazard = None
    elif signature == "review_weight_mismatch":
        manifest = Decimal("1000") if boundary else Decimal(rng.randrange(500, 1500))
        cert_weight = Decimal("1030.001") if boundary else (manifest * Decimal("1.041")).quantize(Decimal("0.001"))
    elif signature == "review_singing_without_certificate":
        singing = True
        cert = "missing"
        waiver = "missing"
        cert_weight = None
    elif signature == "review_class4_waiver_exclusion":
        singing = True
        cert = "expired"
        waiver = "valid"
        hazard = "class4_acoustic"
    elif signature == "review_unknown_certificate":
        cert = None
        cert_weight = None
    elif signature == "hold_low_pressure_overrides_missing":
        pressure = Decimal("42")
        singing = None
        hazard = None
        cert = None
        seal = None
        manifest = None
        cert_weight = None
    elif signature == "hold_damaged_seal_overrides_conflict":
        seal = "damaged"
        conflicts = ["hazard_class"]
    elif signature == "hold_damaged_seal_overrides_waiver":
        seal = "damaged"
        singing = True
        cert = "missing"
        waiver = "valid"
        cert_weight = None
    elif signature == "review_weight_overrides_waiver":
        singing = True
        cert = "expired"
        waiver = "valid"
        manifest = Decimal("800")
        cert_weight = Decimal("860")
    elif signature == "review_conflict_overrides_waiver":
        singing = True
        cert = "missing"
        waiver = "valid"
        cert_weight = None
        conflicts = ["manifest_gross_weight_kg"]
    elif signature != "clear_ordinary":
        raise KeyError(signature)

    payload = {
        "is_singing_or_telepathic": singing,
        "hazard_class": hazard,
        "cert_status": cert,
        "waiver_status": waiver,
        "seal_status": seal,
        "seal_pressure_kpa": None if pressure is None else format(pressure, "f"),
        "manifest_gross_weight_kg": None if manifest is None else format(manifest, "f"),
        "certificate_gross_weight_kg": None if cert_weight is None else format(cert_weight, "f"),
        "conflicted_fields": conflicts,
    }
    facts = Facts.model_validate(payload)
    verdict = decide(facts)
    if verdict != INTENDED[signature]:
        raise RuntimeError(f"{signature} labeled {verdict}, intended {INTENDED[signature]}")
    return facts


def _add(rows: list[tuple[str, str, str]], quotes: dict[str, list[str]], kind: str, field: str, quote: str) -> None:
    rows.append((kind, field, quote))
    if field not in ("header", "distractor", "identity"):
        quotes.setdefault(field, []).append(quote)


def render_case(
    *,
    case_id: str,
    split: str,
    signature: str,
    family: str,
    facts: Facts,
    id_rng: random.Random,
    layout_index: int,
) -> dict[str, Any]:
    token = f"K{id_rng.randrange(100000, 999999)}"
    species = id_rng.choice(SPECIES)
    port = id_rng.choice(PORTS)
    officer = id_rng.choice(OFFICERS)
    chit = id_rng.randrange(1000, 9999)
    rows: list[tuple[str, str, str]] = []
    quotes: dict[str, list[str]] = {}
    folios = [id_rng.randrange(100000, 999999) for _ in range(3)]
    _add(rows, quotes, "cargo_manifest", "identity", f"Reference {token}. Specimen common name: {species}. Folio {folios[0]}.")
    _add(rows, quotes, "containment_certificate", "identity", f"Reference {token}. Specimen common name: {species}. Folio {folios[1]}.")
    _add(rows, quotes, "inspector_note", "identity", f"Reference {token}. Specimen common name: {species}. Folio {folios[2]}.")
    _add(rows, quotes, "cargo_manifest", "distractor", f"Berth note {port}. Canteen chit {chit} has no containment meaning. Officer {officer} printed the slip.")
    if facts.is_singing_or_telepathic is True:
        _add(rows, quotes, "cargo_manifest", "is_singing_or_telepathic", say(family, "singing_true"))
    elif facts.is_singing_or_telepathic is False:
        _add(rows, quotes, "cargo_manifest", "is_singing_or_telepathic", say(family, "singing_false"))
    if facts.hazard_class == "other":
        _add(rows, quotes, "cargo_manifest", "hazard_class", say(family, "hazard_other"))
    elif facts.hazard_class == "class4_acoustic":
        _add(rows, quotes, "cargo_manifest", "hazard_class", say(family, "hazard_class4"))
    if "hazard_class" in facts.conflicted_fields:
        key = "conflict_hazard_class4" if facts.hazard_class == "class4_acoustic" else "conflict_hazard_other"
        _add(rows, quotes, "inspector_note", "hazard_class", say(family, key))
        _add(rows, quotes, "inspector_note", "hazard_class", "The two readings do not agree.")
    if facts.manifest_gross_weight_kg is not None:
        _add(rows, quotes, "cargo_manifest", "manifest_gross_weight_kg", say(family, "manifest_weight", num(facts.manifest_gross_weight_kg)))
    if "manifest_gross_weight_kg" in facts.conflicted_fields:
        _add(rows, quotes, "inspector_note", "manifest_gross_weight_kg", say(family, "conflict_weight"))
        _add(rows, quotes, "inspector_note", "manifest_gross_weight_kg", "The two readings do not agree.")
    if facts.cert_status == "valid":
        _add(rows, quotes, "containment_certificate", "cert_status", say(family, "cert_valid"))
    elif facts.cert_status == "expired":
        _add(rows, quotes, "containment_certificate", "cert_status", say(family, "cert_expired"))
    elif facts.cert_status == "missing":
        _add(rows, quotes, "containment_certificate", "cert_status", say(family, "cert_missing"))
    else:
        _add(rows, quotes, "containment_certificate", "cert_status", say(family, "cert_unknown"))
    if facts.certificate_gross_weight_kg is not None:
        _add(rows, quotes, "containment_certificate", "certificate_gross_weight_kg", say(family, "cert_weight", num(facts.certificate_gross_weight_kg)))
    if facts.waiver_status == "valid":
        _add(rows, quotes, "containment_certificate", "waiver_status", say(family, "waiver_valid"))
    elif facts.waiver_status == "expired":
        _add(rows, quotes, "containment_certificate", "waiver_status", say(family, "waiver_expired"))
    elif facts.waiver_status == "missing":
        _add(rows, quotes, "containment_certificate", "waiver_status", say(family, "waiver_missing"))
    if facts.seal_status == "intact":
        _add(rows, quotes, "inspector_note", "seal_status", say(family, "seal_intact"))
    elif facts.seal_status == "damaged":
        _add(rows, quotes, "inspector_note", "seal_status", say(family, "seal_damaged"))
    if signature == "clear_corrected_suspicion":
        _add(rows, quotes, "inspector_note", "seal_status", say(family, "corrected"))
    if facts.seal_pressure_kpa is not None:
        _add(rows, quotes, "inspector_note", "seal_pressure_kpa", say(family, "pressure", num(facts.seal_pressure_kpa)))

    documents = []
    for kind, header_key in (
        ("cargo_manifest", "header_manifest"),
        ("containment_certificate", "header_certificate"),
        ("inspector_note", "header_note"),
    ):
        body = [item for item in rows if item[0] == kind]
        if layout_index == 1:
            body = list(reversed(body))
        elif layout_index == 2:
            body = body[1:] + body[:1] if body else body
        lines = [say(family, header_key), *[quote for _kind, _field, quote in body]]
        documents.append(
            {
                "document_id": f"DOC-{id_rng.randrange(100000, 999999)}",
                "kind": kind,
                "text": "\n".join(lines),
            }
        )

    annotations: dict[str, Any] = {}
    public = facts.dump_public()
    for field, expected in public.items():
        if field == "conflicted_fields":
            continue
        field_quotes = [{"document_id": doc["document_id"], "quote": quote} for doc in documents for quote in quotes.get(field, []) if quote in doc["text"]]
        if field in facts.conflicted_fields:
            status = "conflicted"
        elif expected == "missing":
            status = "explicitly_absent"
        elif expected is None:
            status = "unknown"
        else:
            status = "stated"
        annotations[field] = {"expected": expected, "status": status, "quotes": field_quotes}
    annotations["conflicted_fields"] = {
        "expected": public["conflicted_fields"],
        "status": "conflicted" if public["conflicted_fields"] else "stated",
        "quotes": [],
    }
    case = {
        "case_id": case_id,
        "split": split,
        "template_family": family,
        "rule_signature": signature,
        "facts": public,
        "documents": documents,
        "source_annotations": {"fields": annotations},
        "expected_verdict": decide(facts),
    }
    Case.model_validate(case)
    rendered = shared_input_for_case(case)
    assert_model_input_allowlist(case, rendered)
    for field, meta in annotations.items():
        for quote in meta["quotes"]:
            owner = next(doc for doc in documents if doc["document_id"] == quote["document_id"])
            if quote["quote"] not in owner["text"]:
                raise RuntimeError(f"{case_id} quote missing from {field}")
    return case


def dev_plan() -> list[tuple[str, bool, str]]:
    queue = [
        ("clear_ordinary", False),
        ("clear_exact_pressure_90", True),
        ("clear_exact_weight_3pct", True),
        ("clear_class4_alone", False),
        ("clear_waiver_exception", False),
        ("clear_expired_certificate_nonsinging", False),
        ("clear_missing_certificate_nonsinging", False),
        ("clear_corrected_suspicion", False),
        ("hold_damaged_seal", False),
        ("hold_low_pressure", True),
        ("review_conflict", False),
        ("review_missing_required", False),
        ("review_weight_mismatch", True),
        ("review_singing_without_certificate", False),
        ("review_class4_waiver_exclusion", False),
        ("review_unknown_certificate", False),
        ("hold_damaged_seal", False),
        ("clear_ordinary", False),
        ("review_weight_mismatch", False),
        ("hold_low_pressure", False),
    ]
    return [(signature, boundary, DEV_FAMILIES[index % 3]) for index, (signature, boundary) in enumerate(queue)]


def test_plan() -> list[tuple[str, bool, str]]:
    items: list[tuple[str, bool]] = []
    items += [("hold_low_pressure_overrides_missing", False)] * 8
    items += [("hold_damaged_seal_overrides_conflict", False)] * 8
    items += [("hold_damaged_seal_overrides_waiver", False)] * 8
    items += [("hold_damaged_seal", False)] * 5
    items += [("hold_low_pressure", False)] * 4
    items += [("review_weight_overrides_waiver", True)] + [("review_weight_overrides_waiver", False)] * 7
    items += [("review_conflict_overrides_waiver", False)] * 8
    items += [("review_weight_mismatch", False)] * 6
    items += [("review_singing_without_certificate", False)] * 4
    items += [("review_class4_waiver_exclusion", False)] * 4
    items += [("review_missing_required", False)] * 4
    items += [("clear_ordinary", False)] * 6
    items += [("clear_exact_pressure_90", True)] + [("clear_exact_pressure_90", False)] * 3
    items += [("clear_exact_weight_3pct", True)] + [("clear_exact_weight_3pct", False)] * 3
    items += [("clear_class4_alone", False)] * 4
    items += [("clear_waiver_exception", False)] * 5
    items += [("clear_expired_certificate_nonsinging", False)] * 4
    items += [("clear_missing_certificate_nonsinging", False)] * 3
    items += [("clear_corrected_suspicion", False)] * 3
    if len(items) != 100:
        raise RuntimeError(f"test plan has {len(items)} items")
    rng = random.Random(TEST_SEED)
    rng.shuffle(items)
    dealt: list[list[tuple[str, bool]]] = [[] for _ in TEST_FAMILIES]
    for index, item in enumerate(items):
        dealt[index % 3].append(item)

    def verdicts(bucket: list[tuple[str, bool]]) -> set[str]:
        return {INTENDED[signature] for signature, _boundary in bucket}

    for _ in range(80):
        gap = next(
            (
                (idx, verdict)
                for idx, bucket in enumerate(dealt)
                for verdict in ("CLEAR", "HOLD", "REVIEW")
                if verdict not in verdicts(bucket)
            ),
            None,
        )
        if gap is None:
            break
        idx, verdict = gap
        donor = next(
            j
            for j, bucket in enumerate(dealt)
            if j != idx and sum(1 for signature, _b in bucket if INTENDED[signature] == verdict) > 1
        )
        take = next(i for i, (signature, _b) in enumerate(dealt[donor]) if INTENDED[signature] == verdict)
        moving = dealt[donor].pop(take)
        back_at = next(
            i
            for i, (signature, _b) in enumerate(dealt[idx])
            if sum(1 for other, _o in dealt[idx] if INTENDED[other] == INTENDED[signature]) > 1
        )
        back = dealt[idx].pop(back_at)
        dealt[idx].append(moving)
        dealt[donor].append(back)
    rows = [(signature, boundary, TEST_FAMILIES[idx]) for idx, bucket in enumerate(dealt) for signature, boundary in bucket]
    random.Random(TEST_SEED + 1).shuffle(rows)
    return rows


def build_cases(plan: list[tuple[str, bool, str]], split: str, prefix: str, fact_seed: int) -> list[dict[str, Any]]:
    fact_rng = random.Random(fact_seed)
    id_rng = random.Random(ID_SEED if split == "dev" else ID_SEED + 1)
    cases = []
    width = 2 if split == "dev" else 3
    for index, (signature, boundary, family) in enumerate(plan):
        facts = build_facts(signature, fact_rng, boundary)
        case_id = f"{prefix}-{index + 1:0{width}d}"
        cases.append(
            render_case(
                case_id=case_id,
                split=split,
                signature=signature,
                family=family,
                facts=facts,
                id_rng=id_rng,
                layout_index=(index * 2 + LAYOUT_SEED) % 3,
            )
        )
    return cases


def canonical(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest(payload: Any) -> str:
    return hashlib.sha256(canonical(payload).encode("utf-8")).hexdigest()


def showcase_for(test_cases: list[dict[str, Any]]) -> dict[str, Any]:
    rng = random.Random(SHOWCASE_SEED)
    picked: list[str] = []
    for verdict, count in (("CLEAR", 3), ("HOLD", 3), ("REVIEW", 4)):
        pool = [case["case_id"] for case in test_cases if case["expected_verdict"] == verdict]
        rng.shuffle(pool)
        picked.extend(pool[:count])
    rng.shuffle(picked)
    return {
        "selection_seed": SHOWCASE_SEED,
        "method": "Stratified by oracle verdict before any model was run. Selection does not use provider output.",
        "case_ids": picked,
    }


def build_all() -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any], dict[str, Any]]:
    dev = build_cases(dev_plan(), "dev", "DEV", DEV_SEED)
    test = build_cases(test_plan(), "test", "TST", TEST_SEED)
    manifest = {
        "seeds": {
            "dev_facts": DEV_SEED,
            "test_facts": TEST_SEED,
            "ids": ID_SEED,
            "layout": LAYOUT_SEED,
            "showcase": SHOWCASE_SEED,
        },
        "reserved_signatures": list(HELDOUT),
        "reserved_templates": list(TEST_FAMILIES),
        "development_templates": list(DEV_FAMILIES),
        "primitive_signatures": list(PRIMITIVES),
        "dataset_hashes": {"dev_20": digest(dev), "test_100": digest(test)},
        "rulebook_version": RULEBOOK_VERSION,
        "prompt_version": PROMPT_VERSION,
        "prompt": prompt_identity(),
        "freeze_time": FREEZE_TIME,
        "description": "Newly generated synthetic packets. Selected interaction signatures and complete wording families are held out of development. This split is not a claim of zero training-data contamination.",
    }
    return dev, test, manifest, showcase_for(test)


def verify(dev: list[dict[str, Any]], test: list[dict[str, Any]], manifest: dict[str, Any]) -> None:
    if len(dev) != 20 or len(test) != 100:
        raise RuntimeError("case counts are not 20 and 100")
    ids = [case["case_id"] for case in dev + test]
    if len(ids) != len(set(ids)):
        raise RuntimeError("duplicate case ids")
    texts = [doc["text"] for case in dev + test for doc in case["documents"]]
    if len(texts) != len(set(texts)):
        raise RuntimeError("duplicate document text")
    counts = {"CLEAR": 0, "HOLD": 0, "REVIEW": 0}
    for case in test:
        counts[case["expected_verdict"]] += 1
        facts = Facts.model_validate(case["facts"])
        if decide(facts) != case["expected_verdict"]:
            raise RuntimeError(f"oracle mismatch {case['case_id']}")
        assert_model_input_allowlist(case, shared_input_for_case(case))
    if counts != {"CLEAR": 33, "HOLD": 33, "REVIEW": 34}:
        raise RuntimeError(f"test balance {counts}")
    for case in dev:
        facts = Facts.model_validate(case["facts"])
        if decide(facts) != case["expected_verdict"]:
            raise RuntimeError(f"oracle mismatch {case['case_id']}")
        assert_model_input_allowlist(case, shared_input_for_case(case))
    dev_signatures = {case["rule_signature"] for case in dev}
    test_signatures = {case["rule_signature"] for case in test}
    if not set(PRIMITIVES) <= dev_signatures:
        raise RuntimeError(f"dev missing primitives {set(PRIMITIVES) - dev_signatures}")
    if set(HELDOUT) & dev_signatures:
        raise RuntimeError("held-out signatures leaked into dev")
    if not set(HELDOUT) <= test_signatures:
        raise RuntimeError("test missing held-out signatures")
    dev_families = {case["template_family"] for case in dev}
    test_families = {case["template_family"] for case in test}
    if dev_families != set(DEV_FAMILIES) or test_families != set(TEST_FAMILIES):
        raise RuntimeError("template families are not separated")
    if dev_families & test_families:
        raise RuntimeError("template families overlap")
    for family in TEST_FAMILIES:
        seen = {case["expected_verdict"] for case in test if case["template_family"] == family}
        if seen != {"CLEAR", "HOLD", "REVIEW"}:
            raise RuntimeError(f"{family} does not cross verdicts")
    pressures = {case["facts"]["seal_pressure_kpa"] for case in dev}
    if "90" not in pressures and "90.0" not in {str(item) for item in pressures}:
        exact = [case["facts"]["seal_pressure_kpa"] for case in dev if case["rule_signature"] == "clear_exact_pressure_90"]
        if not any(Decimal(item) == Decimal("90") for item in exact):
            raise RuntimeError("missing exact 90 kPa fixture")
    if not any(Decimal(case["facts"]["seal_pressure_kpa"]) == Decimal("89.999") for case in dev if case["facts"]["seal_pressure_kpa"]):
        raise RuntimeError("missing 89.999 kPa fixture")
    exact_weight = [
        case
        for case in dev
        if case["rule_signature"] == "clear_exact_weight_3pct"
        and Decimal(case["facts"]["certificate_gross_weight_kg"]) / Decimal(case["facts"]["manifest_gross_weight_kg"]) == Decimal("1.03")
    ]
    over_weight = [
        case
        for case in dev
        if case["rule_signature"] == "review_weight_mismatch"
        and abs(Decimal(case["facts"]["certificate_gross_weight_kg"]) - Decimal(case["facts"]["manifest_gross_weight_kg"]))
        / Decimal(case["facts"]["manifest_gross_weight_kg"])
        > Decimal("0.03")
    ]
    if not exact_weight or not over_weight:
        raise RuntimeError("missing weight boundary fixtures")
    if manifest["dataset_hashes"]["dev_20"] != digest(dev) or manifest["dataset_hashes"]["test_100"] != digest(test):
        raise RuntimeError("manifest hashes do not match the cases")
    hidden = ("expected_verdict", "rule_signature", "template_family", "source_annotations")
    for case in dev + test:
        rendered = shared_input_for_case(case)
        for key in hidden:
            if key in rendered:
                raise RuntimeError(f"{key} leaked")


def write_outputs() -> None:
    dev, test, manifest, showcase = build_all()
    verify(dev, test, manifest)
    DATA.mkdir(parents=True, exist_ok=True)
    (DATA / "dev_20.json").write_text(json.dumps(dev, indent=2) + "\n", encoding="utf-8")
    (DATA / "test_100.json").write_text(json.dumps(test, indent=2) + "\n", encoding="utf-8")
    (DATA / "split_manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    (DATA / "showcase.json").write_text(json.dumps(showcase, indent=2) + "\n", encoding="utf-8")


def print_sample(count: int) -> None:
    dev = json.loads((DATA / "dev_20.json").read_text(encoding="utf-8"))
    for case in dev[:count]:
        print("=" * 72)
        print(case["case_id"], case["rule_signature"], case["expected_verdict"], case["template_family"])
        print(json.dumps(case["facts"], indent=2))
        for document in case["documents"]:
            print("-", document["kind"], document["document_id"])
            print(document["text"])


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--verify", action="store_true")
    parser.add_argument("--sample", type=int, default=0)
    args = parser.parse_args()
    if args.verify:
        dev = json.loads((DATA / "dev_20.json").read_text(encoding="utf-8"))
        test = json.loads((DATA / "test_100.json").read_text(encoding="utf-8"))
        manifest = json.loads((DATA / "split_manifest.json").read_text(encoding="utf-8"))
        verify(dev, test, manifest)
        print("verified", len(dev), "dev and", len(test), "test cases")
        if args.sample:
            print_sample(args.sample)
        return
    write_outputs()
    print("wrote data/dev_20.json data/test_100.json data/split_manifest.json data/showcase.json")


if __name__ == "__main__":
    main()
