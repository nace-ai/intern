---
name: business-logic
title: Business logic, workflows and race conditions
triggers: Load when the app has money, quotas, inventory, coupons, credits, approvals, multi-step workflows (checkout, onboarding, KYC, invitations), limits per user, or any "only once" action. Scanners find none of this — it needs understanding of what the app is for.
attack: initial-access, impact
owasp: A06, API6, API4
payloads: Business Logic Errors; Race Condition; Type Juggling; Insecure Direct Object References
---

Logic flaws are design flaws: every request is valid, the sequence or values aren't. Start from the business rules, then try to break each one. Operate only on test accounts and test data; if a test would move real money or stock, get the operator's go-ahead first.

## 1. Write down the rules
From the UI, docs and API: what may a user do, how often, in what order, with what values? ("one coupon per order", "approval before payout", "max 3 invites", "price set by server"). Each rule is a test case.

## 2. Value tampering
- Prices, totals, discounts, quantities, currency, fees sent by the client: negative numbers, zero, decimals (`0.001`), huge values (overflow), other currencies, swapped product ids with your own price.
- Server recomputes? Compare the stored order/invoice with what you sent.

## 3. Workflow & state
- Skip steps (call the final step directly), repeat steps (apply a coupon twice, confirm twice), go backwards (edit a cart after payment), mix steps from two parallel flows (two carts, one payment).
- State that the client controls: `status=approved`, `step=4`, `verified=true` in requests or hidden fields.

## 4. Race conditions (API4/API6)
- Target "only once" or balance-checked actions: redeem coupon, withdraw, vote, invite, claim a username.
- Send N identical requests at once (start small: 10–20 in parallel via a bounded script or curl `--parallel`, single-packet style if possible) and compare the outcome with the rule (two redemptions, balance below zero).
- One clear double-spend on a test account is the proof. Don't hammer production; this is not a load test.

## 5. Limits and sensitive flows (API6, API4)
- Flows an attacker would automate: account creation, reservation/booking, purchase of limited items, SMS/email sending, password reset. Is there any per-user/IP/device limit? Measure with a small, bounded number of requests and stop once the absence of a limit is clear.
- Expensive operations the user controls: page size (`limit=100000`), export sizes, search complexity, upload size, batch endpoints — test one step up, observe time/size, never push to exhaustion.

## Prove & record
- **Impact:** the business consequence on test data — the extra coupon applied, the order at the wrong price, the action done without approval, N SMS sent where 1 was allowed. Evidence = request sequence + resulting state (screenshot/response).
- `state_update action=vuln owasp=[A06]` (sensitive automated flow: `API6`; missing resource limits: `API4`). Severity = demonstrated business impact.
- Rules tested and enforced → `action=coverage owasp=[A06, API6, API4] coverage_status=tested notes=<rules tested>`; no such flows in the app → `coverage_status=na` with why.
