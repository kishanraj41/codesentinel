# CodeSentinel eval results: live run on Nebius Token Factory

C0 and C0+ stay on the rules pipeline. C1–C4 ran through the real cascade on Token Factory. Nemotron responses were recorded under eval/fixtures/nemotron. Guideline citations target the ICD-10-CM Official Guidelines in force on the run date. Tavily retrieves the passage live from cms.gov or cdc.gov, and it is cited only when it is verbatim in the official PDF fetched in the same run; curated sections are the fallback and cross-check. Citation provenance per config is in summary.json (citation_provenance). Replay those Nemotron completions with CODESENTINEL_REPLAY=1.

## Gold-A: 10 hand-labeled charts (headline)

Ten outpatient charts (`pt-001` to `pt-010`), labeled by hand. Labels are pending certified-coder sign-off (`review_status` in `eval/gold/gold_a.json`), and ten charts give wide confidence intervals.

| Config | Precision | Recall | F1 | TP | FP | FN | Review items / chart | FP / chart | Hard-negative pass | Abstain rate | P50 s | P95 s | $ / chart | Tavily credits / chart | Charts past C1 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| C0 | 0.739 | 0.459 | 0.567 | 17 | 6 | 20 | 2.300 | 0.600 | 0.762 | 0.000 | 0.0008 | 0.0026 | 0.00000 | 0.00 | 0.00 |
| C0+ | 0.755 | 1.000 | 0.860 | 37 | 12 | 0 | 4.900 | 1.200 | 0.571 | 0.000 | 0.0000 | 0.0000 | 0.00000 | 0.00 | 0.00 |
| C1 | 0.857 | 0.973 | 0.911 | 36 | 6 | 1 | 4.500 | 0.600 | 0.857 | 0.000 | 3.9152 | 12.8334 | 0.00058 | 0.00 | 0.00 |
| C2 | 0.854 | 0.946 | 0.897 | 35 | 6 | 2 | 4.400 | 0.600 | 0.857 | 0.173 | 7.7636 | 80.3076 | 0.00158 | 0.00 | 1.00 |
| C3 | 0.895 | 0.919 | 0.907 | 34 | 4 | 3 | 4.100 | 0.400 | 0.857 | 0.173 | 7.1971 | 13.6122 | 0.00177 | 0.00 | 1.00 |
| C4 | 0.892 | 0.892 | 0.892 | 33 | 4 | 4 | 4.000 | 0.400 | 0.857 | 0.173 | 13.2215 | 51.7632 | 0.00657 | 0.00 | 1.00 |
| C4a | 0.897 | 0.946 | 0.921 | 35 | 4 | 2 | 4.200 | 0.400 | 0.905 | 0.038 | 4.1800 | 40.8741 | 0.00613 | 0.00 | 0.50 |

## Gold-B: hard-negative and regression suite

Author-constructed charts built around specific traps (negation, family history, screening, medication-only, wrong specificity). Use it to catch regressions, not as a benchmark.

Dataset **gold-b** split `test`: 66 generated FHIR test charts, seed 20261024. Text source: hand_paraphrase_bank_v1.

| Config | Precision | Recall | F1 | TP | FP | FN | Review items / chart | FP / chart | Hard-negative pass | Abstain rate | P50 s | P95 s | $ / chart | Tavily credits / chart | Charts past C1 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| C0 | 0.697 | 0.785 | 0.738 | 62 | 27 | 17 | 1.348 | 0.409 | 0.491 | 0.000 | 0.0006 | 0.0009 | 0.00000 | 0.00 | 0.00 |
| C0+ | 0.598 | 1.000 | 0.749 | 79 | 53 | 0 | 2.000 | 0.803 | 0.000 | 0.000 | 0.0000 | 0.0000 | 0.00000 | 0.00 | 0.00 |
| C1 | 0.975 | 0.975 | 0.975 | 77 | 2 | 2 | 1.242 | 0.030 | 0.962 | 0.000 | 2.2397 | 4.7756 | 0.00020 | 0.00 | 0.00 |
| C2 | 0.975 | 0.987 | 0.981 | 78 | 2 | 1 | 1.258 | 0.030 | 0.962 | 0.052 | 2.2392 | 7.8802 | 0.00043 | 0.00 | 1.00 |
| C3 | 1.000 | 0.975 | 0.987 | 77 | 0 | 2 | 1.212 | 0.000 | 1.000 | 0.037 | 2.1256 | 29.6223 | 0.00047 | 0.00 | 1.00 |
| C4 | 0.987 | 0.975 | 0.981 | 77 | 1 | 2 | 1.227 | 0.015 | 0.981 | 0.037 | 1.7216 | 20.4769 | 0.00136 | 0.00 | 1.00 |
| C4a | 1.000 | 0.975 | 0.987 | 77 | 0 | 2 | 1.212 | 0.000 | 1.000 | 0.007 | 3.7952 | 13.1968 | 0.00139 | 0.00 | 0.23 |

C0 is the approved bucket only. C0+ is approved plus review, which is the queue a coder actually sees. The headline comparison is C4 against C0+.

Latency is wall time per chart against Token Factory. Cost is reported token usage times the public catalog price.

## Review burden vs recall

| Config | Recall | Review items / chart | $ / chart |
|---|---:|---:|---:|
| C0 | 0.78481 | 1.348 | 0.00000 |
| C0+ | 1.0 | 2.000 | 0.00000 |
| C1 | 0.974684 | 1.242 | 0.00020 |
| C2 | 0.987342 | 1.258 | 0.00043 |
| C3 | 0.974684 | 1.212 | 0.00047 |
| C4 | 0.974684 | 1.227 | 0.00136 |
| C4a | 0.974684 | 1.212 | 0.00139 |

C4 review burden is 38.6% lower than C0+ on this split (2.000 → 1.227 items per chart). Recall 1.0 → 0.974684.

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
| wrong_specificity | 1 | 2 | 0.500 |

## Known failures

These are misses of mock C4 (the real cascade with the recorded client) on the held-out split, plus Gold-A misses. They are not a claim about live Nemotron.

- **gb-0051** (gold-b, hf_specificity) false_positive `I50.9`. The note documents reduced ejection fraction or systolic heart failure. The cascade proposes I50.22 as a specificity upgrade and accepts it only with a verified quote and a guideline citation. I50.9 is dropped only after that more specific code is accepted.
- **gb-0041** (gold-b, combo_forbid) false_negative `I12.9`. C4 did not show a gold gap. If the rules never proposed it, every config misses it.
- **pt-001** (gold-a, hand_labeled) false_negative `D64.9`. Hand-labeled Gold-A chart. See the rationale in eval/gold/gold_a.json.
- **pt-001** (gold-a, hand_labeled) false_negative `E11.22`. Hand-labeled Gold-A chart. See the rationale in eval/gold/gold_a.json.

C4 on gold-b:test: 1 false-positive codes and 2 false-negative codes across 66 charts. The list above is a diverse sample, not every miss.

## Unsupported existing codes

| Config | TP | FP | FN | Precision | Recall |
|---|---:|---:|---:|---:|---:|
| C0 | 0 | 0 | 3 | None | 0.0 |
| C0+ | 0 | 0 | 3 | None | 0.0 |
| C1 | 3 | 0 | 0 | 1.0 | 1.0 |
| C2 | 3 | 0 | 0 | 1.0 | 1.0 |
| C3 | 3 | 0 | 0 | 1.0 | 1.0 |
| C4 | 3 | 0 | 0 | 1.0 | 1.0 |
| C4a | 3 | 0 | 0 | 1.0 | 1.0 |

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
| C3 | 0 | 0 | tavily_live 22 |
| C4 | 0 | 0 | tavily_live 22 |
| C4a | 0 | 1 | tavily_live 23 |

Provenance counts distinct citations per chart. `tavily_live` passages were retrieved by Tavily and matched verbatim against the official PDF for the run's fiscal year; `curated_fallback` is a curated section re-verified against that PDF; `superseded_anchor` counts citations where the curated passage was no longer in the current edition and the gate decided on the live text.

Quote-verifier probe (`hallucinated_quote_dropped`): passed. A candidate whose only quote is absent from the chart is dropped. Rules candidates in this corpus quote the chart, so unsupported-claim drops on the split can be zero while the probe still passes.

## MTSamples review pile

The Kaggle CSV is not in the repo and was not passed to this run. Do not invent a reduced count. Re-run with --mtsamples PATH. Accuracy is not claimed on this corpus.

Status: pending_csv. Historical baseline remains 1188 review items. No reduced count is reported.

## Stability

C4 decision agreement across 2 passes: 0.954545. Two live C4 passes. Agreement is the share of charts with the same shown (code, decision) set.

## What not to claim

- Do not cite mock precision, recall, or dollars as Nemotron or Tavily measurements.
- Do not cite MTSamples for accuracy.
- Gold-A is the headline set, but it is 10 hand-labeled charts and a certified coder has not signed those labels yet (`review_status` in `eval/gold/gold_a.json`).
- Gold-B labels were written by construction, by the same author as the offline policy. The policy does not read the labels, but it was designed around the same phenomena. Held-out means the test charts were not used to edit prompts; it does not mean an independent model.

