# CodeSentinel eval

The published numbers are the live run in `eval/results/live/summary.json`, with the table in `eval/results/live/RESULTS.md` and the recorded Nemotron completions in `eval/results/live/recordings/`. `eval/results/mock/` is offline regression output from the recorded client; it is not live model quality. `--mode live` writes to `eval/results/live/`, mock runs write to `eval/results/mock/`, and `EVAL_OUT_DIR` overrides both. The web Eval tab reads `eval/results/live/summary.json` and falls back to the mock summary.

## What is measured

An item is one patient times one ICD-10-CM code. A prediction counts only when the code matches exactly. A family match (E11.9 when the gold code is E11.65) is a miss on the gold code and a false positive on the shown code.

Shown means `decision` is `accept`, `review`, or `abstain` and `shown_to_coder` is true. Rejected items are not shown.

Gap precision and recall use shown items whose `gap_type` is not `unsupported_existing`.

- True positive: shown gap code is in `expected_gaps`.
- False positive: shown gap code is not in `expected_gaps`.
- False negative: an `expected_gaps` code is not shown.

`unsupported_existing` is a second task. It does not enter gap precision or recall. It does count in review burden when `shown_to_coder` is true.

Review burden is every shown item per chart, including abstentions and unsupported-existing flags.

Hard-negative pass: a `must_not_flag` code is absent from the shown gap codes. Flagging that same code as unsupported-existing does not fail the hard negative.

Precision is null when there are no predicted positives (`tp + fp == 0`). Recall is null when there are no gold gaps. Intervals are 95% Wilson intervals on the micro counts.

Headline comparison is review burden against C0+ at the recall of that config. C0 shows the rules approved bucket only. C0+ shows approved plus review, which is the queue a coder sees.

## Datasets

Gold-B is synthetic FHIR R4. Ninety charts, thirty scenarios, three paraphrases each, ids `gb-0001` through `gb-0090`. Seed `20261024`. Text source `hand_paraphrase_bank_v1`. Names are `Case` plus the chart id. Notes are a hand paraphrase bank, not model output and not PHI.

Split, by scenario index and paraphrase 0:

| Split | Rule | Charts |
|---|---|---:|
| train | scenarios 0–11, paraphrase 0 | 12 |
| dev | scenarios 12–23, paraphrase 0 | 12 |
| test | every remaining chart | 66 |

Every scenario has at least two test charts. The published file is the test split. Dev was used while the harness was built. The test split was scored once after that, and the policy was not edited from the test scores.

Labels are in `eval/gold/gold_b.jsonl` (no note text). Bundles are in `eval/gold/bundles/`. `eval/gold/manifest.json` carries `label_sha256`.

Gold-A is the ten synthetic patients `pt-001` through `pt-010` from `test_dataset.py`, labeled in `eval/gold/gold_a.json`. `review_status` is `pending_coder_signoff`. Gold-A is the headline benchmark in the README, with the caveat that it is ten charts and the labels are pending certified-coder sign-off. Its summary is `gold_a_summary.json` in the same results folder. Gold-B is a hard-negative and regression suite built by the same author as the policy, not a benchmark.

Label change: Gold-A pt-002's expected heart-failure code is I50.22, not I50.9, because the note documents HFrEF (reduced ejection fraction).

MTSamples is workload only. The CSV is not in the repo. The runner counts how many rules review items the offline C4 policy still shows. It does not report precision or recall. The historical figure of 1,188 review items is the `test_mtsamples.py` docstring. `parse_note_to_profile` uses `hash()` for which conditions are already coded, so that count depends on `PYTHONHASHSEED`. Compare `c4_review_items_still_shown` with `rules_review_items` from the same process.

## Configurations

| Id | What runs |
|---|---|
| C0 | Rules pipeline, approved bucket only |
| C0+ | Rules pipeline, approved plus review |
| C1 | Rules candidates, Lightning triage, deterministic quote verifier |
| C2 | C1 plus Super adjudication, no web search |
| C3 | C2 plus Tavily guideline grounding (`cms.gov`, `cdc.gov`, advanced search = 2 credits) |
| C4 | C3 plus Ultra escalation |

Catalog prices, USD per 1M tokens, input/output, from the 2026-10-24 Token Factory list:

| Stage | Model | Input | Output |
|---|---|---:|---:|
| triage | `nvidia/Nemotron-3_5-Lightning` | 0.06 | 0.24 |
| adjudicate | `nvidia/nemotron-3-super-120b-a12b` | 0.30 | 0.90 |
| escalate | `nvidia/Nemotron-3-Ultra-550b-a55b` | 1.00 | 3.00 |

Base URL: `https://api.tokenfactory.nebius.com/v1/`.

## Offline and live

`AuditAdapter.audit(profile, config, mode)` chooses the path.

- `mode=mock`, or `EVAL_OFFLINE=1`, or `mode=auto` with `NEBIUS_API_KEY` unset: C0 and C0+ are the rules pipeline. C1–C3 decisions come from `eval/policy.py`. That policy does not read gold labels. C4 runs the CodeSentinel cascade with the recorded client and guideline fixtures. Cost is `len(prompt)//4` times the catalog price, and the prompt includes the chart note (truncated at 6,000 characters). Latency is local Python time. `summary.mode` is `mock`. Do not cite this path as live Nemotron quality.
- `mode=live` requires both `NEBIUS_API_KEY` and `TAVILY_API_KEY`. The command exits if either key is unset and does not relabel a mock run as live. C1–C4 then share `run_cascade`: C1 is triage and the quote verifier, C2 adds Super, C3 adds Tavily, and C4 adds Ultra. Nemotron responses are written under `eval/fixtures/nemotron` (gitignored). Guideline lookups use `eval/fixtures/guideline_cache` (gitignored). Replay those completions with `CODESENTINEL_REPLAY=1`. Do not set replay on the live command.
- `mode=auto` with `NEBIUS_API_KEY` set and `EVAL_OFFLINE` unset follows the live path. An optional external backend, if configured, is tried first; a raised exception is recorded and the adapter continues with the cascade or policy path above.

Guideline text used offline is in `GUIDELINE_FIXTURES` inside `eval/policy.py`. Those strings are original descriptions plus a public CMS URL. They are not quotations from the ICD-10-CM Official Guidelines.

## Adapter contract

```python
def audit_chart(profile: dict, config_id: str) -> dict:
    ...
```

`profile` matches `test_dataset.get_patient_profile`: `fhir_patient_id`, `demographics`, `clinical_notes[{text}]`, `observations[{loinc,name,value,unit,date}]`, `medications[{name,status}]`, `conditions[{icd10_code,display}]`, `encounters`, `claims_codes`.

Return value:

| Field | Meaning |
|---|---|
| `items` | List of item objects |
| `cost_usd` | Dollars charged or estimated for the chart |
| `tavily_credits` | Credits used |
| `tavily_cache_hits` | Cache hits |
| `tavily_would_search` | Searches that a live C3/C4 run would make |
| `guideline_decision_changes` | Decisions the guideline step changed |
| `mode` | `mock`, `live`, `mock_fallback`, `rules`, or `external` |
| `latency_s` | Filled by the adapter if the caller omits it |

Item fields the scorer reads: `code`, `decision` (`accept`, `review`, `reject`, `abstain`), `shown_to_coder`, `gap_type` (`missing`, `specificity_upgrade`, `combination`, `unsupported_existing`), `drop_reason` (`unsupported_claim` counts as an unsupported-claim drop).

## Schema `codesentinel.eval.summary.v1`

`eval/results/<mode>/summary.json` (Gold-B test):

| Field | Meaning |
|---|---|
| `schema` | `codesentinel.eval.summary.v1` |
| `generated_at` | UTC ISO timestamp |
| `mode` | `mock` or `live` |
| `mode_detail` | Plain-language caveat. Mock rows are not Nemotron quality |
| `keys` | `{nebius, tavily}` booleans, never the secrets |
| `backend` | `codesentinel.nemotron` when an external `audit_chart` was imported, else null |
| `live_errors` | Per-chart fallback messages |
| `dataset` | `name`, `split`, `n_charts`, `seed`, `text_source`, `not_model_authored`, `label_sha256`, `splits` |
| `configs` | One object per config, in order C0, C0+, C1, C2, C3, C4 |
| `chart_series` | `{config, recall, precision, review_burden_per_chart, cost_per_chart_usd}` for the burden-versus-recall plot |
| `comparisons` | C1–C4 against C0+: burden reduction and recall |
| `known_failures` | Up to five Gold-B C4 misses, then up to two Gold-A misses, cap seven. Deduped by scenario, failure type, and code |
| `known_failure_counts` | Full Gold-B C4 false-positive and false-negative counts, not just the sample |
| `stability` | Two C4 passes. Mock agreement is 1 by construction |
| `guardrail_probe` | A hallucinated quote is dropped |
| `mtsamples` | Workload object, or `status: pending_csv` |
| `limitations` | Claims that must not be made from this file |

Each `configs[]` object includes `id`, `label`, `n_charts`, `tp`, `fp`, `fn`, `precision`, `precision_ci95`, `recall`, `recall_ci95`, `f1`, `review_items`, `review_burden_per_chart`, `false_positives_per_chart`, `hard_negative_pass_rate` (`overall`, `pass`, `total`, `by_category`), `unsupported_existing`, `unsupported_claim_drops`, `abstain_count`, `abstain_rate`, `latency_p50_s`, `latency_p95_s`, `cost_per_chart_usd`, `tavily_credits_per_chart`, `tavily_cache_hits_per_chart`, `tavily_credits_if_uncached_per_chart`, `guideline_decision_changes`, `latency_scope`, `cost_basis`.

Per-config files `eval/results/<mode>/C0.json`, `C0_plus.json`, `C1.json`–`C4.json` add a `charts` array (patient id, scenario, tp/fp/fn codes, shown codes, gold codes, review items, latency, cost, mode). Gold-A uses the `gold_a_` prefix. `gold_a_summary.json` is the same schema reduced to `dataset`, `configs`, and `chart_series`.

A known-failure object: `dataset`, `patient_id`, `split`, `scenario`, `code`, `failure_type` (`false_positive` or `false_negative`), `configs_affected`, `cause`.

## How to run

From the repo root, with the rules pipeline importable (`gap_pipeline.py`, `test_dataset.py`):

```powershell
$env:PYTHONHASHSEED = "0"
python -m eval.run --split test --mode mock --mtsamples PATH\to\mtsamples.csv
```

Flags:

- `--split train|dev|test|all` (default `test`). `all` still writes the published `summary.json` from the test split, and writes `gold_b_all_summary.json` for every chart.
- `--mode auto|mock|live` (default `auto`).
- `--mtsamples PATH` or env `MTSAMPLES_CSV`. The CSV is not committed.
- `--skip-mtsamples`, `--skip-gold-a`.
- `--generate-only` rewrites bundles, `gold_b.jsonl`, and `manifest.json`.

Unit checks: `python -m unittest eval.tests.test_harness`.

`EVAL_OFFLINE=1` forces the offline path even when a key is set. `EVAL_IGNORE_RECORDED=1` skips the recorded-response cache.

## What not to claim

- Mock precision, recall, latency, and dollars are not live Nemotron or Tavily measurements. Mock C1–C3 are the offline policy. Mock C4 is the recorded cascade. `mode` in the JSON says which path ran.
- Do not cite a dollar figure from this harness as a production invoice. Mock cost is a character estimate on short synthetic notes. It is not the planning figure for a long chart with many candidates.
- Do not cite MTSamples for accuracy.
- Gold-A is ten hand labels and is pending coder sign-off.
- Gold-B labels and the offline policy were written by the same author. Held-out means the test charts were not used to edit the policy. It does not mean an independent model.
- Do not claim a revenue figure, a false-positive rate under one percent as a product guarantee, or that a shown code was accepted for billing. The task is coding accuracy: which codes are shown to a human, and which of those match the gold gaps.
