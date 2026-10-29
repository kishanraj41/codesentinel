# Gold-A and Gold-B, live v8

Live rerun (`EVAL_OUT_DIR=eval/results/live-v8 python -m eval.run --split test --mode live`) at commit
`def9241` (info gate + adaptive C4a). v7 stays in [../live/](../live/RESULTS.md). Labels unchanged.

| Set | Config | Run | P | R | Review items / chart | Hard negatives | Unsupported | $ / chart | Charts past C1 |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|
| Gold-B (66) | C4 | v7 | 1.000 | 0.987 | 1.227 | 53/53 | 3/3 | 0.00136 | 100% |
| Gold-B | C4 | v8 | 1.000 | 0.987 | 1.227 | 53/53 | 3/3 | 0.00127 | 100% |
| Gold-B | C4a | v8 | 1.000 | 0.975 | 1.212 | 53/53 | 3/3 | 0.00126 | 23% |
| Gold-A (10) | C4 | v7 | 0.868 | 0.892 | 4.1 | 18/21 | 3/3 | 0.00744 | 100% |
| Gold-A | C4 | v8 | 0.872 | 0.919 | 4.2 | 18/21 | 3/3 | 0.00754 | 100% |
| Gold-A | C4a | v8 | 0.872 | 0.919 | 4.2 | 18/21 | 3/3 | 0.00477 | 50% |

C4 stability (two live passes, same shown code/decision set): v7 65/66, v8 64/66. The C4 code path did not
change for these charts (no Gold chart has an info row), so this is live-model variance, reported as is.
Gold-B C4a recall is one gold code below C4 (0.975 vs 0.987).
