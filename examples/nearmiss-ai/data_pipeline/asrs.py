"""Import the official two-header ASRS CSV and build bounded CaseContext values."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import sys
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / "data" / "asrs.duckdb"
SOURCE_URL = "https://asrs.arc.nasa.gov/search/database.html"
DEFAULT_SAMPLE = "NASA ASRS Database Online CSV export; query and export date are recorded in data/manifest.md"
# Months holding at least this share of the records define the displayed period, so the few
# records NASA dates outside the export window do not stretch it.
PERIOD_MIN_SHARE = 0.01
TOKEN = re.compile(r"[a-z0-9]{3,}", re.IGNORECASE)
STOP = {"the", "and", "for", "with", "that", "this", "was", "were", "from", "had", "have", "they", "their", "but", "not", "our", "aircraft", "flight", "reported"}


def _text(value: str) -> str | None:
    value = value.strip()
    return value or None


def _tokens(value: str) -> set[str]:
    return {word for word in TOKEN.findall(value.lower()) if word not in STOP}


_TOKEN_CACHE: dict[tuple, list[tuple]] = {}


def _corpus_tokens(db: duckdb.DuckDBPyConnection, key: tuple) -> list[tuple]:
    """Tokenize every report once per process; neighbour search then only compares token sets.

    Keyed by the import's SHA-256 and row count, so a re-import is picked up automatically.
    """
    if key not in _TOKEN_CACHE:
        rows = db.execute("SELECT acn, narrative, synopsis, primary_problem, communication_breakdown FROM incidents").fetchall()
        _TOKEN_CACHE.clear()  # keep one corpus in memory
        _TOKEN_CACHE[key] = [
            (acn, frozenset(sys.intern(word) for word in _tokens(narrative + " " + (synopsis or ""))), problem, communication)
            for acn, narrative, synopsis, problem, communication in rows
        ]
    return _TOKEN_CACHE[key]


def read_export(path: Path) -> tuple[list[dict], dict]:
    """Read exact NASA export columns; reject unexpected layouts and duplicate ACNs."""
    with path.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.reader(stream)
        groups = next(reader)
        fields = next(reader)
        if len(groups) != len(fields) or fields[0] != "ACN":
            raise ValueError("Expected a two-row NASA ASRS CSV header beginning with ACN")

        def col(group: str, field: str) -> int:
            matches = [i for i, (g, f) in enumerate(zip(groups, fields)) if g.strip() == group and f.strip() == field]
            if len(matches) != 1:
                raise ValueError(f"Expected one {group}/{field} column, found {len(matches)}")
            return matches[0]
        columns = {
            "acn": col("", "ACN"),
            "date": col("Time", "Date"),
            "narrative": col("Report 1", "Narrative"),
            "supplemental_narrative": col("Report 2", "Narrative"),
            "synopsis": col("Report 1", "Synopsis"),
            "anomaly": col("Events", "Anomaly"),
            "primary_problem": col("Assessments", "Primary Problem"),
            "communication_breakdown": col("Person 1", "Communication Breakdown"),
            "human_factors": col("Person 1", "Human Factors"),
            "flight_phase": col("Aircraft 1", "Flight Phase"),
            "locale": col("Place", "Locale Reference"),
            "local_time": col("Time", "Local Time Of Day"),
        }
        records = []
        seen = set()
        for line, row in enumerate(reader, 3):
            if not row:
                continue
            if len(row) != len(fields):
                raise ValueError(f"CSV record near line {line} has {len(row)} columns; expected {len(fields)}")
            acn = row[columns["acn"]].strip()
            if not acn or acn in seen:
                raise ValueError(f"Missing or duplicate ACN: {acn!r}")
            seen.add(acn)
            date = row[columns["date"]].strip()
            if not re.fullmatch(r"\d{4}(0[1-9]|1[0-2])", date):
                raise ValueError(f"Invalid YYYYMM date for ACN {acn}: {date!r}")
            narrative = row[columns["narrative"]]
            if not narrative.strip():
                raise ValueError(f"Missing primary narrative for ACN {acn}")
            record = {name: _text(row[index]) for name, index in columns.items()}
            record["narrative"] = narrative  # Preserve source text, including whitespace.
            record["report_month"] = f"{date[:4]}-{date[4:]}"
            record["raw_fields"] = json.dumps([{"group": group.strip(), "field": field.strip(), "value": value} for group, field, value in zip(groups, fields, row)])
            records.append(record)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    return records, {"sha256": digest, "rows": len(records), "columns": len(fields), "missing": {key: sum(record[key] is None for record in records) for key in columns if key != "narrative"}}


def import_export(path: Path, db_path: Path = DEFAULT_DB, sample: str = DEFAULT_SAMPLE) -> dict:
    records, stats = read_export(path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with duckdb.connect(str(db_path)) as db:
        db.execute("BEGIN TRANSACTION")
        try:
            db.execute("CREATE OR REPLACE TABLE incidents (acn VARCHAR PRIMARY KEY, report_month VARCHAR, narrative VARCHAR NOT NULL, supplemental_narrative VARCHAR, synopsis VARCHAR, anomaly VARCHAR, primary_problem VARCHAR, communication_breakdown VARCHAR, human_factors VARCHAR, flight_phase VARCHAR, locale VARCHAR, local_time VARCHAR, raw_fields JSON)")
            db.executemany("INSERT INTO incidents VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", [tuple(row[key] for key in ("acn", "report_month", "narrative", "supplemental_narrative", "synopsis", "anomaly", "primary_problem", "communication_breakdown", "human_factors", "flight_phase", "locale", "local_time", "raw_fields")) for row in records])
            db.execute("CREATE OR REPLACE TABLE source_info (source_url VARCHAR, sample VARCHAR, sha256 VARCHAR, record_count INTEGER)")
            db.execute("INSERT INTO source_info VALUES (?, ?, ?, ?)", [SOURCE_URL, sample, stats["sha256"], stats["rows"]])
            db.execute("COMMIT")
        except Exception:
            db.execute("ROLLBACK")
            raise
    return stats


def _filters(phrase: str, month: str, category: str) -> tuple[str, list]:
    clauses, args = [], []
    if phrase.strip():
        clauses.append("(narrative ILIKE ? OR synopsis ILIKE ? OR acn ILIKE ?)")
        args.extend([f"%{phrase.strip()}%"] * 3)
    if month.strip():
        clauses.append("report_month = ?")
        args.append(month.strip())
    if category.strip().lower() == "communication":
        # The dashboard's "Communication" filter: reports with a coded Person 1 communication breakdown
        # (values look like "Party1 Flight Crew; Party2 ATC", so a text match on the word never hits).
        clauses.append("communication_breakdown IS NOT NULL")
    elif category.strip():
        clauses.append("(anomaly ILIKE ? OR primary_problem ILIKE ? OR communication_breakdown ILIKE ?)")
        args.extend([f"%{category.strip()}%"] * 3)
    return (" WHERE " + " AND ".join(clauses) if clauses else ""), args


def count_cases(db: duckdb.DuckDBPyConnection, phrase: str = "", month: str = "", category: str = "") -> int:
    where, args = _filters(phrase, month, category)
    return db.execute(f"SELECT count(*) FROM incidents{where}", args).fetchone()[0]


def search_cases(db: duckdb.DuckDBPyConnection, phrase: str = "", month: str = "", category: str = "", limit: int = 100, offset: int = 0) -> list[dict]:
    where, args = _filters(phrase, month, category)
    columns = "acn, report_month, synopsis, anomaly, primary_problem, communication_breakdown"
    result = db.execute(f"SELECT {columns} FROM incidents{where} ORDER BY acn DESC LIMIT ? OFFSET ?", [*args, min(max(limit, 1), 1000), max(offset, 0)])
    return [dict(zip([c[0] for c in result.description], row)) for row in result.fetchall()]


def period_of(db: duckdb.DuckDBPyConnection) -> dict:
    """First and last report month of the archive, ignoring isolated out-of-window records."""
    total = db.execute("SELECT count(*) FROM incidents").fetchone()[0]
    months = [m for m, n in db.execute("SELECT report_month, count(*) FROM incidents GROUP BY 1 ORDER BY 1").fetchall() if total and n / total >= PERIOD_MIN_SHARE]
    return {"first_month": months[0] if months else None, "last_month": months[-1] if months else None}


def get_case_context(db: duckdb.DuckDBPyConnection, acn: str, neighbors: int = 5) -> dict:
    result = db.execute("SELECT * FROM incidents WHERE acn = ?", [acn])
    row = result.fetchone()
    if row is None:
        raise KeyError(f"Unknown ASRS ACN: {acn}")
    case = dict(zip([c[0] for c in result.description], row))
    source_url, sample, sha256, record_count = db.execute("SELECT * FROM source_info").fetchone()
    query_tokens = _tokens(case["narrative"] + " " + (case["synopsis"] or ""))
    ranked = []
    for other_id, other_tokens, problem, communication in _corpus_tokens(db, (sha256, record_count)):
        if other_id == acn:
            continue
        overlap = len(query_tokens & other_tokens)
        union = len(query_tokens) + len(other_tokens) - overlap
        score = overlap / union if union else 0.0
        if problem and problem == case["primary_problem"]:
            score += 0.05
        if communication and communication == case["communication_breakdown"]:
            score += 0.05
        ranked.append((min(score, 1.0), other_id))
    ranked.sort(key=lambda item: (-item[0], item[1]))
    top = ranked[:min(max(neighbors, 0), 5)]
    narratives = dict(db.execute(f"SELECT acn, narrative FROM incidents WHERE acn IN ({','.join('?' * len(top))})", [other_id for _, other_id in top]).fetchall()) if top else {}
    metadata = {key: case[key] for key in ("report_month", "anomaly", "primary_problem", "communication_breakdown", "human_factors", "flight_phase", "locale", "local_time", "synopsis", "supplemental_narrative")}
    return {
        "contract_version": "1.0",
        "case_id": case["acn"],
        "narrative": case["narrative"],
        "metadata": metadata,
        "candidate_incidents": [{"case_id": other_id, "narrative": narratives[other_id], "retrieval_score": round(score, 4)} for score, other_id in top],
        "retrieval_method": "Jaccard overlap of unique non-stopword narrative/synopsis tokens, plus 0.05 for an exact Primary Problem match and 0.05 for an exact nonblank Communication Breakdown match; score capped at 1.0. Text retrieval only, not a safety risk score.",
        "data_source": {"name": "NASA ASRS", "url": source_url, "sample": sample},
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["import", "stats", "context"])
    parser.add_argument("value", nargs="?")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--output", type=Path, help="Write a real CaseContext JSON file")
    parser.add_argument("--sample", default=DEFAULT_SAMPLE, help="Provenance text stored with the import (query window and export date)")
    args = parser.parse_args()
    if args.command == "import":
        if not args.value:
            parser.error("import requires a CSV path")
        print(json.dumps(import_export(Path(args.value), args.db, args.sample), indent=2))
    else:
        with duckdb.connect(str(args.db), read_only=True) as db:
            if args.command == "stats":
                print(json.dumps({"records": db.execute("SELECT count(*) FROM incidents").fetchone()[0], "primary_problems": db.execute("SELECT primary_problem, count(*) FROM incidents GROUP BY 1 ORDER BY 2 DESC").fetchall()}, indent=2))
            else:
                if not args.value:
                    parser.error("context requires an ACN")
                output = json.dumps(get_case_context(db, args.value), indent=2)
                if args.output:
                    args.output.parent.mkdir(parents=True, exist_ok=True)
                    args.output.write_text(output + "\n", encoding="utf-8")
                    print(args.output)
                else:
                    print(output)


if __name__ == "__main__":
    main()
