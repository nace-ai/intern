"""ResolveAI — autonomous customer resolution with Drex as the decision engine.

Pipeline: triage -> look up simulated records -> apply policy -> ask Drex for
one structured decision -> display decision + simulated action.

Run:  streamlit run app.py
"""
from __future__ import annotations

import os

import pandas as pd
import streamlit as st

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

from data import BENCHMARK_CASES, CUSTOMERS, ORDERS, POLICY
from drex import ALLOWED_ACTIONS, DREX_MODEL, ask_drex

st.set_page_config(page_title="ResolveAI", page_icon="🛟", layout="wide")


# ----------------------------------------------------------------- pipeline
TRIAGE_KEYWORDS = {
    "damaged": ["damage", "damaged", "crack", "broken", "defect", "scratch", "burr"],
    "delayed": ["late", "delay", "still", "not arrived", "has not arrived", "slow", "tracking"],
    "refund": ["refund", "money back", "return", "exchange"],
}


def triage(request: str) -> str:
    text = request.lower()
    for category, words in TRIAGE_KEYWORDS.items():
        if any(w in text for w in words):
            return category
    return "general"


def first_matching_rule(category: str, customer: dict, order: dict) -> dict:
    within_window = order["days_since_delivery"] <= order["return_window_days"]
    ctx = {
        "category": category,
        "within_return_window": within_window,
        "paid": order["paid"],
        "delivery_status": order["delivery_status"],
        "order_total_gte": order["total"],
        "customer_open_disputes_gte": customer["open_disputes"],
    }
    for rule in POLICY["rules"]:
        conds = rule["conditions"]
        if all(
            (
                (ctx.get(k) in v)
                if isinstance(v, list)
                else (ctx.get(k) is not None and ctx.get(k) >= v)
                if k.endswith("_gte")
                else (ctx.get(k) == v)
            )
            for k, v in conds.items()
        ):
            return rule
    return POLICY["rules"][-1]


def build_evidence(category: str, customer: dict, order: dict, rule: dict) -> list[str]:
    return [
        f"Triage category: {category}",
        f"Customer {customer['id']} ({customer['tier']}): "
        f"LTV ${customer['lifetime_value']}, open disputes: {customer['open_disputes']}",
        f"Order {order['id']}: {', '.join(order['items'])}, total ${order['total']:.2f}, "
        f"paid: {order['paid']}",
        f"Delivery: {order['delivery_status']}, "
        f"{order['days_since_delivery']} days after a {order['return_window_days']}-day window",
        f"First matching policy rule: {rule['id']} {rule['name']} -> {rule['action']}",
    ]


def resolve_case(request: str, customer_id: str, order_id: str) -> dict:
    customer = CUSTOMERS.get(customer_id)
    order = ORDERS.get(order_id)
    if customer is None or order is None:
        return {"error": f"Unknown customer {customer_id} or order {order_id}."}

    category = triage(request)
    rule = first_matching_rule(category, customer, order)
    evidence = build_evidence(category, customer, order, rule)
    case = {
        "request": request,
        "triage": category,
        "records": {"customer": customer, "order": order},
        "policy": POLICY,
        "context": {
            "within_return_window": order["days_since_delivery"]
            <= order["return_window_days"],
            "matched_rule": rule,
        },
        "evidence": evidence,
    }
    decision = ask_drex(case)
    return {
        "category": category,
        "customer": customer,
        "order": order,
        "rule": rule,
        "evidence": evidence,
        "decision": decision,
    }


def simulated_action(action: str, order: dict) -> str:
    return {
        "refund": f"[SIMULATED] Refund ${order['total']:.2f} to the original payment "
        f"method for order {order['id']} (no real payment call is made).",
        "replacement": f"[SIMULATED] Create replacement shipment for "
        f"{', '.join(order['items'])} under order {order['id']} "
        "(no real shipping call is made).",
        "store_credit": f"[SIMULATED] Issue ${order['total'] * 0.5:.2f} store credit "
        f"(50%) to the account for order {order['id']} (no real wallet call is made).",
        "human_review": f"[SIMULATED] Create a support ticket for order {order['id']} "
        "and route it to a human supervisor (no real ticketing call is made).",
    }[action]


# ---------------------------------------------------------------------- UI
st.title("🛟 ResolveAI — Drex decision engine")
st.caption("Triage → simulated records → policy → one structured decision from Drex.")

api_live = bool(os.environ.get("NACE_API_KEY"))
st.sidebar.header("Configuration")
engine = f"Drex API ({DREX_MODEL})" if api_live else "offline rule fallback"
st.sidebar.markdown(f"Drex engine: **{engine}**")
if not api_live:
    st.sidebar.info("Set `NACE_API_KEY` in `.env` to use the Nace Drex API. "
                    "Until then, decisions come from the deterministic policy fallback.")
st.sidebar.divider()
mode = st.sidebar.radio("Mode", ["Live case", "Benchmark"])

if mode == "Live case":
    st.header("1 · Customer request")
    request = st.text_area(
        "What does the customer say?",
        placeholder="e.g. The espresso grinder arrived with a cracked burr...",
    )
    c1, c2 = st.columns(2)
    customer_id = c1.selectbox("Customer", list(CUSTOMERS),
                               format_func=lambda i: f"{i} — {CUSTOMERS[i]['name']}")
    order_id = c2.selectbox("Order", list(ORDERS),
                            format_func=lambda i: f"{i} — {', '.join(ORDERS[i]['items'])}")

    if st.button("Resolve case", type="primary", disabled=not request.strip()):
        result = st.session_state.setdefault("results", {})
        result[(customer_id, order_id, request)] = resolve_case(
            request, customer_id, order_id
        )

    for key, result in st.session_state.get("results", {}).items():
        if "error" in result:
            st.error(result["error"])
            continue
        st.subheader(f"Case: {key[2][:60]}…")

        c1, c2, c3 = st.columns(3)
        c1.metric("Triage", result["category"])
        d = result["decision"]
        c2.metric("Policy rule", result["rule"]["id"])
        c3.metric("Confidence", f"{d['confidence']:.0%}")

        with st.expander("Simulated records (step 2)", expanded=False):
            st.json(
                {
                    "customer": result.get("customer"),
                    "order": result.get("order"),
                }
            )
        with st.expander("Policy context (step 3)", expanded=False):
            st.write(f"**Rule {result['rule']['id']} — {result['rule']['name']}**")
            st.write(result["rule"]["note"])

        st.markdown("#### Decision (Drex)")
        col_a, col_b = st.columns([1, 3])
        with col_a:
            st.success(f"**{d['action'].replace('_', ' ')}**", icon="✅")
            st.write(f"Model: `{d.get('model', 'drex')}`")
            if d.get("probabilities"):
                st.caption(
                    "Probabilities — "
                    + ", ".join(
                        f"{k} {v:.0%}"
                        for k, v in sorted(
                            d["probabilities"].items(), key=lambda kv: kv[1], reverse=True
                        )
                    )
                )
            if d.get("usage"):
                st.caption(
                    "Usage — " + ", ".join(f"{k}: {v}" for k, v in d["usage"].items())
                )
            if d.get("request_id"):
                st.caption(f"Request ID — `{d['request_id']}`")
        with col_b:
            if d.get("answer"):
                st.caption(f"**Drex answer**\n\n{d['answer']}")
            st.markdown("**Reasons**\n" + "\n".join(f"- {r}" for r in d["reasons"]))
            st.markdown("**Evidence**\n" + "\n".join(f"- {e}" for e in d["evidence"]))

        st.info("Simulated action (step 5)\n\n" + simulated_action(d["action"], result["order"]),
                icon="🔁")
        st.divider()

else:
    st.header("Benchmark mode")
    st.caption("Runs each predefined case through the full pipeline and checks "
               "Drex's action against the expected policy outcome.")
    if st.button("Run all benchmark cases", type="primary"):
        rows = []
        progress = st.progress(0.0)
        for i, case in enumerate(BENCHMARK_CASES):
            result = resolve_case(case["request"], case["customer_id"], case["order_id"])
            d = result.get("decision", {})
            rows.append({
                "Case": case["id"],
                "Title": case["title"],
                "Expected": case["expected_action"],
                "Drex action": d.get("action", "error"),
                "Confidence": d.get("confidence", 0),
                "Pass": d.get("action") == case["expected_action"],
            })
            progress.progress((i + 1) / len(BENCHMARK_CASES))
        st.session_state["bench_rows"] = rows
        progress.empty()

    if st.session_state.get("bench_rows"):
        rows = st.session_state["bench_rows"]
        st.dataframe(
            pd.DataFrame(rows).style.map(
                lambda v: "color: green" if v else "color: red", subset=["Pass"]
            ),
            use_container_width=True,
            hide_index=True,
        )
        passed = sum(r["Pass"] for r in rows)
        st.success(f"{passed}/{len(rows)} cases pass.")
        with st.expander("Decisions, reasons, evidence and simulated actions"):
            for r in rows:
                case = next(c for c in BENCHMARK_CASES if c["id"] == r["Case"])
                result = resolve_case(case["request"], case["customer_id"], case["order_id"])
                d = result["decision"]
                st.markdown(f"##### {r['Case']} — {r['Title']}")
                st.write(f"**{d['action']}** (confidence {d['confidence']:.0%})")
                st.write("\n".join(f"- {x}" for x in d["reasons"]))
                if d.get("usage"):
                    st.caption(
                        "Usage — " + ", ".join(f"{k}: {v}" for k, v in d["usage"].items())
                    )
                if d.get("request_id"):
                    st.caption(f"Request ID — `{d['request_id']}`")
                st.write(simulated_action(d["action"], result["order"]))
                st.divider()
