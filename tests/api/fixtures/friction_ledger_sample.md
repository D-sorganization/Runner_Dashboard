# Friction ledger (started 2026-10-19)

Every slowdown seen in the fleet gets one row. A row is CLOSED only when it has both:

- **Fix:** merged or deployed.
- **Detector:** a check in HEALTH_BRIEF, the dispatcher or CI that makes a recurrence visible.

Review the ledger at the start of each orchestration session. New friction gets a row before anyone works around it.

| #   | Friction (seen)                                                                                                                | Fix                                                                                                | Detector                                                               | Status                            |
| --- | ------------------------------------------------------------------------------------------------------------------------------ | -------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------- | --------------------------------- |
| F1  | Two queue_loop instances raced on queue.txt (double dispatch risk)                                                             | Singleton pidfile guard in queue_loop.sh (swapped in 2026-10-19 ~20:10Z; a second start refuses)   | Guard refuses and logs; `ps` count is in the health brief              | CLOSED                            |
| F2  | agy ends its turn waiting on background tasks; the watchdog kills it at 20m, so the run and its work are lost (#13109, #12688) | ADDENDUM_AGY.md (foreground only, bounded commands, commit early), wired per engine in dispatch.sh | Watchdog note "stalled on a background task" in DISPATCH_LOG; F4 retry | OPEN: verify on the next agy runs |
| F3  | ADDENDUM_CURSOR described the obsolete WSL setup (cursor now runs Windows-native via cursor_win.sh)                            | Retired it to ADDENDUM_CURSOR_WSL.obsolete.md, so it isn't wired in                                | n/a                                                                    | CLOSED                            |
