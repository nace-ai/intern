"""NDI (Nace Document Intelligence): turn any document into the laws of physics of a Drex Life world.

1. NDI Extract reads the document (a public URL or a local file we upload) and fills a schema:
   the actors whose spread / growth / change it describes, and the interactions between them,
   each with citations to the page it was read from.
2. The Life director turns those cited facts into states and rules; every rule ends with its source page.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
from pathlib import Path

import httpx
from dotenv import load_dotenv

from pixeldrex.llm import DeepInfra
from pixeldrex.life import DIRECTOR, World

load_dotenv(Path(__file__).resolve().parent.parent / ".env")
BASE = os.getenv("NACE_BASE_URL", "https://console.nace.ai").rstrip("/")

WORLD_SCHEMA = {
    "type": "object",
    "properties": {
        "topic": {"type": "string", "description": "What the document is about, in a few words."},
        "actors": {
            "type": "array",
            "maxItems": 5,
            "description": "The 3 to 5 kinds of things (organisms, people, materials, conditions or states) whose "
            "spread, growth, decline or transformation the document describes.",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "One or two words."},
                    "role": {"type": "string", "description": "What it is or does, in one short sentence."},
                },
            },
        },
        "interactions": {
            "type": "array",
            "maxItems": 8,
            "description": "How one actor or state causes another to appear, spread, change, die or recover, "
            "as described in the document.",
            "items": {
                "type": "object",
                "properties": {
                    "cause": {"type": "string", "description": "The actor or condition that acts."},
                    "effect": {"type": "string", "description": "What happens to what, in one short sentence."},
                    "speed": {
                        "type": "string",
                        "enum": ["fast", "medium", "slow", "rare"],
                        "description": "How quickly or how often this happens, per the document.",
                    },
                },
            },
        },
    },
    "required": ["topic", "actors", "interactions"],
}


class NDI:
    def __init__(self, api_key: str | None = None):
        key = api_key or os.getenv("NACE_API_KEY")
        if not key:
            raise RuntimeError("NACE_API_KEY is not set")
        self.http = httpx.AsyncClient(
            base_url=BASE, timeout=120, headers={"Authorization": f"Bearer {key}", "X-Nace-Client": "pixeldrex/0.1"}
        )

    async def __aenter__(self) -> "NDI":
        return self

    async def __aexit__(self, *exc) -> None:
        await self.http.aclose()

    async def upload(self, path: str | Path) -> dict:
        """Upload a local file; returns a workspace_file source."""
        p = Path(path)
        grant = (await self.http.post("/v1/documents/upload-grants", json={"path": p.name})).raise_for_status().json()
        async with httpx.AsyncClient(timeout=120) as up:
            r = await up.post(
                grant["upload_url"],
                headers={"X-Upload-Token": grant["token"]},
                files={"file": (p.name, p.read_bytes())},
                data={"metadata": json.dumps({"path": p.name})},
            )
        r.raise_for_status()
        file_id = r.json()["result"]["file"]["file_id"]
        return {"type": "workspace_file", "workspace_id": grant["workspace_id"], "file_id": file_id}

    async def wait(self, job: dict, timeout_s: float = 240) -> dict:
        waited = 0.0
        while job.get("status") not in ("succeeded", "failed", "cancelled"):
            await asyncio.sleep(2)
            waited += 2
            if waited > timeout_s:
                raise TimeoutError(f"NDI job {job.get('job_id')} still {job.get('status')} after {timeout_s}s")
            job = (await self.http.get(f"/v1/documents/jobs/{job['job_id']}")).raise_for_status().json()
        if job["status"] != "succeeded":
            raise RuntimeError(f"NDI job {job.get('job_id')} {job['status']}: {json.dumps(job.get('error'))[:300]}")
        return job

    async def extract(self, source: dict, schema: dict, instructions: str | None = None) -> dict:
        body = {"source": source, "schema": schema, "name": "pixeldrex world extraction"}
        if instructions:
            body["instructions"] = instructions
        r = await self.http.post("/v1/documents/extract", params={"wait_seconds": 60}, json=body)
        if r.status_code >= 400:
            raise RuntimeError(f"NDI extract HTTP {r.status_code}: {r.text[:400]}")
        return (await self.wait(r.json()))["result"]


def source_for(doc: str) -> dict | None:
    """A URL source for http(s) links; None for local files (they need an upload)."""
    if doc.startswith("https://"):
        name = re.sub(r"[^A-Za-z0-9._-]", "_", doc.rstrip("/").split("/")[-1]) or "document"
        if "." not in name[-6:]:
            name += ".pdf"
        return {"type": "url", "url": doc, "file_name": name}
    return None


def cited_facts(result: dict) -> dict:
    """Attach each extracted value's page numbers so the director can cite them."""
    pages: dict[str, list[int]] = {}
    for f in result.get("fields", []):
        ps = sorted({c.get("location", {}).get("page") for c in f.get("citations", []) if c.get("location")} - {None})
        if ps:
            pages[f["path"]] = ps
    data = result.get("data") or {}
    facts = {"topic": data.get("topic"), "actors": [], "interactions": []}
    for i, a in enumerate(data.get("actors") or []):
        facts["actors"].append({**(a or {}), "pages": pages.get(f"/actors/{i}") or pages.get(f"/actors/{i}/name", [])})
    for i, x in enumerate(data.get("interactions") or []):
        pg = (
            pages.get(f"/interactions/{i}")
            or pages.get(f"/interactions/{i}/effect")
            or pages.get(f"/interactions/{i}/cause", [])
        )
        facts["interactions"].append({**(x or {}), "pages": pg})
    return facts


async def world_from_document(doc: str, idea: str | None = None, on_line=None) -> tuple[World, dict]:
    """NDI extracts cited facts from the document; the director turns them into a Life world."""
    say = on_line or (lambda s: None)
    async with NDI() as ndi:
        src = source_for(doc)
        if src is None:
            say(f"uploading {Path(doc).name} to NDI")
            src = await ndi.upload(doc)
        say("NDI is reading the document (extract with citations)")
        result = await ndi.extract(
            src,
            WORLD_SCHEMA,
            instructions=(
                "Focus on processes a grid simulation could show: spreading, growth, decay, infection, recovery, "
                "competition. Use the document's own terms." + (f" The user is interested in: {idea}." if idea else "")
            ),
        )
    facts = cited_facts(result)
    say(
        f"NDI found {len(facts['actors'])} actors and {len(facts['interactions'])} interactions about "
        f"'{facts.get('topic')}'"
    )
    prompt = (
        "Design the world from these facts, extracted with page citations from a document by NDI. Pick 4-5 cell "
        "states from the actors and conditions that matter most (a condition such as warm water or a predator "
        "such as fish must be its own state if a rule depends on it). Rules may ONLY mention the states you "
        "defined. Base every rule on one or more of the interactions, and END each rule with its source page(s) "
        "in the form (p. 3) or (p. 3, 7). "
        + (f"The user's angle: {idea}. " if idea else "")
        + "Facts:\n"
        + json.dumps(facts, indent=1)
    )
    async with DeepInfra() as di:
        text = await di.chat(prompt, system=DIRECTOR, max_tokens=900, temperature=0.4, json_mode=True)
    m = re.search(r"\{.*\}", text, re.S)
    world = World.from_dict(json.loads(m.group(0) if m else text))
    return world, {"document": doc, "facts": facts, "ndi_units": result.get("units")}
