"""CVSS v4.0 on findings: the vector is validated and scored with FIRST's reference algorithm
(the ``cvss`` package). The report shows score and vector; the qualitative band comes from
FIRST's v4.0 ranges, and a severity that differs from the band needs its rationale in the finding."""

from __future__ import annotations

import re
from dataclasses import dataclass

from cvss import CVSS4, CVSS4Error

from intern.engagement.models import Cvss, Severity

_RANK: dict[Severity, int] = {"info": 0, "low": 1, "medium": 2, "high": 3, "critical": 4}


#: FIRST CVSS v4.0 qualitative ranges: None 0.0 · Low 0.1–3.9 · Medium 4.0–6.9 · High 7.0–8.9 ·
#: Critical 9.0–10.0.
def cvss_band(score: float) -> Severity:
    if score >= 9:
        return "critical"
    if score >= 7:
        return "high"
    if score >= 4:
        return "medium"
    return "low" if score > 0 else "info"


_BASE_METRICS = ("AV", "AC", "AT", "PR", "UI", "VC", "VI", "VA", "SC", "SI", "SA")


@dataclass(frozen=True)
class CvssResult:
    score: Cvss


def score_cvss(vector: str) -> CvssResult | Exception:
    """Parse and score a CVSS v4.0 vector ("CVSS:4.0/AV:N/AC:L/…"); a readable error otherwise.

    An Exception return reads better for callers than raising: the tool layer turns it into a
    failed ToolOutput with the message, and the type checker keeps both paths explicit."""
    v = vector.strip()
    if not re.match(r"^CVSS:4\.0/", v):
        return ValueError(f"not a CVSS v4.0 vector (must start with CVSS:4.0/): {v[:60]}")
    missing = [m for m in _BASE_METRICS if f"/{m}:" not in v]
    if missing:
        return ValueError(f"missing base metric(s) {', '.join(missing)}")
    try:
        scores = CVSS4(v).scores()
        base = scores[0] if scores and scores[0] is not None else None
    except CVSS4Error as e:
        return ValueError(f"could not score this vector — {e}")
    if base is None or base <= 0:
        return ValueError(
            "could not score this vector — are all base metrics present (AV, AC, AT, PR, UI, VC, VI, VA, SC, SI, SA)?"
        )
    return CvssResult(Cvss(vector=v, score=round(base, 1), band=cvss_band(base)))


def severity_drift(severity: Severity, band: Severity) -> bool:
    """True when the stated severity is more than one step away from the CVSS band (needs a
    written rationale)."""
    return abs(_RANK[severity] - _RANK[band]) > 1
