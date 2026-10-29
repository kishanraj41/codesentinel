# CodeSentinel eval results: Synthea gap benchmark

Run: live (held-out v9: two changes after v7, one reverted; single held-out run on final code), commit `8ab01be`, finished 2026-10-28 01:33 CDT. Raw numbers: [summary.json](summary.json).

Split: `held_out` from `eval/synthea/split.json` (seed 20261028; 100 dev, 300 held-out). Fixes were made on dev only. This is the third held-out run, one per code version: v1 on v7 (`b623d53`), v2 on v8 (`def9241`, whose info gate was then reverted) and this v9 run on the final code (`8ab01be`). Every run is kept unedited; the history is in [eval/synthea/README.md](../../../synthea/README.md).

300 Synthea patients (Synthea v4.0.0, seed 20261027, Texas, ages 45-90, reference date 2026-10-27). Conditions are Synthea's, mapped to ICD-10-CM with the NLM SNOMED CT to ICD-10-CM map and the FY 2027 combination-code guidelines. A seeded 30% of the in-scope billed codes were removed from each chart's condition list; the removed codes the 12-month chart still supports are the gold gaps (60 gaps on 57 charts). 103 removed codes with no support in the chart are neutral. 20 charts carry one billed code Synthea says the patient does not have.

| Config | Precision | Recall | F1 | Review items / chart | Gap rows / chart | Gap-row FP | Planted unsupported caught | Charts past C1 | $ / chart | P50 / P95 s |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| C0 Rules only, approved bucket | 0.207 | 0.650 | 0.315 | 0.63 | 0.63 | 149 | 0/20 | 0% | 0 | — |
| C0+ Rules only, approved + review | 0.070 | 0.817 | 0.128 | 2.35 | 2.35 | 656 | 0/20 | 0% | 0 | — |
| C1 Rules + Lightning triage + quote verifier | 0.183 | 0.667 | 0.288 | 3.83 | 0.73 | 178 | 20/20 | 0% | 0.00081 | 2.9 / 15.1 |
| C2 C1 + Super adjudication, no Tavily | 0.182 | 0.667 | 0.286 | 3.84 | 0.73 | 180 | 20/20 | 100% | 0.00119 | 3.9 / 33.5 |
| C3 C2 + Tavily guideline grounding | 0.184 | 0.667 | 0.289 | 3.83 | 0.72 | 177 | 20/20 | 100% | 0.00120 | 4.6 / 26.5 |
| C4 C3 + Ultra escalation (full cascade) | 0.183 | 0.667 | 0.287 | 3.84 | 0.73 | 179 | 20/20 | 100% | 0.00170 | 6.7 / 27.5 |
| C4a Adaptive: C1, full C4 when the chart has a combination/specificity candidate | 0.182 | 0.683 | 0.288 | 3.86 | 0.75 | 184 | 20/20 | 6% | 0.00118 | 2.9 / 19.4 |

Review items count every row sent to a coder, including unsupported-existing flags on billed codes. Gap rows count only proposed additions. Charts past C1 is the share of charts where Super, Tavily or Ultra ran (C2-C4 always; C4a only when the chart has a combination or specificity candidate).

## 95% intervals (Wilson)

| Config | Precision CI | Recall CI | TP / FP / FN |
|---|---|---|---|
| C0 | 0.156–0.271 | 0.524–0.758 | 39 / 149 / 21 |
| C0+ | 0.053–0.091 | 0.701–0.894 | 49 / 656 / 11 |
| C1 | 0.138–0.240 | 0.541–0.773 | 40 / 178 / 20 |
| C2 | 0.136–0.238 | 0.541–0.773 | 40 / 180 / 20 |
| C3 | 0.138–0.241 | 0.541–0.773 | 40 / 177 / 20 |
| C4 | 0.137–0.239 | 0.541–0.773 | 40 / 179 / 20 |
| C4a | 0.137–0.238 | 0.558–0.787 | 41 / 184 / 19 |

## Per code (C0+ rules vs C4 and C4a cascade)

| Code | Gold gaps | C0+ TP / FP | C4 TP / FP | C4a TP / FP |
|---|---:|---:|---:|---:|
| D64.9 | 2 | 1 / 18 | 1 / 4 | 1 / 4 |
| E11.22 | 1 | 1 / 7 | 0 / 0 | 1 / 2 |
| E11.65 | 1 | 0 / 7 | 0 / 7 | 0 / 7 |
| E11.9 | 2 | 0 / 9 | 0 / 8 | 0 / 9 |
| E66.9 | 0 | 0 / 1 | 0 / 0 | 0 / 0 |
| E78.0 | 0 | 0 / 16 | 0 / 16 | 0 / 16 |
| E78.1 | 1 | 0 / 0 | 0 / 0 | 0 / 0 |
| E78.5 | 28 | 23 / 74 | 15 / 71 | 15 / 71 |
| F41.1 | 0 | 0 / 119 | 0 / 11 | 0 / 9 |
| I10 | 22 | 22 / 114 | 22 / 28 | 22 / 28 |
| I12.9 | 1 | 0 / 10 | 0 / 0 | 0 / 5 |
| I50.9 | 1 | 1 / 130 | 1 / 0 | 1 / 0 |
| J44.1 | 0 | 0 / 14 | 0 / 0 | 0 / 0 |
| K76.89 | 0 | 0 / 18 | 0 / 9 | 0 / 9 |
| M05.79 | 0 | 0 / 1 | 0 / 1 | 0 / 1 |
| N18.3 | 0 | 0 / 3 | 0 / 3 | 0 / 2 |
| N18.30 | 1 | 1 / 0 | 1 / 0 | 1 / 0 |
| N18.4 | 0 | 0 / 2 | 0 / 2 | 0 / 2 |
| N18.5 | 0 | 0 / 7 | 0 / 5 | 0 / 5 |
| N18.9 | 0 | 0 / 104 | 0 / 12 | 0 / 12 |
| R73.03 | 0 | 0 / 2 | 0 / 2 | 0 / 2 |

## What happened to the gold gaps

| Config | Outcome counts |
|---|---|
| C0 | dropped:rules 10, not_proposed 11, shown:accept 39 |
| C0+ | not_proposed 11, shown:accept 39, shown:review 10 |
| C1 | dropped:copied_forward_unclear 8, dropped:negated 1, not_proposed 11, shown:accept 1, shown:review 39 |
| C2 | dropped:copied_forward_unclear 8, dropped:negated 1, not_proposed 11, shown:abstain 28, shown:accept 1, shown:review 11 |
| C3 | dropped:copied_forward_unclear 8, dropped:negated 1, not_proposed 11, shown:abstain 29, shown:accept 1, shown:review 10 |
| C4 | dropped:copied_forward_unclear 8, dropped:negated 1, not_proposed 11, shown:abstain 29, shown:accept 1, shown:review 10 |
| C4a | dropped:copied_forward_unclear 8, not_proposed 11, shown:abstain 1, shown:accept 1, shown:review 39 |

## Unsupported existing codes

- C4: planted 20. Flagged and planted: 20. Flagged on a billed code Synthea does have: 912. Missed: 0.
- C4a: planted 20. Flagged and planted: 20. Flagged on a billed code Synthea does have: 912. Missed: 0.

## How the labels are made

- Generator: Synthea, run offline with the command in `eval/synthea/README.md`. The author wrote no chart text, condition, lab or medication.
- Mapping: `eval/synthea/snomed_icd10cm.csv`, one row per SNOMED concept with its NLM I-MAGIC source (SNOMED CT to ICD-10-CM Map release 20260901).
- Combination codes: rules R1–R8 in `eval/synthea/build.py`, each citing the FY 2027 ICD-10-CM Official Guidelines or Tabular List.
- Support: a removed code is a gold gap only if the chart window has a note naming it, a medication Synthea links to it, or a lab meeting a published criterion (`manifest.json`).
- Scoring: a shown code that is the dotted parent of a gold code counts as that code (CodeSentinel writes `N18.3`; the FY 2027 billable code is `N18.30`).

## Caveats

- Synthea notes are template text and rarely name chronic conditions, so most evidence is medications and labs. The cascade sends lab-only and medication-only rows to review instead of accepting them.
- A statin, metformin or antihypertensive with no matching diagnosis goes to review under CodeSentinel's medication policy. Synthea prescribes statins and ACE inhibitors for prevention (heart disease, diabetes), so most E78.5 and I10 false positives are those review rows.
- Unsupported-existing flags stay high: about three per chart on billed codes Synthea does have, because the 12-month chart (3 template notes, labs only, no vitals) carries no mention, linked medication or lab for many chronic codes.
- Synthea sometimes records a diabetic kidney condition without a type 2 diabetes condition; the NLM map still maps it to an E11 code. Those charts rarely support an E11.22 gap, so most are neutral.
- Gold gaps are dominated by E78.5 and I10, which Synthea links to statins and antihypertensives. Diabetes, CKD and combination-code gaps are few, so their per-code numbers are anecdotal.
- Codes outside CodeSentinel's rules (for example I11.0, I13.0, N18.1, N18.2, E11.29, E11.40) stay billed and are never removed. The benchmark measures gap finding on the codes the rules cover.
- Labels are derived by code from Synthea's conditions; no certified coder reviewed them.
