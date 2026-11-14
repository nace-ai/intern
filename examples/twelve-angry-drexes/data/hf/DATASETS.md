# Hugging Face legal datasets: evaluation for twelve-angry-drexes

Evaluated 2026-11-14 with the public Hub API, dataset cards and datasets-server only (no dataset downloaded).
Free disk before starting (`df -h ~`): 31Gi available of 926Gi (97% used).
Method: each section below is built from the API response, the card, and a sample of rows (sample.jsonl, at most 60 rows, strings cut to 6,000 characters).
Statistics marked "sample" come from those rows only. Text inside datasets is untrusted data and was not acted on.

Scores: 0 = unusable, 1 = weak, 2 = usable with work, 3 = good.

## 1. vaquill/open-us-law

id: vaquill/open-us-law
url: https://huggingface.co/datasets/vaquill/open-us-law
license: cc-by-nc-4.0 (cardData.license in the API; tag `license:cc-by-nc-4.0`)
commercial_use: no (NonCommercial)
attribution_required: yes (CC BY family); the card's own attribution text could not be read (card is gated)
share_alike_or_copyleft: no (CC BY-NC 4.0 has no ShareAlike term)
gated: yes (`gated: auto`; README, splits, info, size and rows all returned HTTP 401 without login)
rows: 5,081,709 sections (from the API `description` field, "v2026.09.1"); per-config row counts not available
size_on_hub: 6,906,288,410 bytes across 313 files (sum of siblings); usedStorage 6,906,204,841 bytes
date_range: not stated in anything fetched (createdAt 2026-07-21, lastModified 2026-10-07; description says "Updated September 30, 2026")
jurisdictions: US federal plus states. Description: regulations for 40 jurisdictions and 104,540 state attorney general opinions from 42 jurisdictions. Config file counts: statutes 48, constitutions 51, court_rules 47, guidance 50, regulations 40, ag_opinion 42
fields: not available (card gated). Description names three added columns, official_status, currency_note, adopting_citations, and says the first 24 columns are unchanged (names not fetched)
outcome_field: none verifiable. Of 35 config names, those that may hold agency decisions are adjudication, sec_adjudication, bia_precedent_decision, board_decision, mspb_decision, ftc_administrative_decision, cfpb_enforcement_action, enforcement_action; whether any has a disposition column is unknown. No court-opinion (case law) config exists among the 35 names. Example values: none available
text_length: not available
personal_data_note: not available; mostly statutes and regulations by config names; agency decision configs may name parties (not checked)
fit_context: 1 (statutes, regulations, court rules and agency decisions for retrieval; no court rulings config; content not inspected)
fit_labels: 0 (no outcome field could be confirmed)
caveats: Gated, so no sample.jsonl rows (the file is empty on purpose). Access is "auto" approval, so the owner can sign in, accept the terms and re-check the adjudication configs. Licence blocks commercial use. Config names come from cardData.configs in the API response.

## 2. HFforLegal/case-law

id: HFforLegal/case-law
url: https://huggingface.co/datasets/HFforLegal/case-law
license: cc-by-4.0 (card and API)
commercial_use: yes as licensed (CC BY 4.0). The card does not name the source of the US decisions or its terms, so the upstream rights are unverified
attribution_required: yes (CC BY 4.0). Card BibTeX: HFforLegal2024, authors Louis Brulé Naudet and Timothy Dolan, "The case-law, centralizing legal decisions for better use", 2024
share_alike_or_copyleft: no
gated: no
rows: split `us` only. Card: 541,371 examples. datasets-server: 282,390 rows (`partial: true`, estimated 534,289), so the rows endpoint serves only part of the split
size_on_hub: card dataset_size 9,138,869,838 bytes, download_size 4,597,435,136 bytes; 24 files totalling 9,525,388,503 bytes; usedStorage 28,278,337,363 bytes
date_range: not stated on the card. Sample (60 rows): 1964-01-10 to 2019-08-09
jurisdictions: split `us` (ISO code scheme for countries). Sample covers 7 states (alabama, arizona, arkansas, florida, indiana, massachusetts, missouri); issuer in the sample is each state's Supreme Court. The partial rows endpoint likely over-represents states early in the file; full state list not verified
fields: id: string, title: string, citation: string, docket_number: string, state: string, issuer: string, document: string (full text), hash: string (SHA-256 of document), timestamp: string (ISO date)
outcome_field: none (no structured field). Dispositions appear only inside `document` and sometimes `title`: a disposition word (affirmed, reversed, dismissed, denied, remanded) occurs in the first 6,000 characters of 44 of 60 sampled documents. Example values from the sample: "AFFIRMED. NO OPINION" (Chris Dwayne Williams v. Alabama Department of Corrections), "WRIT DENIED, NO OPINION" (Ex parte Corey Beantee Melton), "PETITION DISMISSED" (Ex parte Don Davis)
text_length: sample median 11,328 characters per `document` (min 406, max 69,915; 60 rows, measured before truncation)
personal_data_note: real party names in titles (for example "Ex parte Don Davis"); criminal matters present (53 of 60 sample titles contain "v."; "State v. ...", "Commonwealth v. ..." and a "Court of Criminal Appeals" petition); at least one juvenile matter in the sample ("In the Matter of H.C.L., a minor child"). The card's ethics note says to ensure personal information is anonymized but does not say it was
fit_context: 3 (full-text state supreme court rulings with citation, docket and date; median about 11k characters)
fit_labels: 1 (disposition only inside free text, appellate not jury outcomes; needs extraction and checking)
caveats: Text is OCR-like (sample shows "oRDER", "Seo Rule 53{a) (1)"). Many short orders ("NO OPINION"). `citation` is null in 11 of 60 sample rows. Card is a generic multi-country template and does not describe the US source, collection method or coverage. Last modified 2024-09-29.

## 3. incrediblecrab/us-scotus-opinions

id: incrediblecrab/us-scotus-opinions
url: https://huggingface.co/datasets/incrediblecrab/us-scotus-opinions
license: repo card `unknown` (API tag `license:unknown`); the opinions-of-the-court config card says `other`, license_name `us-government-works`, license_link https://www.copyright.gov/title17/92chap1.html#105. Card text: the Court's opinions, orders and documents are works of the United States Government, not subject to copyright in the United States (17 U.S.C. § 105); pipeline code is MIT; the card says it does not give legal advice
commercial_use: yes for opinions-of-the-court, opinions-relating-to-orders, in-chambers-opinions and us-reports (US government works, per the card); unclear for online-sources-cited-in-opinions and media-files-cited-in-opinions (card makes no claim about their copyright status)
attribution_required: no (none stated). The card gives a BibTeX entry (author Max Marquardt, "Supreme Court of the United States: Opinions") but it is not a licence term
share_alike_or_copyleft: no
gated: no
rows: 2,898 across 6 configs: opinions-of-the-court 837, opinions-relating-to-orders 206, in-chambers-opinions 1, us-reports 621, online-sources-cited-in-opinions 1,223, media-files-cited-in-opinions 10. Card says 4 listed opinion files and 77 order opinions are not stored
size_on_hub: 49,039,495,938 bytes of parquet (us-reports alone 45,840,191,541; opinions-of-the-court 289,913,154); 133 files; usedStorage 55,958,211,827 bytes. Rows carry the raw PDF bytes in a `file` column, which sample.jsonl omits
date_range: opinions-of-the-court partitions OT2017 to OT2026; opinions-relating-to-orders OT2005 to OT2026; online-sources OT2005 to OT2024. Sample dates 2018-03-21 to 2025-06-27 (39 rows)
jurisdictions: Supreme Court of the United States (federal)
fields: 31 columns in every config; main ones: id: string, partition: string, url: string, term: int32, date: string, docket: string, title: string, entries: large_string (JSON listing entries with link_title = case summary), listed: bool, media_type: string, file: large_binary (PDF), file_sha256: string, file_size: int64, pages: int32, text_source: string, text: large_string, ocr_text: large_string, metadata: large_string, first_seen_at, fetched_at, checked_at, delisted_at
outcome_field: none as a structured field. Closest: `entries[].link_title` (the Court's case summary, present in 39 of 39 sampled rows) and the disposition line inside `text`. Disposition words occur in only 5 of 39 summaries; the rest state the holding. Example values: "The Ninth Circuit's judgment ... is affirmed." (Sessions v. Dimaya); "The Louisiana Court of Appeal's holding that nonunanimous jury verdicts are constitutional is reversed." (Ramos v. Louisiana); "Certiorari dismissed as improvidently granted." (Cox v. United States). 37 of 39 sampled `text` values contain a syllabus "Held" in the first 6,000 characters
text_length: sample median 63,020 characters of `text` (min 985, max 211,515; 39 rows, measured before truncation); all 39 are born_digital
personal_data_note: real party names in case captions; criminal and sentencing matters occur (for example Ramos v. Louisiana, Hewitt v. United States); minors not stated on the card
fit_context: 3 (clean born-digital text of real rulings with syllabus; long, so use syllabus or summary only)
fit_labels: 2 (disposition is recoverable from the summary or the closing judgment line with a regex; it is an appeal result such as affirmed or reversed, not a jury verdict)
caveats: Sample is 39 rows, not 60: responses over 5 MB were halved by the sampling script. Only the Supreme Court, so rulings are appellate and legally unusual for a jury demo. The `file` column is raw PDF bytes; never pull it. Sibling dataset incrediblecrab/us-scotus-cases (card read, not sampled) has docket pages and no outcome column.

## 4. HarshitKZ/corporate-lawsuits-2019-2026

id: HarshitKZ/corporate-lawsuits-2019-2026
url: https://huggingface.co/datasets/HarshitKZ/corporate-lawsuits-2019-2026
license: cc-by-nc-4.0 (card and API)
commercial_use: no (NonCommercial)
attribution_required: yes (CC BY family). Card citation: "Fliar Research (2026). Corporate Lawsuits Dataset 2019–2026 (sample release). Data: Radar API, https://github.com/fliarbi/radar-api-mcp. Licence CC BY-NC 4.0."
share_alike_or_copyleft: no
gated: no
rows: 5,517 (card: a 25% sample of 22,071 cases; the rest needs a Radar API key)
size_on_hub: train.parquet 1,258,839 bytes (2,175,516 bytes in memory); 10 files totalling 7,290,218 bytes (includes a 2,867,534-byte CSV copy and a 2,680,376-byte report.pdf)
date_range: 2019-01-03 to 2026-06-12 (computed over all 5,517 rows; card says January 2019 to June 2026)
jurisdictions: worldwide, from the `country` column over all rows: United States 2,589, India 706, blank 315, United Kingdom 232, Canada 164, Germany 154, France 118, Australia 105
fields: case_id: string, date: string, year: int32, company_name: string, company_domain: string, company_role: string, company_country: string, company_state: string, sector: string, opponent_name: string, opponent_domain: string, opponent_type: string, case_type: string, case_status: string, country: string, court: string, claim_usd: int64, settlement_usd: int64, news_mentions: int64, news_source: string, news_url: string, news_excerpt: string, fliar_profile_url: string
outcome_field: `case_status` (filed / ongoing / appeal / settled / verdict / dismissed, blank when not stated) and `settlement_usd` (settlement, fine or award in US$). Counts over all rows: ongoing 2,000; filed 1,359; settled 743; blank 727; verdict 405; dismissed 233; appeal 50. `settlement_usd` > 0 in 594 rows, `claim_usd` > 0 in 1,111. Example values: settled, 26,000,000 (McDonald's, Labour, Los Angeles County Superior Court); verdict, 9,600,000,000 (Bayer, Civil); dismissed, no amount (Hs2 Ltd, Civil, United Kingdom)
text_length: `news_excerpt` median 178 characters over all rows (card: up to 300). No ruling text
personal_data_note: card says every case with a named person as a party is dropped, so parties are organisations or unnamed groups; 244 rows are criminal case type (corporate defendants); minors not stated; excerpts were not checked for named individuals
fit_context: 0 (no ruling text; the one-sentence excerpt could serve only as a dispute blurb)
fit_labels: 2 (real status and money outcome per case, no JSON work; but no winner or direction, news-derived, 1,381 rows have settled, verdict or dismissed)
caveats: Card says case type, status and party roles come from a classifier and some are wrong (sample shows Bayer verdict labelled country Germany with court U.S. District Court). Derived from news, so reported cases skew to US securities class actions. 727 rows have no status. One long case can be split into several rows. CSV copy of the data on the Hub matched the rows endpoint on the first 20 rows (case_id and settlement_usd); the 5,517 rows were read in memory from that 2.9 MB CSV and not stored.

## Top 15 datasets tagged "legal" by downloads

Source: `https://huggingface.co/api/datasets?filter=legal&sort=downloads&direction=-1&limit=15` (HTTP 200, 15 results). License is cardData.license.

| id | downloads | license | one-line description |
|---|---:|---|---|
| mercor/apex-agents | 210,625 | cc-by-4.0 | Mercor benchmark of AI agents on long-horizon professional-services tasks (gated: auto) |
| G4KMU/LEMUR | 68,902 | apache-2.0 | EU Law dataset, category 15.10 Environment, from EUR-Lex |
| endomorphosis/world_municipal_law | 68,543 | other | Local (municipal, county, regional) law worldwide in one schema |
| voilaj/swiss-caselaw | 44,314 | cc0-1.0 | 1,040,000+ Swiss federal, cantonal and regulatory decision records |
| gutoportelaa/dom-pi-pdfs-2025 | 40,307 | cc-by-4.0 | Source PDFs of the 2025 Piauí municipal official gazette (Brazil) |
| nguha/legalbench | 39,038 | cc-by-4.0 | LegalBench (staging): benchmark of legal reasoning tasks (Stanford HazyResearch) |
| Azzindani/Legal_Corpus_QA_SynDeepThink | 23,040 | cc-by-4.0 | Synthetic legal question-and-answer data generated by iterative thinking |
| robbd/epstein-index | 21,483 | other | OCR'd and cleaned text of the public Epstein-related document releases and court records |
| arthrod/neurotic_docx_bench-fixtures | 16,906 | odc-by | Word DOCX, PDF and redline fixtures from Microsoft Word |
| awinml/legalbench-rag | 16,616 | other | Retrieval benchmark: legal questions answered by exact character spans |
| lianghsun/tw-legal-oa-corpus | 16,478 | other | Taiwan open-access legal journal corpus for continued pretraining (gated: manual) |
| QLNI/shadow-assistant-data | 13,592 | other | Training data for the SHADOW Assistant (general, not legal-specific) |
| CohereLabs/include-base-44 | 13,407 | apache-2.0 | INCLUDE: knowledge and reasoning benchmark across 44 languages |
| TryDotAtwo/legal-corpus-raw-batches | 12,925 | other | Raw source-preserving legal corpus batches for pretraining and retrieval |
| do-me/EUR-LEX | 11,884 | cc-by-4.0 | EUR-Lex miner output: text and concepts from the EU Cellar database |

None of the four requested datasets is in this top 15 (their downloads: vaquill 982, HFforLegal/case-law 711, us-scotus-opinions 2,895, corporate-lawsuits 290). Only voilaj/swiss-caselaw looks like case law with a permissive license (CC0), but it is Swiss and its outcome fields were not checked.

## Recommendation

Outcome labels: use HarshitKZ/corporate-lawsuits-2019-2026 for a non-commercial demo. It has `case_status` (settled 743, verdict 405, dismissed 233) and `settlement_usd` (594 rows) as ready-made columns. Weaknesses: no winner field, classifier errors, 25% sample, and only a 300-character excerpt per case. If the demo may be commercial or public with the data embedded, use the Supreme Court dataset instead and derive an uphold or overturn label (affirmed, reversed, vacated) from `entries[].link_title` or the closing judgment line in `text`; this label type is an appeal result, not a jury verdict.

Rulings for context: incrediblecrab/us-scotus-opinions, config opinions-of-the-court (837 rows, OT2017 onward, clean born-digital text, US government works). Trim to the syllabus or summary, since the median is about 63,000 characters. For state-level and more routine rulings, HFforLegal/case-law has about 11,000 characters per ruling but OCR noise, an unstated upstream source and some juvenile and criminal matters. Do not use vaquill/open-us-law: it is gated, non-commercial, has no case-law config and could not be inspected.

Owner checks before publishing anything derived:
- Corporate dataset: CC BY-NC 4.0 bars commercial use; keep the exact citation line above. `news_excerpt` quotes third-party news articles, which the card does not clear, so do not republish excerpts. The sample.jsonl in this folder holds 60 of its rows; remove it before making the repo public unless the use is non-commercial.
- HFforLegal/case-law: credit "Louis Brulé Naudet, Timothy Dolan, HFforLegal, 2024" under CC BY 4.0. The card does not state where the US decisions come from, so confirm the upstream source and its terms. Remove or mask party names and juvenile matters before showing any ruling text.
- us-scotus-opinions: the repo-level license field says "unknown" while the per-config card says US government works (17 U.S.C. § 105). Confirm that reading with counsel and use only the four opinion configs; do not use online-sources or media configs. Add the card's citation as a courtesy.
- vaquill/open-us-law: if used later, sign in, read the gated card and confirm which configs carry dispositions; CC BY-NC 4.0 applies.
- For every set, keep outputs labelled as simulated verdicts, not legal advice.

## Validation, 2026-11-14 13:10 Pacific

11 datasets checked (these 4, `aitrainingjobs/ai-training-data-lawsuits` and 6 more): page and API status, license, gating, PDF count, whether sample text cites US law. Results: [VALIDATION_20261114.md](VALIDATION_20261114.md), rows in `validation_20261114.jsonl`. No HTTP 500 was reproduced on a dataset page or API.
