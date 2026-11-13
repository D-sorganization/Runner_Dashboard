# `fleet-rec` Windows (PowerShell / Windows Terminal) and WSL Coexistence

`clients/fleet/fleet_rec.py` provides opt-in session recording for `claude`, `codex`, `agy`, and `cursor-agent` across both Linux/WSL (`--platform posix`) and Windows PowerShell / Windows Terminal (`--platform windows`).

## 1. Windows PowerShell / Windows Terminal Setup

1. Generate the PowerShell profile wrapper functions and aliases:
   ```powershell
   python clients/fleet/fleet_rec.py --print-powershell-profile >> $PROFILE
   ```
2. Enable recording for the current PowerShell session (off by default):
   ```powershell
   $env:FLEET_REC_ENABLED = "1"
   $env:FLEET_URL = "http://127.0.0.1:8321"
   ```
3. Launch any supported CLI (`claude`, `codex`, `agy`, `cursor-agent`) from PowerShell or Windows Terminal. `fleet-rec` normalizes `.exe`, `.cmd`, `.ps1`, and `.bat` executable names to their canonical CLI identifier (`claude`, `codex`, `agy`, `cursor-agent`).

## 2. WSL Setup

Inside WSL (`bash` or `zsh`), invoke `fleet-rec` in POSIX PTY mode:

```bash
export FLEET_REC_ENABLED=1
export FLEET_URL="http://127.0.0.1:8321"
python3 clients/fleet/fleet_rec.py --platform posix -- claude --resume
```

## 3. Windows + WSL Coexistence

- **Separate default spool files**:
  - WSL / Linux (`--platform posix`) spools to `${XDG_CONFIG_HOME:-~/.config}/runner-dashboard/fleet-rec-spool.json`.
  - Windows PowerShell (`--platform windows`) spools to `%LOCALAPPDATA%\runner-dashboard\fleet-rec-spool-windows.json` (or `$RUNNER_DASHBOARD_CONFIG_DIR/fleet-rec-spool-windows.json` when a shared config directory is configured).
  - Because the filenames are distinct (`fleet-rec-spool.json` vs `fleet-rec-spool-windows.json`), concurrent Windows PowerShell and WSL sessions never contend on or overwrite each other's offline spool even when sharing a mounted directory.
- **Identical event and gap semantics**:
  - Both adapters share the same node-side redaction (`redact_node_stream`), `session_start` (`seq=0`), `chunk`, `OverflowGap` (`local_spool_overflow`), `session_end`, and ordered `flush_recorder_spool` replay pipeline.
