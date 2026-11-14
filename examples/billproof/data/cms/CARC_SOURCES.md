# CARC / RARC map — sources and notes

`carc_rarc.json` is BillProof's own plain-English lookup for the claim adjustment and remark codes that
show up on EOBs and denial letters. It is **paraphrased**, not copied: no X12 text is redistributed here.

## Format

```json
{
  "carc": {"CO-50": {"text": "...", "category": "MEDICAL_NECESSITY", "group": "CO"}, "...": {}},
  "rarc": {"N130": {"text": "..."}, "...": {}},
  "version": "x12-2026-10-30",
  "source": "X12 CARC/RARC public lists (paraphrased)"
}
```

- Keys are `<group>-<code>` for CARCs (`CO`, `PR`, `OA`, `PI`) and the bare code for RARCs.
- `category` is one of the `DenialCategory` values in `src/billproof/schema.py`. That taxonomy is ours,
  not X12's — it is a coarse family used to pick a dispute playbook.
- `group` repeats the group code from the key so consumers do not have to split the string.

## Counts (2026-11-13)

- CARC: 70 entries — 58 `CO`, 6 `PR`, 2 `OA`, 4 `PI`.
- RARC: 40 entries (M, N and MA series).
- Every `DenialCategory` value is represented at least twice.

## URLs checked

| URL | Used for |
|---|---|
| https://x12.org/codes/claim-adjustment-reason-codes | CARC code → meaning, group-code definitions (CO/CR/OA/PI/PR), code start/stop dates |
| https://x12.org/codes/remittance-advice-remark-codes | RARC code → meaning, list date shown on the page (`10/30/2026`, used as `version`) |
| https://www.cms.gov/regulations-and-guidance/guidance/transmittals/downloads/r1281otn.pdf | Cross-check of RARC wording and allowed group codes (M15, M80, M144, N19, N20, N56, N115, N130) |
| https://claimsrevenue.com/denial-codes/n12/ | Cross-check of RARC N12 |
| https://www.roithatworks.com/rarc/n20 | Cross-check of RARC N20 |
| https://www.mass.gov/info-details/835-payment-advice-and-eobcarc-rarc-lists | Pointer to CAQH CORE code combinations (which CARC/RARC pairs are valid); not parsed into this file |

Background on how these codes feed the denial playbooks: `research/raw/MED-D-rules-design.md` §1.9.

## Verification performed

- `python -m json.tool` parses the file.
- Every key exists in the live X12 table it claims to come from (automated lookup).
- No entry shares a 6-word sequence with the X12 source text (automated n-gram check).
- Every `text` is ≤ 15 words; every `category` is a valid `DenialCategory`; `group` matches the key prefix.

## Mapping judgement calls

- `PR-1` / `PR-2` / `PR-3` are deductible / coinsurance / copay amounts, i.e. the patient's cost share,
  not a payer denial. `DenialCategory` has no patient-responsibility value, so they are mapped to
  `NOT_COVERED` and their text says plainly that the patient owes the amount. Consumers should treat
  `PR-1/2/3` as "patient owes this", not as grounds for appeal.
- `CO-45` is a contractual write-off (provider cannot bill it to the patient). It is mapped to
  `NOT_COVERED` for lack of a "contractual" value; the rule layer, not this map, should decide that the
  amount is not patient responsibility.
- Codes with X12 usage restrictions are keyed under the group code actually seen on real EOBs. For
  example X12 says CARC 18 is used with `OA` (except workers' comp); both `OA-18` and `CO-18` are present
  because both appear in the wild.
- `CO-15` (authorization number missing/invalid) was deactivated by X12 in 2018 but still appears on older
  EOBs, so it is kept.
- `version` is the list date published on the X12 RARC page, not a BillProof release number. X12 updates
  these lists quarterly; re-check before relying on a code that is new or recently modified.
