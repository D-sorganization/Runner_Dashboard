# Lane launcher (Windows)

`lane-launch.ps1` starts a CLI-agent lane (agy, claude, codex, gemini) on a
Windows host so that a console CTRL+C sent by any descendant cannot kill the
lane.

Why: on Windows `os.kill(pid, 0)` is not a liveness probe. Signal `0` is
`CTRL_C_EVENT`, and a console CTRL+C reaches every process attached to the
console, including the agent CLI and its wrapper shell (#4421).

```powershell
pwsh -NoProfile -File scripts/lanes/lane-launch.ps1 -Lane <name> -Exe <agent exe> `
     [-AgentArgs <arg>] [-PromptFile <file>] [-WorkDir <dir>] [-LogDir <dir>]
```

- The launcher sets the CTRL+C-ignore flag before starting the agent; children
  inherit it. Ctrl+Break and closing the window still stop the lane.
- It sets `TERM=xterm-256color` when `TERM` is `dumb` or unset.
- It logs the start line, the agent's exit code and `wrapper end` to
  `<LogDir>\relaunch.log` (default `%USERPROFILE%\fleet-lanes\<Lane>`). The
  prompt text is not logged.

In Python, ask whether a process exists with `scripts/pid_probe.py::pid_alive`.
`scripts/check_no_kill_zero.py` (pre-commit hook `no-kill-zero`, and a unit
test) fails on `os.kill(x, 0)` and `os.kill(x, signal.CTRL_C_EVENT)` anywhere
else.

Self-test: `tests/unit/test_pid_probe_4421.py::test_launcher_self_test_agent_and_wrapper_survive_console_ctrl_c`
(Windows only; it runs the launcher in its own hidden console).
