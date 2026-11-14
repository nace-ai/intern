"""Read-only access to the NDI evidence cache produced by evidence/ingest.py.

Retrieval here is deterministic lexical ranking (allowed by the plan: Python may
"retrieve candidates"). Whether a passage actually supports a claim is decided
by the Evidence Agent through Drex, not here.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from service.contracts import EvidenceRef

_STOPWORDS = set(
    """a an and are as at be been but by for from had has have he her his i in into is it its of on or our
    she so that the their them then there these they this to too was we were what when where which while who
    will with would you your not no do did does during after before over under about than also just only very
    can could should may might must shall""".split()
)
_TOKEN = re.compile(r"[a-z][a-z\-]{2,}")


def _tokens(text: str) -> set[str]:
    return {t for t in _TOKEN.findall(text.lower()) if t not in _STOPWORDS}


@dataclass(frozen=True)
class Passage:
    passage_id: str
    document_id: str
    title: str
    source: str
    topic: str
    passage_text: str
    page: int | None
    status: str  # "grounded" | "parsed"

    def to_ref(self, relevant_claim: str) -> EvidenceRef:
        return EvidenceRef(
            document_id=self.document_id,
            title=self.title,
            source=self.source,
            page_or_region=f"p. {self.page}" if self.page is not None else None,
            passage_text=self.passage_text,
            status=self.status,  # type: ignore[arg-type]
            relevant_claim=relevant_claim,
        )


class EvidenceStore:
    def __init__(self, cache_dir: Path):
        self.path = cache_dir / "passages.json"
        self.passages: list[Passage] = []
        self.documents: list[dict[str, Any]] = []
        self.load_error: str | None = None
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            for doc in data.get("documents", []):
                self.documents.append(
                    {k: doc.get(k) for k in ("document_id", "title", "source", "page_count", "parse_job_id")}
                )
                for p in doc.get("passages", []):
                    # Only NDI-derived passages with real text are loadable.
                    if p.get("status") not in ("grounded", "parsed") or not (p.get("passage_text") or "").strip():
                        continue
                    self.passages.append(
                        Passage(
                            passage_id=p["passage_id"],
                            document_id=p["document_id"],
                            title=p["title"],
                            source=p["source"],
                            topic=p["topic"],
                            passage_text=p["passage_text"],
                            page=p.get("page"),
                            status=p["status"],
                        )
                    )
        except (OSError, ValueError, KeyError, TypeError) as error:
            self.passages, self.documents = [], []
            self.load_error = f"Evidence cache could not be read ({type(error).__name__})."

    # ------------------------------------------------------------------ status

    def status(self) -> dict[str, Any]:
        if self.load_error:
            return {"status": "error", "document_count": 0, "detail": self.load_error}
        if not self.passages:
            detail = (
                "No NDI evidence cache found; run `python -m evidence.ingest`."
                if not self.path.exists()
                else "Evidence cache exists but holds no located passages."
            )
            return {"status": "not_ready", "document_count": len(self.documents), "detail": detail}
        grounded = sum(1 for p in self.passages if p.status == "grounded")
        docs_with_passages = len({p.document_id for p in self.passages})
        return {
            "status": "ready",
            "document_count": docs_with_passages,
            "detail": f"{len(self.passages)} NDI passages ({grounded} grounded to a page) from "
            f"{docs_with_passages} document(s).",
        }

    @property
    def ready(self) -> bool:
        return bool(self.passages) and self.load_error is None

    # --------------------------------------------------------------- retrieval

    def retrieve(self, query_text: str, family: str | None, k: int = 4) -> list[Passage]:
        """Rank passages by topic match and token overlap with the query. Deterministic."""
        if not self.passages:
            return []
        query = _tokens(query_text)
        scored = []
        for index, passage in enumerate(self.passages):
            overlap = len(query & _tokens(passage.passage_text))
            topic_bonus = 3 if family and passage.topic == family else 0
            grounded_bonus = 0.5 if passage.status == "grounded" else 0
            score = overlap + topic_bonus + grounded_bonus
            if score > 0:
                scored.append((-score, index, passage))
        scored.sort()
        return [p for _, _, p in scored[:k]]
