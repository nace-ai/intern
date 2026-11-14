"""Read-only local HTTP access to Builder A's ASRS DuckDB data."""

from __future__ import annotations

import re

import duckdb
from fastapi import FastAPI, HTTPException, Query

from data_pipeline.asrs import DEFAULT_DB, count_cases, get_case_context, period_of, search_cases

app = FastAPI(title="NearMiss ASRS data", version="1.0")


def connect() -> duckdb.DuckDBPyConnection:
    if not DEFAULT_DB.exists():
        raise HTTPException(status_code=503, detail="ASRS database is not imported")
    return duckdb.connect(str(DEFAULT_DB), read_only=True)


@app.get("/health")
def health() -> dict:
    with connect() as db:
        count = db.execute("SELECT count(*) FROM incidents").fetchone()[0]
    return {"status": "ready", "records": count}


@app.get("/stats")
def stats() -> dict:
    with connect() as db:
        source_url, sample, _, count = db.execute("SELECT * FROM source_info").fetchone()
        categories = db.execute("SELECT primary_problem, count(*) AS count FROM incidents GROUP BY 1 ORDER BY 2 DESC").fetchall()
        period = period_of(db)
    return {"records": count, **period, "source_url": source_url, "sample": sample, "primary_problems": [{"name": name or "Unspecified", "count": value} for name, value in categories]}


@app.get("/cases")
def cases(phrase: str = "", month: str = "", category: str = "", limit: int = Query(default=1000, ge=1, le=1000), offset: int = Query(default=0, ge=0)) -> dict:
    with connect() as db:
        records = search_cases(db, phrase, month, category, limit, offset)
        total = count_cases(db, phrase, month, category)
    return {"cases": records, "count": len(records), "total": total, "offset": offset}


@app.get("/cases/{acn}/context")
def context(acn: str) -> dict:
    if not re.fullmatch(r"\d{5,10}", acn):
        raise HTTPException(status_code=422, detail="Invalid ACN")
    with connect() as db:
        try:
            return get_case_context(db, acn)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="ASRS record not found") from exc
