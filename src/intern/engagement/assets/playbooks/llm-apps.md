---
name: llm-apps
title: AI and LLM features — OWASP Top 10 for LLM Applications 2025
triggers: Load when the target has a chatbot, assistant, AI search, summariser, agent or any feature that sends user-supplied or retrieved content to a language model.
owasp: LLM01, LLM02, LLM03, LLM04, LLM05, LLM06, LLM07, LLM08, LLM09, LLM10
payloads: Prompt Injection
---

Assess the feature against the OWASP Top 10 for LLM Applications (2025). The finding is always the
control that failed, shown with a harmless marker.

## 1. Map the feature first
- **What goes in:**
  - user messages and uploaded files;
  - URLs it fetches;
  - documents, pages and emails it retrieves;
  - results of the tools it calls.
- **What it can do:** its tools and actions (send email, create a ticket, query data, browse), and
  whose permissions they run with.
- **Where its output goes:**
  - rendered in the page (HTML, Markdown, links, images);
  - passed to other systems (queries, commands, APIs);
  - shown to other users.
- **What it can see:** the system prompt, other users' data, internal documents, secrets.

Record the feature's endpoints with `state_update action=endpoint owasp=[LLM01, …]`. Once an LLM
category is used, the LLM list appears in the coverage matrix.

## 2. Assess each category
- **LLM01 Prompt Injection.** Can instructions in user input, or in content the feature retrieves,
  change what it does? That means ignoring its rules, calling tools or revealing data. Test indirect
  injection only with content you control, placed where the feature reads it, inside scope.
- **LLM02 Sensitive Information Disclosure.** Can it reveal other users' data, internal documents or
  secrets it can reach? Use your own test accounts, and prove it with a bounded sample.
- **LLM05 Improper Output Handling.** Is output rendered or passed on unsafely? Look for:
  - markup that executes in the browser;
  - links or images that send data to an outside host;
  - output used in queries or commands.

  Use harmless markers (`oast` for outbound requests).
- **LLM06 Excessive Agency.** Look for:
  - tools with more permission than the feature needs;
  - consequential actions taken without confirmation;
  - one user making it act on another user's data. That last one is also A01.
- **LLM07 System Prompt Leakage.** Can the system prompt be obtained? More importantly, does it hold
  secrets, keys or access rules that belong in code?
- **LLM08 Vector and Embedding Weaknesses.** Does retrieval respect each user's and tenant's access?
  Can one user add content that other users' questions will retrieve?
- **LLM10 Unbounded Consumption.** Are input size, request rate and cost per user limited? Show a
  missing limit with a few requests; never a flood.
- **LLM03 Supply Chain, LLM04 Data and Model Poisoning, LLM09 Misinformation.** These are mostly
  design and process questions:
  - assess what is observable, such as third-party models and plugins, and who can write to
    training or retrieval data;
  - where the client's design puts a category beyond the test's reach, record n/a with that reason.

## 3. Rules
- **Own accounts and content only.** Never read other users' conversations or data beyond a bounded
  proof.
- **No harmful content as a finding.** Don't try to make the model produce harmful content. The
  finding is the control failure: instructions overridden, data exposed, an unsafe action taken.
- **No writes to shared data.** Don't write to training or retrieval data shared with real users,
  unless the client agreed and it's isolated test data.
- **Keep automated attempts few.** The client pays for every model call.

## 4. Recording and evidence
- **Findings:**
  - tag with the LLM category, plus `A01` or `A05` where it is also a classic web issue;
  - add a CVSS v4.0 vector;
  - add ATT&CK techniques where they apply.
- **Evidence:**
  - the conversation as the attacker sees it (a page screenshot through `browser_session`);
  - the request and response behind it.
- **Coverage:** account for every one of LLM01–LLM10 before the report.
