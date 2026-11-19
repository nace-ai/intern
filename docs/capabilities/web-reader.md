# Web reader (opt-in)

Add `web-read` to a profile's `tool_packs`. `web_read(url)` returns deterministic HTML/text
extraction, source ID, final URL, fetch time, content hash and a ResourceRef to the complete retained
UTF-8 text. Long previews clearly identify truncation and the file to page through with `read`.
JavaScript is not executed. Empty extraction reports a coverage warning; PDFs/binary formats ask
for the appropriate reader rather than pretending extraction succeeded. Document ingestion/OCR
continues to belong to the existing workspace effort.

The default reader permits only public HTTP(S) destinations. URL credentials, private/loopback/
link-local addresses, mixed public/private DNS answers and unsupported schemes are refused. A
custom HTTPCore network backend resolves and validates each new connection and connects to that
numeric address; HTTP Host and TLS certificate/SNI identity remain the original host. Redirects use
the same policy. No system proxy, cookies, inherited authorization or anonymous gateway is used.
Encoded and decompressed responses have separate enforced limits within one total deadline.

Embedded clients may construct `WebReader(ReadSettings(...))` and inject it into `WebReadTool`.
Private URLs require the operator's explicit `ReadSettings(allow_private=True)`; this is never a
model argument. `TextExtractor` is replaceable for teams preferring a readability library or
managed extraction backend. The initial deterministic extractor removes scripts/styles and keeps
block text; it does not claim article-ranking quality or full JS-site coverage. There is no silent
paid service or browser escalation.

Full text defaults to `$INTERN_HOME/web-content` and may be rebound to a private session store
with `WebReadTool(storage_dir=...)`. Publication uses a private staging file and a no-clobber link;
duplicate content is checked for integrity. The configured directory is owner-controlled, not an
OS sandbox against another process able to modify its parents. URLs and source text may be private:
choose store scope/retention accordingly. The shared ArtifactStore capability can replace this
minimal retained-text path once those independent features land together.
Retained files use mode0600 in a mode0700 owner directory (POSIX). Existing broad-permission or
symlink store targets are refused. Duplicate verification opens without following links or blocking
on FIFOs, checks ownership/type/size first and reads only the expected bytes plus one. Ordinary OS
path aliases such as a /tmp parent are allowed; a symlink used as the store target is refused.

No shipped profile, default prompt, journal or engine loop is changed. This capability uses the
existing typed Tool seam; no reasoning hook is needed. The cold-start test checks the actual
default profile rather than every optional installed tool pack, keeping its existing ceiling.

## The decision model

With the decision model on (`[capabilities.web_reader] system1 = true`), a page longer than the preview is judged before the agent sees it,
instead of being cut at the preview's end. The page is cut into line-aligned parts of up to 1,200 characters; the decision model
reads the session (what the user asked, what the agent has done) and up to 16 parts per ask, the first 128 parts in
all, and gives each the probability that the task needs it. The parts at 0.5 or above, best first until the preview
is full, come back in page order, the lines between them named as skipped, then the retained file to read them from.
When the decision model keeps no part, the page's start comes back as before, with a line saying so. A page that fits the
preview is never judged. Asks are reported as `web_read` decisions. Off by default: the cut-off is not yet measured.

The agent reads the page's own words, never a summary: pruning, as in
[CRAG](https://arxiv.org/abs/2401.15884)'s decompose-then-recompose and
[Provence](https://huggingface.co/blog/nadiinchi/provence), not the summarising model of Claude Code's WebFetch.
The decision model returns only probabilities, so a page's text cannot instruct it to do anything.

## Validation

Validation covers a real local HTTP server (explicit private opt-in only), connection-level address
pinning, private redirects, cancellation/timeouts, gzip/size/MIME errors and the scripted engine
path with full retained content. Paired eval results are still required before
default promotion. Primary interface reference: [HTTPCore network backends](https://www.encode.io/httpcore/network-backends/).
