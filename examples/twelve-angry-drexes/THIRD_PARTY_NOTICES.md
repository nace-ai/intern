# Third-party notices

No third-party source code is copied into this repository. The engine uses the Python standard library only. These tools and services are called at run time and are installed by the person running the demo:

| Component | Use | License |
|---|---|---|
| `nace-cli` (the `nace` command) | NDI document jobs | MIT |
| `nace-mcp` | Nace's local MCP server (Drex decisions, NDI tools) | MIT |
| Intern (`intern-agent`) | Agent harness | Apache-2.0 |
| `slack_bolt` | Slack Socket Mode bridge (optional, `requirements-slack.txt`) | MIT |

Fonts: the three screens in `mocks/` load Geist Mono, Inter from Google Fonts at view time (SIL Open Font License 1.1); no font file is stored here.

Data: statute text from LawWiki (public law). One court opinion, *Naranjo v. Spectrum Security Services, Inc.* (Cal. 2024), text from the `HFforLegal/case-law` dataset, CC BY 4.0, by Louis Brulé Naudet and Timothy Dolan; see `data/rulings/`.
