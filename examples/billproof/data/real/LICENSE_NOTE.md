# License note — data/real/

## Source: CA DMHC Independent Medical Review (IMR) Determinations, Trend

- Publisher: California Department of Managed Health Care (DMHC) via the
  California Health and Human Services open data portal (data.chhs.ca.gov).
- Dataset page: https://data.chhs.ca.gov/dataset/independent-medical-review-imr-determinations-trend
- Licence: the CKAN record declares **no licence field**. The file is a public
  government record published on the state open-data portal; contents are
  de-identified case-level summaries (no PHI: no names, dates are ranges/years).
- Use here: a ~400-row balanced evaluation subset (dev/test), reduced to
  pre-decision fields. The `Findings` narrative (post-decision reviewer text)
  is never shipped in an item.
- Attribution (as credited everywhere the data appears):
  "Source: California Department of Managed Health Care, Independent Medical
  Review Determinations (data.chhs.ca.gov)."

## Secondary source (raw download only, NOT used in shipped items)

- https://huggingface.co/datasets/Persius/imr-appeals — processed version of the same DMHC source, 74k rows,
  licence **CC-BY-SA-4.0**, © Persius / TPAFS (github.com/TPAFS/hicric).
  Fetched to data/raw/ for cross-checking; shipped items derive solely from
  the DMHC CSV above.
