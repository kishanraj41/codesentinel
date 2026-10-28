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

This section describes the first held-out run (v1). The second (v2) is below.

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
python -m eval.synthea.run --bench BENCH --out OUT_HELD --mode live --split held_out
```

## Second held-out run (held-out v2)

This is the **second** run on the 300 held-out charts. The first (v1, `b623d53`, results in
[eval/results/synthea/](../results/synthea/RESULTS.md)) is unchanged. v2 results:
[eval/results/synthea/heldout-v2/](../results/synthea/heldout-v2/RESULTS.md), chart
`docs/assets/synthea-frontier-v2.{svg,png}`.

Why a second run. The v1 **aggregate** showed two things: about 3 unsupported-existing flags per chart
(912 in total) on billed codes Synthea does have, because thin template charts carry no mention of most
chronic codes; and C1 to C4 nearly identical, because Synthea has almost no combination or specificity
gaps, so Super, Tavily and Ultra cost money without changing answers. No held-out chart was opened to
design either fix. Both were designed from that aggregate and tuned on the 100 **dev** charts only
([dev-v2/](../results/synthea/dev-v2/README.md), two live rounds):

1. **Info gate.** When no note sentence in the chart window names a condition (a term from the code
   lexicon or from any billed condition; negated and family-history sentences count; clauses naming a
   procedure or screening instrument do not), a billed code with no evidence becomes an informational row
   (`decision: info`). It is shown, but it is not review work and is outside review items and P/R.
   Charts with any condition mention keep the old behavior. Dev round 2 added the procedure/instrument rule.
2. **Adaptive depth (C4a).** C1, escalating the chart to the full C4 path only when a combination or
   specificity candidate survives triage. C0-C4 are unchanged, so the comparison holds. The service and
   web demo default to C4a.

The held-out split was then run once with every config (`--split held_out`, commit `def9241`). Nothing
was changed after the run. Labels and chart bundles are the v1 ones.

| Config | Run | P | R | F1 | Review items / chart | Gap rows / chart | Gap-row FP | Info rows / chart | $ / chart | Charts past C1 |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| C0+ | v1 | 0.070 | 0.817 | 0.128 | 2.35 | 2.35 | 656 | — | 0 | 0% |
| C0+ | v2 | 0.070 | 0.817 | 0.128 | 2.35 | 2.35 | 656 | 0.00 | 0 | 0% |
| C1 | v1 | 0.187 | 0.667 | 0.292 | 3.82 | 0.71 | 174 | — | 0.00085 | 0% |
| C1 | v2 | 0.183 | 0.667 | 0.288 | 2.03 | 0.73 | 178 | 1.81 | 0.00082 | 0% |
| C4 | v1 | 0.183 | 0.667 | 0.288 | 3.83 | 0.73 | 178 | — | 0.00175 | 100% |
| C4 | v2 | 0.184 | 0.667 | 0.289 | 2.02 | 0.72 | 177 | 1.81 | 0.00173 | 100% |
| C4a (adaptive, new) | v2 | 0.184 | 0.667 | 0.289 | 2.02 | 0.72 | 177 | 1.81 | 0.00087 | 2% |

Unsupported existing codes (C4 and C4a identical): v1 flagged 19 of 20 planted codes and 912 billed codes Synthea does have; v2 flags 7 of 20 planted and 383 billed codes Synthea has. **Planted unsupported codes flagged fell from 19/20 (v1) to 7/20 (v2), because the info gate turned 13 of them into info rows** (they sit on charts whose notes name no condition, so the gate treats silence as no evidence either way). The v1 C4 figures differ slightly from the v1 C1 figures because each config is a separate live pass.


## Limits

- One planted unsupported code (`I50.9`) on held-out was excused by carvedilol, which the unsupported-existing fix counted as a heart-failure drug even though the triage policy treats it as nonspecific. Found on held-out after the single held-out run; fixed afterward. Held-out numbers are from before this fix and were not rerun.

- Synthea notes are template text. They rarely name the chronic conditions, so evidence is mostly
  medications and labs.
- Gold gaps are mostly `E78.5` and `I10`. Diabetes, CKD and combination-code gaps are few.
- Labels are derived by code from Synthea's condition list. No certified coder reviewed them.
