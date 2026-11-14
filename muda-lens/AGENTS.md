# Muda Lens

A reading glass for a wafer line. Lean and Six Sigma score a 12-step power-MOSFET line. Drex answers four typed questions in one pass: which waste, which step started it, which countermeasure, and whether the lots release, hold, or rework. A person still makes the call when confidence is under 50%.

Intern can work in this repository. Drex is System 1. The page, the bench, and the synthetic lots stay as they are unless the task says otherwise.

## What is already decided

- Lot data is synthetic, seeded, and modelled on a power-MOSFET flow. Do not add employer data, customer names, or real fab photos that are not already credited in `assets/samples/CREDITS.md`.
- Keys live in `.env` only. Never print them, commit them, or send them to the browser.
- The public page cannot call Drex live. `bench/recorded.json` is replayed and labelled. Leave the label.
- The proof is `bench/results.json`. Do not hand-edit it. Regenerate it with `npm run bench` only when the questions or the case generator change.
- Element ids in `index.html` are read by `src/app.js`. Rename one only if you rename the other.

## Checks

- `npm test` after a change to `src/engine.js` or `bench/cases.js`.
- `npm start` serves the page and the Drex proxy at `http://localhost:5173`.
- Do not commit `node_modules`, `.env`, or `.nace-workspace.json`.

## The four questions

`root_waste`, `culprit_step`, `countermeasure`, `disposition`. A case in the bench counts only when all four are right. The plant rule, told to every contender: product-quality risk outranks flow waste.
