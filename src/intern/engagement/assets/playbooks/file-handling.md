---
name: file-handling
title: Path traversal, file inclusion and file upload
triggers: Load when a parameter names a file, path, template, language or page (`?file=`, `?path=`, `?page=`, `?lang=`, `?template=`, download/export endpoints), or the app accepts uploads (avatars, documents, imports, attachments).
attack: initial-access, execution
owasp: A01, A05, A08
payloads: Upload Insecure Files; File Inclusion; Directory Traversal; Zip Slip; CSV Injection
---

Two directions, one goal: read what you shouldn't (traversal/LFI) or get the server to store and serve/execute what you control (upload). Prove with real file contents or a harmless executed marker.

## Path traversal / LFI
- **Probe** each file-ish parameter: `../../../../etc/passwd`, `....//....//etc/passwd`, `..%2f..%2f`, `%252e%252e%252f` (double-decode), `..\..\windows\win.ini`, absolute paths (`/etc/passwd`), null byte on legacy stacks (`%00.png`), and the app's own prefix kept intact (`images/../../../etc/passwd`) when it validates the start.
- **Differential:** a known-existing vs known-missing file (`/etc/hostname` vs `/etc/nonexistent-pwn`) gives different responses even when content isn't shown.
- **Prove impact read-only:** app config and secrets beat `/etc/passwd` — `.env`, `config/*.yml`, `web.config`, `application.properties`, `/proc/self/environ`, `/proc/self/cmdline`, cloud credential files, SSH keys of the service user. Save the retrieved contents as loot.
- **Inclusion → execution (LFI→RCE):** PHP wrappers (`php://filter/convert.base64-encode/resource=index.php` reads source; `data://`/`php://input` execute only where enabled), log or upload poisoning with a harmless `<?php echo 'pwn7x3q'; ?>` marker. Stop at the marker or `id`, then develop the foothold.
- **Chain:** secrets from config → DB/API/cloud logins (`cloud-enum`), source code → hidden routes and more bugs.

## File upload
- **Map the pipeline:** where the file is stored, how it's served back (same origin? content-type? `Content-Disposition`?), whether it's processed (image resize, AV, PDF render, unzip).
- **Bypass the type check:** extension variants (`.php5`, `.phtml`, `.jsp`, `.aspx`, `.svg`, `.html`), double extensions (`x.php.png`), case, trailing dot/space, mismatched `Content-Type`, magic-byte prefix (`GIF89a`) on a script.
- **Prove with the least dangerous artifact:** an `.html`/`.svg` that runs JS on the app origin (stored XSS → `xss`), or a server-side script that only echoes a marker. Retrieve it and show the marker executed. Delete your test files if the app lets you; record what you left behind.
- **Processing attacks:** zip-slip (archive entries named `../../x`), image-library and PDF-renderer bugs (→ `ssrf` / `xxe-deserialization` for SVG/XML), filename injection into shell commands (→ `server-injection`).
- **Access to others' uploads:** predictable paths or ids → `access-control`.

## Record & validate
- Traversal/LFI → `state_update action=vuln owasp=[A01]` (inclusion that executes: add `A05`); upload to code execution → `owasp=[A08, A05]`; evidence = request + retrieved contents / executed marker.
- File parameters and upload endpoints tested clean → `action=endpoint owasp=[A01|A08]` with the bypasses tried in `result`.
