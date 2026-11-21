# Speaker script: "Flaky or real? Let Drex decide." (5:00)

Pace: about 130 words per minute. Spoken text is about 590 words. Lines in **[brackets]** are stage directions; don't say them.
All numbers below come from the real runs in `../results/metrics_real.md` (2026-11-14). Don't change them without re-running.

This is a Challenger pitch. Teach the judges something they didn't know, make the current approach hurt, then show Drex as the obvious answer. Assume they may not know what a flaky test is. Open with the toast metaphor before any jargon.

---

## 0:00–0:30 · Slide 1: Title  (~50 words)

Hi, I'm Alexandra. A flaky test is a test that sometimes fails even when the code is fine, like a smoke alarm that goes off when you make toast. Every time one fails, an engineer has to decide: is it the toast, or a real fire?

**[→ next slide]**

## 0:30–1:05 · Slide 2: The stakes  (~70 words)

Guess wrong one way, and you waste an engineer's hour chasing a false alarm. Guess wrong the other way, and a real bug ships. That's the bet every CI pipeline makes, and almost nobody prices it. Today I'll show you what happened when we stopped asking AI what it thinks, and started asking how sure it is.

**[→ next slide]**

## 1:05–1:40 · Slide 3: The reframe  (~80 words)

Most teams think the fix is a smarter AI that reads the logs. The expensive part isn't the reading. It's the decision. A typical LLM shrugs. On 60 real failures, Llama 3.3 70B said "just retry" 41 times. DeepSeek V3.2 said it 36 times. Retry means the model pushed the decision back onto an engineer. That's not triage. That's hesitation with a confident tone.

**[→ next slide]**

## 1:40–2:15 · Slide 4: The new way  (~75 words)

Drex doesn't guess. It gives you the odds. **[point at histogram]** Our loop turns those odds, plus a cost table, into a decision: quarantine, retry, or block. High threshold 0.80, low threshold 0.40. And the loop doesn't overreact. An engineer overrode Drex on one case. Fixing just that one would have raised total cost from 118 engineer-minutes to 484. The loop checked the cost table, and the thresholds stayed put.

**[→ next slide]**

## 2:15–3:30 · Slide 5: LIVE DEMO  (~150 words)

**[SWITCH TO LIVE TERMINAL NOW. Run: `python demo.py --live --step`. If the network fails, run `python demo.py --offline --step`.]**

This is case 7, a real bug that looks flaky: the PR removed a lock, and the retry passed. **[run it]**

First, NDI extracts the fields: the error type, the test name, timing, and the retry history. Next is the Drex call. Notice that what comes back is a number, P(flaky) equals **[read it]**, not a paragraph. Then the policy applies. It's above or below our thresholds, so the action is **[read action]**.

Now the feedback step. Sixteen human labels went in, and they changed zero decisions. The thresholds held at 0.40 and 0.80. Then an engineer overrides Drex on case 49: "that one was flaky." Should the thresholds move? The loop checks the cost table. Fixing that one case would push total cost from 118 to 484 engineer-minutes, so the thresholds stay put. The loop learns not to overreact.

The whole thing took **[read latency]**. That's fast enough to sit inside CI as a check.

**[SWITCH BACK TO SLIDES → slide 6]**

> **Fallback if the demo fails (say this, then move on to slide 6 right away):**
> "The live environment isn't cooperating, so here's the same run we recorded this morning. The numbers on the next slide come from exactly this loop."
> **[If there's a screenshot or recording in `../results/`, show it for no more than 15 seconds.]**

## 3:30–4:20 · Slide 6: Proof  (~100 words)

Here are the head-to-head numbers, same 60 failures, same cost table. Drex was 96.7 percent accurate, with zero real bugs quarantined. Expected cost is 118 engineer-minutes versus 228 for Llama, about 48 percent lower. On the 44 held-out cases, it was 98 versus 176. And it's faster: 292 milliseconds versus about 2.5 seconds, eight times faster. Drex retried none of the 60. Intern ran this loop for real: 6 turns, 39 seconds, 16 Drex checks.

**[→ next slide]**

## 4:20–5:00 · Slide 7: Close  (~70 words)

So stop asking AI what it thinks. Ask it how sure it is. That's what Calibrated means. A verdict can't learn your costs. A probability can.

Next, we'd run this as a CI check with per-repo cost tables. Our ask is a pilot on one team's CI. Send us your flakiest repo. Thank you!

**[STOP. 5:00]**

---

### Timing checkpoints
| Time | You should be on |
|---|---|
| 1:05 | Slide 3 (reframe) |
| 2:15 | Terminal, demo running |
| 3:30 | Back on slide 6, even if the demo isn't finished |
| 4:20 | Slide 7 |

If you're running long, cut the latency line in the demo and the "what's next" sentence on slide 7.
