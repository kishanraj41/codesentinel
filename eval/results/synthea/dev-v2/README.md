# Synthea dev split, second tuning pass (v2)

Live runs on the 100 `dev` charts of `eval/synthea/split.json`, used to tune the two v2 fixes
(unsupported-existing info gate and the adaptive config C4a) before the second held-out run.
Held-out results were not opened while tuning. Before = the v1 dev run in [../dev/](../dev/RESULTS.md).

| Run | Commit | Change |
|---|---|---|
| [round1/](round1/RESULTS.md) | `4445291` | Info gate (notes name no lexicon or billed condition) + C4a |
| [round2/](round2/RESULTS.md) | `24ba20d` | Procedure / screening-instrument clauses ("diabetic retinal eye exam (procedure)", "generalized anxiety disorder 7 item (gad-7)") no longer count as condition mentions |

| Config | Dev v1 | Round 1 | Round 2 |
|---|---|---|---|
| C4 P / R | 0.212 / 0.933 | 0.212 / 0.933 | 0.212 / 0.933 |
| C4 review items / chart | 3.55 | 2.99 | 2.13 |
| C4 info rows / chart | — | 0.56 | 1.42 |
| C4 unsupported flags on billed codes Synthea has | 275 | 222 | 140 |
| C4 planted unsupported caught | 14 / 14 | 11 / 14 | 7 / 14 |
| C4a P / R | — | 0.212 / 0.933 | 0.212 / 0.933 |
| C4a review items / chart | — | 2.99 | 2.13 |
| C4a charts past C1 | — | 3% | 3% |
| C4a $ / chart (C4) | — | 0.00092 (0.00170) | 0.00088 (0.00164) |
| Run cost | $0.476 | $0.581 | $0.574 |

Round 2's gate is the one the held-out v2 run used. Its cost: on dev the planted unsupported codes
that sat on charts whose notes name no condition (7 of 14) became info rows, so unsupported-existing
recall fell from 14/14 to 7/14 while false flags fell from 275 to 140. Gated flags were planted at
about the same rate as the rest (7 of 142 vs 7 of 147), which is the point of the gate: on those charts
silence carries no signal either way.
