# Gold-A and Gold-B, live v9

Live rerun (`EVAL_OUT_DIR=eval/results/live-v9 python -m eval.run --split test --mode live`) on the shipped
commit `8ab01be`: the v8 info gate reverted, C4a kept as the service default, and C4a now sends every
combination/specificity candidate to Super, Tavily and Ultra. v7 is in [../live/](../live/RESULTS.md) and v8 in
[../live-v8/](../live-v8/README.md). Labels unchanged.

| Set | Config | P | R | F1 | Review items / chart | Hard negatives | Categories all pass | Unsupported | $ / chart | Charts past C1 |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Gold-B (66) | C0+ | 0.598 | 1.000 | 0.749 | 2.000 | 0/53 | 0/11 | 0/3 | 0 | 0% |
| Gold-B | C1 | 0.975 | 0.975 | 0.975 | 1.242 | 51/53 | 10/11 | 3/3 | 0.00020 | 0% |
| Gold-B | C3 | 1.000 | 0.975 | 0.987 | 1.212 | 53/53 | 11/11 | 3/3 | 0.00047 | 100% |
| Gold-B | C4 | 0.987 | 0.975 | 0.981 | 1.227 | 52/53 | 10/11 | 3/3 | 0.00136 | 100% |
| Gold-B | C4a | 1.000 | 0.975 | 0.987 | 1.212 | 53/53 | 11/11 | 3/3 | 0.00139 | 23% |
| Gold-A (10) | C0+ | 0.755 | 1.000 | 0.860 | 4.9 | 12/21 | 3/7 | 0/3 | 0 | 0% |
| Gold-A | C1 | 0.857 | 0.973 | 0.911 | 4.5 | 18/21 | 6/7 | 3/3 | 0.00058 | 0% |
| Gold-A | C3 | 0.895 | 0.919 | 0.907 | 4.1 | 18/21 | 6/7 | 3/3 | 0.00177 | 100% |
| Gold-A | C4 | 0.892 | 0.892 | 0.892 | 4.0 | 18/21 | 6/7 | 3/3 | 0.00657 | 100% |
| Gold-A | C4a | 0.897 | 0.946 | 0.921 | 4.2 | 19/21 | 6/7 | 3/3 | 0.00613 | 50% |

**Regression, fixed C4 on Gold-B:** P 1.000 → 0.987 and R 0.987 → 0.975 compared with v7/v8. Hard negatives
52/53: gb-0051 (wrong_specificity) shows I50.9. C4 stability is 63/66 (v7 65/66, v8 64/66). The fixed-C4 code
path is the same as in v7 for these charts (no Gold chart had an info row, and the routing change applies only to
C4a), so this is live-model variance. It is reported as is. C4a passes gb-0051 and all 53 hard negatives. Its
missed gold codes include the I12.9 on gb-0041 (combo_forbid), as in v8.
