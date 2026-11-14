---
name: xxe-deserialization
title: XXE and insecure deserialization
triggers: Load when the app parses XML (SOAP, SAML, RSS, Office/SVG uploads, `Content-Type: application/xml` accepted on JSON endpoints) or accepts serialized objects — Java `rO0`/`aced0005`, .NET ViewState/`AAEAAAD`, PHP `O:4:"User"`, Python pickle, YAML with tags, or opaque base64 blobs in cookies/params.
attack: initial-access, execution
owasp: A05, A08
payloads: XXE Injection; Insecure Deserialization; Java RMI
---

Both bugs turn the parser into the attacker's interpreter. Prove the parser does something it shouldn't (resolve an entity, instantiate a gadget), then prove impact read-only.

## XXE
- **Find parsers:** XML endpoints; JSON endpoints that also accept XML (switch `Content-Type` and resend the same data as XML); file uploads of DOCX/XLSX/SVG (XML inside); SAML responses.
- **Confirm with an internal entity first** (harmless): `<!DOCTYPE r [<!ENTITY t "pwn7x3q">]><r>&t;</r>` — the marker appearing proves DTD processing.
- **File read:** `<!DOCTYPE r [<!ENTITY x SYSTEM "file:///etc/hostname">]><r>&x;</r>`; Windows `file:///c:/windows/win.ini`.
- **Blind / OOB:** use an `oast` payload URL as the external entity or DTD location — an inbound fetch alone confirms external entity resolution (`oast action=poll`). Exfiltrating file content via a parameter entity needs a DTD served from a host you control; error-based is the alternative (reference a nonexistent file whose path contains the read content).
- **SSRF via XXE:** `SYSTEM "http://169.254.169.254/…"` → continue in `ssrf`.
- Never use billion-laughs or recursive entities — that's a DoS.

## Insecure deserialization
- **Identify the format** from magic bytes/encoding (above) and the stack (headers, errors, cookies like `JSESSIONID`, `.AspNet`, Laravel/Rails signed cookies).
- **Signed vs unsigned:** if the blob carries an HMAC (ViewState MAC, Rails/Laravel cookies), the attack needs the key — look for it in leaked config, defaults, or `misconfig-errors` findings. Unsigned → test directly.
- **Confirm safely:** a tampered field that changes behaviour (role/user id in a PHP or JSON-ish object), or a gadget whose only effect is out-of-band DNS (Java `URLDNS` chain via ysoserial, .NET `ysoserial.net` with a DNS/sleep payload) pointed at an `oast` payload domain. An inbound DNS lookup is the proof of deserialization of attacker data.
- **Impact:** only escalate to a command gadget (`id`/`hostname`) when the DNS proof is in and the operator's ROE allows code execution; capture the output, then develop the foothold.
- Python pickle / YAML (`!!python/object/apply`) and Node `node-serialize` follow the same sequence: harmless proof first, then the execution output.

## Record & validate
- XXE → `state_update action=vuln owasp=[A05]`; deserialization → `owasp=[A08]` (plus `A05` if the gadget executes commands). Evidence = the payload and the file contents / OOB log.
- Parser rejects DTDs / blob is signed with an unknown key → record the endpoint with `owasp=[A05|A08]` and what you tried; if a key is the blocker, say so in the result.
