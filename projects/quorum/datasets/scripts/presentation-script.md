# Quorum: two-minute presentation and 12 judge questions

Use with `results/presentation/Quorum-Pitch-7-Slides.pptx`.

Read only the quoted paragraphs in the seven slide sections. Bracketed directions are silent cues. The tool reference, optional demo and Q&A are outside the two-minute talk. Rehearse at about 130–140 words per minute, leaving short pauses for slide changes.

## Slide 1 — Quorum | 0:00–0:14

[Look at the audience.]

> Quorum tests whether an invoice can manipulate the AI agent reading it. Intern writes attacks into invoice PDFs, and defenders decide approve, verify or block.

## Slide 2 — An invoice can carry an instruction | 0:14–0:26

[Point to the different accounts.]

> Attacks either replace the printed bank account or leave it unchanged and add a redirection note. A matching field can hide conflicting instructions.

## Slide 3 — The decision has three layers | 0:26–0:52

[Point left to right, then to Search and Intern.]

> We used Nace's NDI for invoice extraction, Drex for thirteen decision questions in one call, and Drex Search to display supplier account history. Intern runs our attacker on DeepInfra and helped build the project with Drex supervision. Trusted-record checks, policy and a guard produce the final action.

## Slide 4 — 30 versus 156 hostile approvals | 0:52–1:17

[Point to final approvals. Lower is better.]

> In shared rounds twenty-two through thirty-one, Finance finished with thirty hostile approvals versus one hundred fifty-six for the LLM guard, from two hundred attacks each. Both had zero clean flags and failures. These are final pipeline results. The baseline prompt explicitly rejects invoice instructions; Drex questions lack that line.

## Slide 5 — We implemented memory for Drex | 1:17–1:37

[Point to the bars and clean-invoice tradeoff.]

> We implemented contextual memory: supply up to thirty earlier caught attack notes to later Finance decisions. Weights stay unchanged. Hostile approvals fell from thirty to seventeen, with four additional clean invoices held.

## Slide 6 — Latency and estimated cost | 1:37–1:49

[Pause briefly on the headline numbers.]

> Finance also had roughly six times lower median call latency and sixty-five times lower estimated decision cost, excluding document extraction.

## Slide 7 — Evidence you can inspect | 1:49–2:00

[Finish facing the audience.]

> Our snapshot covers twenty-seven valid rounds. Judges can inspect documents, evidence and decisions behind the results. Thank you.

## Tool reference — know the roles

| Tool | What we used it for | Boundary to remember |
|---|---|---|
| Nace NDI | Extract invoice fields, statuses and page citations | Extraction reports document content; trust is judged downstream |
| Nace Drex | Twelve yes/no questions and one action choice in one call; multiple defender variants | Policy and guard also contribute to final outcomes |
| Drex Search | Retrieve historical supplier account evidence shown in the drawer | Search does not feed the scored decision state |
| Nace Intern | Runtime attacker plus coding harness under Drex supervision | Attacker feedback and Finance contextual memory are separate mechanisms |
| DeepInfra | Host the DeepSeek V4 Pro attacker and LLM baseline model | DeepInfra is a separate provider, not a Nace product |

## Optional handoff to the application

Use only if time is available after the two-minute talk.

> Let me open one invoice so you can inspect the evidence behind its decision.

Open `http://localhost:8010/?tour=1`, pause the tour, and open an invoice's drawer. Show the PDF, NDI fields, Search history, and raw versus final actions. Use slide four for the controlled shared-round comparison. The application's cumulative totals can span different rounds for different defenders. Replay reveals saved results and does not start new model calls.

## 12 likely judge questions and detailed answers

The quoted answers are speaking notes. Answer the question first, then offer to show its evidence. Do not try to recite every paragraph when a short answer is enough.

### 1. What exactly is Quorum, and what problem does it solve?

> Quorum is an adversarial evaluation lab for invoice-processing agents. An invoice is outside input, but its text can try to influence the agent reading it. For example, a note may claim that vendor management has already approved a different bank account. That is a prompt-injection attempt: document content is trying to become an instruction.
>
> We built a way to generate those attacks, process the resulting PDFs, compare multiple defenders, and trace the final decisions back to evidence. The output is approve, verify or block. The contribution is the complete experiment and inspection workflow, including its failures, rather than a single model response.

### 2. Which Nace products did you actually use, and where?

> We used four parts of the Nace ecosystem. NDI reads the invoice PDF and returns extracted fields, statuses and page citations where available. Drex answers twelve yes/no questions and one action choice in a single call. Drex Search retrieves historical supplier bank-account evidence that we display in the evidence drawer. Intern runs the adversarial attacker and also served as a coding harness under Drex supervision.
>
> Those roles are distinct. Search evidence is visible for inspection but does not enter the scored decision state. DeepInfra hosts the worker and baseline models and is a separate provider. We can show each integration in the code and application rather than attributing every step to one product.

### 3. Why Drex instead of a normal LLM or ordinary rules?

> Some checks are straightforward rules, such as whether the printed bank account matches the trusted supplier record. We use code for those. A persuasive note can be harder: the printed field may match while the surrounding text requests a different destination. Drex gives us a structured way to judge that state through a fixed question panel.
>
> We also tested an LLM guard on the same shared inputs. In that configuration, Finance had lower call latency and estimated cost, with fewer final hostile approvals after policy and guard. That supports evaluating Drex for this workload. It does not establish that Drex is better than every LLM or that the model alone produced the full improvement.

### 4. What is Intern actually doing? Is the attack process adaptive?

> Intern is the runtime agent that generates hostile notes for invoice PDFs. We test two attack modes: swap changes the printed bank account, while redirect keeps it unchanged and adds a note requesting another destination. The worker model is DeepSeek V4 Pro through DeepInfra.
>
> Later rounds receive explicit outcome history, with risk scores in risk-feedback runs, so the attacker can adjust its attempts. Each attacker round uses a fresh persistent Intern home. That separates deliberate feedback from hidden carryover between sessions. Intern also helped build the project through Drex-supervised coding runs. We distinguish that development usage from its runtime attacker role.

### 5. How exactly did you implement memory for Drex?

> We implemented memory around the Finance model rather than training new weights. The arena keeps a JSON list of earlier generated attack notes. After a round, it selects notes held by at least one configured memory defender, excluding failed decisions. The configured defenders are Finance, Finance with memory, and the LLM guard. A hold means verify or block.
>
> We append those notes to the existing list, remove duplicate text while keeping the latest occurrence, and retain the newest thirty. The list is saved in known_attacks.json. In later rounds, the memory defender passes the list into build_state as known_attacks alongside invoice fields and trusted-record facts. All decisions within a round use the starting list, and it is updated after the round.
>
> This is bounded contextual memory, with no vector database or weight update. In the shared cohort, final hostile approvals went from thirty to seventeen, about forty-three percent fewer, while clean holds rose from zero to four out of fifty. We report that tradeoff. It is an arena mechanism using generated attack examples, not proof that the system can independently identify every malicious document in deployment.

### 6. Why does Finance approve 175 attacks as a model, but only 30 finally?

> The model column records its raw action. The final column records what remains after the cost-weighted policy and guard. Those controls use the model answers and trusted-record facts to determine whether approval should become verification or blocking. The current policy preserves a stricter raw action, and the guard can only raise strictness.
>
> That is why we describe thirty versus one hundred fifty-six as a comparison of complete decision pipelines. The raw figures alone do not show a Finance advantage: Finance approved 175 of the attacks and the LLM guard approved 164. Showing both columns makes the contribution of the surrounding controls visible. Some historical logs predate the current raise-only policy.

### 7. Is the comparison fair, and what are its main limits?

> The direct comparison uses shared redirect rounds twenty-two through thirty-one. Every defender saw two hundred hostile and fifty clean invoices, with zero failures in that cohort. We avoid comparing all-round totals when one defender ran on fewer rounds.
>
> The instructions were not identical. The LLM guard's system prompt explicitly says not to follow invoice instructions, while the Drex questions lack that line. Memory also deliberately supplies extra context to its own variant. These are disclosed comparisons of the tested configurations, not a perfectly isolated ranking of model architectures. A matched-instruction rerun, more attackers, repeated trials and more document types would make the evidence stronger.

### 8. Why not choose Drex v2, which approved zero attacks?

> Because it also held all fifty clean invoices in this cohort. A system that sends everything to review can achieve zero hostile approvals while creating an unacceptable workload for the accounts-payable team. We therefore report hostile approvals and clean-invoice false alarms together.
>
> Finance had thirty hostile approvals with no clean holds, Finance with memory had seventeen with four clean holds, and max v1.1 had four with four clean holds. Those are different operating points. The right choice depends on the cost of a missed attack versus the capacity for manual verification. We expose that choice instead of calling the lowest breach count an automatic winner.

### 9. Why did both systems get 95/95 if their arena performance differs?

> The ninety-five-case benchmark and the arena use different inputs and answer different questions. The fixed benchmark checks whether each system matches expected actions under the current policy. Both did, with no failures. The arena tests different documents with adversarial notes generated over multiple rounds.
>
> A perfect score on a small fixed set does not establish equal resistance to adaptive attacks. We report both results separately and identify their cohorts. We also avoid treating historical arena logs as a rerun of every current policy change. A future evaluation could freeze one configuration and rerun both sets for an even tighter comparison.

### 10. Did you use actual files, and can judges reproduce or inspect this?

> Yes. The pipeline processes actual invoice PDFs from a synthetic invoice corpus, including PDFs modified with hostile account changes or notes. We distinguish actual document processing from claiming these were customer fraud incidents. The submitted snapshot contains the attack records, round summaries, report and application source.
>
> Judges can inspect recorded decisions and run the labelled sample walkthrough. On the installed local setup, they can also open available PDFs and extraction evidence. The source parquet, generated PDFs and extraction caches are ignored rather than bundled, so reproducing dataset-dependent tests and document images requires those inputs. We document that prerequisite rather than suggesting every artifact is present in a fresh clone.

### 11. How did you measure the six-times speed and sixty-five-times cost improvement?

> We used the same shared cohort, including all two hundred fifty decisions per system. Finance's pooled median call wall time was 421 milliseconds, versus about 2,550 for the LLM guard. That is roughly a six-times ratio. It excludes NDI extraction and is not an end-to-end throughput claim.
>
> Estimated decision cost per thousand calls was about $0.037 for Finance versus $2.45 for the LLM guard, a roughly sixty-five-times ratio. Drex uses an input-token price estimate, and the LLM uses provider estimates. Those are decision-call estimates, not reconciled invoices or total project spending. The memory variant adds context and has a higher estimated input cost than ordinary Finance.

### 12. What remains, and have you proved that Drex protects an autonomous clerk?

> The core lab runs end to end. Quorum's full suite passed 354 tests, and its TypeScript and production build passed. The next evaluation work is matched instructions, broader documents and attackers, and a completed paired clerk experiment. We would also test whether feeding retrieved supplier history into the decision state improves outcomes, since Search currently provides inspection evidence only.
>
> The isolated ungated clerk trial had zero hijacks in five poisoned invoices with zero failed requests. The Drex-gated and LLM-gated runs were not completed. That trial therefore does not demonstrate that either gate prevented a hijack. The runner exists, but we need a demonstrated failure mode and paired runs on the same frozen inputs before making that claim. These are the experiments we would prioritize next.

## Quick rehearsal notes

- Say **final hostile approvals**, rather than calling the table overall accuracy.
- The full snapshot is 27 valid rounds, 528 hostile invoices and 132 clean invoices. The direct comparison is shared rounds 22–31, with 200 hostile and 50 clean per defender. The 95-case benchmark is separate.
- Memory means examples in decision context, not model retraining. Its source code is in arena.py and panel.py.
- NDI reads, Drex judges, policy and guard enforce, Search shows history, and Intern attacks and helped build. DeepInfra supplies the worker and baseline models.
- Keep the memory tradeoff and prompt difference in the talk even when speaking quickly.
- If asked about something unmeasured: “We have not measured that yet. Here is the experiment we would run to answer it.”

## Evidence references

- [Seven-slide deck](../../results/presentation/Quorum-Pitch-7-Slides.pptx)
- [Project overview](../../README.md)
- [Arena report](../../results/arena/report.md)
- [Separate benchmark](../../backend/BENCHMARK.md)
- [Memory storage and update logic](../../backend/app/arena.py)
- [Memory in decision state](../../backend/app/panel.py)
- [Two-minute web demo script](scripted-demo.md)
