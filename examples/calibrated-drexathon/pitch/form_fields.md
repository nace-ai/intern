# Drexathon submission form fields (Team Calibrated)

All numbers come from `results/metrics_real.md`, `results/intern_excerpt.md`, and `results/*_real.json` (real API runs, 2026-11-14).

## Project name
Calibrated: Flaky or Real? (Drex triages your red CI builds)

## Track
Apex Predator

## Team
Calibrated (Alexandra, solo)

## Brief explanation

Every CI pipeline has a smoke alarm that goes off when you make toast: flaky tests. Teams assume they need a smarter AI to read the logs, but the expensive part is the decision. One wrong quarantine ships a real bug, and one wrong block burns an engineer's hour. LLMs hedge: Llama-3.3-70B said "retry" on 41 of 60 real failures. Calibrated uses Drex probabilities to make the call, quarantining, retrying, or blocking based on what each mistake costs. On real logs, it was 96.7% accurate, quarantined zero real bugs, and cut expected engineer-time cost by 48%, at 292 ms per decision. Built with Intern, NDI, and DeepInfra.

## Presentation comment
Live demo: `python demo.py --live` (NDI → Drex → policy → feedback on case_007). Offline replay of the logged real results: `python demo.py --offline`. The deck is pitch/index.html, with a PDF at pitch/Calibrated_Drexathon.pdf.
