---
name: graphql
title: GraphQL APIs
triggers: Load when you find `/graphql`, `/gql`, `/api/graphql`, `/v1/graphql`, GraphiQL/Playground/Altair UIs, requests with a `query` field containing `{ … }`, or `__typename` in responses.
attack: reconnaissance, initial-access
owasp: API1, API3, API4, API5, A05
payloads: GraphQL Injection
---

GraphQL collapses the whole API into one endpoint, so authorization must hold on every type, field and resolver. Get the schema, then test authz per resolver, not per URL.

## 1. Schema
- Introspection: `{"query":"{__schema{types{name fields{name args{name}}}}}"}` (or the full introspection query); load it in a local viewer to read it.
- Introspection disabled? Field suggestions still leak names (`{"query":"{usr}"}` → "Did you mean `user`?") — use a suggestion-based enumerator (clairvoyance) or wordlists from the JS bundle, which usually contains the client's queries.
- Note mutations, admin-sounding types/fields, and `node(id:)`/`nodes` interfaces (global id lookup = IDOR candidate).

## 2. Authorization per resolver (API1, API5, API3)
- Object level: query other users' objects by id via every path that reaches them (`user(id:)`, `node(id:)`, nested `order { user { … } }`). Nested traversal often skips the check the top-level query has.
- Function level: call privileged mutations as a low-priv user and unauthenticated.
- Property level: request sensitive fields (`email`, `passwordHash`, `apiKey`, `role`) on types you can reach; add privileged fields to mutation inputs (mass assignment).
- Same matrix and differential as `access-control` — record each break with the paired responses.

## 3. Injection through arguments (A05)
- Arguments feed resolvers that hit SQL/NoSQL/search backends — run `web-sqli` / `server-injection` differentials inside argument values, including filter/`where` objects.

## 4. Abuse of the query language (API4)
- Batching: an array of operations, or aliases (`a1: login(...) a2: login(...)`) — bypasses per-request rate limits (e.g. on login or OTP). Demonstrate with a small batch (5–10) on your own account.
- Depth/complexity: a moderately nested query that should be rejected; observe whether limits exist. Do not send exponential queries — show the missing limit with one step, not an outage.

## 5. Transport
- GET-based queries and `application/x-www-form-urlencoded` acceptance enable CSRF on mutations (→ `browser-trust`); check CORS on the endpoint.
- Debug/IDE exposure (GraphiQL in production, verbose errors with stack traces) → `misconfig-errors`.

## Record & validate
- Each resolver tested → `state_update action=endpoint method=POST url=<endpoint>#<operation> owasp=[API1|API3|API5]`.
- Breaks → `action=vuln` with the matching owasp ids; batching bypass → `API4` (plus `A07` if it defeats login/OTP limits). Evidence = the query, the variables and the response.
