# CodeSentinel

CodeSentinel reviews ICD-10-CM diagnosis codes on a clinical chart. Deterministic rules propose missed codes. NVIDIA Nemotron on Nebius Token Factory cross-examines each one. A verifier outside the model drops any quote that is not in the chart. Tavily cites official ICD-10-CM and CMS pages for combination-code and specificity decisions, and those decisions stay in review until a citation is attached.

The audience is a risk-adjustment coder. The worklist is what they would sign. Dollar fields are RAF times a base rate: a relative-weight illustration, not a billing recommendation.

## Synthetic data only

Every chart in this repository is synthetic. `pt-001` through `pt-010` live in `test_dataset.py`. Gold-B is a generated FHIR set (`gb-0001` through `gb-0090`). Do not paste real notes, and do not send PHI to this demo. `POST /audit` refuses SSN-like, MRN-like, email, and US phone patterns.

## Architecture

`gap_pipeline.py` proposes candidates in four tiers, then `codesentinel.nemotron.cascade` decides what a coder sees:

1. LOINC lab thresholds
2. Phenotype rules over medications, labs, and note patterns
3. Regex note patterns with a negation and screening window
4. Specificity upgrades and combination codes, then a small HCC table and a MEAT checklist

| Step | Model | Role |
|---|---|---|
| Triage | `nvidia/Nemotron-3_5-Lightning` | One structured JSON call per candidate. Temperature 0, thinking off. Falls back to `nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B` when a live `models.list()` does not include Lightning. |
| Adjudication | `nvidia/nemotron-3-super-120b-a12b` | Medium-confidence survivors, plus every specificity upgrade and combination code. Temperature 0.6, thinking on, reasoning effort low. |
| Escalation and physician query | `nvidia/Nemotron-3-Ultra-550b-a55b` | Conflicts, high-value HCC upgrades (RAF at least 0.3), and non-leading physician queries. Temperature 0.6, thinking on. |
| Quote verifier | none | Deterministic. A supporting quote must be a whitespace-normalized substring of the note (at least 12 characters), an exact structured lab or medication line, or a structured record id. Anything else is dropped as `unsupported`. |
| Guidelines | Tavily | Combination codes and specificity upgrades. Live search uses `search_depth=advanced`, `include_domains=["cms.gov","cdc.gov"]`, `chunks_per_source=3`, and `max_results=5`, with a query suffix for the FY2026 ICD-10-CM Official Guidelines for Coding and Reporting. That PDF outranks other CMS pages. A row with no citation is forced to `review` in code after the model returns. |

The same cascade has four depths. C1 stops after triage and the quote verifier. C2 adds Super. C3 adds the guideline lookup. C4 adds Ultra.

Calls use `POST /chat/completions` on `https://api.tokenfactory.nebius.com/v1/` with `response_format.type = json_schema`. A bad JSON payload gets one repair retry, then the candidate is held for review. HTTP 429 retries with backoff. Every audit returns a receipt: model, calls, tokens, latency, and catalog cost.

If evidence is thin, or triage and Super disagree, the decision is `abstain` or `review`.

`mcp_server.py` is the FastAPI service. `POST /audit` is contract 1.0.0 ([docs/api.md](docs/api.md)). `POST /mcp` is the SHARP MCP endpoint (`detect_coding_gaps`, `get_patient_summary`, `validate_gap`, `list_detected_gaps`, `draft_physician_query`). `detect_coding_gaps` runs the same verified pipeline as `POST /audit`.

The running server does not load ClinicalBERT, NegSpacy, or medspaCy. `agents/` remains in the tree and is not imported. FHIR base URLs from `X-FHIR-Server-URL` or `fhir_server_url` must be loopback or on `FHIR_ALLOWED_HOSTS` (`hapi.fhir.org` is allowed by default).

## Quickstart

```bash
git clone https://github.com/kishanraj41/codesentinel.git
cd codesentinel
python -m venv .venv
.venv\Scripts\activate          # Windows
# source .venv/bin/activate     # macOS / Linux
pip install -r requirements.txt
copy .env.example .env          # leave both keys blank for the recorded client
python mcp_server.py
```

```bash
curl http://localhost:8000/health
curl http://localhost:8000/patients
curl -X POST http://localhost:8000/audit -H "Content-Type: application/json" -d "{\"patient_id\":\"pt-001\"}"
```

With `NEBIUS_API_KEY` and `TAVILY_API_KEY` unset, `mode` is `mock` and `guideline_mode` is `fixture`. Fixture quotes are labeled and are not live official excerpts. Set both keys to call Token Factory and Tavily.

The web demo is in `web/`. From that directory, `uvicorn app.main:app --reload --port 8080` serves the Audit and Eval tabs. With `AUDIT_API_BASE` unset, Audit posts to the in-process cascade. See [web/README.md](web/README.md).

```bash
python -m unittest discover -s tests
python run_tests.py
python -m unittest eval.tests.test_harness
python -m pytest web/tests/test_app.py -q
```

`run_tests.py` checks the ten synthetic patients through the recorded Nemotron client. `test_mtsamples.py` stays on the rules-only count (`verify=False`).

For an MCP client, point it at `http://localhost:8000/mcp`. Initialize advertises `fhir_context_required: true`. Tool calls need `X-FHIR-Server-URL` on an allowlisted host. `python mock_fhir_server.py` listens on port 9090 with patients `synth-001`, `synth-002`, and `synth-003`.

## Eval results

**Mock run. These numbers are not live Nemotron or Tavily quality.** `NEBIUS_API_KEY` and `TAVILY_API_KEY` were unset. The published file is `eval/results/summary.json` (`mode` is `mock`). The full write-up is [eval/RESULTS.md](eval/RESULTS.md). The schema is [docs/eval.md](docs/eval.md).

Dataset: Gold-B split `test`, 66 synthetic FHIR charts, seed `20261024`. C0 is the rules approved bucket. C0+ is approved plus review. Mock C1–C3 come from the deterministic policy in `eval/policy.py`. Mock C4 is the real cascade with the recorded client and guideline fixtures. The headline comparison is C4 against C0+.

| Config | Precision | Recall | F1 | TP | FP | FN | Review / chart |
|---|---:|---:|---:|---:|---:|---:|---:|
| C0 | 0.697 | 0.785 | 0.738 | 62 | 27 | 17 | 1.348 |
| C0+ | 0.598 | 1.000 | 0.749 | 79 | 53 | 0 | 2.000 |
| C1 | 0.929 | 1.000 | 0.963 | 79 | 6 | 0 | 1.333 |
| C2 | 0.929 | 1.000 | 0.963 | 79 | 6 | 0 | 1.333 |
| C3 | 0.963 | 1.000 | 0.981 | 79 | 3 | 0 | 1.288 |
| C4 | 1.000 | 1.000 | 1.000 | 79 | 0 | 0 | 1.197 |

Mock C4 hard-negative pass rate on that split is 1.000, including `acute_not_chronic` 3/3. Latency is local Python time. Cost is `characters/4` times the public catalog price.

Gold-A is 10 hand-labeled synthetic charts and is not the headline benchmark (`review_status` is `pending_coder_signoff`). Mock C4 on Gold-A: precision 0.878, recall 0.973, 5 false positives, 1 false negative, 4.1 review items per chart. Known misses: `pt-004` false negative `I50.9`, `pt-001` false positive `E78.5`.

A live run is a separate command and will not relabel this file:

```bash
python -m eval.run --split test --mode live
```

That command exits unless both `NEBIUS_API_KEY` and `TAVILY_API_KEY` are set. It records Nemotron responses for replay (`CODESENTINEL_REPLAY=1`). Do not set replay on the live command.

## Limitations

- The table above is a mock run. Do not quote it as live model quality.
- Gold-B labels were written by construction, by the same author as the offline policy. The policy does not read the labels. Held-out means the test charts were not used to edit prompts.
- English outpatient-style notes. The regex tiers miss paraphrases the recorded client will not invent.
- No claim of HIPAA compliance. A production chart with PHI would need Zero Data Retention, a dedicated endpoint, and the right agreements.
- `MAX_DAILY_USD` is documented in `.env.example` and is not enforced on the audit service yet.
- `test_mtsamples.py` counts gaps on unlabeled notes. It is not a precision or false-positive rate. The Kaggle CSV is not in the repo.

## License

MIT

## Project history

CodeSentinel evolved from an earlier prototype (snapshot 2026-05-07); see [docs/changes.md](docs/changes.md) for what was rebuilt during the Nebius hackathon window.
