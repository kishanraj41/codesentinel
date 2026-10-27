# Changes since the earlier prototype

CodeSentinel evolved from Ctrl+Alt+Heal, Kishan's earlier hackathon project ([Devpost entry](https://devpost.com/software/ctrl-alt-heal), Agents Assemble hackathon), last snapshotted on 2026-05-07 (commit `cdaccca`). This repository starts fresh from the rebuilt code. The prototype's full history is archived separately and can be shared with judges on request.

Ctrl+Alt+Heal was an MCP gap finder. Its README described ClinicalBERT, NegSpacy, and a 33-agent runtime. That `agents/` package and the `data/` reference tables only it read were deleted from this tree during the Nebius window; the CodeSentinel server never imported them. The five MCP tool names carry over from the prototype.

## What the current tree adds

- Product name CodeSentinel, and `POST /audit` contract 1.0.0 (`docs/api.md`) with a worklist, dropped rows, timeline, receipt, and citations. Debug routes that fetched arbitrary FHIR URLs are gone. FHIR base URLs must be loopback or on `FHIR_ALLOWED_HOSTS`.
- Nemotron on Nebius Token Factory (`codesentinel/nemotron/`): Lightning triages, Super adjudicates, Ultra escalates and drafts physician queries. Output is JSON schema. A quote verifier outside the model drops quotes that are not in the chart. Thin evidence or a model disagreement becomes `abstain` or `review`.
- Tavily guideline gate (`codesentinel/tavily/`). Combination and specificity rows cannot be accepted without a citation. The target is the ICD-10-CM Official Guidelines edition in force on the run date (FY 2027 from 2026-10-01). Tavily retrieves the passage from cms.gov or cdc.gov, and it is cited only if it is verbatim in the official PDF; curated sections are the fallback and cross-check, and each citation records its provenance, fiscal year, URL, section and page. No keys means the recorded client and guideline fixtures (`mode=mock`). `python -m eval.run --split test --mode live` exits unless both `NEBIUS_API_KEY` and `TAVILY_API_KEY` are set, and it does not relabel a mock run as live.
- Cascade depths C1–C4 share `run_cascade`. C1 is triage plus the verifier. C2 adds Super. C3 adds Tavily. C4 adds Ultra. In mock eval, C1–C3 still use `eval/policy.py`. Mock C4 runs the real cascade.
- Combination codes can be proposed from chart evidence, not only when both parent codes are already on the condition list. A retraction in the next sentence is visible to triage. An accepted more-specific code drops the unspecified sibling in the same three-character family. Reduced ejection fraction can propose `I50.22`. An `N18` code requires a documented CKD diagnosis or chronicity of at least three months; one low eGFR or one acute kidney lab is not enough.
- Eval harness (`eval/`): Gold-A (10 hand-labeled charts, the headline set, labels pending coder sign-off) and Gold-B (90 author-constructed FHIR charts used as a hard-negative and regression suite, held-out test split of 66, seed `20261024`). The published live run is `eval/results/live/` (summary, RESULTS.md, recorded Nemotron completions). `eval/results/mock/` is offline regression output.
- Web demo (`web/`): Audit posts to the in-process cascade when `AUDIT_API_BASE` is unset. The Eval tab reads `eval/results/live/summary.json`, falling back to the mock summary. The hosted demo is `Dockerfile.web` on Render's free tier (`render.yaml`); inference stays on Nebius Token Factory.

## What this rebrand removes from the working tree

Slide pages `Architecture.html` and `Workflow.html`, and the three images under `screenshots/`. The old product name is gone from the README, from `run_tests.py`, and from the Artifact Registry name in `cloudbuild-mock-fhir.yaml` (`codesentinel-repo`). The disclosure of the earlier project is this file and the one-line Project history section of the README.

HCC dollar fields are still RAF times a base rate. They are illustrative. Revenue figures from the May 2026 README are not repeated.
