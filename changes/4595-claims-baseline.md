---
issue: 4595
summary: "Instrument the Desk hub claims path (GET /api/coordination/claims): p50/p95 latency, RM subprocess GitHub-call proxy, blocked lane exits and expired holds recorded to a JSONL baseline with a claims_baseline.py reporter."
dl_state: "in_review"
next_step: "Open the PR, pass CI, arm squash auto-merge and release the lease."
branch: "deepseek-ct-1/4595-claims-baseline"
---

Claims-path telemetry for the overnight baseline ahead of the #4594 read-model
slices. `backend/coordination/claim_telemetry.py` is a process-local, thread-safe
recorder wired into the two choke points: `rm_scripts.run_module` (every
Repository_Management subprocess — the hub-visible GitHub-call proxy) and
`claims_check_cache.read` (the `GET /api/coordination/claims` outcome, feeding
the p50/p95 reservoir). Blocked lane exits are recorded when a claim is held
(409), a subprocess times out (rc 130) or the request budget is exceeded.
`CLAIMS_BASELINE_FILE` enables a JSONL append; `scripts/claims_baseline.py`
turns that file into the markdown write-up. No latency or GitHub-call target is
pre-declared. Merges-per-hour-per-repo stays an outcome signal recorded by the
existing merge-queue batching path, not the claims path.
