---
issue: 2904
type: chore
---

Add A5 andon and claim-gate metrics exporter (`scripts/andon_metrics.py`) reusing `scripts.ci_slo_check` and `scripts.pr_claim_inventory` DRY helpers, with baseline replay fixture `tests/fixtures/andon_metrics/baseline_2026_10_26.json` and unit tests in `tests/test_andon_metrics.py`.
