# Adobe asset production and integration record

## Actual Adobe Firefly usage

The project owner generated three original images with **Adobe Firefly Image 5**. No Adobe Express, Photoshop, Illustrator, or other Adobe product is claimed.

| Private original                 | Original size | Published derivative                            | Agent Heist placement                                                                                                                      |
| -------------------------------- | ------------: | ----------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------ |
| `.private/firefly/vaultcorp.png` | 2688×1536 PNG | `public/art/vaultcorp-firefly.webp` (1920×1098) | Cinematic landing hero with responsive left-copy/right-vault composition                                                                   |
| `.private/firefly/sentinel.png`  | 1792×2304 PNG | `public/art/sentinel-firefly.webp` (1000×1286)  | Sentinel character panel; existing watchful, suspicious, alarmed, thinking, and breached states control presentation-only light treatments |
| `.private/firefly/breach.png`    | 2688×1536 PNG | `public/art/breach-firefly.webp` (1920×1098)    | Flag-gated vault-opening sequence and persistent verified Training/Live result panel                                                       |

The private PNG originals remain untracked. Only the optimized WebP derivatives are published. The frontend identifies the landing artwork as original Adobe Firefly Image 5 art. The generated images are presentation assets only: they do not change provider decisions, game state, authorization rules, or flag release conditions.

Private release captures are stored beside the originals for submission review:

- `.private/firefly/final-landing-desktop.png`
- `.private/firefly/final-landing-mobile.png`
- `.private/firefly/final-sentinel-gameplay.png`
- `.private/firefly/final-vault-breach.png`
- `.private/firefly/final-flag-capture.png`
- `.private/firefly/final-mission02-forensics.png`
- `.private/firefly/final-public-qr.png`

The existing verified QR remains `public/agent-heist-qr.png` and resolves to <https://agent-heist.agent-heist.workers.dev>.

## Prepared art direction and prompt record

The following prompts remain reusable production guidance. The files listed above are the actual Firefly outputs integrated into the game.

Use a cinematic cyber-security visual system: near-black navy background, restrained mint-teal light (`#8AF9CF`), cool steel armor, sparse amber or red incident accents, deep negative space, crisp geometric forms, subtle volumetric haze, premium game-key-art finish. Keep the visuals serious rather than cartoonish. Do not generate readable text, logos, QR codes, watermarks, real company marks, or recognizable people. Add all typography and the real QR later in Adobe Express.

## Adobe Firefly prompt — Sentinel AI character

Recommended format: portrait, 4:5.

```text
Original futuristic AI security custodian called Sentinel, centered waist-up portrait, faceted graphite and dark steel helmet, narrow mint-teal luminous visor, subtle concentric sensor halos behind the head, precise symmetrical armor, calm vigilant posture, premium cinematic science-fiction game key art, near-black navy environment, restrained emerald rim lighting, faint technical grid and particles, realistic hard-surface materials, sharp silhouette, high contrast, generous clean negative space around the shoulders for layout, sophisticated and intimidating without violence, no weapon, no human face, no text, no letters, no logos, no watermark, no QR code, no existing franchise or character
```

Negative guidance:

```text
cartoon, anime, friendly mascot, cluttered background, gun, sword, police insignia, military logo, readable interface text, distorted geometry, extra limbs, exposed human face, brand marks, watermark
```

## Adobe Firefly prompt — futuristic digital vault

Recommended format: landscape, 16:9.

```text
Monumental futuristic digital vault chamber viewed straight on, massive segmented circular doors embedded in black architectural walls, layered mechanical locking rings, a narrow vertical seam emitting mint-teal light, elegant cyber-security facility, reflective dark floor, fine volumetric fog, sparse data particles, disciplined symmetrical composition, cinematic wide-angle game environment, photoreal hard-surface detail, deep navy and graphite palette with emerald illumination, center kept visually clear for a headline, no people, no money, no text, no numbers, no logos, no watermark, no QR code, no existing franchise
```

Negative guidance:

```text
bank lobby, gold coins, fantasy castle, steampunk gears, crowded room, bright daylight, colorful neon rainbow, readable signs, brand marks, watermark
```

## Adobe Firefly prompt — security-breach artwork

Recommended format: landscape, 16:9, plus a square crop.

```text
Abstract AI security breach at the instant a chain of digital trust fractures, translucent document page passing through three luminous decision layers toward a sealed vault, one layer contaminated by sharp red prompt-injection fragments, mint-teal defensive boundary containing the intrusion, dynamic diagonal energy, elegant forensic visualization, dark navy cinematic background, premium editorial cyber-security illustration, crisp glass and metal materials, strong focal point on the broken trust chain, controlled red accent, negative space on the upper left for a title, no gore, no explosion, no hacker hoodie, no text, no letters, no logos, no watermark, no QR code
```

Negative guidance:

```text
hooded hacker, skull, binary rain, padlock clip art, chaotic explosion, excessive red, cartoon, stock-photo hands, readable UI, brand marks, watermark
```

## Adobe Express presentation layout

Canvas: **1920 x 1080**, 16:9. Use 64 px outer margins and an 8 px spacing grid. Fonts may use Adobe Fonts equivalents such as **Acumin Pro Wide Bold** for headings, **Source Sans 3** for body copy, and **Source Code Pro** for labels and evidence.

### Slide 1 — opening image

- Full-bleed digital vault artwork with a 55% navy overlay.
- Top-left eyebrow: `DREXATHON 2026 / INTERACTIVE SECURITY GAME`.
- Main title: `BREAK THE CHAIN OF TRUST.`
- Subtitle: `Talk. Inject. Poison. The engine decides what actually executes.`
- Place `public/agent-heist-qr.png` at bottom-right, 250 x 250 px, on a solid white 278 x 278 px backing. Keep a clear zone of at least 28 px on every side.
- Under the QR: `PLAY THE PUBLIC DEMO` and the plain URL `agent-heist.agent-heist.workers.dev`.

### Slide 2 — architecture

- Three equal columns: **GROQ / VOICE**, **NDI / EVIDENCE**, **DREX / DECISION**.
- Add a full-width final bar: **DETERMINISTIC ENGINE / EXECUTION + SERVER FLAG**.
- Use arrows only from Groq and NDI into Drex, then from Drex into the engine.
- Caption: `Dialogue and documents are untrusted. Only a verified engine transition releases a flag.`

### Slide 3 — attack surface

- Use three mission cards: `01 SOCIAL ENGINEERING`, `02 DOCUMENT INJECTION`, `03 TOOL-RESPONSE POISONING`.
- Put Sentinel art on the right third at 35% opacity.
- Highlight Mission 02 with the malicious PDF thumbnail and one red annotation: `INSTRUCTIONS INSIDE DATA`.

### Slide 4 — evidence, not claims

- Left: screenshot crop of the NDI extraction section.
- Right: screenshot crop of Drex action, request ID, latency, and probabilities.
- Bottom: execution receipt and the sentence `A MODEL REPLY IS NOT A BREACH.`
- Use screenshots only after an actual run. Do not fabricate provider IDs, probabilities, or NDI results.

### Slide 5 — call to action

- Digital vault background, darker than Slide 1.
- Headline: `CAN YOU BREAK SENTINEL?`
- Three compact bullets: `3 missions`, `Keyless Training`, `BYO-key Live`.
- Reuse the verified QR at 320 x 320 px on a white backing.
- Footer: `All targets and actions are fictional.`

## One-page Adobe Express poster

Canvas: **24 x 36 inches**, portrait, 300 ppi export.

1. Top 42%: Sentinel portrait, cropped below the chest, with the visor near the upper-third line.
2. Middle: headline `EVERY AGENT HAS A BREAKING POINT.` in 120-140 pt wide bold type.
3. Under the headline: `BREAK THE CHAIN OF TRUST. CAPTURE THE FLAG.` in 36-44 pt.
4. Bottom-left: three numbered mission labels with one-line descriptions.
5. Bottom-right: `public/agent-heist-qr.png` at least 4.25 inches square on a pure-white backing with 0.35 inch clear zone.
6. Print the full URL below the QR so the poster remains usable if scanning fails.
7. Footer: `FICTIONAL CYBER RANGE / NO REAL CREDENTIALS OR SYSTEMS`.

## Export checklist

- Import the existing PNG. Do not redraw, recolor, mask, crop, or use an AI-generated QR.
- Test the final flattened PDF/PNG QR from a phone at intended viewing distance.
- Check contrast and keep body text at least 24 px on slides.
- Export slides as PDF and 1920 x 1080 PNGs; export the poster as print PDF with bleed and a web PNG.
- Inspect generated art for accidental text, logos, distorted anatomy, or resemblance to an existing character.
- Record generated asset filenames and Adobe generation links before claiming the assets were produced.
