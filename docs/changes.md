# Changes since the earlier prototype

CodeSentinel grew out of an earlier prototype, last snapshotted on 2026-05-07 (commit `cdaccca`). This repository starts fresh from the rebuilt code. The prototype's full history is archived separately and can be shared with judges on request.

The earlier prototype was an MCP gap finder. Its README described ClinicalBERT, NegSpacy, and a 33-agent runtime. `agents/` is still in the tree. The CodeSentinel server does not import it, and `requirements.txt` does not install those models. `agent_15_reconciliation.py` talked to local Ollama and parsed JSON with a regex. That path is unused.

## What the current tree adds

- Product name CodeSentinel, and `POST /audit` contract 1.0.0 (`docs/api.md`) with a worklist, dropped rows, timeline, receipt, and citations. Debug routes that fetched arbitrary FHIR URLs are gone. FHIR base URLs must be loopback or on `FHIR_ALLOWED_HOSTS`.
- Nemotron on Nebius Token Factory (`codesentinel/nemotron/`): Lightning triages, Super adjudicates, Ultra escalates and drafts physician queries. Output is JSON schema. A quote verifier outside the model drops quotes that are not in the chart. Thin evidence or a model disagreement becomes `abstain` or `review`.
- Tavily guideline gate (`codesentinel/tavily/`). Combination and specificity rows cannot be accepted without a citation. No keys means the recorded client and guideline fixtures (`mode=mock`). `python -m eval.run --split test --mode live` exits unless both `NEBIUS_API_KEY` and `TAVILY_API_KEY` are set, and it does not relabel a mock run as live.
- Cascade depths C1–C4 share `run_cascade`. C1 is triage plus the verifier. C2 adds Super. C3 adds Tavily. C4 adds Ultra. In mock eval, C1–C3 still use `eval/policy.py`. Mock C4 runs the real cascade.
- Combination codes can be proposed from chart evidence, not only when both parent codes are already on the condition list. A retraction in the next sentence is visible to triage. An accepted more-specific code drops the unspecified sibling in the same three-character family. Reduced ejection fraction can propose `I50.22`. An `N18` code requires a documented CKD diagnosis or chronicity of at least three months; one low eGFR or one acute kidney lab is not enough.
- Eval harness (`eval/`): Gold-A (10 synthetic charts) and Gold-B (90 synthetic FHIR charts, held-out test split of 66, seed `20261024`). Results the web demo reads are `eval/results/summary.json`. Published numbers in this tree are mock.
- Web demo (`web/`): Audit posts to the in-process cascade when `AUDIT_API_BASE` is unset. The Eval tab reads `eval/results/summary.json`.

## What this rebrand removes from the working tree

Slide pages `Architecture.html` and `Workflow.html`, and the three images under `screenshots/`. The Devpost link and the old product name are gone from the README, from `run_tests.py`, from the `agents` package docstring, and from the Artifact Registry name in `cloudbuild-mock-fhir.yaml` (`codesentinel-repo`). The disclosure of the earlier prototype is this file and the Project history section of the README.

HCC dollar fields are still RAF times a base rate. They are illustrative. Revenue figures from the May 2026 README are not repeated.
