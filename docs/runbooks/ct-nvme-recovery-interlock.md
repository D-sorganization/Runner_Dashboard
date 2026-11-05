# CT-NVMe recovery interlock (#4046)

Recovery scripts that stop idle runners must defer when a `job-started` hook lock
shows a live `Runner.Worker`. Use `backend.runner_recovery_interlock.should_defer_runner_stop`
(or the same rules in `deploy/runner-scheduler.py` freeze/recheck) before `systemctl stop`.

Host hook presence is healed by `deploy/runner-hooks-restore.sh` (see `install-node-self-heal.sh`).

Follow-up slices: wire CT-NVMe recovery script, health probe for missing hooks, Barb attention feed.
