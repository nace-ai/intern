"""The hard-case set is separate from the measured 100-case benchmark."""

import json
from decimal import Decimal
from pathlib import Path

from generate_cases import digest
from generate_hard_cases import SPEC, build_hard, verify_hard
from prompts import assert_model_input_allowlist, shared_input_for_case
from rules import decide
from schemas import Facts

ROOT = Path(__file__).resolve().parents[1]
PUBLISHED = ("dev_20.json", "test_100.json", "split_manifest.json", "showcase.json")

# Labels below are copied from the rulebook precedence, not from rules.decide.
PAIRS = {
    "P01": ("seal_pressure_kpa", ("CLEAR", "HOLD")),
    "P02": ("certificate_gross_weight_kg", ("CLEAR", "REVIEW")),
    "P03": ("seal_status", ("REVIEW", "HOLD")),
    "P04": ("cert_status", ("CLEAR", "REVIEW")),
    "P05": ("conflicted_fields", ("CLEAR", "REVIEW")),
    "P06": ("waiver_status", ("CLEAR", "REVIEW")),
    "P07": ("hazard_class", ("CLEAR", "REVIEW")),
    "P08": ("seal_status", ("CLEAR", "HOLD")),
    "P09": ("certificate_gross_weight_kg", ("CLEAR", "REVIEW")),
    "P10": ("seal_pressure_kpa", ("REVIEW", "HOLD")),
    "P11": ("conflicted_fields", ("CLEAR", "REVIEW")),
    "P12": ("hazard_class", ("CLEAR", "REVIEW")),
    "P13": ("is_singing_or_telepathic", ("CLEAR", "REVIEW")),
}
SINGLES = {
    "s_class4_alone": "CLEAR",
    "s_corrected_suspicion": "CLEAR",
    "s_singing_valid_certificate": "CLEAR",
    "s_waiver_explicitly_missing": "REVIEW",
    "s_certificate_weight_absent": "REVIEW",
    "s_pressure_unknown": "REVIEW",
    "s_seal_unknown": "REVIEW",
    "s_manifest_weight_unknown": "REVIEW",
}


def spec_missing(facts: Facts) -> bool:
    required = (
        "is_singing_or_telepathic",
        "hazard_class",
        "seal_status",
        "seal_pressure_kpa",
        "manifest_gross_weight_kg",
        "cert_status",
    )
    if any(getattr(facts, name) is None for name in required):
        return True
    if facts.is_singing_or_telepathic is True and facts.cert_status != "valid" and facts.waiver_status is None:
        return True
    if facts.cert_status in ("valid", "expired") and facts.certificate_gross_weight_kg is None:
        return True
    return False


def spec_verdict(facts: Facts) -> str:
    """Second encoding of docs/SPEC.md precedence, used only to cross-check the oracle."""
    if facts.seal_status == "damaged":
        return "HOLD"
    if facts.seal_pressure_kpa is not None and facts.seal_pressure_kpa < Decimal("90"):
        return "HOLD"
    if facts.conflicted_fields or spec_missing(facts):
        return "REVIEW"
    if facts.cert_status in ("valid", "expired") and None not in (facts.certificate_gross_weight_kg, facts.manifest_gross_weight_kg):
        ratio = abs(facts.certificate_gross_weight_kg - facts.manifest_gross_weight_kg) / facts.manifest_gross_weight_kg
        if ratio > Decimal("0.03"):
            return "REVIEW"
    if facts.is_singing_or_telepathic is True and facts.cert_status != "valid":
        waiver_clears = facts.waiver_status == "valid" and facts.hazard_class != "class4_acoustic"
        if not waiver_clears:
            return "REVIEW"
    return "CLEAR"


def _load():
    cases = json.loads((ROOT / "data" / "hard_cases.json").read_text(encoding="utf-8"))
    manifest = json.loads((ROOT / "data" / "hard_manifest.json").read_text(encoding="utf-8"))
    return cases, manifest


def test_hard_set_matches_the_builder_and_leaves_published_files_alone(tmp_path):
    before = {name: (ROOT / "data" / name).read_bytes() for name in PUBLISHED}
    cases, manifest = build_hard()
    on_disk, on_disk_manifest = _load()
    assert cases == on_disk
    assert manifest == on_disk_manifest
    verify_hard(on_disk, on_disk_manifest)
    assert len(cases) >= 30
    assert len(manifest["counterfactual_pairs"]) >= 10
    assert manifest["dataset_hash"] == digest(cases)
    assert manifest["verdict_counts"] == {"CLEAR": 14, "HOLD": 4, "REVIEW": 16}
    for name, payload in before.items():
        assert (ROOT / "data" / name).read_bytes() == payload
    prompts = (ROOT / "prompts.py").read_bytes()
    assert prompts == (ROOT / "prompts.py").read_bytes()


def test_counterfactual_pairs_and_oracle_agree_with_the_independent_spec():
    cases, manifest = _load()
    by_id = {case["case_id"]: case for case in cases}
    assert {pair["pair_id"] for pair in manifest["counterfactual_pairs"]} == set(PAIRS)
    for pair in manifest["counterfactual_pairs"]:
        field, expected = PAIRS[pair["pair_id"]]
        assert pair["changed_fact"] == field
        assert tuple(pair["expected_verdicts"]) == expected
        left = by_id[pair["case_ids"][0]]
        right = by_id[pair["case_ids"][1]]
        changed = [key for key in left["facts"] if left["facts"][key] != right["facts"][key]]
        assert changed == [field]
        for case, label in ((left, expected[0]), (right, expected[1])):
            facts = Facts.model_validate(case["facts"])
            assert case["expected_verdict"] == label
            assert decide(facts) == label
            assert spec_verdict(facts) == label
            rendered = shared_input_for_case(case)
            assert_model_input_allowlist(case, rendered)
            evidence = rendered.split("EVIDENCE", 1)[1]
            for hidden in ("expected_verdict", "rule_signature", "template_family", "source_annotations", case["rule_signature"]):
                assert hidden not in rendered
            for word in ("CLEAR", "HOLD", "REVIEW"):
                assert word not in evidence.upper()
    singles = {item["key"]: item for item in manifest["single_cases"]}
    assert set(singles) == set(SINGLES)
    for key, label in SINGLES.items():
        case = by_id[singles[key]["case_id"]]
        facts = Facts.model_validate(case["facts"])
        assert singles[key]["expected_verdict"] == label
        assert decide(facts) == label
        assert spec_verdict(facts) == label
    assert len(SPEC) == len(cases)


def test_hard_documentation_names_every_pair_and_the_hash():
    _cases, manifest = _load()
    text = (ROOT / "docs" / "HARD_CASES.md").read_text(encoding="utf-8")
    assert manifest["dataset_hash"] in text
    assert "not part of the measured 100-case benchmark" in text.lower() or "not the measured 100-case benchmark" in text.lower()
    for pair in manifest["counterfactual_pairs"]:
        assert pair["pair_id"] in text
    assert "--confirm-live" in text
    assert "--mock" in text
