---
issue: 3064
summary: "rd-barb watchdog restarts her on the next provider after auth, rate-limit or crash failures"
---

`scripts/rd_barb_watchdog.py run` launches the rd-barb session from `RD_BARB_LAUNCH_CMD` (shlex, no shell), classifies non-zero exits with `staff.classifier.classify_run_failure` (auth_expired, rate_limited, quota_exhausted, provider_capacity, else crash), and calls `rd_barb_provider.py next`, which sends the "switched provider" FYI with its recommendation or the exhausted alert. Restarts back off 30 s steps up to 300 s and stop at `--max-restarts`. Fails closed when the launch command or `next` output is missing or malformed. When the restart cap is hit it sends a high-urgency `alert` with a `recommended_action` through `rd_barb_notify`.
