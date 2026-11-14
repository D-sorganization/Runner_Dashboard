---
issue: 4421
summary: "Adds a shared Windows-safe pid probe (scripts/pid_probe.py), replaces the three os.kill(pid, 0) probes, adds a gate that fails on os.kill(x, 0) and CTRL_C_EVENT, and checks in the hardened Windows lane launcher."
paths: "scripts/pid_probe.py,scripts/check_no_kill_zero.py,scripts/lanes/lane-launch.ps1,scripts/barb_backlog_push_relay.py,scripts/barb-worker/backlog-push-spawn.py,tests/unit/test_pid_probe_4421.py"
---
