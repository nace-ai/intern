"""Drex decision client: typed questions in, probabilities out. Every decision of a trial goes through here.

Real HTTP (urllib) only when NACE_API_KEY or DREX_API_KEY is set and the run is not simulated.
Otherwise a deterministic seeded simulation answers, and every record says `simulated: True`.

Design rules this module enforces:
- many questions per request (1-512), one shared `state`, knowledge goes in the state;
- `choice` questions are asked twice, the second time with the options in a seeded shuffled order;
  if the top option changes, `stable` is False and the decision routes to a person;
- every request body and raw response is saved to runs/<run_id>/requests/<req>.json (the audit record,
  written once, never overwritten; the API key is never written);
- price is per input token (config.USD_PER_M_INPUT): a request costs its `usage.input_tokens`, split evenly
  across its questions, so asking many questions about one state is cheaper than one request each;
- after every real call the response's `model` is compared with the requested id (a mistyped or retired id is
  answered by another model, not an error): a difference is flagged as `model_answered`;
- hard caps (config.MAX_REQUESTS, config.MAX_USD) abort the run.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import mimetypes
import sys
import time
from pathlib import Path

from . import config, net, sim

TYPES = ("noul", "choice", "score")


class DrexError(RuntimeError):
    """The decision API failed, answered in a shape we cannot read, or a question was invalid."""


class BudgetExceeded(DrexError):
    """The run hit the request or dollar cap."""


def api_key() -> str | None:
    return config.env_key("NACE_API_KEY", "DREX_API_KEY")


def has_key() -> bool:
    return api_key() is not None


def resolve_simulate(simulate: bool | None) -> bool:
    """None means "simulate unless a key is set". Asking for a real run without a key is an error."""
    if simulate is None:
        return not has_key()
    if not simulate and not has_key():
        raise DrexError("no API key: set NACE_API_KEY or DREX_API_KEY, or pass --simulate")
    return bool(simulate)


def est_tokens(text: str) -> int:
    """Rough input-token count (4 characters per token); used for simulated runs and --estimate."""
    return max(1, round(len(text) / 4))


# ---- confidence (ported from tad-sim.js) ----
def conf_noul(p: float) -> float:
    return abs(2 * p - 1)


def conf_choice(d: list[float]) -> float:
    return (max(d) - 1 / len(d)) / (1 - 1 / len(d))


def conf_score(d: list[float]) -> float:
    mu = sum(v * i for i, v in enumerate(d))
    return 1 - sum(v * abs(i - mu) for i, v in enumerate(d)) / (len(d) - 1)


# ---- question helpers ----
def labels_of(spec: dict) -> list[str]:
    return spec.get("options") or spec.get("levels") or []


def _validate(questions: dict) -> None:
    if not 1 <= len(questions) <= config.MAX_QUESTIONS_PER_REQUEST:
        raise DrexError(f"a request carries 1-{config.MAX_QUESTIONS_PER_REQUEST} questions, got {len(questions)}")
    for qid, s in questions.items():
        if s.get("type") not in TYPES or not s.get("instructions"):
            raise DrexError(f"question {qid!r}: needs a type {TYPES} and instructions")
        if s["type"] != "noul" and not 2 <= len(labels_of(s)) <= config.MAX_OPTIONS:
            raise DrexError(f"question {qid!r}: choice and score take 2-{config.MAX_OPTIONS} options")


def _wire(spec: dict) -> dict:
    """Body form of a question, as POST /v1/systemone takes it: a choice's `criteria` is an object of
    label to description (null here), a score's is the array of levels from low to high."""
    out = {"type": spec["type"], "instructions": spec["instructions"]}
    if spec["type"] == "choice":
        out["criteria"] = {label: None for label in spec["options"]}
    elif spec["type"] == "score":
        out["criteria"] = list(spec["levels"])
    return out


def from_wire(wire: dict, hint: dict | None = None) -> dict:
    """Inverse of `_wire`, used by replay."""
    spec = {"type": wire["type"], "instructions": wire["instructions"]}
    if "criteria" in wire:
        spec["options" if wire["type"] == "choice" else "levels"] = list(wire["criteria"])  # dict keys or list items
    if hint:
        spec["_sim"] = hint
    return spec


# ---- reading answers. The API returns {"type": "noul", "noul": p}, {"type": "choice", "probabilities": {label: p}}
# and {"type": "score", "probabilities": {"0": p, ...}}; the older guesses stay as a fallback. ----
def _num(x) -> float | None:
    return float(x) if isinstance(x, (int, float)) and not isinstance(x, bool) else None


def _yes_prob(ans) -> float | None:
    n = _num(ans)
    if n is not None:
        return n
    if isinstance(ans, dict):
        for k in ("noul", "p", "p_yes", "probability", "prob", "yes"):
            if _num(ans.get(k)) is not None:
                return _num(ans[k])
        for k in ("probabilities", "distribution", "probs"):
            if k in ans:
                return _yes_prob(ans[k])
    if isinstance(ans, list) and ans and _num(ans[0]) is not None:
        return _num(ans[0])  # assumed order [yes, no]
    return None


def _values(ans, labels: list[str]) -> list[float] | None:
    if isinstance(ans, dict):
        for k in ("probabilities", "distribution", "probs", "scores"):
            if k in ans:
                ans = ans[k]
                break
    if isinstance(ans, dict):
        if all(lab in ans for lab in labels):
            return [_num(ans[lab]) for lab in labels]
        if all(str(i) in ans for i in range(len(labels))):
            return [_num(ans[str(i)]) for i in range(len(labels))]
        return None
    if isinstance(ans, list) and len(ans) == len(labels):
        if all(_num(v) is not None for v in ans):
            return [float(v) for v in ans]
        if all(isinstance(v, dict) for v in ans):
            by = {str(v.get("label") or v.get("option") or v.get("name")): _yes_prob(v) for v in ans}
            if all(lab in by and by[lab] is not None for lab in labels):
                return [by[lab] for lab in labels]
    return None


def normalize(spec: dict, ans) -> dict:
    """Turn one raw answer into {"p": float} or {"dist": [floats summing to 1]}."""
    if spec["type"] == "noul":
        p = _yes_prob(ans)
        if p is None:
            raise DrexError(f"cannot read a yes/no probability from {json.dumps(ans)[:120]}")
        return {"p": min(1.0, max(0.0, p))}
    vals = _values(ans, labels_of(spec))
    if vals is None or any(v is None for v in vals):
        raise DrexError(f"cannot read {len(labels_of(spec))} probabilities from {json.dumps(ans)[:120]}")
    vals = [max(0.0, v) for v in vals]
    total = sum(vals)
    if total <= 0:
        raise DrexError("all probabilities are zero")
    return {"dist": [v / total for v in vals]}


def _usage_tokens(usage) -> int | None:
    """Input tokens from a usage object. Unconfirmed key names: tries the common ones."""
    if not isinstance(usage, dict):
        return None
    for k in ("input_tokens", "prompt_tokens", "total_tokens", "tokens"):
        if _num(usage.get(k)) is not None:
            return int(usage[k])
    return None


def _reported_cost(resp: dict) -> float | None:
    """Cost in USD if the response reports one. Unconfirmed key names: looks in `usage` then at top level."""
    for box in (resp.get("usage"), resp):
        if isinstance(box, dict):
            for k in ("cost", "cost_usd", "usd", "total_cost"):
                if _num(box.get(k)) is not None:
                    return float(box[k])
    return None


def _shuffled_order(qid: str, digest: str, n: int) -> list[int]:
    """Seeded permutation of range(n) that is never the identity."""
    r = sim.rng(sim.fnv(f"{qid}|{digest}|order"))
    order = list(range(n))
    for i in range(n - 1, 0, -1):
        j = int(r() * (i + 1))
        order[i], order[j] = order[j], order[i]
    return order if order != list(range(n)) else order[1:] + order[:1]


def _top(d: list[float]) -> int:
    return d.index(max(d))


def is_challenge(text: str, status: int | None = 403) -> bool:
    """True for the bot check a Nace host can put in front of API clients (HTTP 403, code "challenge")."""
    return status == 403 and "challenge" in text.lower()


def post(body: dict) -> tuple[dict, int]:
    """POST one request to Drex and return (response, milliseconds). When the host answers with a bot check
    instead of the API, the request goes to the other documented Nace host, and the process stays on it."""
    while True:
        try:
            _, resp, ms = net.json_call("POST", config.SYSTEMONE_URL, body, {"Authorization": "Bearer " + str(api_key())})
            return resp, ms
        except net.NetError as e:
            if not is_challenge(str(e), e.status) or not config.use_other_nace_host():
                raise
            print(f"note: bot check on the Nace host; now using {config.NACE_BASE}", file=sys.stderr)


def live_base() -> str:
    """The Nace host that answers API clients right now, checked with a free GET /v1/models. Moves this process
    to the other documented host when the first one answers with a bot check."""
    while True:
        try:
            net.json_call("GET", config.NACE_BASE + "/v1/models", None, {"Authorization": "Bearer " + str(api_key())}, tries=1)
            return config.NACE_BASE
        except net.NetError as e:
            if not is_challenge(str(e), e.status) or not config.use_other_nace_host():
                raise


class Session:
    """One run's Drex client: counts requests and dollars, saves the audit record, enforces the caps."""

    def __init__(self, run_dir: Path | str | None = None, simulate: bool | None = None,
                 max_requests: int = config.MAX_REQUESTS, max_usd: float = config.MAX_USD):
        self.simulate = resolve_simulate(simulate)
        self.run_dir = Path(run_dir) if run_dir else None
        self.max_requests, self.max_usd = max_requests, max_usd
        self.requests, self.usd, self.warnings = 0, 0.0, []
        self.ndi_usd = 0.0  # what the document jobs of this run cost (engine.ndi adds to it)
        if self.run_dir:
            (self.run_dir / "requests").mkdir(parents=True, exist_ok=True)

    # -- public --
    def decide(self, state: str, questions: dict[str, dict], model: str = config.MODEL_TEXT,
               images: list | None = None, force_sim: bool = False) -> dict[str, dict]:
        """Answer every question. Returns {qid: record}; a record holds type, p or dist, options/levels,
        raw, conf, stable, route, model, req, tok, usd, ms and simulated."""
        _validate(questions)
        sim_mode = self.simulate or force_sim
        digest = hashlib.sha256(state.encode("utf-8")).hexdigest()[:16]
        first = self._ask(state, questions, model, images, sim_mode, digest)
        recs: dict[str, dict] = {}
        for qid, spec in questions.items():
            body = first["answers"][qid]
            recs[qid] = {"type": spec["type"], **body, **({"options": spec["options"]} if "options" in spec else {}),
                         **({"levels": spec["levels"]} if "levels" in spec else {}), "raw": first["raw"][qid],
                         "model": model, "req": first["req"], "tok": first["tok"][qid], "usd": first["usd"][qid],
                         "ms": first["ms"][qid], "simulated": sim_mode, "stable": True}
            for k in ("api_req", "model_answered", "cost_raw"):
                if first[k] is not None:
                    recs[qid][k] = first[k]
        self._reask(state, questions, recs, model, sim_mode, digest)
        for rec in recs.values():
            conf = {"noul": lambda: conf_noul(rec.get("p", 0.5)), "choice": lambda: conf_choice(rec.get("dist", [])),
                    "score": lambda: conf_score(rec.get("dist", []))}[rec["type"]]()
            rec["conf"] = conf
            rec["route"] = "person" if conf < config.CONF_MIN or (config.ROUTE_UNSTABLE_TO_PERSON and not rec["stable"]) else "auto"
        return recs

    # -- internals --
    def _reask(self, state, questions, recs, model, sim_mode, digest) -> None:
        """Order-stability check: ask the choice questions again with options in a shuffled order."""
        ids = [q for q, s in questions.items() if s["type"] == "choice"]
        if not ids:
            return
        second, orders = {}, {}
        for qid in ids:
            spec, d = questions[qid], recs[qid]["dist"]
            orders[qid] = _shuffled_order(qid, digest, len(d))
            q2 = {**spec, "options": [spec["options"][i] for i in orders[qid]]}
            if sim_mode:  # simulated second look: mostly agrees, sometimes the runner-up wins (rates from tad-sim.js)
                flip = sim.rng(sim.fnv(f"{qid}|{digest}|stable"))() <= (0.13 if len(d) >= 6 else 0.22 if max(d) < 0.6 else 0.03)
                d2 = list(d)
                if flip:
                    runner = sorted(range(len(d)), key=lambda i: -d[i])[1]
                    d2[_top(d)], d2[runner] = d2[runner], d2[_top(d)]
                q2["_sim"] = {"dist": [d2[i] for i in orders[qid]]}
            second[qid] = q2
        reply = self._ask(state, second, model, None, sim_mode, digest)
        for qid in ids:
            shuffled = reply["answers"][qid]["dist"]
            back = [0.0] * len(shuffled)
            for j, i in enumerate(orders[qid]):
                back[i] = shuffled[j]
            rec = recs[qid]
            rec["stable"] = _top(back) == _top(rec["dist"])
            rec.update(req2=reply["req"], raw2=reply["raw"][qid], tok=rec["tok"] + reply["tok"][qid],
                       usd2=reply["usd"][qid], ms=rec["ms"] + reply["ms"][qid])  # re-ask is billed too: usd2
            if reply["model_answered"] is not None:
                rec["model_answered"] = reply["model_answered"]

    def _charge_check(self) -> None:
        if self.requests >= self.max_requests:
            raise BudgetExceeded(f"aborted: request cap of {self.max_requests} reached")
        if self.usd > self.max_usd:
            raise BudgetExceeded(f"aborted: spend ${self.usd:.4f} is above the ${self.max_usd:.2f} cap")

    def _ask(self, state, questions, model, images, sim_mode, digest) -> dict:
        self._charge_check()
        self.requests += 1
        req = f"req_{self.requests:03d}"
        body = {"model": model, "state": state, "questions": {q: _wire(s) for q, s in questions.items()}}
        image_paths = [str(p) for p in images or []]
        answered = None
        if sim_mode:
            ms_by_q = self._sim_ms(questions, model, digest)
            resp = self._simulate(state, questions, model, digest, req, ms_by_q)
            ms_total = sum(ms_by_q.values())
        else:
            if images:
                body["images"] = [_data_url(p) for p in images]
            resp, ms_total = post(body)
            if resp.get("model") and resp["model"] != model:
                answered = str(resp["model"])
                self.warnings.append(f"{req}: asked {model}, answered {answered}")
                if len(self.warnings) == 1:
                    print(f"warning: asked {model} but {answered} answered ({req}); see summary.json for every mismatch",
                          file=sys.stderr)
            ms_by_q = {q: ms_total // len(questions) for q in questions}
        answers = resp.get("answers") if isinstance(resp, dict) else None
        if isinstance(answers, list):
            answers = {a.get("id"): a for a in answers if isinstance(a, dict)}
        if not isinstance(answers, dict) or any(q not in answers for q in questions):
            raise DrexError(f"{req}: response has no `answers` entry for every question")
        norm = {q: normalize(s, answers[q]) for q, s in questions.items()}
        marg = {q: est_tokens(s["instructions"] + " ".join(labels_of(s))) for q, s in questions.items()}
        total_tok = _usage_tokens(resp.get("usage")) or sum(marg.values()) + est_tokens(state)
        head = next(iter(questions))
        tok = {**marg, head: max(1, total_tok - sum(v for q, v in marg.items() if q != head))}
        cost = None if sim_mode else _reported_cost(resp)
        billed = cost if cost is not None else total_tok * config.usd_per_m(answered or model) / 1e6
        usd = {q: billed / len(questions) for q in questions}
        self.usd += sum(usd.values())
        self._save(req, {"req": req, "url": config.SYSTEMONE_URL, "simulated": sim_mode, "model": model,
                         "request": {**body, **({"images": [Path(p).name for p in images]} if images else {})},
                         "image_paths": image_paths, "response": resp, "ms": ms_total, "tok": sum(tok.values()),
                         "cost_raw": cost, "model_answered": answered, "hints": {q: s["_sim"] for q, s in questions.items() if "_sim" in s} if sim_mode else {}})
        return {"req": req, "answers": norm, "raw": answers, "tok": tok, "usd": usd, "ms": ms_by_q,
                "api_req": resp.get("request_id") if not sim_mode else None, "model_answered": answered,
                "cost_raw": cost}

    @staticmethod
    def _sim_ms(questions, model, digest) -> dict[str, int]:
        r = sim.rng(sim.fnv(f"{digest}|{model}|{'|'.join(questions)}"))
        out = {q: round(18 + r() * 14) for q in questions}
        out[next(iter(questions))] = round((183 if model == config.MODEL_IMAGE else 151) * (0.78 + r() * 0.5))
        return out

    @staticmethod
    def _simulate(state, questions, model, digest, req, ms_by_q) -> dict:
        """A response in the API's own shape, with seeded numbers in place of the model's."""
        answers = {}
        for qid, s in questions.items():
            r = sim.rng(sim.fnv(f"{model}|{qid}|{digest}|{s['instructions']}"))
            hint, labels = s.get("_sim") or {}, labels_of(s)
            n = len(labels)
            if s["type"] == "noul":
                p = hint.get("p", sim.clamp(0.5 + (r() - 0.5) * 1.4, 0.02, 0.98))
                answers[qid] = {"type": "noul", "noul": p, "confidence": max(p, 1 - p)}
            elif s["type"] == "choice":
                d = hint.get("dist") or sim.pick(n, 0.5 + r() * 0.3, r)
                answers[qid] = {"type": "choice", "choice": labels[_top(d)], "confidence": max(d),
                                "probabilities": dict(zip(labels, d))}
            else:
                d = hint.get("dist") or sim.dist(n, r() * (n - 1), r, 0.8)
                answers[qid] = {"type": "score", "score": sum(i * v for i, v in enumerate(d)), "confidence": max(d),
                                "legend": list(labels), "probabilities": {str(i): v for i, v in enumerate(d)}}
        return {"model": model, "answers": answers, "usage": {"input_tokens": est_tokens(state) + sum(
            est_tokens(s["instructions"] + " ".join(labels_of(s))) for s in questions.values())},
            "evaluation_time_ms": sum(ms_by_q.values()), "request_id": req, "simulated": True}

    def _save(self, req: str, doc: dict) -> None:
        """Write the audit record once ('x' mode: an existing capture is never overwritten)."""
        if self.run_dir:
            with open(self.run_dir / "requests" / f"{req}.json", "x", encoding="utf-8") as fh:
                json.dump(doc, fh, indent=1)


def _data_url(path) -> str:
    mime = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
    return f"data:{mime};base64," + base64.b64encode(Path(path).read_bytes()).decode("ascii")


def decide(state: str, questions: dict[str, dict], model: str = config.MODEL_TEXT, images: list | None = None,
           session: Session | None = None) -> dict[str, dict]:
    """Module-level shortcut: `Session().decide(...)` unless a session (with its caps and audit folder) is passed."""
    return (session or Session()).decide(state, questions, model, images)


def probe() -> int:
    """Send ONE tiny noul question and print the raw JSON, so the owner can confirm the response shape."""
    body = {"model": config.MODEL_TEXT, "state": "The invoice total is $120.",
            "questions": {"probe": {"type": "noul", "instructions": "Is the invoice total above $100?"}}}
    if not has_key():
        print(f"No NACE_API_KEY or DREX_API_KEY set, nothing sent. Would POST {config.SYSTEMONE_URL} with:")
        print(json.dumps(body, indent=2))
        return 0
    resp, ms = post(body)
    print(json.dumps(resp, indent=2))
    out = config.RUNS_DIR / f"probe-{time.strftime('%Y%m%d-%H%M%S')}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "x", encoding="utf-8") as fh:
        json.dump({"request": body, "response": resp, "ms": ms}, fh, indent=1)
    print(f"raw response saved to {out} ({ms} ms)", file=sys.stderr)
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python3 -m engine.drex", description="Drex client helper")
    ap.add_argument("--probe", action="store_true", help="send one tiny question and print the raw response")
    args = ap.parse_args(argv)
    if args.probe:
        try:
            return probe()
        except net.NetError as e:
            print(f"probe failed: {e}", file=sys.stderr)
            if e.status in (404, 405):  # the docs name two hosts; see config._nace_base
                print("try the other documented host: NACE_API_BASE=https://drex.nace.ai python3 -m engine.drex --probe",
                      file=sys.stderr)
            return 1
    ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
