# Drexathon submission — PixelDrex

**Title:** PixelDrex — GIFs made entirely of decisions

**Track:** 🎨 Wildcard

**Repo:** https://github.com/mauryantitans/PixelDrex

**Presentation:** Adobe Express deck (8 slides) — https://new.express.adobe.com/id/urn:aaid:sc:AP:6b92f91d-58a2-4ff1-bfd2-276901be6e56

**Demo:** `pixeldrex-web` (live) · backup video `docs/video/pixeldrex_demo.mp4` · animated results in the README gallery

---

## One-liner

Drex can't draw — it only answers multiple-choice questions — so we made every pixel of every frame a question and turned the answers into animated GIFs: paint a scene from one sentence, or let a world live, where Drex decides what every pixel becomes in the next frame.

## Short explanation (~100 words)

PixelDrex makes animated GIFs where every pixel is decided by Drex, two ways. **Paint it:** the trick is asking the right question — not "what color is pixel (8, 12)?" but "which part of the rocket covers it: nose cone, body, window, fins or flame?" — crisp shaded 32×32 sprites, where every edge pixel then asks Drex how to smooth itself from its neighbours. **Let it live (Drex Life):** you describe a world, a director writes its rules in English, and every frame each pixel asks Drex "I'm a tree with 2 burning neighbours — what am I next?" Cells in the same situation share one question, so a 40-frame forest fire took 41,984 decisions from 1,832 Drex questions (22 s, under 2¢). Worlds can come from this week's news (Keenable) or any PDF (NDI), with every rule citing its source — and in **god mode**, Intern rewrites a dying world's laws of physics until it comes alive.

## Full explanation (~300 words)

Drex can't draw. It only answers typed multiple-choice questions with calibrated probabilities. PixelDrex takes that literally:

**Paint it: ask the right question.** Asking "what color is pixel (8, 12)?" gives blobs. Asking "which part of the rocket covers pixel (8, 12)?" — plus each part's color, all in one Drex request — gives crisp sprites, because parts are meanings (a nose cone goes on top). Symmetric sprites ask half and mirror it. For 32×32, asking about exact rows got noisy, so the 16×16 shape is enlarged and every edge pixel asks Drex, from only its 8 neighbours, which part it should be for a smooth outline (identical neighbourhoods share one question — about one call per sprite). Motion comes from `score` questions whose expected values give smooth paths. Type "a ufo abducting a cow" and a director LLM plans the scene while Drex paints and animates it on a 64×64 canvas: 5–8 Drex calls, 5–9 s, under a cent, a 512 px GIF. Nobody told the rocket to shrink as it flies away — Drex decided that. The same request asks how each sprite leans and squashes or stretches, frame by frame, and sprites that **change shape** get 2–4 poses that Drex paints part by part and then picks per frame (a sapling grows into a tree, a butterfly flaps, a candle flickers). Resolution is a choice from 16×16 to 128×128.

**Then we let it live (Drex Life, the extension).** Describe a world; a director writes its laws of physics in plain English; then every step, every cell asks Drex what it becomes given its 8 neighbours. Two ideas make it work: cells in the same situation share one question and answers are cached (the world *learns its physics* — a 24×24 forest fire made 17,280 decisions from 191 questions), and the next state is *sampled from Drex's calibrated probabilities*, so "lightning rarely strikes" and "ash slowly regrows" really are rare and slow. Forest fires spread as glowing ragged fronts and regrow; a zombie outbreak spreads in waves through a city. Known-good worlds are saved and can be replayed with one click.

**The real world in.** `--news "bird flu"` uses Keenable to search and read this week's reporting and builds a world whose rules cite the articles ([1, 2, 3]). `--doc file.pdf` uses NDI to OCR and extract a document's actors and interactions with page citations, so every rule cites its page (p. 2).

**God mode.** Intern becomes the god of a world: it runs it, reads the report, and when a state dies out or the world freezes, rewrites the English rules and tries again — keeping document citations and tagging its own laws (god). A dead coral reef became a living one after 3 rewrites. Two Drexes: one decides every cell of the universe, the other (Intern's System 1) supervises the god.

## How we used each tool

- **Drex (Max v1.1):** every pixel's part, every edge pixel's smoothing, every part's color, every cell's state, motion paths — and Intern's System 1.
- **Intern:** our coding agent for part of the build (it implemented `--world` / `--report`; logs in `intern_logs/`), and inside the product as the god that rewrites a world's rules.
- **NDI:** upload + OCR + Extract with page citations — any PDF becomes a world with cited rules.
- **Keenable:** search (last 7 days) + full-article fetch — this week's news becomes a world with cited rules.
- **DeepInfra:** the scene and world directors, and Intern's System 2 (DeepSeek-V4-Pro).
- **Adobe:** Adobe Fonts and the pitch deck, built in Adobe Express.

## Numbers (real runs, Nov 14 2026)

| Run | Decisions | Drex questions / calls | Time | Drex cost |
|---|---|---|---|---|
| "a ufo abducting a cow" (scene, 32×32 sprites, 512 px) | 1,046 | 8 calls | 9 s | $0.009 |
| "a rocket launching at night" (scene, 32×32 sprites, 512 px) | 594 | 5 calls | 5 s | $0.005 |
| Forest fire (Life, 24×24 × 30) | 17,280 cells | 191 questions | 15 s | $0.004 |
| Zombie outbreak (Life, 32×32 × 40) | 40,960 cells | 590 questions | 17 s | $0.008 |
| Forest fire (Life, 32×32 × 40, gallery) | 41,984 cells | 1,832 questions | 22 s | $0.015 |
| God mode, coral reef | 4 world runs, 3 rewrites | 29 Intern System 1 asks | 128 s | — |

## Honest limits

- Drex paints small, iconic subjects well; large detailed images pixel-by-pixel don't work (we tried — plain downscaling beat it; see `scripts/experiments/` and `docs/gallery/large_image_experiment.png`).
- Side-view animals (a running cat, a jumping whale) are still blobby; front-view subjects (rockets, UFOs, trees, houses, faces) come out best.
- Not every world is interesting on the first try (rock-paper-scissors was noise; a world built straight from the sample PDF froze) — which is exactly what god mode is for, though it doesn't always hit its target (it reported missing one by 7 cells).
