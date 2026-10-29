# Synthea gap benchmark

An outside benchmark for CodeSentinel's gap finding. The patients, conditions, labs,
medications and notes come from [Synthea](https://github.com/synthetichealth/synthea), MITRE's
open-source patient simulator. The author wrote no chart content and no per-chart label. Labels
are derived by code from Synthea's own condition list.

Results: [eval/results/synthea/RESULTS.md](../results/synthea/RESULTS.md) (held-out, the headline), [dev/](../results/synthea/dev/RESULTS.md) and the pre-fix [baseline-v0/](../results/synthea/baseline-v0/RESULTS.md).

## How a chart and its label are made

1. **Generate.** Synthea v4.0.0, 400 living adults aged 45–90 in Texas, seed `20261027`
   (clinician seed `20261027`), reference date 2026-10-27, 10 years of history, FHIR R4 export
   (`generate.sh`).
2. **Map.** Every active Synthea condition is mapped from SNOMED CT to ICD-10-CM with
   `snomed_icd10cm.csv`. Each row names its source: NLM I-MAGIC, which runs the
   [NLM SNOMED CT to ICD-10-CM Map](https://www.nlm.nih.gov/research/umls/mapping_projects/snomedct_to_icd10cm.html)
   (release 20260901), with the query URL that reproduces it. `imagic_lookup.py` is the lookup script.
3. **Combine.** Combination codes follow the FY 2027 ICD-10-CM Official Guidelines and Tabular
   List: rules R1–R8 in `build.py` (diabetes with CKD, diabetes with hyperglycemia, R73 excludes,
   E11.9 only without complications, hypertension with CKD / heart failure / both, one CKD stage).
   The result is the patient's billed code list.
4. **Make gaps.** The in-scope codes are the ones CodeSentinel's rules can propose: `E11.9`,
   `E11.22`, `E11.65`, `N18.30`, `N18.4`, `I10`, `I12.9`, `I50.9`, `E78.5`, `E78.1`, `D64.9`, `F32.9`.
   A seeded 30% of in-scope (patient, code) pairs are removed from the chart's Condition list.
   Every other code stays billed.
5. **Check support.** The chart is the last 12 months: lab results (up to 3 per LOINC), active or
   recent medications, the 3 latest notes, and encounters. A removed code is a gold gap only if
   that chart still supports it: a note names the condition, a medication has a Synthea
   `reasonReference` to it, or the latest lab meets a published criterion (ADA, KDIGO, NCEP ATP III,
   WHO; listed in the generated `manifest.json`). A combination code needs both parts supported.
   Removed codes without support are neutral: a shown row for one is not counted either way.
6. **Hard negatives.** About 8% of charts (seeded) get one extra billed code from a category the
   patient's Synthea record does not have. Those are scored as unsupported existing codes.
7. **Read.** Each chart is read by `fhir_adapter.FHIRPatientExtractor`, the production FHIR
   adapter, through an in-memory FHIR client (`BundleFHIRClient` in `run.py`).

Scoring is the same item-level scorer as Gold-A and Gold-B (`eval/score.py`), with two
adjustments in `run.py`: a shown code that is the dotted parent of a label code counts as that
code (CodeSentinel writes `N18.3`; the FY 2027 billable code is `N18.30`), and neutral rows are
removed before scoring.

## Reproduce

```bash
# 1. Synthea (Java 17+). Writes synthea-out/fhir/*.json, about 2.2 GB.
eval/synthea/generate.sh synthea-out

# 2. Charts and labels. Writes BENCH/bundles/, BENCH/labels.jsonl, BENCH/manifest.json.
python -m eval.synthea.build --synthea-dir synthea-out/fhir --out BENCH --seed 20261027

# 3. Live run (both keys set). --split dev|held_out reads split.json; --limit 20 is the smoke subset.
python -m eval.synthea.run --bench BENCH --out eval/results/synthea --mode live --split held_out --workers 5

# 4. Frontier chart (needs matplotlib).
python -m eval.synthea.frontier eval/results/synthea/summary.json docs/assets/synthea-frontier
```

`eval/results/synthea/manifest.json` records the SHA-256 of every chart bundle the published run
used, so a rebuilt benchmark can be checked chart by chart.

## Dev / held-out protocol

This section describes the first held-out run (v1), which ran on the v7 code. The later runs (v2 on the v8 code, then v9) are below.

The first full run (all 400 charts, before any change) exposed three cascade bugs. They were
fixed under a split fixed in advance, so the published number is on charts the fixes never saw.

1. **Split first.** `split.json` assigns the 400 patient ids with `random.Random(20261028)` over the
   sorted ids: the first 100 are `dev`, the other 300 `held_out`. It was committed (`ebf1f9c`)
   before any cascade change.
2. **Fix on dev only.** Bugs were diagnosed from dev charts and from the dev rows of the first
   run. Held-out per-chart outcomes were not opened while fixing. The live iteration runs were
   `--split dev` only.
3. **One held-out run.** After the fixes were committed, the 300 held-out charts were run once
   with every configuration (`--split held_out`). No change was made after that run.
4. **Benchmark unchanged.** `labels.jsonl` and the chart bundles are the ones from the first
   run (manifest SHA-256s unchanged). No label was edited.

Known exposure before the split: the aggregate 400-chart results and a handful of smoke-run
charts (which can include held-out patients) had been read while writing up the first run.
No fix was designed from a specific held-out chart.

Post-run fix: carvedilol and furosemide were removed from the heart-failure (`I50`) drug list in
`codesentinel/policy.py`, because they excused a planted unsupported `I50.9`. Found on held-out after the single held-out run; fixed afterward. Held-out numbers are from before this fix and were not rerun.
The same two drugs were then removed from the `I13` (hypertensive heart and chronic kidney disease) list,
so they no longer excuse an `I13` code on their own either; also not rerun.

```bash
python -m eval.synthea.run --bench BENCH --out OUT_DEV --mode live --split dev
python -m eval.synthea.run --bench ## Later held-out runs: v8 (gate + adaptive, gate reverted) and v9

The 300 held-out charts have been run three times, once per code version. Each run is kept; none
was edited afterward. Labels and chart bundles are the v1 ones throughout.

| Held-out run | Code | Results |
|---|---|---|
| v1 | v7 (`b623d53`) | [eval/results/synthea/](../results/synthea/RESULTS.md) |
| v2 | v8: info gate + adaptive C4a (`def9241`) | [heldout-v2/](../results/synthea/heldout-v2/RESULTS.md), `docs/assets/synthea-frontier-v2.{svg,png}` |
| v9 | v9: gate reverted, C4a always escalates combination/specificity (`8ab01be`) | [heldout-v9/](../results/synthea/heldout-v9/RESULTS.md), `docs/assets/synthea-frontier-v9.{svg,png}` |

**v7 (held-out v1).** The aggregate showed about 3 unsupported-existing flags per chart (912 in total)
on billed codes Synthea does have, because thin template charts carry no mention of most chronic codes;
and C1 to C4 nearly identical, because Synthea has almost no combination or specificity gaps.

**v8 (held-out v2).** Two fixes designed from that aggregate (no held-out chart opened) and tuned on
the 100 dev charts only ([dev-v2/](../results/synthea/dev-v2/README.md), two live rounds):

1. *Info gate.* When no note sentence in the chart window named a condition, a billed code with no
   evidence became an informational row (`decision: info`), outside review items and P/R.
2. *Adaptive depth (C4a).* C1, escalating to the full C4 path only when a combination or specificity
   candidate survived triage.

Held-out v2 cut C4 review items per chart from 3.83 to 2.02 with gap P/R unchanged, but **planted
unsupported codes flagged fell from 19/20 (v1) to 7/20 (v2), because the info gate demoted 13 of the 20
planted codes to info rows.** Catching a billed code with no support is the point of that check, so the
gate was **reverted** (`4d13c16`, `fdf174c`, normal revert commits). The unsupported-existing check is
again exactly the v7 one (`codesentinel/policy.py` matches `main`).

**v9 (held-out v9).** v8 minus the gate, plus one routing change to C4a, which stays the service
default: a chart goes past C1 when the rules pipeline proposes **any** combination or specificity
candidate, whatever triage said or how confident it was, and every such candidate goes to Super, the
Tavily gate and Ultra as in C4. If triage would have dropped it and the models accept it anyway, it is
held as review. The routing change was checked only on mock dev; nothing was tuned on held-out.
Held-out was then run once with every config on the shipped commit `8ab01be`.

| Config | Run | P | R | F1 | Review items / chart | Gap rows / chart | Gap-row FP | Planted unsupported caught | $ / chart | Charts past C1 |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| C0+ | v1 | 0.070 | 0.817 | 0.128 | 2.35 | 2.35 | 656 | 0/20 | 0 | 0% |
| C0+ | v9 | 0.070 | 0.817 | 0.128 | 2.35 | 2.35 | 656 | 0/20 | 0 | 0% |
| C1 | v1 | 0.187 | 0.667 | 0.292 | 3.82 | 0.71 | 174 | 19/20 | 0.00085 | 0% |
| C1 | v2 | 0.183 | 0.667 | 0.288 | 2.03 | 0.73 | 178 | 7/20 | 0.00082 | 0% |
| C1 | v9 | 0.183 | 0.667 | 0.288 | 3.83 | 0.73 | 178 | 20/20 | 0.00081 | 0% |
| C3 | v9 | 0.184 | 0.667 | 0.289 | 3.83 | 0.72 | 177 | 20/20 | 0.00120 | 100% |
| C4 | v1 | 0.183 | 0.667 | 0.288 | 3.83 | 0.73 | 178 | 19/20 | 0.00175 | 100% |
| C4 | v2 | 0.184 | 0.667 | 0.289 | 2.02 | 0.72 | 177 | 7/20 | 0.00173 | 100% |
| C4 | v9 | 0.183 | 0.667 | 0.287 | 3.84 | 0.73 | 179 | 20/20 | 0.00170 | 100% |
| C4a | v2 | 0.184 | 0.667 | 0.289 | 2.02 | 0.72 | 177 | 7/20 | 0.00087 | 2% |
| C4a | v9 | 0.182 | 0.683 | 0.288 | 3.86 | 0.75 | 184 | 20/20 | 0.00118 | 6% |

In v9 the unsupported check flags all 20 planted codes, and again 912 billed codes Synthea does have
(the v1 figure; review items per chart are back to about 3.8). The 20th planted code (19/20 in v1) is the `I50.9` that carvedilol and furosemide used to excuse; the post-v1 drug-list fix above caught it. C4a v9 finds one more gold gap than C4
(recall 0.683 vs 0.667) and shows 7 more false-positive gap rows (184 vs 177–179). It costs about
70% of C4 per chart, with 6% of charts past C1 (2% in v2, because candidates dropped at triage now
escalate too). Each config is a separate live pass, so small differences between configs and runs
include live variance.

figures because each config is a separate live pass.


## Limits

- One planted unsupported code (`I50.9`) on held-out was excused by carvedilol, which the unsupported-existing fix counted as a heart-failure drug even though the triage policy treats it as nonspecific. Found on held-out after the single held-out run; fixed afterward. Held-out numbers are from before this fix and were not rerun.

- Synthea notes are template text. They rarely name the chronic conditions, so evidence is mostly
  medications and labs.
- Gold gaps are mostly `E78.5` and `I10`. Diabetes, CKD and combination-code gaps are few.
- Labels are derived by code from Synthea's condition list. No certified coder reviewed them.
