# CodeSentinel eval results: Synthea gap benchmark

Run: live, commit `b623d53`, finished 2026-10-27 20:39 CDT. Raw numbers: [summary.json](summary.json).

Split: `held_out` from `eval/synthea/split.json` (seed 20261028; 100 dev, 300 held-out). Fixes were made on dev only; held-out was run once after they were committed.

## Protocol and before/after

The first 400-chart run ([baseline-v0/](baseline-v0/RESULTS.md)) exposed three cascade bugs: gold
gaps with medication or lab support were dropped as `copied_forward_unclear` (56 of 75), triage saw
only the first 40 structured lines, and the unsupported-existing check flagged 1,962 billed codes that
Synthea does have. The 400 patients were then split by seed 20261028 into 100 dev and 300 held-out
(`eval/synthea/split.json`, committed in `ebf1f9c` before any fix). Fixes were made and checked on dev
only (`47bfbd1`, `b623d53`), then the held-out charts were run once. Nothing was changed after that run.

TP / FP / FN, precision, recall, review items per chart. "Before" rows are the dev or held-out
charts of the baseline run, rescored with the same scorer.

| Set | Config | Before | After |
|---|---|---|---|
| Dev (100, 15 gaps) | C0+ | 14/292/1, P 0.046, R 0.933, 3.06 | 14/210/1, P 0.062, R 0.933, 2.24 |
| Dev | C4 | 1/14/14, P 0.067, R 0.067, 5.06 | 14/52/1, P 0.212, R 0.933, 3.55 |
| Held-out (300, 60 gaps) | C0+ | 49/881/11, P 0.053, R 0.817, 3.10 | 49/656/11, P 0.070, R 0.817, 2.35 |
| Held-out | C4 | 1/33/59, P 0.029, R 0.017, 5.13 | 40/178/20, P 0.183, R 0.667, 3.83 |

Dev iteration 1 (`47bfbd1`) reached C4 10/49/5; iteration 2 (`b623d53`) fixed lab-panel names
counting as condition mentions. Dev results: [dev/RESULTS.md](dev/RESULTS.md).

Held-out recall (0.667) is below dev (0.933): 8 held-out gaps were still dropped as
`copied_forward_unclear`, 1 as negated, and 11 were never proposed by the rules. One planted
unsupported code was missed (an `I50.9` excused by carvedilol, which the fix wrongly counts as a
heart-failure drug). Those are reported, not fixed.
Note: the carvedilol `I50.9` miss was fixed after the held-out run in `c670460` (with the matching
`I13` change in the follow-up commit). The held-out numbers here are from before the fix and were not rerun.

300 Synthea patients (Synthea v4.0.0, seed 20261027, Texas, ages 45-90, reference date 2026-10-27). Conditions are Synthea's, mapped to ICD-10-CM with the NLM SNOMED CT to ICD-10-CM map and the FY 2027 combination-code guidelines. A seeded 30% of the in-scope billed codes were removed from each chart's condition list; the removed codes the 12-month chart still supports are the gold gaps (60 gaps on 57 charts). 103 removed codes with no support in the chart are neutral. 20 charts carry one billed code Synthea says the patient does not have.

| Config | Precision | Recall | F1 | Review items / chart | Gap rows / chart | $ / chart | P50 / P95 s |
|---|---:|---:|---:|---:|---:|---:|---:|
| C0 Rules only, approved bucket | 0.207 | 0.650 | 0.315 | 0.63 | 0.63 | 0 | — |
| C0+ Rules only, approved + review | 0.070 | 0.817 | 0.128 | 2.35 | 2.35 | 0 | — |
| C1 Rules + Lightning triage + quote verifier | 0.187 | 0.667 | 0.292 | 3.82 | 0.71 | 0.00085 | 1.9 / 8.4 |
| C2 C1 + Super adjudication, no Tavily | 0.185 | 0.667 | 0.290 | 3.82 | 0.72 | 0.00115 | 4.6 / 18.0 |
| C3 C2 + Tavily guideline grounding | 0.183 | 0.667 | 0.287 | 3.83 | 0.73 | 0.00121 | 3.7 / 17.7 |
| C4 C3 + Ultra escalation (full cascade) | 0.183 | 0.667 | 0.288 | 3.83 | 0.73 | 0.00175 | 4.7 / 19.0 |

Review items count every row sent to a coder, including unsupported-existing flags on billed codes. Gap rows count only proposed additions.

## 95% intervals (Wilson)

| Config | Precision CI | Recall CI | TP / FP / FN |
|---|---|---|---|
| C0 | 0.156–0.271 | 0.524–0.758 | 39 / 149 / 21 |
| C0+ | 0.053–0.091 | 0.701–0.894 | 49 / 656 / 11 |
| C1 | 0.140–0.244 | 0.541–0.773 | 40 / 174 / 20 |
| C2 | 0.139–0.242 | 0.541–0.773 | 40 / 176 / 20 |
| C3 | 0.137–0.239 | 0.541–0.773 | 40 / 179 / 20 |
| C4 | 0.138–0.240 | 0.541–0.773 | 40 / 178 / 20 |

## Per code (C0+ rules vs C4 cascade)

| Code | Gold gaps | C0+ TP / FP | C4 TP / FP |
|---|---:|---:|---:|
| D64.9 | 2 | 1 / 18 | 1 / 4 |
| E11.22 | 1 | 1 / 7 | 0 / 0 |
| E11.65 | 1 | 0 / 7 | 0 / 7 |
| E11.9 | 2 | 0 / 9 | 0 / 8 |
| E66.9 | 0 | 0 / 1 | 0 / 0 |
| E78.0 | 0 | 0 / 16 | 0 / 16 |
| E78.1 | 1 | 0 / 0 | 0 / 0 |
| E78.5 | 28 | 23 / 74 | 15 / 71 |
| F41.1 | 0 | 0 / 119 | 0 / 9 |
| I10 | 22 | 22 / 114 | 22 / 28 |
| I12.9 | 1 | 0 / 10 | 0 / 0 |
| I50.9 | 1 | 1 / 130 | 1 / 0 |
| J44.1 | 0 | 0 / 14 | 0 / 0 |
| K76.89 | 0 | 0 / 18 | 0 / 9 |
| M05.79 | 0 | 0 / 1 | 0 / 1 |
| N18.3 | 0 | 0 / 3 | 0 / 3 |
| N18.30 | 1 | 1 / 0 | 1 / 0 |
| N18.4 | 0 | 0 / 2 | 0 / 2 |
| N18.5 | 0 | 0 / 7 | 0 / 6 |
| N18.9 | 0 | 0 / 104 | 0 / 12 |
| R73.03 | 0 | 0 / 2 | 0 / 2 |

## What happened to the gold gaps

| Config | Outcome counts |
|---|---|
| C0 | dropped:rules 10, not_proposed 11, shown:accept 39 |
| C0+ | not_proposed 11, shown:accept 39, shown:review 10 |
| C1 | dropped:copied_forward_unclear 8, dropped:negated 1, not_proposed 11, shown:accept 1, shown:review 39 |
| C2 | dropped:copied_forward_unclear 8, dropped:negated 1, not_proposed 11, shown:abstain 34, shown:accept 1, shown:review 5 |
| C3 | dropped:copied_forward_unclear 8, dropped:negated 1, not_proposed 11, shown:abstain 36, shown:accept 1, shown:review 3 |
| C4 | dropped:copied_forward_unclear 8, dropped:negated 1, not_proposed 11, shown:abstain 30, shown:accept 1, shown:review 9 |

## Unsupported existing codes (C4)

Planted: 20. Flagged and planted: 19. Flagged on a billed code Synthea does have: 912. Missed: 1.

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
