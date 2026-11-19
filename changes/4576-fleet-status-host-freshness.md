---
issue: 4576
summary: "Fleet status keeps aggregate _cache_stale and adds per-host stale plus _sampled_at"
dl_state: "in_review"
next_step: "Review the PR. Do not arm or merge from this lane."
branch: "cursor-oglaptop-2/4576-host-freshness"
---

Slice 1 of #4576 only. `/api/fleet/status` still reports aggregate cache age as `_cache_stale` (the old `_stale` name is gone). Each host record gains `stale` and `_sampled_at` so a consumer can skip stale hosts without treating the whole fleet as unknown. Cleanup, scaling, Brick, locks, and watchdogs are untouched.
