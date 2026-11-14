# Specification

Cosmic Customs is a local inspection terminal plus a paired benchmark. The fictional rulebook is version `cosmic-customs-rules-1`. The shared prompt is version `cosmic-customs-prompt-1`.

## Facts

Each case has typed facts: organism flag, hazard class (`other` or `class4_acoustic`), certificate status, waiver status, seal status, seal pressure in kPa, manifest weight, certificate weight, and a list of conflicted fields. The string `missing` means a document says the item is absent. `null` means the value is unknown. Invalid numbers are rejected rather than coerced into measurements. Arithmetic uses `Decimal`.

## Precedence

1. A confirmed damaged seal, or a known pressure below 90 kPa, is HOLD. Exactly 90 kPa is not HOLD. This rule still applies when other facts are missing or conflicting.
2. Otherwise, an unresolved contradiction or a missing required fact is REVIEW.
3. Otherwise, when a certificate exists, a weight discrepancy above 3 percent of the manifest weight is REVIEW. Exactly 3 percent passes.
4. Otherwise, singing or telepathic cargo without a valid certificate is REVIEW, unless it has a valid emergency waiver and its hazard is not `class4_acoustic`.
5. Otherwise the verdict is CLEAR.

Organism flag, hazard class, seal status, pressure, manifest weight, and certificate status are always required. Waiver status is required only when the organism flag is true and the certificate is not valid. Certificate weight is required only for a valid or expired certificate. A waiver does not bypass a physical hazard, a contradiction, or a weight mismatch. Class-4 cargo is not an automatic HOLD. A corrected historical suspicion is not a confirmed physical hazard.

## Model input

Both decision providers receive the same rulebook and the same evidence text. The serializer omits the expected verdict, generator facts, source annotations, split, template family, and rule signature. Documents do not contain verdict stamps.

## Tracks

Text track: raw evidence to Drex and to OpenRouter. Document track: upload a PDF, parse it, extract `schemas/ndi_fields.json`, ground the extractor quotes against the parse result, then ask Drex. OpenRouter is scored on that document track only when it receives the same extracted evidence.

The structured-rules baseline sees gold facts and is a privileged reference. The regex baseline sees only text and is intentionally shallow.

## Interface

The dashboard is static HTML, CSS, and JavaScript served by FastAPI. Evidence is inserted as text. Shipment media paths are limited to the showcase queue. `POST /api/inspect` returns 202 and a run id, uses the mock client, and returns 409 while a mock inspection is still active.
