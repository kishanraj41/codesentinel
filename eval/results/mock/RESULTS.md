# CodeSentinel eval results: mock regression run (not live model quality)

> This file is offline regression output from the recorded client. The published live numbers are in [eval/results/live/RESULTS.md](../live/RESULTS.md).

NEBIUS_API_KEY and TAVILY_API_KEY were unset, or the run was forced to mock. C0 and C0+ are the rules pipeline. C1–C3 decisions come from the deterministic policy in eval/policy.py. C4 decisions come from the CodeSentinel cascade in mock mode: the recorded client applies the same chart-text instructions as the live prompts (negation, family history, screening, and medication-only). Curated sections of the current guidelines are cited for the codes they cover; other guideline quotes are labeled fixtures. Do not cite the mock C4 row as live model quality.

## Gold-A: 10 hand-labeled charts

Ten outpatient charts (`pt-001` to `pt-010`), labeled by hand. Labels are pending certified-coder sign-off (`review_status` in `eval/gold/gold_a.json`), and ten charts give wide confidence intervals.

| Config | Precision | Recall | F1 | TP | FP | FN | Review items / chart | FP / chart | Hard-negative pass | Abstain rate | P50 s | P95 s | $ / chart | Tavily credits / chart |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| C0 | 0.739 | 0.459 | 0.567 | 17 | 6 | 20 | 2.300 | 0.600 | 0.762 | 0.000 | 0.0007 | 0.0021 | 0.00000 | 0.00 |
| C0+ | 0.755 | 1.000 | 0.860 | 37 | 12 | 0 | 4.900 | 1.200 | 0.571 | 0.000 | 0.0000 | 0.0000 | 0.00000 | 0.00 |
| C1 | 0.878 | 0.973 | 0.923 | 36 | 5 | 1 | 4.400 | 0.500 | 0.857 | 0.269 | 0.0001 | 0.0013 | 0.00009 | 0.00 |
| C2 | 0.947 | 0.973 | 0.960 | 36 | 2 | 1 | 4.100 | 0.200 | 1.000 | 0.212 | 0.0001 | 0.0013 | 0.00010 | 0.00 |
| C3 | 0.947 | 0.973 | 0.960 | 36 | 2 | 1 | 4.100 | 0.200 | 1.000 | 0.154 | 0.0001 | 0.0014 | 0.00010 | 0.00 |
| C4 | 0.878 | 0.973 | 0.923 | 36 | 5 | 1 | 4.400 | 0.500 | 0.857 | 0.058 | 0.0028 | 0.0123 | 0.00267 | 0.00 |

## Gold-B: hard-negative and regression suite

Author-constructed charts built around specific traps (negation, family history, screening, medication-only, wrong specificity). Use it to catch regressions, not as a benchmark.

Dataset **gold-b** split `test`: 66 generated FHIR test charts, seed 20261024. Text source: hand_paraphrase_bank_v1.

| Config | Precision | Recall | F1 | TP | FP | FN | Review items / chart | FP / chart | Hard-negative pass | Abstain rate | P50 s | P95 s | $ / chart | Tavily credits / chart |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| C0 | 0.697 | 0.785 | 0.738 | 62 | 27 | 17 | 1.348 | 0.409 | 0.491 | 0.000 | 0.0008 | 0.0014 | 0.00000 | 0.00 |
| C0+ | 0.598 | 1.000 | 0.749 | 79 | 53 | 0 | 2.000 | 0.803 | 0.000 | 0.000 | 0.0000 | 0.0000 | 0.00000 | 0.00 |
| C1 | 0.929 | 1.000 | 0.963 | 79 | 6 | 0 | 1.333 | 0.091 | 0.887 | 0.081 | 0.0001 | 0.0002 | 0.00003 | 0.00 |
| C2 | 0.929 | 1.000 | 0.963 | 79 | 6 | 0 | 1.333 | 0.091 | 0.887 | 0.081 | 0.0001 | 0.0002 | 0.00003 | 0.00 |
| C3 | 0.963 | 1.000 | 0.981 | 79 | 3 | 0 | 1.288 | 0.045 | 0.943 | 0.044 | 0.0001 | 0.0002 | 0.00003 | 0.00 |
| C4 | 1.000 | 1.000 | 1.000 | 79 | 0 | 0 | 1.242 | 0.000 | 1.000 | 0.044 | 0.0021 | 0.0035 | 0.00070 | 0.00 |

C0 is the approved bucket only. C0+ is approved plus review, which is the queue a coder actually sees. The headline comparison is C4 against C0+.

Latency on a mock run is local Python time. It is not Token Factory latency. Cost on a mock run is `characters/4` times the public catalog price and is labeled as an estimate.

## Review burden vs recall

| Config | Recall | Review items / chart | $ / chart |
|---|---:|---:|---:|
| C0 | 0.78481 | 1.348 | 0.00000 |
| C0+ | 1.0 | 2.000 | 0.00000 |
| C1 | 1.0 | 1.333 | 0.00003 |
| C2 | 1.0 | 1.333 | 0.00003 |
| C3 | 1.0 | 1.288 | 0.00003 |
| C4 | 1.0 | 1.242 | 0.00070 |

C4 review burden is 37.9% lower than C0+ on this split (2.000 → 1.242 items per chart). Recall 1.0 → 1.0.

## Hard-negative pass rate (C4)

| Category | Pass | Total | Rate |
|---|---:|---:|---:|
| acute_not_chronic | 3 | 3 | 1.000 |
| copied_forward | 2 | 2 | 1.000 |
| cross_sentence_negation | 3 | 3 | 1.000 |
| family_history | 2 | 2 | 1.000 |
| hypothetical_plan | 5 | 5 | 1.000 |
| medication_only | 20 | 20 | 1.000 |
| negation | 7 | 7 | 1.000 |
| resolved | 2 | 2 | 1.000 |
| screening | 2 | 2 | 1.000 |
| unspecified_sibling | 5 | 5 | 1.000 |
| wrong_specificity | 2 | 2 | 1.000 |

## Known failures

These are misses of mock C4 (the real cascade with the recorded client) on the held-out split, plus Gold-A misses. They are not a claim about live Nemotron.

- **pt-004** (gold-a, hand_labeled) false_negative `I50.9`. Hand-labeled Gold-A chart. See the rationale in eval/gold/gold_a.json.
- **pt-001** (gold-a, hand_labeled) false_positive `E78.5`. Hand-labeled Gold-A chart. See the rationale in eval/gold/gold_a.json.

C4 on gold-b:test: 0 false-positive codes and 0 false-negative codes across 66 charts. The list above is a diverse sample, not every miss.

## Unsupported existing codes

| Config | TP | FP | FN | Precision | Recall |
|---|---:|---:|---:|---:|---:|
| C0 | 0 | 0 | 3 | None | 0.0 |
| C0+ | 0 | 0 | 3 | None | 0.0 |
| C1 | 3 | 0 | 0 | 1.0 | 1.0 |
| C2 | 3 | 0 | 0 | 1.0 | 1.0 |
| C3 | 3 | 0 | 0 | 1.0 | 1.0 |
| C4 | 3 | 0 | 0 | 1.0 | 1.0 |

This task is separate from gap precision and recall. C0 and C0+ do not run the two-way pass.

## Degraded runs

Charts that fell back to the recorded client: 0.
No degraded reasons recorded.

## Guideline decisions

| Config | Decisions changed by the guideline step | Unsupported-claim drops | Citations by provenance |
|---|---:|---:|---|
| C0 | 0 | 0 | — |
| C0+ | 0 | 0 | — |
| C1 | 0 | 0 | — |
| C2 | 0 | 0 | — |
| C3 | 5 | 0 | — |
| C4 | 0 | 0 | curated_fallback 24 |

Provenance counts distinct citations per chart. `tavily_live` passages were retrieved by Tavily and matched verbatim against the official PDF for the run's fiscal year; `curated_fallback` is a curated section re-verified against that PDF; `superseded_anchor` counts citations where the curated passage was no longer in the current edition and the gate decided on the live text.

Quote-verifier probe (`hallucinated_quote_dropped`): passed. A candidate whose only quote is absent from the chart is dropped. Rules candidates in this corpus quote the chart, so unsupported-claim drops on the split can be zero while the probe still passes.

## MTSamples review pile

The Kaggle CSV is not in the repo and was not passed to this run. Do not invent a reduced count. Re-run with --mtsamples PATH. Accuracy is not claimed on this corpus.

Status: pending_csv. Historical baseline remains 1188 review items. No reduced count is reported.

## Stability

C4 decision agreement across 2 passes: 1.0. Mock C4 is the recorded cascade, which is deterministic, so agreement is 1 by construction and is not a live-model stability measurement. Re-run with NEBIUS_API_KEY set for a live pair.

## What not to claim

- Do not cite mock precision, recall, or dollars as Nemotron or Tavily measurements.
- Do not cite MTSamples for accuracy.
- Gold-A is the headline set, but it is 10 hand-labeled charts and a certified coder has not signed those labels yet (`review_status` in `eval/gold/gold_a.json`).
- Gold-B labels were written by construction, by the same author as the offline policy. The policy does not read the labels, but it was designed around the same phenomena. Held-out means the test charts were not used to edit prompts; it does not mean an independent model.

