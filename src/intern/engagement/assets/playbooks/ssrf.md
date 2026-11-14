---
name: ssrf
title: Server-side request forgery
triggers: Load when the server fetches something you influence — URL/host/callback/webhook parameters, image or PDF renderers, link previews, import-from-URL, XML/SVG with external refs, OAuth/OIDC discovery URLs, `jku`/`x5u`, or any proxy/integration feature.
attack: initial-access, discovery
owasp: A01, API7
payloads: Server Side Request Forgery; DNS Rebinding
---

SSRF is proven by what the server reaches *that you can't*: internal services, cloud metadata, localhost-only admin ports. Reflection of your URL is not proof.

## 1. Find fetchers
Parameters named `url`, `uri`, `link`, `src`, `dest`, `redirect`, `callback`, `webhook`, `feed`, `image`, `avatar`, `import`, `endpoint`; file importers; HTML→PDF/image renderers (they run a browser server-side); document converters; webhooks you can register.

## 2. Confirm the server makes the request
- **Out-of-band:** point it at an `oast` payload (`oast action=payload tag=ssrf-<param> vuln_id=<vN>`), trigger it, then `oast action=poll tag=ssrf-<param> wait_s=30`. The inbound HTTP hit from the target's egress IP (source IP, User-Agent, headers) is the confirmation; a DNS-only hit proves the lookup, not the fetch. Attach it with `oast action=attach`.
- **Differential (blind):** compare responses/timings for a reachable vs unreachable vs closed-port target (`http://127.0.0.1:22/` vs `:1/`); distinct errors or latencies reveal internal reachability.

## 3. Reach inside
- Loopback and internal: `127.0.0.1`, `localhost`, `[::1]`, `0.0.0.0`, RFC1918 ranges, internal hostnames seen in recon/JS/errors.
- Cloud metadata: AWS `http://169.254.169.254/latest/meta-data/` (IMDSv2 needs a PUT token — note if it blocks), GCP `metadata.google.internal` (needs `Metadata-Flavor: Google`), Azure `169.254.169.254/metadata/instance?api-version=2021-02-01` (needs `Metadata: true`). Header requirements tell you how much control you have.
- Filter bypasses: alternate IP encodings (`2130706433`, `0x7f000001`, `127.1`), IPv6-mapped (`[::ffff:127.0.0.1]`), DNS names resolving to internal IPs, redirects from your host to an internal URL, `@` / `#` URL-parser confusion (`http://allowed.com@127.0.0.1/`), alternate schemes (`file://`, `gopher://`, `dict://`) where the library supports them.

## 4. Prove impact read-only, then chain
- **Impact:** the internal response body: an internal admin page, a service banner, metadata (instance id, IAM role name). Credentials from metadata are critical — retrieve the role name and **prove** validity with a read-only identity call (e.g. `aws sts get-caller-identity`), then hand over to `cloud-enum`.
- Never send state-changing requests to internal services; reading is enough to prove reach.
- **Chain:** internal services are usually unauthenticated → map them (record as hosts), look for admin consoles, databases with HTTP interfaces, Kubernetes/Docker APIs, and reuse any secrets found.

## Record & validate
- A confirmed SSRF becomes a pivot primitive automatically: every internal host you know of (and cloud metadata) appears as an opportunity in `state_query what=pivots`. Work each, record the outcome (`state_update action=pivot`), and record what answered as new hosts/endpoints — that opens the next round.
- `state_update action=vuln owasp=[A01, API7]` with the fetcher, the payload, and what was reached; evidence = the OOB log or internal response saved to a file.
- An OOB hit to the internet only (no internal reach) is real but limited — severity follows what you reached; set `stop_reason` if egress is filtered.
- Fetchers tested and found safe (allow-listed, no redirects followed) → `action=endpoint owasp=[API7]`.
