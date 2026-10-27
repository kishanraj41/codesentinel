<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="docs/assets/logo-dark.svg">
    <img src="docs/assets/logo.svg" alt="CodeSentinel" width="88">
  </picture>
</p>

<p align="center">
  <img src="docs/assets/banner.png" alt="CodeSentinel. Guideline-cited clinical coding audits on NVIDIA Nemotron." width="1000">
</p>

<h1 align="center">CodeSentinel: guideline-cited clinical coding audits on NVIDIA Nemotron</h1>

<p align="center">
  <strong>A risk-adjustment coder signs only what the chart and the ICD-10-CM guideline can defend.</strong><br>
  Runs on <strong>Nebius Token Factory</strong>: <code>nvidia/Nemotron-3_5-Lightning</code> triage, <code>nvidia/nemotron-3-super-120b-a12b</code> adjudication, <code>nvidia/Nemotron-3-Ultra-550b-a55b</code> escalation.
</p>

<p align="center">
  <a href="https://www.python.org/"><img alt="Python 3.12" src="https://img.shields.io/badge/python-3.12-3776AB?style=flat-square"></a>
  <a href="LICENSE"><img alt="MIT license" src="https://img.shields.io/badge/license-MIT-08382d?style=flat-square"></a>
  <img alt="Tests" src="https://img.shields.io/badge/tests-unittest%20%7C%20pytest-1c3330?style=flat-square">
  <img alt="NVIDIA Nemotron" src="https://img.shields.io/badge/NVIDIA-Nemotron-76B900?style=flat-square">
  <img alt="Nebius Token Factory" src="https://img.shields.io/badge/Nebius-Token%20Factory-111111?style=flat-square">
  <img alt="Tavily" src="https://img.shields.io/badge/Tavily-guideline%20citations-5B4BFF?style=flat-square">
</p>

<p align="center">
  <img src="docs/assets/audit-findings.gif" alt="Selecting the Margaret Whitmore chart and opening the gap worklist." width="1000">
</p>

<p align="center">
  <em>Mock mode, no API keys. The worklist is what a coder would sign.</em>
</p>

CodeSentinel audits outpatient clinical charts for ICD-10-CM and HCC coding gaps. Deterministic rules propose missed or under-specified codes. A three-model NVIDIA Nemotron cascade on Nebius Token Factory cross-examines each one. A verifier outside the model drops any quote that is not in the chart. Combination-code and specificity decisions cite the ICD-10-CM Official Guidelines, and those decisions stay in review until a guideline citation is attached.

This is clinical coding integrity, not software security: the "code" is a diagnosis code.

## Results

Live run on Nebius Token Factory, commit `cfaee89`, 2026-10-27. Source: [eval/results/live/RESULTS.md](eval/results/live/RESULTS.md). C0+ is the rules queue a coder sees today (approved plus review). C4 is the full cascade.

| Set | Charts | Config | Precision | Recall | F1 | Review items / chart | $ / chart | P50 / P95 s |
|---|---:|---|---:|---:|---:|---:|---:|---:|
| **Gold-A** (headline, hand-labeled) | 10 | C0+ rules | 0.755 | 1.000 | 0.860 | 4.90 | 0 | — |
| | | **C4 cascade** | **0.872** | **0.919** | **0.895** | **4.20** | 0.0062 | 12.2 / 29.2 |
| Gold-B (hard-negative and regression suite) | 66 | C0+ rules | 0.598 | 1.000 | 0.749 | 2.00 | 0 | — |
| | | **C4 cascade** | **1.000** | **1.000** | **1.000** | **1.24** | 0.0014 | 2.18 / 14.90 |

**Gold-A is the benchmark.** Ten outpatient charts labeled by hand. The labels are pending certified-coder sign-off (`review_status` in [eval/gold/gold_a.json](eval/gold/gold_a.json)), and ten charts give wide intervals (C4 precision 95% CI 0.73–0.94, recall 0.79–0.97). C4 raises precision from 0.755 to 0.872 and cuts the queue from 4.9 to 4.2 items per chart, at the cost of three missed codes (`D64.9` and `I12.9` on pt-001, `I50.9` on pt-004).

**Gold-B is a regression suite, not a benchmark.** The charts were built by the same author as the rules, around known traps. It answers one question: does the cascade still clear the hard negatives? On this run C4 passed all 11 categories (negation, family history, screening, medication-only, hypothetical plan, resolved, copied-forward, cross-sentence negation, acute-not-chronic, unspecified sibling, wrong specificity), flagged 3/3 unsupported existing codes, and cut review burden 37.9% against C0+ (2.00 to 1.24) at $0.00136 per chart. Two live C4 passes agreed on 64 of 66 charts (0.970). One Super call returned HTTP 529 on a C2 chart, and that chart fell back to review.

Cost is reported Token Factory usage times the public catalog price. `eval/results/mock/` holds offline regression output from the recorded client; those numbers are not model quality. The schema is in [docs/eval.md](docs/eval.md).

## Why it matters

Risk-adjustment coding pays on the conditions the chart actually supports. An unspecified diabetes or heart-failure code leaves a more specific HCC off the record. A negated diagnosis, a family history, or a screening score can put a code on the record that the note does not support. Either mistake is a queue a coder has to clear by hand. CodeSentinel shortens that queue and attaches the chart quote and the guideline text a coder needs to sign each row.

## How it works

`gap_pipeline.py` proposes candidates. `codesentinel.nemotron.cascade` decides what is shown. C1 stops after triage and the quote verifier. C2 adds Super. C3 adds the guideline lookup. C4 adds Ultra.

```mermaid
flowchart TD
  chart[Chart] --> rules[Deterministic rules]
  rules --> light[Nemotron Lightning triage]
  light --> verify[Quote verifier]
  verify --> guide[ICD-10-CM guideline citation]
  guide --> super[Nemotron Super adjudication]
  super --> ultra[Nemotron Ultra escalation]
  ultra --> list[Coder worklist]
  verify -->|quote not in the chart| drop[Dropped as unsupported]
  guide -->|no citation on a combination or specificity row| review[Held in review]
```

| Step | Model | What it does |
|---|---|---|
| Triage | `nvidia/Nemotron-3_5-Lightning` | One structured JSON call per candidate. Temperature 0, thinking off. If a live `models.list()` ever omits Lightning, triage falls back to `nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B`. |
| Adjudication | `nvidia/nemotron-3-super-120b-a12b` | Medium-confidence survivors, plus every specificity upgrade and combination code. Temperature 0.6, thinking on. |
| Escalation and physician query | `nvidia/Nemotron-3-Ultra-550b-a55b` | Conflicts, high-value HCC upgrades (RAF at least 0.3), and non-leading physician queries. |
| Quote verifier | none | A supporting quote must be a whitespace-normalized substring of the note (at least 12 characters), an exact lab or medication line, or a structured record id. Anything else is `unsupported`. |
| Guidelines | Tavily | Official ICD-10-CM guideline text outranks other CMS pages. A code printed next to the wrong title is rejected. A secondary page is kept only when the titles match, and the API labels it `Secondary source`. No accepted passage means no citation. |

Calls use `POST /chat/completions` on `https://api.tokenfactory.nebius.com/v1/` with `response_format.type = json_schema`. A bad JSON payload gets one repair retry, then the candidate is held for review. Every audit returns a receipt: model, calls, tokens, latency, and catalog cost.

Model ids are checked against Token Factory at startup. A live `GET /v1/models` on 2026-10-27 returned these Nemotron ids, and the client uses the first four with this exact casing:

```text
nvidia/Nemotron-3_5-Lightning
nvidia/nemotron-3-super-120b-a12b
nvidia/Nemotron-3-Ultra-550b-a55b
nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B
nvidia/Nemotron-3.5-Super-VL
```

<details>
<summary>Two more walks through the demo</summary>

<p align="center">
  <img src="docs/assets/guideline-evidence.gif" alt="Evidence panel for Robert Williams, with a guideline citation chip on E11.22." width="1000">
</p>

<p align="center"><em>Evidence for E11.22: the chart quote, the guideline citation, and its source tier.</em></p>

<p align="center">
  <img src="docs/assets/review-and-eval.gif" alt="Accept and dismiss on a worklist, then the eval dashboard." width="1000">
</p>

<p align="center"><em>The coder accepts or dismisses. Nothing is submitted. The Eval tab reads <code>eval/results/live/summary.json</code>.</em></p>

</details>

## Features

- Four rule tiers: lab thresholds, phenotype patterns, note patterns with a negation and screening window, then specificity upgrades and combination codes.
- Nemotron cascade with a depth switch: C1 triage, C2 Super, C3 guidelines, C4 Ultra.
- Quote verifier that the model cannot override.
- Guideline gate: combination and specificity rows stay in `review` until a citation is attached.
- Source tier on every citation: `Official guidelines` or `Secondary source`. Mislabeled code lists are dropped.
- Receipt per audit, plus `POST /audit` contract 1.0.0 and an MCP endpoint.
- PHI patterns (SSN, MRN, email, US phone) are refused before a note is sent.
- Mock mode with the keys unset, so the demo and the test suite run offline.

## Quickstart

Python 3.12. Keys are optional. Leave both blank for the recorded client.

```bash
git clone https://github.com/kishanraj41/codesentinel.git
cd codesentinel
python -m venv .venv
.venv\Scripts\activate          # Windows
# source .venv/bin/activate     # macOS / Linux
pip install -r requirements.txt
copy .env.example .env          # Windows
# cp .env.example .env          # macOS / Linux
python mcp_server.py
```

```bash
curl http://localhost:8000/health
curl http://localhost:8000/patients
curl -X POST http://localhost:8000/audit -H "Content-Type: application/json" -d "{\"patient_id\":\"pt-001\"}"
```

| Variable | Role |
|---|---|
| `NEBIUS_API_KEY` | Token Factory. Empty runs the recorded Nemotron client (`mode: mock`). |
| `NEBIUS_BASE_URL` | Default `https://api.tokenfactory.nebius.com/v1/`. |
| `TAVILY_API_KEY` | Confirms the official FY2026 guideline PDF and fills codes with no curated section. Empty skips that check; curated sections are still cited. |
| `GUIDELINE_CACHE_DIR` | Disk cache for Tavily extracts. Default `.cache/guidelines`. |

With both keys unset, curated guideline sections are still cited. Set both keys for Token Factory and Tavily.

Web demo, from `web/`:

```bash
pip install -r requirements.txt
uvicorn app.main:app --port 8080
```

Open http://localhost:8080, choose **Margaret Whitmore**, and click **Audit chart**. With `AUDIT_API_BASE` unset, that call stays in-process and the cascade runs in mock mode. See [web/README.md](web/README.md).

Eval:

```bash
python -m eval.run --split test --mode mock    # writes eval/results/mock/
python -m eval.run --split test --mode live    # writes eval/results/live/, requires both API keys
```

Tests (run with the keys unset so nothing calls the network):

```bash
python -m unittest discover -s tests
python -m unittest eval.tests.test_harness
python -m pytest web/tests -q
python run_tests.py
```

<details>
<summary>Project structure</summary>

```text
mcp_server.py            POST /audit and the MCP endpoint
gap_pipeline.py          Rule tiers that propose candidates
codesentinel/nemotron/   Lightning, Super, Ultra cascade
codesentinel/tavily/     Guideline search, extract, and citation checks
codesentinel/guidelines/ Curated ICD-10-CM guideline sections
codesentinel/policy.py   Production checks. The server does not import eval/
eval/                    Gold sets, harness, and mock or live runner
eval/results/live/       Published live run: summary, RESULTS.md, recorded completions
eval/results/mock/       Offline regression output
web/                     Judge demo (Audit and Eval tabs)
docs/api.md              Audit contract 1.0.0
docs/eval.md             Metrics schema
docs/assets/             Logo, banner, and demo GIFs
```

</details>

## API

`POST /audit` is contract 1.0.0. The field list, citation object (`source_tier`, `source_label`), and the guideline gate are in [docs/api.md](docs/api.md).

`POST /mcp` exposes `detect_coding_gaps`, `get_patient_summary`, `validate_gap`, `list_detected_gaps`, and `draft_physician_query`. `detect_coding_gaps` runs the same verified pipeline as `POST /audit`. Point an MCP client at `http://localhost:8000/mcp`. `python mock_fhir_server.py` serves patients `synth-001`, `synth-002`, and `synth-003` on port 9090.

Dollar fields are RAF times a base rate: a relative-weight illustration, not a billing recommendation.

## Safety and limitations

- Demo and evaluation charts are generated test records; CodeSentinel is a reviewer aid, not medical advice.
- `POST /audit` refuses SSN-like, MRN-like, email, and US phone patterns.
- A person decides. Accept and dismiss stay on the coder. This does not submit a claim.
- Combination and specificity rows without a guideline citation stay in review.
- English, outpatient-style notes. The regex tiers miss paraphrases the recorded client will not invent.
- No claim of HIPAA compliance. A production deployment would need Zero Data Retention, a dedicated endpoint, and the right agreements.
- `MAX_DAILY_USD` is documented in [.env.example](.env.example) and is not enforced on the audit service yet.
- Gold-A is 10 charts pending coder sign-off. Gold-B was built around the same phenomena as the rules, so it is a regression suite rather than an independent benchmark.

<details>
<summary>Roadmap</summary>

- Certified-coder sign-off on Gold-A, then grow it past ten charts.
- Enforce the daily dollar cap on the audit service.
- Keep MTSamples as a queue count until it has labels.

</details>

## License

MIT. See [LICENSE](LICENSE).

## Acknowledgments

NVIDIA Nemotron on Nebius Token Factory for triage, adjudication, and escalation. Tavily for guideline retrieval. CMS and CDC publish the ICD-10-CM Official Guidelines the citations quote.

## Project history

CodeSentinel evolved from Ctrl+Alt+Heal, Kishan's earlier hackathon project (snapshot 2026-05-07); see [docs/changes.md](docs/changes.md) for what was rebuilt during the Nebius hackathon window.
