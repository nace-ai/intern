"""Dataset integrity: counts, oracle, split, and model-input allowlist."""

import json
from pathlib import Path

from generate_cases import build_all, verify
from prompts import shared_input_for_case

ROOT = Path(__file__).resolve().parents[1]


def test_generated_files_match_the_builder():
    dev, test, manifest, _showcase = build_all()
    verify(dev, test, manifest)
    on_disk_dev = json.loads((ROOT / "data" / "dev_20.json").read_text(encoding="utf-8"))
    on_disk_test = json.loads((ROOT / "data" / "test_100.json").read_text(encoding="utf-8"))
    assert on_disk_dev == dev
    assert on_disk_test == test


def test_model_input_excludes_generator_fields():
    dev = json.loads((ROOT / "data" / "dev_20.json").read_text(encoding="utf-8"))
    rendered = shared_input_for_case(dev[0])
    for hidden in ("expected_verdict", "rule_signature", "template_family", "source_annotations", "is_singing_or_telepathic"):
        assert hidden not in rendered
    evidence = rendered.split("EVIDENCE", 1)[1]
    for word in ("CLEAR", "HOLD", "REVIEW"):
        assert word not in evidence.upper()


def test_ndi_schema_uses_supported_keywords_only():
    schema = json.loads((ROOT / "schemas" / "ndi_fields.json").read_text(encoding="utf-8"))
    banned = {"anyOf", "$ref", "format", "oneOf", "allOf"}

    def walk(node: object) -> None:
        if isinstance(node, dict):
            assert banned.isdisjoint(node)
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(schema)
    assert schema["type"] == "object"
    assert schema["additionalProperties"] is False
    assert "seal_pressure_kpa" in schema["properties"]
