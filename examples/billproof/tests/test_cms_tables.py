"""Tiny guard test: catalog loads and lookups honor CONTRACT §3 shapes."""

from billproof.rules import cms_tables as c


def test_catalog_lookups():
    codes = c.all_codes()
    assert 60 <= len(codes) <= 200

    info = c.code_info("99213")
    assert info and info["label"] and info["fee_cents"] > 0
    assert c.code_info("ZZZZZ") is None

    assert c.fee_cents("99213") == info["fee_cents"]
    assert c.fee_cents("99213", facility=True) == info["fee_facility_cents"]
    assert c.fee_cents("ZZZZZ") is None

    # real 2026-Q4 MUE: 99486 is a published MAI-1 code
    mue, mai = c.mue_limit("99486")
    assert mue == 4 and mai == 1
    assert all(c.mue_limit(code)[1] in (1, 2, 3) for code in codes if c.mue_limit(code))

    # real Medicaid PTP: lab panel -> component test is an active edit
    assert c.ptp_edit("80053", "82947") == 1
    assert c.ptp_edit("82947", "80053") is None
    assert c.ptp_edit("99213", "36415") is None
    assert any(
        p["modifier_indicator"] == 0 and c.ptp_edit(p["col1"], p["col2"]) == 0
        for p in __import__("json").load(open(c._CATALOG))["ptp"]
    )
