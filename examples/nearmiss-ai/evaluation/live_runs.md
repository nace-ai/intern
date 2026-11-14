# Real ASRS live runs

Captured 2026-11-14 using Builder A's official June 2024 NASA ASRS export, Builder B's FastAPI service, Drex (`drex-max-v1.1` through `drex-latest`), and NDI output from FAA AC 120-74B and AC 91-73B. The local evidence cache contained 88 located passages (86 page-grounded). Every request used the dashboard's `/api/data/cases/{acn}/context` response and was posted through `/api/analyze`. The JSON files below are actual service responses, not fixtures or generated examples.

| Case | Saved response | Observed live behavior |
| --- | --- | --- |
| ASRS 2134024 | [`dashboard/web/public/demos/2134024.json`](../dashboard/web/public/demos/2134024.json) | Four agents, Evidence selected `supports` with one FAA citation, Coordinator accepted `investigate_cluster`. Run `run-563fc962c061`. |
| ASRS 2132897 | [`dashboard/web/public/demos/2132897.json`](../dashboard/web/public/demos/2132897.json) | Eight agents over two rounds. Evidence selected `unresolved` in round 1; Coordinator selected `reevaluate`; Pattern revised `runway_surface` to `communication_coordination`; the second Evidence pass cited one FAA passage; final action `specialist_review`. Run `run-447a10b4487f`. |

The second case demonstrates a real challenge round and revised claim. It **does not** meet the plan's stricter swarm-proof example of an Evidence Agent selecting `conflicts` with a located citation in round 1. Across the real cases evaluated so far, that exact behavior has not occurred; the dashboard does not relabel an `unresolved` decision as a conflict. Fresh model decisions may differ from these saved runs.

Taxonomy review of the 503-record export found 200 records coded `Human Factors`, 168 `Aircraft`, 53 `Ambiguous`, and 35 `Procedure` under the ASRS `Primary Problem` field. Builder B's initial `other` family grouped aircraft malfunctions with weather and other topics, so the integrated taxonomy adds `aircraft_systems`. The two FAA taxi guidance PDFs do not cover aircraft-system malfunctions; the Evidence Agent receives no passage for that family rather than treating taxi guidance as support. For the three covered families, the Evidence Agent sees passages from the claimed topic and two alternative topics when available.
