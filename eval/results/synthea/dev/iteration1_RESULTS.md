# CodeSentinel eval results: Synthea gap benchmark

Run: live, commit `47bfbd1`, finished 2026-10-27 19:57 CDT. Raw numbers: [summary.json](summary.json).

Split: `dev` from `eval/synthea/split.json` (seed 20261028; 100 dev, 300 held-out). Fixes were made on dev only; held-out was run once after they were committed.

100 Synthea patients (Synthea v4.0.0, seed 20261027, Texas, ages 45-90, reference date 2026-10-27). Conditions are Synthea's, mapped to ICD-10-CM with the NLM SNOMED CT to ICD-10-CM map and the FY 2027 combination-code guidelines. A seeded 30% of the in-scope billed codes were removed from each chart's condition list; the removed codes the 12-month chart still supports are the gold gaps (15 gaps on 15 charts). 36 removed codes with no support in the chart are neutral. 14 charts carry one billed code Synthea says the patient does not have.

| Config | Precision | Recall | F1 | Review items / chart | Gap rows / chart | $ / chart | P50 / P95 s |
|---|---:|---:|---:|---:|---:|---:|---:|
| C0 Rules only, approved bucket | 0.129 | 0.533 | 0.208 | 0.62 | 0.62 | 0 | — |
| C0+ Rules only, approved + review | 0.061 | 0.933 | 0.114 | 2.30 | 2.30 | 0 | — |
| C1 Rules + Lightning triage + quote verifier | 0.167 | 0.667 | 0.267 | 3.49 | 0.60 | 0.00095 | 2.2 / 47.4 |
| C2 C1 + Super adjudication, no Tavily | 0.167 | 0.667 | 0.267 | 3.49 | 0.60 | 0.00120 | 3.6 / 36.9 |
| C3 C2 + Tavily guideline grounding | 0.169 | 0.667 | 0.270 | 3.48 | 0.59 | 0.00112 | 3.7 / 17.3 |
| C4 C3 + Ultra escalation (full cascade) | 0.169 | 0.667 | 0.270 | 3.48 | 0.59 | 0.00171 | 4.2 / 50.9 |

Review items count every row sent to a coder, including unsupported-existing flags on billed codes. Gap rows count only proposed additions.

## 95% intervals (Wilson)

| Config | Precision CI | Recall CI | TP / FP / FN |
|---|---|---|---|
| C0 | 0.067–0.234 | 0.301–0.752 | 8 / 54 / 7 |
| C0+ | 0.037–0.100 | 0.702–0.988 | 14 / 216 / 1 |
| C1 | 0.093–0.280 | 0.417–0.848 | 10 / 50 / 5 |
| C2 | 0.093–0.280 | 0.417–0.848 | 10 / 50 / 5 |
| C3 | 0.095–0.285 | 0.417–0.848 | 10 / 49 / 5 |
| C4 | 0.095–0.285 | 0.417–0.848 | 10 / 49 / 5 |

## Per code (C0+ rules vs C4 cascade)

| Code | Gold gaps | C0+ TP / FP | C4 TP / FP |
|---|---:|---:|---:|
| D64.9 | 0 | 0 / 3 | 0 / 0 |
| E11.22 | 0 | 0 / 4 | 0 / 1 |
| E11.65 | 0 | 0 / 4 | 0 / 4 |
| E11.9 | 1 | 0 / 1 | 0 / 1 |
| E78.0 | 0 | 0 / 3 | 0 / 2 |
| E78.5 | 5 | 5 / 29 | 1 / 21 |
| F41.1 | 0 | 0 / 39 | 0 / 1 |
| I10 | 8 | 8 / 38 | 8 / 9 |
| I12.9 | 0 | 0 / 2 | 0 / 0 |
| I50.9 | 0 | 0 / 45 | 0 / 0 |
| J44.1 | 0 | 0 / 5 | 0 / 0 |
| K76.89 | 0 | 0 / 3 | 0 / 1 |
| N18.30 | 1 | 1 / 0 | 1 / 0 |
| N18.4 | 0 | 0 / 2 | 0 / 1 |
| N18.5 | 0 | 0 / 3 | 0 / 0 |
| N18.9 | 0 | 0 / 29 | 0 / 2 |
| R73.03 | 0 | 0 / 6 | 0 / 6 |

## What happened to the gold gaps

| Config | Outcome counts |
|---|---|
| C0 | dropped:rules 6, not_proposed 1, shown:accept 8 |
| C0+ | not_proposed 1, shown:accept 8, shown:review 6 |
| C1 | dropped:copied_forward_unclear 4, not_proposed 1, shown:accept 1, shown:review 9 |
| C2 | dropped:copied_forward_unclear 4, not_proposed 1, shown:abstain 6, shown:accept 1, shown:review 3 |
| C3 | dropped:copied_forward_unclear 4, not_proposed 1, shown:abstain 6, shown:accept 1, shown:review 3 |
| C4 | dropped:copied_forward_unclear 4, not_proposed 1, shown:abstain 8, shown:accept 1, shown:review 1 |

## Unsupported existing codes (C4)

Planted: 14. Flagged and planted: 14. Flagged on a billed code Synthea does have: 275. Missed: 0.

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
