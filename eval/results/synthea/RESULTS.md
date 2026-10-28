# CodeSentinel eval results: Synthea gap benchmark

Run: live, commit `9528f43+eval/synthea`, finished 2026-10-27 19:25 CDT. Raw numbers: [summary.json](summary.json), per-chart outcomes [charts.json](charts.json), labels [labels.jsonl](labels.jsonl), chart hashes [manifest.json](manifest.json). Code: `9528f43` plus the `eval/synthea/` files committed in `dde71c1`.

400 Synthea patients (Synthea v4.0.0, seed 20261027, Texas, ages 45-90, reference date 2026-10-27). Conditions are Synthea's, mapped to ICD-10-CM with the NLM SNOMED CT to ICD-10-CM map and the FY 2027 combination-code guidelines. A seeded 30% of the in-scope billed codes were removed from each chart's condition list; the removed codes the 12-month chart still supports are the gold gaps (75 gaps on 72 charts). 139 removed codes with no support in the chart are neutral. 34 charts carry one billed code Synthea says the patient does not have.

| Config | Precision | Recall | F1 | Review items / chart | Gap rows / chart | $ / chart | P50 / P95 s |
|---|---:|---:|---:|---:|---:|---:|---:|
| C0 Rules only, approved bucket | 0.126 | 0.627 | 0.210 | 0.93 | 0.93 | 0 | — |
| C0+ Rules only, approved + review | 0.051 | 0.840 | 0.096 | 3.09 | 3.09 | 0 | — |
| C1 Rules + Lightning triage + quote verifier | 0.043 | 0.027 | 0.033 | 5.11 | 0.12 | 0.00106 | 4.8 / 24.6 |
| C2 C1 + Super adjudication, no Tavily | 0.041 | 0.027 | 0.032 | 5.11 | 0.12 | 0.00107 | 4.3 / 21.8 |
| C3 C2 + Tavily guideline grounding | 0.058 | 0.040 | 0.047 | 5.12 | 0.13 | 0.00106 | 3.9 / 21.9 |
| C4 C3 + Ultra escalation (full cascade) | 0.041 | 0.027 | 0.032 | 5.11 | 0.12 | 0.00114 | 4.1 / 31.6 |

Review items count every row sent to a coder, including unsupported-existing flags on billed codes. Gap rows count only proposed additions.

## 95% intervals (Wilson)

| Config | Precision CI | Recall CI | TP / FP / FN |
|---|---|---|---|
| C0 | 0.096–0.164 | 0.514–0.727 | 47 / 326 / 28 |
| C0+ | 0.040–0.065 | 0.741–0.906 | 63 / 1173 / 12 |
| C1 | 0.012–0.145 | 0.007–0.092 | 2 / 44 / 73 |
| C2 | 0.011–0.137 | 0.007–0.092 | 2 / 47 / 73 |
| C3 | 0.020–0.156 | 0.014–0.111 | 3 / 49 / 72 |
| C4 | 0.011–0.137 | 0.007–0.092 | 2 / 47 / 73 |

## Per code (C0+ rules vs C4 cascade)

| Code | Gold gaps | C0+ TP / FP | C4 TP / FP |
|---|---:|---:|---:|
| D64.9 | 2 | 1 / 21 | 1 / 0 |
| E11.22 | 1 | 1 / 11 | 0 / 0 |
| E11.65 | 1 | 0 / 11 | 0 / 6 |
| E11.9 | 3 | 0 / 10 | 0 / 4 |
| E66.9 | 0 | 0 / 1 | 0 / 0 |
| E78.0 | 0 | 0 / 19 | 0 / 6 |
| E78.1 | 1 | 0 / 0 | 0 / 0 |
| E78.5 | 33 | 28 / 103 | 0 / 1 |
| F41.1 | 0 | 0 / 158 | 0 / 6 |
| I10 | 30 | 30 / 152 | 1 / 1 |
| I12.9 | 1 | 0 / 12 | 0 / 0 |
| I50.9 | 1 | 1 / 175 | 0 / 0 |
| J44.1 | 0 | 0 / 19 | 0 / 1 |
| K76.89 | 0 | 0 / 21 | 0 / 0 |
| M05.79 | 0 | 0 / 1 | 0 / 1 |
| N18.3 | 0 | 0 / 3 | 0 / 0 |
| N18.30 | 2 | 2 / 0 | 0 / 0 |
| N18.4 | 0 | 0 / 4 | 0 / 1 |
| N18.5 | 0 | 0 / 10 | 0 / 1 |
| N18.9 | 0 | 0 / 133 | 0 / 6 |
| R73.03 | 0 | 0 / 17 | 0 / 13 |
| R81 | 0 | 0 / 292 | 0 / 0 |

## What happened to the gold gaps

| Config | Outcome counts |
|---|---|
| C0 | dropped:rules 16, not_proposed 12, shown:accept 47 |
| C0+ | not_proposed 12, shown:accept 47, shown:review 16 |
| C1 | dropped:copied_forward_unclear 57, dropped:hypothetical_plan 2, dropped:negated 2, not_proposed 12, shown:accept 2 |
| C2 | dropped:copied_forward_unclear 56, dropped:hypothetical_plan 2, dropped:negated 2, dropped:unsupported_claim 1, not_proposed 12, shown:accept 2 |
| C3 | dropped:copied_forward_unclear 56, dropped:hypothetical_plan 2, dropped:negated 2, not_proposed 12, shown:accept 3 |
| C4 | dropped:copied_forward_unclear 56, dropped:hypothetical_plan 2, dropped:negated 2, dropped:unsupported_claim 1, not_proposed 12, shown:accept 2 |

## Unsupported existing codes (C4)

Planted: 34. Flagged and planted: 34. Flagged on a billed code Synthea does have: 1962. Missed: 0.

## How the labels are made

- Generator: Synthea, run offline with the command in `eval/synthea/README.md`. The author wrote no chart text, condition, lab or medication.
- Mapping: `eval/synthea/snomed_icd10cm.csv`, one row per SNOMED concept with its NLM I-MAGIC source (SNOMED CT to ICD-10-CM Map release 20260901).
- Combination codes: rules R1–R8 in `eval/synthea/build.py`, each citing the FY 2027 ICD-10-CM Official Guidelines or Tabular List.
- Support: a removed code is a gold gap only if the chart window has a note naming it, a medication Synthea links to it, or a lab meeting a published criterion (`manifest.json`).
- Scoring: a shown code that is the dotted parent of a gold code counts as that code (CodeSentinel writes `N18.3`; the FY 2027 billable code is `N18.30`).

## Caveats

- Synthea notes are template text and rarely name chronic conditions, so most evidence is medications and labs. The cascade sends lab-only and medication-only rows to review instead of accepting them.
- The cascade prompt carries the first 40 structured lines of the chart, labs first, so medication lines can be cut on lab-heavy charts.
- Synthea sometimes records a diabetic kidney condition without a type 2 diabetes condition; the NLM map still maps it to an E11 code. Those charts rarely support an E11.22 gap, so most are neutral.
- Gold gaps are dominated by E78.5 and I10, which Synthea links to statins and antihypertensives. Diabetes, CKD and combination-code gaps are few, so their per-code numbers are anecdotal.
- Codes outside CodeSentinel's rules (for example I11.0, I13.0, N18.1, N18.2, E11.29, E11.40) stay billed and are never removed. The benchmark measures gap finding on the codes the rules cover.
- Labels are derived by code from Synthea's conditions; no certified coder reviewed them.
- Seven charts hit a Token Factory models.list timeout during the run (six in C4, one in C1); the cascade used its default model ids for those charts.
