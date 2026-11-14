"""Process-local shims around FrontierAdapter. Does not edit adapters/frontier.py."""
from __future__ import annotations

import json
import os
import time
from typing import Any

import httpx

import billproof.adapters.frontier as frontier_mod
from billproof.adapters.frontier import FrontierAdapter, _pace

_MAX_ATTEMPTS = 3
CALLS_PATH_NAME = "ladder_router_calls.json"
CAP_CALLS = 1200
CAP_USD = 20.0


def calls_path():
    from billproof.config import RUNS_DIR

    return RUNS_DIR / CALLS_PATH_NAME


def _read_calls(path) -> int:
    if not path.exists():
        return 0
    try:
        return int(json.loads(path.read_text()).get("calls") or 0)
    except (json.JSONDecodeError, ValueError, OSError):
        return 0


def note_router_call() -> None:
    """Count one router attempt. Raises LadderStop at the call cap."""
    import fcntl

    path = calls_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    lock = path.with_suffix(".lock")
    with open(lock, "a+") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        calls = _read_calls(path)
        if calls >= CAP_CALLS:
            raise LadderStop(f"router call cap {CAP_CALLS} reached ({calls})")
        calls += 1
        path.write_text(json.dumps({"calls": calls, "cap": CAP_CALLS}) + "\n")


class LadderStop(RuntimeError):
    pass


def _http_error(resp: httpx.Response) -> str:
    return frontier_mod._http_error(resp)


def _backoff_s(resp, attempt: int) -> float:
    return frontier_mod._backoff_s(resp, attempt)


_ORIGINAL_POST = FrontierAdapter._post


def output_cap_path():
    from billproof.config import RUNS_DIR

    return RUNS_DIR / "ladder_output_cap.json"


def _max_tokens() -> int:
    """One completion cap for every ladder model, from the frozen file.

    An environment override is accepted only when it equals that file.
    A different value would make one process use a shorter cap than the others.
    """
    path = output_cap_path()
    try:
        document = json.loads(path.read_text())
        cap = int(document["output_tokens"])
    except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        raise RuntimeError(f"frozen ladder output cap is missing: {path}: {exc}") from exc
    if cap < 256:
        raise RuntimeError(f"frozen ladder output cap {cap} is below 256")
    override = os.environ.get("LADDER_MAX_TOKENS")
    if override is not None and override.strip() != str(cap):
        raise RuntimeError(
            f"LADDER_MAX_TOKENS={override} does not match the frozen cap {cap} in {path.name}"
        )
    return cap


def install_ladder_post(*, prompt_only: bool) -> None:
    """Same request as FrontierAdapter._post, plus an output cap.

    The router otherwise stops a packed Hard answer at 8,192 tokens with
    empty content and finish_reason=length. Prompt-only omits response_format
    for models that return an empty body when it is set. A 400 that names
    max_tokens is retried once as max_completion_tokens.
    """

    def _post(self, messages, schema_name, schema):  # noqa: ANN001
        cfg = frontier_mod.settings()
        url = f"{cfg.frontier_base_url}/chat/completions"
        headers = {"Content-Type": "application/json"}
        if cfg.frontier_api_key:
            headers["Authorization"] = f"Bearer {cfg.frontier_api_key}"
        owns = self._client is None
        client = self._client or httpx.Client(transport=self._transport, timeout=90.0)
        send_temperature = True
        use_schema = not prompt_only
        fell_back = False
        token_field = "max_tokens"
        last_error = "frontier request failed"
        try:
            attempt = 0
            while attempt < _MAX_ATTEMPTS:
                body: dict[str, Any] = {
                    "model": cfg.frontier_model,
                    "messages": messages,
                    "stream": False,
                    token_field: _max_tokens(),
                }
                if use_schema:
                    body["response_format"] = frontier_mod._response_format(
                        schema_name, schema, use_schema=True,
                    )
                if send_temperature:
                    body["temperature"] = 0
                try:
                    note_router_call()
                    _pace()
                    resp = client.post(url, json=body, headers=headers)
                except LadderStop:
                    raise
                except httpx.HTTPError as exc:
                    attempt += 1
                    last_error = f"frontier transport error: {exc}"
                    if attempt >= _MAX_ATTEMPTS:
                        break
                    time.sleep(_backoff_s(None, attempt - 1))
                    continue
                if resp.status_code in (400, 422) and token_field == "max_tokens" and "max_tokens" in resp.text:
                    token_field = "max_completion_tokens"
                    last_error = _http_error(resp)
                    continue
                if resp.status_code in (400, 422) and send_temperature and "temperature" in resp.text:
                    send_temperature = False
                    last_error = _http_error(resp)
                    continue
                if resp.status_code in (400, 422) and use_schema and not fell_back:
                    use_schema = False
                    fell_back = True
                    last_error = _http_error(resp)
                    continue
                if resp.status_code == 429:
                    attempt += 1
                    last_error = _http_error(resp)
                    if attempt >= _MAX_ATTEMPTS:
                        break
                    time.sleep(61.0)
                    continue
                if resp.status_code >= 500:
                    attempt += 1
                    last_error = _http_error(resp)
                    if attempt >= _MAX_ATTEMPTS:
                        break
                    time.sleep(_backoff_s(resp, attempt - 1))
                    continue
                if resp.status_code >= 400:
                    return None, _http_error(resp)
                try:
                    data = resp.json()
                except json.JSONDecodeError as exc:
                    return None, f"frontier response was not JSON: {exc}"
                if not isinstance(data, dict):
                    return None, "frontier response was not a JSON object"
                return data, None
            return None, last_error
        finally:
            if owns:
                client.close()

    FrontierAdapter._post = _post  # type: ignore[method-assign]


def install_call_counter() -> None:
    if getattr(FrontierAdapter._post, "_ladder_counts", False):
        return
    original = FrontierAdapter._post

    def _post(self, messages, schema_name, schema):  # noqa: ANN001
        note_router_call()
        return original(self, messages, schema_name, schema)

    _post._ladder_counts = True  # type: ignore[attr-defined]
    FrontierAdapter._post = _post  # type: ignore[method-assign]


def install_lenient_json() -> None:
    """Keep the first answers object when a model appends a second JSON value.

    FrontierAdapter._loads_object uses json.loads, which rejects that as
    Extra data and marks every question invalid. This does not invent tokens.
    """
    original = frontier_mod._loads_object

    def _loads_object(text: str) -> dict:
        cleaned = text.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.split("\n", 1)[1] if "\n" in cleaned else cleaned
            if cleaned.rstrip().endswith("```"):
                cleaned = cleaned.rstrip()[: cleaned.rstrip().rfind("```")]
            cleaned = cleaned.strip()
            if cleaned.lower().startswith("json"):
                cleaned = cleaned[4:].strip()
        decoder = json.JSONDecoder()
        objs: list[dict] = []
        idx = 0
        while idx < len(cleaned):
            while idx < len(cleaned) and cleaned[idx].isspace():
                idx += 1
            if idx >= len(cleaned):
                break
            try:
                obj, end = decoder.raw_decode(cleaned, idx)
            except json.JSONDecodeError:
                break
            if isinstance(obj, dict):
                objs.append(obj)
            idx = end
        if not objs:
            return original(text)
        for obj in objs:
            if isinstance(obj.get("answers"), dict):
                return obj
        return objs[0]

    frontier_mod._loads_object = _loads_object


def configure(prompt_only: bool) -> None:
    """Install one shim on the live FrontierAdapter class. Safe to call again."""
    FrontierAdapter._post = _ORIGINAL_POST
    prompt_only = bool(prompt_only or os.environ.get("LADDER_PROMPT_ONLY") == "1")
    if prompt_only:
        os.environ["LADDER_PROMPT_ONLY"] = "1"
    else:
        os.environ.pop("LADDER_PROMPT_ONLY", None)
    install_lenient_json()
    install_ladder_post(prompt_only=prompt_only)
