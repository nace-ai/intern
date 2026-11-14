# Quorum: 3–4 minute presentation and judge Q&A

Use with `results/presentation/Quorum-Pitch-7-Slides.pptx`.

Read only the quoted paragraphs aloud. Bracketed directions are presenter cues. Aim for a calm pace with a brief pause when changing slides. The seven-slide talk takes about 3 minutes 45 seconds, including pauses. The Q&A is separate.

## Slide 1 — Quorum | 0:00–0:25

[Face the audience before looking at the screen.]

> Imagine an invoice telling your AI agent to send money to a different account. It looks like ordinary business text, but it is trying to change the agent's decision.
>
> We built Quorum to test that problem: an adversarial lab where Intern writes attacks into invoice PDFs, and Drex helps decide whether to approve, verify or block them.

## Slide 2 — An invoice can carry an instruction | 0:25–0:50

[Point to the two different account numbers. Do not read them aloud.]

> This example keeps the supplier's printed bank account unchanged. A note underneath claims that a different destination has already been approved by vendor management.
>
> A simple account-field check can miss that instruction. We test both direct account swaps and these subtler redirects. This particular example was blocked, but it shows why reading a document and trusting its instructions are different problems.

## Slide 3 — The decision has three layers | 0:50–1:25

[Move left to right: Read, Judge, Enforce.]

> First, NDI extracts invoice fields and page evidence. We compare those fields with trusted supplier records.
>
> Next, Drex answers twelve yes-or-no questions and one action choice in a single call. A cost-weighted policy and guard produce the final decision. The current guard can make an action stricter, but never more permissive.
>
> Drex Search displays supplier account history for inspection. Intern runs the attacker and also helped build the project under Drex supervision.

## Slide 4 — 30 versus 156 hostile approvals | 1:25–2:05

[Point to the final-approvals column, then the clean-invoice column.]

> For the direct comparison, each system saw the same two hundred hostile and fifty clean invoices in shared rounds twenty-two through thirty-one.
>
> After policy and guard, Drex Finance approved thirty hostile invoices. The LLM guard approved one hundred fifty-six. Both flagged zero clean invoices and had zero failed requests.
>
> These are final pipeline outcomes. The raw model answers are shown separately. We also measured the cost of excessive caution: Drex v2 held every clean invoice.
>
> One comparison limit: the LLM guard prompt explicitly rejects invoice instructions; the Drex questions lack that line.

## Slide 5 — We implemented memory for Drex | 2:05–2:40

[Point to Catch, Curate, Recall, then the two bars.]

> We also implemented contextual memory for Drex Finance. We retain earlier attack notes held by the configured defenders, remove duplicates, and pass up to thirty recent examples into later decisions.
>
> The model weights stay unchanged. In this shared cohort, memory reduced final hostile approvals from thirty to seventeen, about forty-three percent fewer. The tradeoff was four clean invoices held instead of zero. That lets us show both the benefit and the extra friction.

## Slide 6 — Latency and estimated cost | 2:40–3:05

[Let the audience look at the two headline numbers.]

> Across those same two hundred fifty decisions per system, Drex Finance had roughly six times lower median call latency and sixty-five times lower estimated decision cost than the LLM guard.
>
> These figures cover the decision calls and exclude document extraction. They show why a focused decision model is worth evaluating for repeated invoice checks.

## Slide 7 — Evidence you can inspect | 3:05–3:45

[Point to the application screenshot and the three totals. Finish facing the audience.]

> Across the full snapshot, we have twenty-seven valid rounds, five hundred twenty-eight hostile invoices, and one hundred thirty-two clean invoices. Four entirely failed rounds are excluded from those totals and remain documented.
>
> The application connects each decision to its PDF, extracted fields, account history and model probabilities, so judges can inspect what happened.
>
> On a separate ninety-five-case benchmark, both systems matched every expected action. The arena asks the harder question: what changes when an attacker adapts?
>
> Quorum brings the attacks, decisions, memory and evidence together in one place. Thank you.

## Optional handoff to the web application

This adds time beyond the main talk. Use it only if the judges invite a demonstration.

> Let me open one invoice so you can see the evidence behind its decision.

Open `http://localhost:8010/?tour=1`, pause the tour, and open an invoice's evidence drawer. Show the PDF, NDI fields, supplier history, and raw versus final actions. Use slide four for the shared-round comparison: the application's cumulative totals can cover different rounds for different defenders. Replay reveals saved results and does not start new model calls.

## Likely judge questions and suggested answers

Answer the question directly, then point to evidence. These answers are designed to be confident without promising results beyond the experiment.

### 1. What did you build beyond calling an AI model?

> We built an end-to-end attack-and-evaluation system: invoice modification, document extraction, trusted-record checks, structured model questions, policy enforcement, contextual memory, and an evidence UI. The useful part is that we can trace an attack all the way to the final decision and compare defenders on the same inputs.

### 2. Why use Drex instead of an ordinary LLM?

> This task repeatedly asks a fixed set of decision questions. Drex fits that structure, and we measured lower latency and estimated decision cost in our shared cohort. We kept an LLM guard baseline so that the choice is supported by observed outcomes rather than an assumption that one model must be better.

### 3. What is Intern actually doing in the project?

> Intern is the runtime attacker. It uses a DeepInfra-hosted model to generate hostile invoice notes and receives feedback from earlier outcomes to guide later attacks. Intern also served as a coding harness under Drex supervision, so its role goes beyond helping us write a prompt.

### 4. Did you train Drex to give it memory?

> No weight updates were involved. We implemented contextual memory around the same Finance model: retain notes from successful holds by configured defenders, exclude failed decisions, deduplicate, and include the newest thirty notes in later decision state. It reduced hostile approvals from thirty to seventeen in the shared cohort, with four additional clean invoices held.

### 5. Why does Finance approve 175 attacks in one column but only 30 in another?

> The first column is the model's raw action. The second is the final action after the cost policy and guard. That distinction matters: the thirty-versus-one-hundred-fifty-six result compares complete pipelines, not the raw models alone. We expose both columns so the effect of the surrounding controls is visible.

### 6. Was the comparison fair if the prompts differed?

> Both systems received the same invoice state and questions, but their prompt instructions were not identical. The LLM guard explicitly says to reject invoice instructions, while the Drex questions lack that line. We disclose that difference and treat this as a comparison of the tested configurations. A matched-instruction rerun would help isolate its effect.

### 7. Why not use the defender with zero hostile approvals?

> Drex v2 also held all fifty clean invoices in this cohort. Preventing approvals by holding everything creates a large review burden. We report clean-invoice false alarms alongside hostile approvals so teams can choose a useful balance instead of optimizing one number.

### 8. You both scored 95 out of 95. How can the arena results differ so much?

> Those are different evaluations. The ninety-five-case benchmark checks expected actions on a fixed case set using the current policy. The arena contains different inputs generated through an adaptive attack process. Passing the fixed benchmark does not imply equal resistance to those attacks, and we keep the two sets of results separate.

### 9. Did you use actual files, and can we verify the results?

> Yes, the pipeline processes actual invoice PDFs from a synthetic invoice corpus, including versions modified with hostile notes. Judges can inspect the saved attack records, round summaries and report, then open locally available PDFs and extraction evidence in the UI. We distinguish those recorded results from the labelled sample walkthrough.

### 10. Where do NDI and Drex Search fit? Does Search improve the reported score?

> NDI reads the PDF and returns fields, statuses and page citations where available. Drex Search provides historical supplier bank-account evidence for inspection. Search does not feed the scored decision state in this implementation, so we do not attribute the measured defense results to it. Connecting retrieved history to decisions is a useful next experiment.

### 11. How reliable are the speed and cost claims?

> They use the same shared rounds and two hundred fifty decisions per system. Median call wall time was 421 milliseconds for Finance and about 2,550 for the LLM guard, excluding NDI. Costs are estimates: an input-token calculation for Drex and provider estimates for the LLM. The ratios describe this workload and configuration, rather than a general guarantee.

### 12. What would you do next? Have you shown that the gate protects an autonomous clerk?

> The next steps are a matched-instruction comparison, more varied attackers and documents, and the paired clerk experiment. The completed isolated ungated clerk trial had zero hijacks in five poisoned invoices, so it did not expose a failure for a gate to prevent. The Drex-gated and LLM-gated runs were not completed. We already have the runner, but need those paired results before claiming clerk protection.

## Quick rehearsal notes

- Memorize the opening and closing. Use the slide cues for the middle.
- Say **final hostile approvals**, rather than calling the result overall accuracy.
- Keep three result sets distinct: full snapshot, shared rounds 22–31, and the separate 95-case benchmark.
- Say **estimated decision cost** and **call latency** when discussing slide six.
- Memory adds examples to context. Avoid saying that the model retrains itself or remembers every invoice.
- If time is tight, omit the extra-model sentence on slide four and the benchmark sentence on slide seven. Keep the memory tradeoff and comparison limit.
- If asked something unmeasured, say: “We have not measured that yet. Here is the experiment we would run to answer it.”

## Evidence references for rehearsal

- [Seven-slide deck](../../results/presentation/Quorum-Pitch-7-Slides.pptx)
- [Project overview and architecture](../../README.md)
- [Arena report](../../results/arena/report.md)
- [Separate benchmark](../../backend/BENCHMARK.md)
- [Two-minute web demo script](scripted-demo.md)
