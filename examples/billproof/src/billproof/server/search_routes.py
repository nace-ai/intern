"""Ask-your-documents routes (T3).

GET  /api/search/{case_id}/chain       recorded NDI→Search→Drex→letter chain (?live=1 re-runs it)
GET  /api/search/{case_id}/questions   demo questions with their recorded answers and grades
POST /api/ask                          {case_id, question | qid, mode?, schema?, live?}

Replay by default: nothing here touches the network unless `live` is set.
"""
from __future__ import annotations

import asyncio
import re

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel

import importlib

ask_mod = importlib.import_module("billproof.search.ask")
from billproof.search import ask_case

router = APIRouter()
_CASE = re.compile(r"(SYN-(?:\d{4}|D\d{2})|SC-\d{2})")


def _source_id(case_id: str) -> str | None:
    if not _CASE.fullmatch(case_id or ""):
        return None
    if case_id.startswith("SC-"):
        try:
            from billproof.showcase import showcase_case_entry

            return showcase_case_entry(case_id)["source_case_id"]
        except Exception:
            return None
    return case_id


def _err(status: int, msg: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": msg})


@router.get("/api/search/{case_id}/chain")
async def api_search_chain(case_id: str, live: bool = False):
    cid = _source_id(case_id)
    if cid is None:
        return _err(404, "unknown case")
    try:
        result = await asyncio.to_thread(ask_case, cid, live)
    except Exception as exc:  # live path: network/credentials
        return _err(502, f"{type(exc).__name__}: {exc}")
    if result.get("status") == "not_recorded":
        return _err(404, "no recorded chain for this case")
    return result


@router.get("/api/search/{case_id}/questions")
async def api_search_questions(case_id: str):
    cid = _source_id(case_id)
    if cid is None:
        return _err(404, "unknown case")
    out = []
    for q in ask_mod.DEMO_QUESTIONS.get(cid, []):
        rec = ask_mod.ask(cid, q["question"], schema=q["schema"])
        recorded = rec.get("status") != "not_recorded"
        out.append({"id": q["id"], "question": q["question"], "expect": q["expect"], "recorded": recorded,
                    "correct": ask_mod.grade(q["expect"], rec.get("structured_answer")) if recorded else None,
                    "result": rec if recorded else None})
    return {"case_id": cid, "questions": out}


class AskBody(BaseModel):
    case_id: str
    question: str | None = None
    qid: str | None = None
    mode: str = "fact"
    schema_: dict | None = None
    live: bool = False


@router.post("/api/ask")
async def api_ask(body: dict):
    try:
        b = AskBody.model_validate({**{k: v for k, v in body.items() if k != "schema"}, "schema_": body.get("schema")})
    except Exception as exc:
        return _err(422, str(exc))
    cid = _source_id(b.case_id)
    if cid is None:
        return _err(404, "unknown case")
    question, schema = b.question, b.schema_
    if b.qid:
        demo = ask_mod.demo_question(cid, b.qid)
        if demo is None:
            return _err(404, "unknown qid")
        question, schema = demo["question"], demo["schema"]
    if not question or len(question) > 1000:
        return _err(422, "question required (max 1000 chars)")
    if b.mode not in ("fact", "agent", "auto"):
        return _err(422, "mode must be fact, agent or auto")
    try:
        return await asyncio.to_thread(ask_mod.ask, cid, question, mode=b.mode, schema=schema, live=b.live)
    except Exception as exc:
        return _err(502, f"{type(exc).__name__}: {exc}")
