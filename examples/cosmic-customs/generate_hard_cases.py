"""Deterministic hard-case diagnostic set. It does not rewrite the 100-case benchmark."""

from __future__ import annotations

import argparse
import json
import random
from pathlib import Path
from typing import Any

import generate_cases
from generate_cases import digest, render_case
from prompts import assert_model_input_allowlist, prompt_identity, shared_input_for_case
from rules import decide
from schemas import Facts

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
HARD_ID_SEED = 271828
FAMILY = "dock_slip"

DOCK_PHRASES = {
    "singing_true": "Dock slip: the cargo is singing or telepathic.",
    "singing_false": "Dock slip: the cargo is not singing and is not telepathic.",
    "hazard_other": "Dock slip: hazard class is ordinary, not acoustic class 4.",
    "hazard_class4": "Dock slip: hazard class is acoustic class 4.",
    "seal_intact": "Dock slip: containment seal condition is intact.",
    "seal_damaged": "Dock slip: containment seal condition is damaged.",
    "pressure": "Dock slip: seal pressure gauge reads {value} kPa.",
    "manifest_weight": "Dock slip: manifest gross weight is {value} kg.",
    "cert_weight": "Dock slip: certificate gross weight is {value} kg.",
    "cert_valid": "Dock slip: containment certificate status is valid.",
    "cert_expired": "Dock slip: containment certificate status is expired.",
    "cert_missing": "Dock slip: containment certificate status is not on file.",
    "cert_unknown": "Dock slip: containment certificate status was not read and remains unknown.",
    "waiver_valid": "Dock slip: emergency waiver status is valid.",
    "waiver_expired": "Dock slip: emergency waiver status is expired.",
    "waiver_missing": "Dock slip: emergency waiver status is not on file.",
    "conflict_hazard_other": "A second dock clerk recorded this lot as acoustic class 4.",
    "conflict_hazard_class4": "A second dock clerk recorded this lot as ordinary, not acoustic class 4.",
    "conflict_weight": "A duplicate dock slip lists a gross weight that does not agree with the manifest line.",
    "corrected": "An earlier dock watch suspected a ruptured seal. That suspicion was corrected after the gauge was reread. The seal is intact.",
    "header_manifest": "DOCK SLIP MANIFEST",
    "header_certificate": "DOCK SLIP CERTIFICATE",
    "header_note": "DOCK SLIP NOTE",
}


def base(**overrides: Any) -> dict[str, Any]:
    facts: dict[str, Any] = {
        "is_singing_or_telepathic": False,
        "hazard_class": "other",
        "cert_status": "valid",
        "waiver_status": None,
        "seal_status": "intact",
        "seal_pressure_kpa": "101",
        "manifest_gross_weight_kg": "1000",
        "certificate_gross_weight_kg": "1000",
        "conflicted_fields": [],
    }
    facts.update(overrides)
    return facts


def _waiver_case(**overrides: Any) -> dict[str, Any]:
    facts = base(
        is_singing_or_telepathic=True,
        cert_status="missing",
        waiver_status="valid",
        certificate_gross_weight_kg=None,
        seal_pressure_kpa="110",
    )
    facts.update(overrides)
    return facts


# Expected verdicts are fixed from docs/SPEC.md. rules.decide is a cross-check, not the source.
SPEC: list[dict[str, Any]] = [
    {
        "key": "p01_exact_90",
        "pair": "P01",
        "changed_fact": "seal_pressure_kpa",
        "identity": 1,
        "facts": base(seal_pressure_kpa="90"),
        "expected": "CLEAR",
        "note": "Exactly 90 kPa is not a pressure hold.",
    },
    {
        "key": "p01_below_90",
        "pair": "P01",
        "changed_fact": "seal_pressure_kpa",
        "identity": 1,
        "facts": base(seal_pressure_kpa="89.999"),
        "expected": "HOLD",
        "note": "Exactly 90 kPa is not a pressure hold.",
    },
    {
        "key": "p02_weight_exact_3pct",
        "pair": "P02",
        "changed_fact": "certificate_gross_weight_kg",
        "identity": 2,
        "facts": base(manifest_gross_weight_kg="1000", certificate_gross_weight_kg="1030"),
        "expected": "CLEAR",
        "note": "Exactly 3 percent is not a weight review. Held-out misses sent some of these exact-boundary CLEAR cases to REVIEW.",
    },
    {
        "key": "p02_weight_over_3pct",
        "pair": "P02",
        "changed_fact": "certificate_gross_weight_kg",
        "identity": 2,
        "facts": base(manifest_gross_weight_kg="1000", certificate_gross_weight_kg="1030.001"),
        "expected": "REVIEW",
        "note": "Exactly 3 percent is not a weight review. Held-out misses sent some of these exact-boundary CLEAR cases to REVIEW.",
    },
    {
        "key": "p03_conflict_intact_seal",
        "pair": "P03",
        "changed_fact": "seal_status",
        "identity": 3,
        "facts": base(conflicted_fields=["hazard_class"]),
        "expected": "REVIEW",
        "note": "A damaged seal is HOLD even when another field conflicts.",
    },
    {
        "key": "p03_conflict_damaged_seal",
        "pair": "P03",
        "changed_fact": "seal_status",
        "identity": 3,
        "facts": base(conflicted_fields=["hazard_class"], seal_status="damaged"),
        "expected": "HOLD",
        "note": "A damaged seal is HOLD even when another field conflicts.",
    },
    {
        "key": "p04_certificate_explicitly_missing",
        "pair": "P04",
        "changed_fact": "cert_status",
        "identity": 4,
        "facts": base(cert_status="missing", certificate_gross_weight_kg=None),
        "expected": "CLEAR",
        "note": "The explicit status not-on-file is known. An unknown certificate status is a missing required fact.",
    },
    {
        "key": "p04_certificate_unknown",
        "pair": "P04",
        "changed_fact": "cert_status",
        "identity": 4,
        "facts": base(cert_status=None, certificate_gross_weight_kg=None),
        "expected": "REVIEW",
        "note": "The explicit status not-on-file is known. An unknown certificate status is a missing required fact.",
    },
    {
        "key": "p05_documents_agree",
        "pair": "P05",
        "changed_fact": "conflicted_fields",
        "identity": 5,
        "facts": base(),
        "expected": "CLEAR",
        "note": "One unresolved contradiction is REVIEW.",
    },
    {
        "key": "p05_documents_conflict",
        "pair": "P05",
        "changed_fact": "conflicted_fields",
        "identity": 5,
        "facts": base(conflicted_fields=["hazard_class"]),
        "expected": "REVIEW",
        "note": "One unresolved contradiction is REVIEW.",
    },
    {
        "key": "p06_waiver_valid",
        "pair": "P06",
        "changed_fact": "waiver_status",
        "identity": 6,
        "facts": _waiver_case(),
        "expected": "CLEAR",
        "note": "A valid emergency waiver clears singing cargo only when the certificate is absent and the hazard is not class 4.",
    },
    {
        "key": "p06_waiver_expired",
        "pair": "P06",
        "changed_fact": "waiver_status",
        "identity": 6,
        "facts": _waiver_case(waiver_status="expired"),
        "expected": "REVIEW",
        "note": "A valid emergency waiver clears singing cargo only when the certificate is absent and the hazard is not class 4.",
    },
    {
        "key": "p07_waiver_not_class4",
        "pair": "P07",
        "changed_fact": "hazard_class",
        "identity": 7,
        "facts": _waiver_case(),
        "expected": "CLEAR",
        "note": "Class 4 acoustic cargo is excluded from the waiver exception and is not an automatic hold by itself.",
    },
    {
        "key": "p07_waiver_class4",
        "pair": "P07",
        "changed_fact": "hazard_class",
        "identity": 7,
        "facts": _waiver_case(hazard_class="class4_acoustic"),
        "expected": "REVIEW",
        "note": "Class 4 acoustic cargo is excluded from the waiver exception and is not an automatic hold by itself.",
    },
    {
        "key": "p08_waiver_intact_seal",
        "pair": "P08",
        "changed_fact": "seal_status",
        "identity": 8,
        "facts": _waiver_case(),
        "expected": "CLEAR",
        "note": "A waiver does not bypass a damaged seal.",
    },
    {
        "key": "p08_waiver_damaged_seal",
        "pair": "P08",
        "changed_fact": "seal_status",
        "identity": 8,
        "facts": _waiver_case(seal_status="damaged"),
        "expected": "HOLD",
        "note": "A waiver does not bypass a damaged seal.",
    },
    {
        "key": "p09_waiver_weights_match",
        "pair": "P09",
        "changed_fact": "certificate_gross_weight_kg",
        "identity": 9,
        "facts": base(
            is_singing_or_telepathic=True,
            cert_status="expired",
            waiver_status="valid",
            manifest_gross_weight_kg="800",
            certificate_gross_weight_kg="800",
        ),
        "expected": "CLEAR",
        "note": "A waiver does not bypass a weight discrepancy above 3 percent.",
    },
    {
        "key": "p09_waiver_weights_mismatch",
        "pair": "P09",
        "changed_fact": "certificate_gross_weight_kg",
        "identity": 9,
        "facts": base(
            is_singing_or_telepathic=True,
            cert_status="expired",
            waiver_status="valid",
            manifest_gross_weight_kg="800",
            certificate_gross_weight_kg="860",
        ),
        "expected": "REVIEW",
        "note": "A waiver does not bypass a weight discrepancy above 3 percent.",
    },
    {
        "key": "p10_missing_organism_exact_90",
        "pair": "P10",
        "changed_fact": "seal_pressure_kpa",
        "identity": 10,
        "facts": base(is_singing_or_telepathic=None, seal_pressure_kpa="90"),
        "expected": "REVIEW",
        "note": "Pressure below 90 is still HOLD when another required fact is unknown. Exactly 90 leaves that missing fact as REVIEW.",
    },
    {
        "key": "p10_missing_organism_below_90",
        "pair": "P10",
        "changed_fact": "seal_pressure_kpa",
        "identity": 10,
        "facts": base(is_singing_or_telepathic=None, seal_pressure_kpa="89.99"),
        "expected": "HOLD",
        "note": "Pressure below 90 is still HOLD when another required fact is unknown. Exactly 90 leaves that missing fact as REVIEW.",
    },
    {
        "key": "p11_waiver_without_conflict",
        "pair": "P11",
        "changed_fact": "conflicted_fields",
        "identity": 11,
        "facts": _waiver_case(),
        "expected": "CLEAR",
        "note": "Observed held-out REVIEW to CLEAR misses: Drex cleared singing cargo with a valid waiver while a weight contradiction remained.",
    },
    {
        "key": "p11_waiver_with_weight_conflict",
        "pair": "P11",
        "changed_fact": "conflicted_fields",
        "identity": 11,
        "facts": _waiver_case(conflicted_fields=["manifest_gross_weight_kg"]),
        "expected": "REVIEW",
        "note": "Observed held-out REVIEW to CLEAR misses: Drex cleared singing cargo with a valid waiver while a weight contradiction remained.",
    },
    {
        "key": "p12_hazard_known",
        "pair": "P12",
        "changed_fact": "hazard_class",
        "identity": 12,
        "facts": base(),
        "expected": "CLEAR",
        "note": "Observed held-out REVIEW to CLEAR misses: OpenRouter cleared packets whose hazard class was unknown.",
    },
    {
        "key": "p12_hazard_unknown",
        "pair": "P12",
        "changed_fact": "hazard_class",
        "identity": 12,
        "facts": base(hazard_class=None),
        "expected": "REVIEW",
        "note": "Observed held-out REVIEW to CLEAR misses: OpenRouter cleared packets whose hazard class was unknown.",
    },
    {
        "key": "p13_expired_certificate_nonsinging",
        "pair": "P13",
        "changed_fact": "is_singing_or_telepathic",
        "identity": 13,
        "facts": base(cert_status="expired"),
        "expected": "CLEAR",
        "note": "Observed held-out CLEAR to REVIEW miss: an expired certificate on non-singing cargo is still CLEAR. Singing cargo with that certificate and an unknown waiver is REVIEW.",
    },
    {
        "key": "p13_expired_certificate_singing_unknown_waiver",
        "pair": "P13",
        "changed_fact": "is_singing_or_telepathic",
        "identity": 13,
        "facts": base(cert_status="expired", is_singing_or_telepathic=True),
        "expected": "REVIEW",
        "note": "Observed held-out CLEAR to REVIEW miss: an expired certificate on non-singing cargo is still CLEAR. Singing cargo with that certificate and an unknown waiver is REVIEW.",
    },
    {
        "key": "s_class4_alone",
        "pair": None,
        "changed_fact": None,
        "identity": 101,
        "facts": base(hazard_class="class4_acoustic"),
        "expected": "CLEAR",
        "note": "Class 4 acoustic cargo is not an automatic hold.",
    },
    {
        "key": "s_corrected_suspicion",
        "pair": None,
        "changed_fact": None,
        "identity": 102,
        "facts": base(seal_pressure_kpa="101"),
        "expected": "CLEAR",
        "note": "A corrected historical suspicion is not a confirmed damaged seal.",
    },
    {
        "key": "s_singing_valid_certificate",
        "pair": None,
        "changed_fact": None,
        "identity": 103,
        "facts": base(is_singing_or_telepathic=True, waiver_status=None),
        "expected": "CLEAR",
        "note": "A valid certificate means waiver status is not required.",
    },
    {
        "key": "s_waiver_explicitly_missing",
        "pair": None,
        "changed_fact": None,
        "identity": 104,
        "facts": _waiver_case(waiver_status="missing"),
        "expected": "REVIEW",
        "note": "An explicit not-on-file waiver is known and is not a valid waiver.",
    },
    {
        "key": "s_certificate_weight_absent",
        "pair": None,
        "changed_fact": None,
        "identity": 105,
        "facts": base(certificate_gross_weight_kg=None),
        "expected": "REVIEW",
        "note": "Certificate gross weight is required when a certificate exists.",
    },
    {
        "key": "s_pressure_unknown",
        "pair": None,
        "changed_fact": None,
        "identity": 106,
        "facts": base(seal_pressure_kpa=None),
        "expected": "REVIEW",
        "note": "Unknown pressure is a missing required fact, not a pressure hold.",
    },
    {
        "key": "s_seal_unknown",
        "pair": None,
        "changed_fact": None,
        "identity": 107,
        "facts": base(seal_status=None),
        "expected": "REVIEW",
        "note": "Unknown seal condition is a missing required fact.",
    },
    {
        "key": "s_manifest_weight_unknown",
        "pair": None,
        "changed_fact": None,
        "identity": 108,
        "facts": base(manifest_gross_weight_kg=None),
        "expected": "REVIEW",
        "note": "Unknown manifest weight is a missing required fact.",
    },
]


def _install_family() -> None:
    generate_cases.PHRASES[FAMILY] = DOCK_PHRASES


def build_hard() -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if len(SPEC) < 30:
        raise RuntimeError("hard spec has fewer than 30 cases")
    _install_family()
    cases: list[dict[str, Any]] = []
    for index, spec in enumerate(SPEC):
        facts = Facts.model_validate(spec["facts"])
        oracle = decide(facts)
        if oracle != spec["expected"]:
            raise RuntimeError(f"{spec['key']} oracle {oracle} does not match spec {spec['expected']}")
        signature = "clear_corrected_suspicion" if spec["key"] == "s_corrected_suspicion" else spec["key"]
        case = render_case(
            case_id=f"HRD-{index + 1:03d}",
            split="hard",
            signature=signature,
            family=FAMILY,
            facts=facts,
            id_rng=random.Random(HARD_ID_SEED + spec["identity"]),
            layout_index=0,
        )
        if case["expected_verdict"] != spec["expected"]:
            raise RuntimeError(f"{spec['key']} rendered {case['expected_verdict']}")
        cases.append(case)
    manifest = _manifest(cases)
    verify_hard(cases, manifest)
    return cases, manifest


def _manifest(cases: list[dict[str, Any]]) -> dict[str, Any]:
    groups: dict[str, list[tuple[dict[str, Any], dict[str, Any]]]] = {}
    order: list[str] = []
    for case, spec in zip(cases, SPEC):
        pair = spec["pair"]
        if not pair:
            continue
        if pair not in groups:
            order.append(pair)
            groups[pair] = []
        groups[pair].append((case, spec))
    pairs = []
    for pair_id in order:
        members = groups[pair_id]
        if len(members) != 2:
            raise RuntimeError(f"{pair_id} does not have two cases")
        if members[0][1]["changed_fact"] != members[1][1]["changed_fact"]:
            raise RuntimeError(f"{pair_id} changed facts disagree")
        pairs.append(
            {
                "pair_id": pair_id,
                "changed_fact": members[0][1]["changed_fact"],
                "case_ids": [members[0][0]["case_id"], members[1][0]["case_id"]],
                "expected_verdicts": [members[0][1]["expected"], members[1][1]["expected"]],
                "note": members[0][1]["note"],
            }
        )
    counts = {"CLEAR": 0, "HOLD": 0, "REVIEW": 0}
    for case in cases:
        counts[case["expected_verdict"]] += 1
    identity = prompt_identity()
    return {
        "dataset_id": "hard-cases-1",
        "description": (
            "Separate deterministic diagnostic set. It is not part of the measured 100-case benchmark. "
            "Labels come from the published rulebook. A mock run is simulated and is not model performance."
        ),
        "seeds": {"ids": HARD_ID_SEED, "layout": 0, "phrase_family": FAMILY},
        "fact_policy": "Fact values are fixed in the spec table. Only identity lines use the id seed.",
        "case_count": len(cases),
        "id_prefix": "HRD",
        "verdict_counts": counts,
        "counterfactual_pairs": pairs,
        "single_cases": [
            {"case_id": case["case_id"], "key": spec["key"], "expected_verdict": spec["expected"], "note": spec["note"]}
            for case, spec in zip(cases, SPEC)
            if spec["pair"] is None
        ],
        "dataset_hash": digest(cases),
        "rulebook_version": identity["rulebook_version"],
        "prompt_version": identity["prompt_version"],
        "rulebook_sha256": identity["rulebook_sha256"],
        "live_policy": (
            "Mock is the offline default. A live run requires benchmark_decisions.py --hard --confirm-live "
            "and stops when the configured Drex or OpenRouter budget would be exceeded."
        ),
    }


def verify_hard(cases: list[dict[str, Any]], manifest: dict[str, Any]) -> None:
    if len(cases) < 30:
        raise RuntimeError("hard set has fewer than 30 cases")
    pairs = manifest["counterfactual_pairs"]
    if len(pairs) < 10:
        raise RuntimeError("hard set has fewer than 10 counterfactual pairs")
    if manifest["case_count"] != len(cases) or manifest["dataset_hash"] != digest(cases):
        raise RuntimeError("hard manifest hash does not match the cases")
    if [case["case_id"] for case in cases] != [f"HRD-{index + 1:03d}" for index in range(len(cases))]:
        raise RuntimeError("hard case ids are not sequential")
    by_id = {case["case_id"]: case for case in cases}
    if len(by_id) != len(cases):
        raise RuntimeError("duplicate hard case ids")
    for pair in pairs:
        left = by_id[pair["case_ids"][0]]
        right = by_id[pair["case_ids"][1]]
        changed = [key for key in left["facts"] if left["facts"][key] != right["facts"][key]]
        if changed != [pair["changed_fact"]]:
            raise RuntimeError(f"{pair['pair_id']} changed {changed}, expected {pair['changed_fact']}")
        if [left["expected_verdict"], right["expected_verdict"]] != pair["expected_verdicts"]:
            raise RuntimeError(f"{pair['pair_id']} verdicts drifted")
        if left["expected_verdict"] == right["expected_verdict"]:
            raise RuntimeError(f"{pair['pair_id']} does not change the verdict")
        if [doc["document_id"] for doc in left["documents"]] != [doc["document_id"] for doc in right["documents"]]:
            raise RuntimeError(f"{pair['pair_id']} does not share document identity")
        if left["documents"] == right["documents"]:
            raise RuntimeError(f"{pair['pair_id']} documents are identical")
    identity_of: dict[str, int] = {}
    next_identity = 0
    for pair in pairs:
        next_identity += 1
        for case_id in pair["case_ids"]:
            identity_of[case_id] = next_identity
    for case in cases:
        if case["case_id"] not in identity_of:
            next_identity += 1
            identity_of[case["case_id"]] = next_identity
    texts: dict[str, int] = {}
    ids: dict[str, int] = {}
    for case in cases:
        facts = Facts.model_validate(case["facts"])
        if decide(facts) != case["expected_verdict"]:
            raise RuntimeError(f"oracle mismatch {case['case_id']}")
        rendered = shared_input_for_case(case)
        assert_model_input_allowlist(case, rendered)
        for hidden in ("expected_verdict", "rule_signature", "template_family", "source_annotations", "generator_facts"):
            if hidden in rendered:
                raise RuntimeError(f"{hidden} leaked into {case['case_id']}")
        if case["rule_signature"] in rendered:
            raise RuntimeError(f"signature leaked into {case['case_id']}")
        identity = identity_of[case["case_id"]]
        for document in case["documents"]:
            prior_text = texts.get(document["text"])
            if prior_text is None:
                texts[document["text"]] = identity
            elif prior_text != identity:
                raise RuntimeError("document text reused across counterfactual identities")
            prior_id = ids.get(document["document_id"])
            if prior_id is None:
                ids[document["document_id"]] = identity
            elif prior_id != identity:
                raise RuntimeError("document id reused across counterfactual identities")
    published_text = set()
    for name in ("dev_20.json", "test_100.json"):
        for case in json.loads((DATA / name).read_text(encoding="utf-8")):
            if case["case_id"] in by_id:
                raise RuntimeError("hard id collides with a published case")
            for document in case["documents"]:
                published_text.add(document["text"])
    if published_text & set(texts):
        raise RuntimeError("hard document text collides with the published sets")
    identity = prompt_identity()
    if manifest["rulebook_sha256"] != identity["rulebook_sha256"]:
        raise RuntimeError("hard manifest does not match the current rulebook")


def write_hard(directory: Path | None = None) -> dict[str, Any]:
    cases, manifest = build_hard()
    target = directory or DATA
    target.mkdir(parents=True, exist_ok=True)
    (target / "hard_cases.json").write_text(json.dumps(cases, indent=2) + "\n", encoding="utf-8")
    (target / "hard_manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description="Build or verify the hard-case diagnostic set.")
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args()
    if args.verify:
        cases = json.loads((DATA / "hard_cases.json").read_text(encoding="utf-8"))
        manifest = json.loads((DATA / "hard_manifest.json").read_text(encoding="utf-8"))
        verify_hard(cases, manifest)
        print(
            "verified",
            len(cases),
            "hard cases and",
            len(manifest["counterfactual_pairs"]),
            "counterfactual pairs",
            manifest["dataset_hash"],
        )
        return
    manifest = write_hard()
    print(
        "wrote data/hard_cases.json data/hard_manifest.json",
        manifest["case_count"],
        "cases",
        len(manifest["counterfactual_pairs"]),
        "pairs",
        manifest["dataset_hash"],
    )


if __name__ == "__main__":
    main()
