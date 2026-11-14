---
name: api-surface
title: API surface — inventory, mass assignment, data exposure, unsafe consumption
triggers: Load for any REST/JSON API (including the one an SPA or mobile app talks to): when you find `/api/`, OpenAPI/Swagger, versioned paths (`/v1`, `/v2`), JSON bodies that map to objects, or responses with more fields than the UI shows.
attack: reconnaissance, discovery
owasp: API3, API9, API4, API10, A03
payloads: Hidden Parameters; HTTP Parameter Pollution; Mass Assignment; API Key Leaks; Web Sockets
---

APIs leak through breadth: forgotten versions, fields the UI hides, properties the client shouldn't set. Inventory first, then test property-level authorization on every object type.

## 1. Inventory (API9)
- Tools first: `js_analyze` (routes, hosts, secrets from bundles and source maps), `api_discover source=crawl` as each `browser_session` identity, `source=openapi` for any spec; then `api_probe` for the method × identity matrix.
- Specs: `/openapi.json`, `/swagger.json`, `/v2/api-docs`, `/v3/api-docs`, `/swagger-ui/`, `/api-docs`, `/.well-known/`, GraphQL endpoints (→ `graphql`).
- JS bundles and mobile apps: extract every route string and base URL.
- Versions and environments: `/v1` next to `/v2`, `/beta`, `/internal`, `/legacy`, `api-dev.` / `staging.` hosts — older versions often lack newer checks. Re-run your strongest tests against each version.
- Record every route with `state_update action=endpoint owasp=[API9]` — this *is* the inventory the report shows.

## 2. Excessive data exposure (API3 — read side)
- Compare the raw JSON with what the UI renders: hashes, tokens, internal ids, other users' emails, PII, feature flags, `isAdmin`, cost/price internals.
- List endpoints that return full objects for many records (user search, autocomplete, export).
- Proof = the response with the sensitive fields (redact in the report).

## 3. Mass assignment (API3 — write side)
- For each update/create endpoint, add properties the UI never sends: `role`, `isAdmin`, `permissions`, `verified`, `balance`, `price`, `ownerId`, `tenantId`, `createdAt`. Take names from read responses and the spec.
- Try nested objects (`{"user":{"role":"admin"}}`) and alternate casing (`is_admin`, `IsAdmin`).
- **Confirm** by reading the object back (or by the new capability working), on your own test account.

## 4. Resource consumption (API4)
- Missing pagination caps (`limit=`/`per_page=` one order of magnitude up), unbounded batch arrays, expensive filters/sorts, big uploads, no rate limit on costly endpoints (SMS, email, PDF generation). Measure one step up and stop — demonstrate the missing limit, not an outage.

## 5. Unsafe consumption & supply chain (API10, A03)
- Data the API pulls from third parties/webhooks and trusts: inject via the third-party side you control (your profile on the integrated service, a webhook payload you can forge without a signature) and see it processed unvalidated.
- Webhook endpoints without signature verification → forge an event for your own test account.
- Components: versions from headers, JS bundles (`/static/js/*.js` banners), `package.json`/lockfiles exposed, SBOMs. Map each version to advisories and test applicability (per the doctrine: a version is not a finding).

## Record & validate
- Exposure/mass assignment → `state_update action=vuln owasp=[API3]`; old/undocumented API → `API9`; limits → `API4`; trusted third-party data or unsigned webhooks → `API10`; vulnerable component with a proven trigger → `A03`.
- When the API is fully inventoried and these checks are clean → `action=coverage owasp=[API3, API9] coverage_status=tested notes=<object types / versions covered>`.
