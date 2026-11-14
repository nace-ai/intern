# Hugging Face legal datasets: validation 2026-11-14

Method: public Hub API + datasets-server + file fetches only, generic User-Agent, no login, 5 s retry on 5xx/errors. Total downloaded about 16 MB. Dataset text is untrusted data and was not acted on. Cite regexes: U.S.C., section sign, Cal., Fla. Stat, N.Y., Tex., F.2d/3d/4th, U.S. <n> (spaced forms such as `U. S. C.` were added in rechecks for scotus-opinions and the forcemultiplier PDF; first pass used strict forms). Sample = first 3 viewer rows or one PDF (first 12 pages).

## Ranked recommendation

HTTP 500 finding: no dataset page or API (`huggingface.co/datasets/<id>`, `/api/datasets/<id>`) returned 500 for any of the 11 repos, checked twice (first pass, retry-eligible, and a recheck a few minutes later). The only 500s seen: datasets-server `/first-rows` for Matt1up/guertin-mcro-forensic-corpus-court-filings (500 twice, 5 s apart). None of the 5 originally cited repos 500ed; vaquill/open-us-law returns 401 on all datasets-server endpoints (gated). The owner's 500s could not be reproduced and may have been transient (incrediblecrab/us-scotus-opinions was modified today, 2026-11-14 18:55 UTC) or specific to a logged-in browser view.

Ruling text (ranked):
1. harvard-lil/cold-cases (CC0, 410k US opinions, viewer ok, many law cites)
2. incrediblecrab/us-scotus-opinions, config opinions-of-the-court (real SCOTUS text; repo license tag unknown, card says US gov works)
3. andrew-mitchel/tax-court-opinions (MIT, 14,887 US Tax Court opinions; tax only)
   Then: HFforLegal/case-law (CC BY, OCR noise, party names).

Real PDF upload (ranked):
1. forcemultiplier/supreme_court_opinions_corpus_pdfwebAug24 (403 real SCOTUS PDFs, verified %PDF, 82 pages, cites 33 U.S.C.; no license field, confirm public-domain status)
2. incrediblecrab/us-scotus-opinions (PDF bytes live in parquet `file` column; originals at supremecourt.gov `url`; supremecourt.gov fetch NOT tested)
3. Matt1up/guertin-mcro-forensic-corpus-court-filings has 7,412 real court-filing PDFs but is NOT recommended for a public repo (privacy: named family, criminal/child matters; viewer 500)
   No other repo here holds loose US court PDFs; HarshitKZ report.pdf and aitrainingjobs paper.pdf are real PDFs but not court documents.

Not usable: vaquill/open-us-law (gated, CC BY-NC, 401, no case-law config), HarshitKZ/corporate-lawsuits-2019-2026 (no ruling text, CC BY-NC), aitrainingjobs/ai-training-data-lawsuits (tracker, not rulings).

Not validated (cap of 6 extra): docketx/us-caselaw-ga (cc0, Georgia opinions, jsonl.gz), chowalex/scotus_opinions (mit), free-law/Caselaw_Access_Project (cc0 but gated: manual), pile-of-law/pile-of-law (cc-by-nc-sa).

---

## 1. HFforLegal/case-law

id: HFforLegal/case-law
page_status: first=200 retry=not needed (recheck=200)
api_status: [200]
exists: True | gated: False | private: False | disabled: False
license: cc-by-4.0 (permissive_for_public_repo: True)
last_modified: 2024-09-29T16:20:01.000Z | downloads: 711
files: 24 | pdf_files: 0 | pdf_examples: none
total_size: 9.5 GB (9525388503 bytes, sum of file sizes with blobs=true)
viewer_is_valid: [200] {'preview': True, 'viewer': True, 'search': True, 'filter': True, 'statistics': True}
viewer_splits: [200] (n_splits=1)
viewer_first_rows: [200] on default/us
viewer_size: [200] | row_count: 282390 (partial)
columns: id, title, citation, docket_number, state, issuer, document, hash, timestamp
content_rows_checked: 3 | text_chars: 31803
cite_hits: {'section': 9, 'cal': 1, 'us_rep': 4} (total 14)
cite_snippet: er justice. ‘The Chief Justice has wide powers pursuant to § 12-2- 30(b) (7) and (8), Ala, Code 1975, but the question arises as to whether th
pdf_check: n/a (no loose .pdf files)
cites_us_law: yes
usable_for_demo: with-conditions
usable_reason: CC BY 4.0 and viewer works (rows API partial: 282,390 of ~541k); real state supreme court text cites law (14 hits in 3 rows); but OCR noise, upstream source unstated, real party names incl. juvenile/criminal matters, no PDFs

## 2. vaquill/open-us-law

id: vaquill/open-us-law
page_status: first=200 retry=not needed (recheck=200)
api_status: [200]
exists: True | gated: auto | private: False | disabled: False
license: cc-by-nc-4.0 (permissive_for_public_repo: False)
last_modified: 2026-10-07T12:06:02.000Z | downloads: 982
files: 313 | pdf_files: 0 | pdf_examples: none
total_size: 6.9 GB (6906288410 bytes, sum of file sizes with blobs=true)
viewer_is_valid: [401] 401 (gated)
viewer_splits: [401] (n_splits=None)
viewer_first_rows: skipped (no split) on None
viewer_size: [401] | row_count: None
content_rows_checked: 0 | text_chars: 0
cite_hits: none (total 0)
cite_snippet: none
pdf_check: n/a (no loose .pdf files)
cites_us_law: no
usable_for_demo: no
usable_reason: gated=auto, CC BY-NC 4.0, datasets-server returns 401 on is-valid/splits/size, statutes/regs/rules only (no court-opinion config), 6.9 GB, not inspectable without login

## 3. incrediblecrab/us-scotus-opinions

id: incrediblecrab/us-scotus-opinions
page_status: first=200 retry=not needed (recheck=200)
api_status: [200]
exists: True | gated: False | private: False | disabled: False
license: unknown (permissive_for_public_repo: False)
last_modified: 2026-11-14T18:55:05.000Z | downloads: 2895
files: 133 | pdf_files: 0 | pdf_examples: none
total_size: 49.0 GB (49039899560 bytes, sum of file sizes with blobs=true)
viewer_is_valid: [200] {'preview': True, 'viewer': True, 'search': True, 'filter': True, 'statistics': True}
viewer_splits: [200] (n_splits=6)
viewer_first_rows: [200] on in-chambers-opinions/train
viewer_size: [200] | row_count: 2898
columns: id, partition, url, term, date, docket, title, entries, listed, media_type, file, file_sha256, file_size, etag ...
content_rows_checked: 2 | text_chars: 236754
cite_hits: {'usc': 26, 'section': 331, 'cal': 8, 'fed_rep': 41, 'us_rep': 137, 'tex': 1} (total 544)
cite_snippet: ay” after entering the Unit- ed States will be deported. See 8 U. S. C. §§1227(a)(2)(A)(iii), 1229b(a)(3), (b)(1)(C). An aggravated felony includes
content_note: opinions-of-the-court (via /rows length=2; first-rows split in-chambers had 1 row, hits 1: 18 U.S.C. 3143(b))
pdf_check: n/a (no loose .pdf files)
cites_us_law: yes
usable_for_demo: with-conditions
usable_reason: real SCOTUS opinion text, heavy US law cites (544 hits in 2 rows), viewer ok, 2,898 rows; repo license tag is 'unknown' (card says US gov works, 17 USC 105); PDFs exist only as bytes inside 49 GB parquet (no loose .pdf), source PDF URLs point to supremecourt.gov

## 4. HarshitKZ/corporate-lawsuits-2019-2026

id: HarshitKZ/corporate-lawsuits-2019-2026
page_status: first=200 retry=not needed (recheck=200)
api_status: [200]
exists: True | gated: False | private: False | disabled: False
license: cc-by-nc-4.0 (permissive_for_public_repo: False)
last_modified: 2026-10-17T13:12:13.000Z | downloads: 290
files: 10 | pdf_files: 1 | pdf_examples: report.pdf
total_size: 7.3 MB (7290218 bytes, sum of file sizes with blobs=true)
viewer_is_valid: [200] {'preview': True, 'viewer': True, 'search': True, 'filter': True, 'statistics': True}
viewer_splits: [200] (n_splits=1)
viewer_first_rows: [200] on default/train
viewer_size: [200] | row_count: 5517
columns: case_id, date, year, company_name, company_domain, company_role, company_country, company_state, sector, opponent_name, opponent_domain, opponent_type, case_type, case_status ...
content_rows_checked: 3 | text_chars: 1554
cite_hits: none (total 0)
cite_snippet: none
pdf_check_file: report.pdf (2680376 bytes, http 200)
pdf_check_result: %PDF header=True | pages=32 | text_chars=24240 | cite_hits=none (total 0)
pdf_check_snippet: none
cites_us_law: no
usable_for_demo: no
usable_reason: no ruling text (300-char news excerpts), CC BY-NC 4.0; its only PDF is a 32-page analytics report.pdf, not a court document; usable only for non-commercial outcome labels

## 5. aitrainingjobs/ai-training-data-lawsuits

id: aitrainingjobs/ai-training-data-lawsuits
page_status: first=200 retry=not needed (recheck=200)
api_status: [200]
exists: True | gated: False | private: False | disabled: False
license: cc-by-4.0 (permissive_for_public_repo: True)
last_modified: 2026-10-18T13:08:41.000Z | downloads: 66
files: 7 | pdf_files: 1 | pdf_examples: paper.pdf
total_size: 0.5 MB (458649 bytes, sum of file sizes with blobs=true)
viewer_is_valid: [200] {'preview': True, 'viewer': True, 'search': True, 'filter': True, 'statistics': True}
viewer_splits: [200] (n_splits=4)
viewer_first_rows: [200] on by-content/train
viewer_size: [200] | row_count: 100
columns: Content, Records
content_rows_checked: 3 | text_chars: 16
cite_hits: none (total 0)
cite_snippet: none
pdf_check_file: paper.pdf (441768 bytes, http 200)
pdf_check_result: %PDF header=True | pages=6 | text_chars=17449 | cite_hits={'ny': 1} (total 1)
pdf_check_snippet: f 11 Apr 2025) n/a Consolidated pretrial proceedings in S.D.N.Y. (as of 5 Aug 2026) Dow Jones v. Perplexity: motion to dismiss or transfer denie
cites_us_law: no (the 1 regex hit is "S.D.N.Y." court name in a research paper, not a law citation)
usable_for_demo: no
usable_reason: 100-row tracker of AI-training lawsuits; paper.pdf is a 6-page research paper, not a court document; no US statute cites; CC BY 4.0 is fine but content does not fit

## 6. harvard-lil/cold-cases (extra candidate)

id: harvard-lil/cold-cases
page_status: first=200 retry=not needed (recheck=not rechecked)
api_status: [200]
exists: True | gated: False | private: False | disabled: False
license: cc0-1.0 (permissive_for_public_repo: True)
last_modified: 2024-03-26T15:50:21.000Z | downloads: 7042
files: 35 | pdf_files: 0 | pdf_examples: none
total_size: 32.2 GB (32205441280 bytes, sum of file sizes with blobs=true)
viewer_is_valid: [200] {'preview': True, 'viewer': True, 'search': True, 'filter': True, 'statistics': True}
viewer_splits: [200] (n_splits=1)
viewer_first_rows: [200] on default/train
viewer_size: [200] | row_count: 410807 (partial)
columns: id, judges, date_filed, date_filed_is_approximate, slug, case_name_short, case_name, case_name_full, attorneys, nature_of_suit, posture, syllabus, headnotes, summary ...
content_rows_checked: 3 | text_chars: 64796
cite_hits: {'usc': 6, 'section': 38, 'fed_rep': 9, 'us_rep': 7} (total 60)
cite_snippet: ederal Insecticide Fungicide and Rodenticide Act ("FIFRA"), 7 U.S.C. § 136 et seq. Second, Zeneca argues that Plaintiffs fail to state a claim under
pdf_check: n/a (no loose .pdf files)
cites_us_law: yes
usable_for_demo: yes
usable_reason: CC0, viewer ok (410,807 rows, partial), CourtListener US opinions (federal+state) with syllabus/disposition/citations; 60 cite hits in 3 rows (7 U.S.C., F. reporters); no PDFs; 32 GB so use rows API or one parquet shard, never full download

## 7. endomorphosis/Caselaw_Access_Project_JSON (extra candidate)

id: endomorphosis/Caselaw_Access_Project_JSON
page_status: first=200 retry=not needed (recheck=not rechecked)
api_status: [200]
exists: True | gated: False | private: False | disabled: False
license: cc0-1.0 (permissive_for_public_repo: True)
last_modified: 2026-11-06T09:35:06.000Z | downloads: 4408
files: 6482 | pdf_files: 0 | pdf_examples: none
total_size: 96.5 MB (96539586 bytes, sum of file sizes with blobs=true)
viewer_is_valid: [200] {'preview': False, 'viewer': False, 'search': False, 'filter': False, 'statistics': False}
viewer_splits: [200] (n_splits=None)
viewer_first_rows: skipped (no split) on None
viewer_size: [200] | row_count: 0
content_rows_checked: 0 | text_chars: None
cite_hits: none (total 0)
cite_snippet: none
content_note: viewer disabled; one raw JSON file (double-encoded JSON string) fetched via /resolve
pdf_check: n/a (no loose .pdf files)
cites_us_law: no
usable_for_demo: with-conditions
usable_reason: CC0 US case law as 6,482 per-case JSON files (96 MB), but datasets-server viewer disabled (no splits, 0 rows) and files are double-encoded JSON strings; sample file had 0 regex cite hits (short Alaska 1995 opinion); fetch raw via /resolve only

## 8. andrew-mitchel/tax-court-opinions (extra candidate)

id: andrew-mitchel/tax-court-opinions
page_status: first=200 retry=not needed (recheck=not rechecked)
api_status: [200]
exists: True | gated: False | private: False | disabled: False
license: mit (permissive_for_public_repo: True)
last_modified: 2026-10-03T00:03:43.000Z | downloads: 156
files: 4 | pdf_files: 0 | pdf_examples: none
total_size: 0.2 GB (174603976 bytes, sum of file sizes with blobs=true)
viewer_is_valid: [200] {'preview': True, 'viewer': True, 'search': True, 'filter': True, 'statistics': True}
viewer_splits: [200] (n_splits=1)
viewer_first_rows: [200] on default/train
viewer_size: [200] | row_count: 14887
columns: filename, year, month, day, opinion_type, opinion_name, num_pages, docket_number, judge_name, text
content_rows_checked: 3 | text_chars: 119455
cite_hits: {'usc': 4, 'cal': 1, 'fed_rep': 42, 'us_rep': 28} (total 75)
cite_snippet: s, other evidence and/or instrumentalities of violations of 26 U.S.C. Sections 7201 and 7206(2), and 18 U.S.C. Section 371 for the years 1976 up to a
pdf_check: n/a (no loose .pdf files)
cites_us_law: yes
usable_for_demo: yes
usable_reason: MIT license, viewer ok, 14,887 US Tax Court opinions in one 175 MB parquet with docket, judge, num_pages and full text; 75 cite hits in 3 rows (26 U.S.C., F.2d); domain is tax only, bench rulings not jury trials; no PDFs

## 9. safelegalaidata/us-genai-court-opinions (extra candidate)

id: safelegalaidata/us-genai-court-opinions
page_status: first=200 retry=not needed (recheck=not rechecked)
api_status: [200]
exists: True | gated: False | private: False | disabled: False
license: cc-by-4.0 (permissive_for_public_repo: True)
last_modified: 2026-09-08T22:36:59.000Z | downloads: 54
files: 13 | pdf_files: 0 | pdf_examples: none
total_size: 4.7 MB (4718183 bytes, sum of file sizes with blobs=true)
viewer_is_valid: [200] {'preview': True, 'viewer': True, 'search': True, 'filter': True, 'statistics': True}
viewer_splits: [200] (n_splits=2)
viewer_first_rows: [200] on decisions/train
viewer_size: [200] | row_count: 575
columns: decision_id, ai_mention, ai_passage, ai_tool_named, archive_url, case_name, citation, cited_authorities, court, court_code, court_level, court_used_ai, courtlistener_url, date_filed ...
content_rows_checked: 3 | text_chars: 9341
cite_hits: none (total 0)
cite_snippet: none
content_extra: {"status": 200, "court": "District of Columbia Court of Appeals", "state": "DC", "disposition": "The court struck Deutsche Bank's appellee brief and referred the matter to the Office of Disciplinary Counsel.", "citation": null, "cited_authorities": "[\"Abadie v. District of Columbia\", \"Cason v. Nat’l Consumer Co-op Bank\", \"Osborne v. District of Columbia\", \"Woods v. United States\"]", "summary": "The District o
pdf_check: n/a (no loose .pdf files)
cites_us_law: no
usable_for_demo: with-conditions
usable_reason: CC BY 4.0, viewer ok, 575 rows, but metadata and summaries only (no full-opinion text column, no PDFs); topic is AI-hallucinated citations sanctions; has cited_authorities and disposition fields and CourtListener links

## 10. Matt1up/guertin-mcro-forensic-corpus-court-filings (extra candidate)

id: Matt1up/guertin-mcro-forensic-corpus-court-filings
page_status: first=200 retry=not needed (recheck=200)
api_status: [200]
exists: True | gated: False | private: False | disabled: False
license: cc-by-4.0 (permissive_for_public_repo: True)
last_modified: 2026-09-15T04:21:36.000Z | downloads: 479
files: 47448 | pdf_files: 7412 | pdf_examples: (file name left out); (file name left out); (file name left out)
total_size: 8.6 GB (8556261090 bytes, sum of file sizes with blobs=true)
viewer_is_valid: [200] {'preview': False, 'viewer': False, 'search': False, 'filter': False, 'statistics': False}
viewer_splits: [200] (n_splits=1)
viewer_first_rows: [500, 500] on default/train
viewer_size: [200] | row_count: 0
content_rows_checked: 0 | text_chars: 0
cite_hits: none (total 0)
cite_snippet: none
pdf_check_file: children-pdf/pdf/MCRO_27-CR-21-23456_Order Revoking Interim Conditions of Release_2023-05-10_20240430084313.pdf (399837 bytes, http 200)
pdf_check_result: %PDF header=True | pages=2 | text_chars=2016 | cite_hits=none (total 0)
pdf_check_snippet: none
cites_us_law: no
usable_for_demo: no
usable_reason: 7,412 real court-filing PDFs (Minnesota criminal/children case, one named family) is a privacy risk for a public repo despite CC BY 4.0; viewer first-rows returned HTTP 500 twice, splits exist but is-valid says viewer false; 47k files / 8.6 GB; sampled PDF had no matching law cites

## 11. forcemultiplier/supreme_court_opinions_corpus_pdfwebAug24 (extra candidate)

id: forcemultiplier/supreme_court_opinions_corpus_pdfwebAug24
page_status: first=200 retry=not needed (recheck=not rechecked)
api_status: [200]
exists: True | gated: False | private: False | disabled: False
license: None (permissive_for_public_repo: False)
last_modified: 2024-10-21T21:45:21.000Z | downloads: 1207
files: 638 | pdf_files: 403 | pdf_examples: pdf/04pdf/04-278.pdf; pdf/05pdf/04-1084.pdf; pdf/05pdf/04-607.pdf
total_size: 0.3 GB (253003084 bytes, sum of file sizes with blobs=true)
viewer_is_valid: [200] {'preview': True, 'viewer': False, 'search': False, 'filter': False, 'statistics': False}
viewer_splits: [200] (n_splits=1)
viewer_first_rows: [200] on default/train
viewer_size: [200] | row_count: 0
columns: pid, docid, content, metadata, error, page_number
content_rows_checked: 3 | text_chars: 3238
cite_hits: {'section': 1} (total 1)
cite_snippet: ory pending a proceeding under section 1229a.” 8 U. S. C. §1225(b)(2)(C). Following a change in Presidential administrations, the Biden 
pdf_check_file: pdf/22pdf/21-454_4g15.pdf (398518 bytes, http 200)
pdf_check_result: %PDF header=True | pages=82 | text_chars=26619 | cite_hits={'usc': 11, 'section': 36, 'fed_rep': 3, 'us_rep': 13} (total 63)
pdf_check_snippet: charging pollutants into “the waters of the United States.” 33 U. S. C. §1362(7). The EPA ordered the Sacketts to restore the site, threatening penalti
cites_us_law: yes
usable_for_demo: with-conditions
usable_reason: 403 real SCOTUS slip-opinion PDFs (253 MB repo), sample PDF valid (%PDF, 82 pages, 63 cite hits: 33 U.S.C. 1362, F.3d); viewer preview ok; repo has NO license field (SCOTUS opinions are US gov works, owner should confirm before bundling); last modified 2024-10
