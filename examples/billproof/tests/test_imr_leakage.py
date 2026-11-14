"""IMR leakage guard: the reviewer `Findings` narrative names the outcome, so
every shipped item field must be free of decision language (CONTRACT §2)."""

from billproof.bench.real_imr import (
    ITEM_FIELDS,
    OUTCOME_RE,
    list_items,
    strip_decision_language,
)

LEAKY_SNIPPET = (
    "Requested coverage for Omnitrope. The physician reviewer found that the "
    "treatment is medically necessary. Final Result: The reviewer determined "
    "the Health Plan's denial should be overturned. Credentials/Qualifications: "
    "board-certified in pediatrics."
)


def test_stripping_and_no_outcome_words_in_items():
    clean = strip_decision_language(LEAKY_SNIPPET)
    assert "Omnitrope" in clean and not OUTCOME_RE.search(clean)

    items = list_items()
    assert items, "data/real/imr_items.jsonl missing — run scripts/fetch_imr.py"
    for item in items:
        assert set(item) == set(ITEM_FIELDS), item["case_id"]
        for field, value in item.items():
            if field == "case_id":
                continue
            s = str(value)
            assert len(s) <= 300, f"{item['case_id']}.{field}: narrative text leaked"
            assert not OUTCOME_RE.search(s), f"{item['case_id']}.{field}: outcome language: {s!r}"
