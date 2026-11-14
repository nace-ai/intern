---
name: access-control
title: Access control — IDOR/BOLA, function-level authz, forced browsing
triggers: Load the moment you hold ANY authenticated session or token, see an object id in a URL/body (numeric, UUID, slug, filename), find admin/internal routes, or the app has more than one role or tenant. This is the #1 OWASP category — every authenticated surface gets this pass.
attack: initial-access
owasp: A01, API1, API5
payloads: Insecure Direct Object References; Mass Assignment; Business Logic Errors; Hidden Parameters
---

Access control fails per object and per function, not per app. Test it as a matrix: **identities × objects × actions**, and prove each break with a before/after differential.

## 1. Build the identity matrix
You need at least two principals at the same level (user A, user B) and, if possible, two levels (user, admin) plus **unauthenticated**. Sources: provided creds, self-registration, invited accounts, tokens found during the test. Record each as a `cred`, and hold each one as a `browser_session` (login or import) so every proof can be captured as that identity. With only one account, still test vertical access and unauthenticated access; note the missing horizontal pair as a gap.

## 2. Object-level (IDOR / BOLA — A01, API1)
- Harvest object ids from every response as user A: `/orders/1042`, `{"invoiceId":"…"}`, `?file=report-7.pdf`, GraphQL node ids, ids inside JWT claims or cookies.
- Replay each request as user B (and unauthenticated) with A's id. Also try: sequential neighbours (±1), ids from list endpoints, ids in nested routes (`/users/B/orders/A-order`), ids in the body when the path uses yours (`PUT /me` with `"userId": A`).
- Read **and** write paths are separate checks — a read-protected object can still accept `PUT/PATCH/DELETE` from the wrong user. Test writes only on objects you own or test accounts the operator provided; for others, prove with a harmless field or a read.
- UUIDs are not authorization: if they leak anywhere (lists, emails, logs, other endpoints), they're enumerable.

## 3. Function-level (BFLA — API5)
- Enumerate privileged functions: admin routes in JS bundles, `/admin`, `/internal`, `/manage`, OpenAPI tags, role-gated UI buttons, HTTP methods the docs hide.
- Call each as the low-priv user and unauthenticated. Swap methods (`GET`→`POST/PUT/DELETE`), try `X-HTTP-Method-Override`, `_method=`, and verb case.
- Path-normalization bypasses on gateway rules: `/admin/` vs `/admin`, `/ADMIN`, `/admin;/`, `/admin%2f`, `/./admin`, `//admin`, `/admin.json`, trailing `?`/`#`, and `X-Original-URL`/`X-Rewrite-URL` headers when a proxy sits in front.
- Role/permission parameters the client sends (`role=admin`, `isAdmin:true`, `tenantId`) → see also `api-surface` (mass assignment).

## 4. Forced browsing & multi-tenant
- Unlinked content: backups (`.bak`, `.old`, `~`), exports, `/uploads/`, report/generated-file paths, predictable filenames.
- Tenant isolation: tenant id in host, path, header or claim — swap it while keeping your token.
- Step-skipping in multi-step flows (jump straight to step 3) → `business-logic`.

## 5. Confirm, prove, chain
- **Confirm (differential):** the same request returns 403/404 for a principal that should lack access in at least one case, and 200 with the other principal's data in the broken case. Without that control you may be looking at public data.
- **Impact:** a bounded sample of the other principal's objects (redact PII in the report), or proof of the privileged action on a test object. Picture it with `screenshot`: the same URL with `session=<owner>` and `session=<attacker>` (two images, one finding), `har=true` on the attacker's capture.
- **Chain:** other users' data often carries emails, tokens, API keys, reset links — reuse them; admin functions often expose user management, config, file upload, or integrations → follow each.

## Record & validate
- Every tested route × principal → `state_update action=endpoint` with `auth`, `http_status`, `owasp=[A01]` (plus `API1`/`API5`).
- A break → `state_update action=vuln owasp=[A01, API1|API5]`, advance `depth` as you prove it; evidence = the paired requests/responses.
- Tested clean across the matrix → `state_update action=coverage owasp=[A01, API1, API5] coverage_status=tested notes=<identities × routes covered>`.
