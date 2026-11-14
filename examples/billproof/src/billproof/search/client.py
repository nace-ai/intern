"""Thin client for Nace Workspaces: create, upload, ingest, automatic search, QA."""
from __future__ import annotations

import json
import time
from typing import Any

import httpx

from billproof.config import settings

_FINAL = ("succeeded", "failed", "cancelled")


class SearchUnavailable(RuntimeError):
    pass


class WorkspaceClient:
    def __init__(self, api_key: str | None = None, base_url: str | None = None,
                 client: httpx.Client | None = None):
        s = settings()
        self.key = api_key if api_key is not None else s.nace_api_key
        self.base = (base_url or s.nace_base_url).rstrip("/")
        if not self.key:
            raise SearchUnavailable("no NACE_API_KEY")
        self.http = client or httpx.Client(timeout=90)

    def _h(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.key}"}

    def _req(self, method: str, path: str, **kw) -> dict[str, Any]:
        for attempt in range(4):
            r = self.http.request(method, self.base + path, headers=self._h(), **kw)
            if r.status_code in (429, 503, 529) and attempt < 3:
                time.sleep(float(r.headers.get("retry-after", 2 ** attempt)))
                continue
            if r.status_code >= 400:
                raise SearchUnavailable(f"{method} {path} -> {r.status_code} {r.text[:300]}")
            return r.json()
        raise SearchUnavailable("retries exhausted")

    def workspace(self, name: str) -> str:
        """workspace_id of `name`, created if missing."""
        items = self._req("GET", "/v1/workspaces", params={"name_contains": name}).get("items") or []
        for w in items:
            if w.get("name") == name:
                return w["workspace_id"]
        return self._req("POST", "/v1/workspaces", json={"name": name})["workspace_id"]

    def files(self, ws: str) -> list[dict]:
        return self._req("GET", f"/v1/workspaces/{ws}/files", params={"limit": 100}).get("items") or []

    def upload(self, ws: str, path: str, data: bytes, mime: str = "application/pdf") -> None:
        g = self._req("POST", "/v1/documents/upload-grants",
                      json={"workspace_id": ws, "path": path, "ttl_seconds": 600})
        r = self.http.post(g["upload_url"], headers={"X-Upload-Token": g["token"]},
                           files={"file": (path.rsplit("/", 1)[-1], data, mime)},
                           data={"metadata": json.dumps({"path": path})})
        if r.status_code >= 400:
            raise SearchUnavailable(f"upload {path} -> {r.status_code} {r.text[:200]}")

    def wait(self, job: dict, timeout: float = 400) -> dict:
        t = time.time()
        while job.get("status") not in _FINAL and time.time() - t < timeout:
            time.sleep(3)
            job = self._req("GET", f"/v1/documents/jobs/{job['job_id']}")
        return job

    def ingest(self, ws: str, prefix: str) -> dict:
        job = self._req("POST", f"/v1/workspaces/{ws}/ingestions", params={"wait_seconds": 60},
                        json={"selection": {"type": "path_prefix", "path_prefix": prefix, "stale_only": True}})
        return self.wait(job)

    def search(self, ws: str, query: str, *, mode: str = "agent", path_prefix: str | None = None,
               output_schema: dict | None = None, context: str | None = None, top_k: int | None = None) -> dict:
        body: dict[str, Any] = {"query": query, "mode": mode}
        for k, v in (("path_prefix", path_prefix), ("output_schema", output_schema),
                     ("context", context), ("top_k", top_k)):
            if v is not None:
                body[k] = v
        job = self._req("POST", f"/v1/workspaces/{ws}/automatic-search", params={"wait_seconds": 60}, json=body)
        return self.wait(job, timeout=300)

    def qa(self, ws: str, query: str, *, path_prefix: str | None = None,
           output_schema: dict | None = None, context: str | None = None) -> dict:
        body: dict[str, Any] = {"query": query, "allow_clarification": False}
        for k, v in (("path_prefix", path_prefix), ("output_schema", output_schema), ("context", context)):
            if v is not None:
                body[k] = v
        job = self._req("POST", f"/v1/workspaces/{ws}/qa", params={"wait_seconds": 60}, json=body)
        return self.wait(job, timeout=300)


def answer_of(job: dict) -> dict:
    """Flatten a fact or agent search job into {answer, structured_answer, confidence, evidences, route}."""
    res = job.get("result") or {}
    out = res.get("output") or res
    inner = out.get("answer") if isinstance(out.get("answer"), dict) else out
    return {
        "route": (res.get("routing") or {}).get("final") or out.get("result_type"),
        "answer": inner.get("answer") if isinstance(inner.get("answer"), str) else out.get("answer"),
        "structured_answer": inner.get("structured_answer"),
        "reasoning": inner.get("reasoning"),
        "confidence": inner.get("confidence"),
        "evidences": inner.get("evidences") or out.get("evidences") or [],
        "exhausted": inner.get("exhausted"),
    }


def cost_of(job: dict) -> float | None:
    for k in ("cost", "usage", "billing"):
        v = job.get(k)
        if isinstance(v, dict):
            for f in ("cost_usd", "total_usd", "amount_usd", "usd"):
                if isinstance(v.get(f), (int, float)):
                    return float(v[f])
        if isinstance(v, (int, float)):
            return float(v)
    return None
