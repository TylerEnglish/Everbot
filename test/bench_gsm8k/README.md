# GSM8K (local)

Place Hendrycks `test` repo subsets here, e.g.:

- `test/bench_gsm8k/data/*` (CSV/JSON as provided by the repo)

The Everabot harness will detect this folder and report `SKIP` until a scorer
is wired. Once wired, thresholds can be enforced from `ruleset/promotion.yaml`.
