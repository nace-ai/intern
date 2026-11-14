# data/real/ — real IMR benchmark sources

## Retrieval
- CKAN record: https://data.chhs.ca.gov/dataset/independent-medical-review-imr-determinations-trend (licence field: none declared; metadata modified 2026-06-01T13:00:32.782240)
- CSV: https://data.chhs.ca.gov/dataset/b79b3447-4c10-4ae6-84e2-1076f83bb24e/resource/3340c5d7-4054-4d03-90e0-5f44290ed095/download/independent-medical-review-imr-determinations-trends.csv
  - retrieved 2026-11-13 · 85.4 MB · sha256 f68951823f6ffc09…
- Data dictionary: https://data.chhs.ca.gov/dataset/b79b3447-4c10-4ae6-84e2-1076f83bb24e/resource/2c5c2144-138f-4feb-b2af-a1176995e1b1/download/imrdatadictionary.pdf
- Secondary: https://huggingface.co/datasets/Persius/imr-appeals (CC-BY-SA-4.0) — test=9920, train=64067 — raw parquet only, not shipped.

## Row counts
- Raw CSV rows: 42749 · usable after cleaning: 42749
- Shipped: 400 items — test 300 (150 overturn / 150 upheld) ·
  dev 100 (50 / 50) · seed 20261113
- Leakage control: `Findings` reviewer narrative excluded; model input =
  pre-decision fields only; every field passes the outcome-word ban
  (tests/test_imr_leakage.py). `treatment_requested` extracts only the
  "requested ... for <X>" span (a pre-decision fact), else falls back to the
  TreatmentSubCategory column; plan_reason is a template restating the `Type`
  column. DaysToReview/DaysToAdopt (post-decision) are dropped.

## Base rates (overturn share of determinations)
- Overall: 52.5% (22,445/42,749)
- By denial type:
  - experimental_investigational: 45.3% (4,749/10,476)
  - medical_necessity: 55.1% (17,371/31,504)
  - urgent_care: 42.3% (325/769)
- By period:
  - 2001–2010: 41.3% (4,141/10,036)
  - 2011–2020: 50.4% (10,366/20,567)
  - 2021–2026: 65.4% (7,938/12,146)
- Top diagnosis categories by volume:
  - Orth/Musculoskeletal: 41.6% (3,054/7,337)
  - Mental Disorder: 56.6% (3,462/6,120)
  - Cancer: 48.0% (1,781/3,709)
  - Endocrine/Metabolic: 67.3% (2,248/3,342)
  - CNS/ Neuromusc Dis: 55.0% (1,817/3,304)
  - Cardiac/Circ Problem: 40.2% (868/2,158)
  - Digestive System/ GI: 48.1% (893/1,857)
  - Infectious Disease: 70.6% (1,268/1,795)
  - Autism Spectrum: 66.9% (930/1,390)
  - Skin Disorders: 55.7% (730/1,311)
