"""Standalone diagnostics artifact generator.

Gathers full system state, WSL information, database locks, and recent logs,
producing a sanitized markdown report that can be attached to issues or PRs.
Resilient to missing dependencies so it can run even after a dashboard crash.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import shutil
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

# Configure logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("diagnostics_artifact")


def get_system_info() -> dict[str, Any]:
    """Retrieve basic host system information and disk space."""
    import socket

    disk_info = {}
    # Check disk space for root and C: drive mount
    for label, path in [("WSL Root", "/"), ("Windows C:", "/mnt/c")]:
        try:
            total, used, free = shutil.disk_usage(path)
            disk_info[label] = {
                "total_gb": round(total / (1024**3), 1),
                "used_gb": round(used / (1024**3), 1),
                "free_gb": round(free / (1024**3), 1),
                "percent_used": round((used / total) * 100, 1),
            }
        except OSError:
            pass

    return {
        "hostname": socket.gethostname(),
        "platform": sys.platform,
        "python_version": sys.version,
        "disk_space": disk_info,
        "time": datetime.now(UTC).isoformat(),
    }


def get_git_info() -> dict[str, Any]:
    """Gather Git branch, last commit, status, and drift metadata."""
    repo_root = Path(__file__).resolve().parent.parent
    git_info: dict[str, Any] = {
        "commit": "unknown",
        "branch": "unknown",
        "status": "unknown",
        "diff_summary": "",
    }
    if not shutil.which("git"):
        return git_info

    try:
        commit_res = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            cwd=repo_root,
            timeout=5,
            check=False,
        )
        if commit_res.returncode == 0:
            git_info["commit"] = commit_res.stdout.strip()

        branch_res = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True,
            text=True,
            cwd=repo_root,
            timeout=5,
            check=False,
        )
        if branch_res.returncode == 0:
            git_info["branch"] = branch_res.stdout.strip()

        status_res = subprocess.run(
            ["git", "status", "--porcelain"],
            capture_output=True,
            text=True,
            cwd=repo_root,
            timeout=5,
            check=False,
        )
        if status_res.returncode == 0:
            git_info["status"] = status_res.stdout.strip() or "clean"

        diff_res = subprocess.run(
            ["git", "diff", "--stat"],
            capture_output=True,
            text=True,
            cwd=repo_root,
            timeout=5,
            check=False,
        )
        if diff_res.returncode == 0:
            git_info["diff_summary"] = diff_res.stdout.strip()
    except Exception as exc:  # noqa: BLE001
        log.warning("Failed to gather git info: %s", exc)

    return git_info


def get_wsl_status() -> str:
    """Execute wsl -l -v and return the stdout distribution list."""
    if not shutil.which("wsl"):
        return "WSL binary not found in PATH"

    try:
        # Try UTF-16-LE decoding (standard for WSL output)
        res = subprocess.run(["wsl", "-l", "-v"], capture_output=True, timeout=10, check=False)
        output = res.stdout.decode("utf-16-le", errors="replace").strip()
        if not output or "default" not in output.lower():
            # Fall back to standard decode
            output = res.stdout.decode("utf-8", errors="replace").strip()
        return output
    except Exception as exc:  # noqa: BLE001
        return f"Failed to execute wsl -l -v: {exc}"


def query_wsl_vhdx_status() -> list[dict[str, Any]]:
    """Locate VHDX files and check their attachment using Get-DiskImage via powershell."""
    wsl_vhdx_status: list[dict[str, Any]] = []
    powershell_bin = shutil.which("powershell.exe") or shutil.which("powershell")
    if not powershell_bin:
        log.warning("powershell not found, skipping VHDX attachment checks")
        return wsl_vhdx_status

    ps_cmd = (
        "Get-ItemProperty -Path "
        "'HKCU:\\Software\\Microsoft\\Windows\\CurrentVersion\\Lxss\\*' "
        "-ErrorAction SilentlyContinue | ForEach-Object { "
        "  `$path = Join-Path `$_.BasePath 'ext4.vhdx'; "
        "  if (Test-Path `$path) { "
        "    `$attached = (Get-DiskImage -ImagePath `$path "
        "      -ErrorAction SilentlyContinue).Attached; "
        "    [PSCustomObject]@{Distribution=`$_.DistributionName; "
        "      Path=`$path; Attached=`$attached} "
        "  } "
        "} | ConvertTo-Json"
    )
    try:
        res = subprocess.run(
            [powershell_bin, "-NoProfile", "-Command", ps_cmd],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        if res.returncode == 0 and res.stdout.strip():
            data = json.loads(res.stdout)
            if isinstance(data, dict):
                wsl_vhdx_status = [data]
            elif isinstance(data, list):
                wsl_vhdx_status = data
    except Exception as exc:  # noqa: BLE001
        log.warning("Failed to query WSL VHDX status: %s", exc)

    return wsl_vhdx_status


def detect_sharing_violations(wsl_vhdx_status: list[dict[str, Any]], wsl_status_str: str) -> dict[str, Any]:
    """Check replay/push databases and VHDX files for active sharing violations."""
    storage_incident: dict[str, Any] = {
        "detected": False,
        "error_code": None,
        "target_file": None,
        "message": None,
    }

    db_paths = [
        Path.home() / "actions-runners" / "dashboard" / "replay.db",
        Path.home() / "actions-runners" / "dashboard" / "push.db",
    ]
    for db_path in db_paths:
        if db_path.exists():
            try:
                with open(db_path, "a"):
                    pass
            except PermissionError as exc:
                if getattr(exc, "winerror", None) == 32 or "sharing violation" in str(exc).lower():
                    storage_incident = {
                        "detected": True,
                        "error_code": "ERROR_SHARING_VIOLATION",
                        "target_file": str(db_path),
                        "message": (
                            f"Storage handle conflict: {db_path.name} is locked "
                            f"by another process (ERROR_SHARING_VIOLATION)."
                        ),
                    }
                    return storage_incident
            except Exception:  # noqa: BLE001
                pass

    if wsl_vhdx_status:
        wsl_status_lower = wsl_status_str.lower()
        for item in wsl_vhdx_status:
            vhdx_path_str = item.get("Path") or item.get("path")
            if not vhdx_path_str:
                continue
            vhdx_path = Path(vhdx_path_str)
            if vhdx_path.exists():
                try:
                    with open(vhdx_path, "rb"):
                        pass
                except PermissionError as exc:
                    if getattr(exc, "winerror", None) == 32 or "sharing violation" in str(exc).lower():
                        is_running = False
                        distro_name = str(item.get("Distribution", "")).lower()
                        if distro_name and distro_name in wsl_status_lower:
                            for line in wsl_status_lower.splitlines():
                                if distro_name in line and "running" in line:
                                    is_running = True
                                    break
                        if not is_running:
                            distro_name_err = item.get("Distribution")
                            storage_incident = {
                                "detected": True,
                                "error_code": "ERROR_SHARING_VIOLATION",
                                "target_file": str(vhdx_path),
                                "message": (
                                    f"Storage handle conflict: VHDX file {vhdx_path.name} "
                                    f"for distribution '{distro_name_err}' is locked by "
                                    f"another process (ERROR_SHARING_VIOLATION) despite "
                                    f"not running."
                                ),
                            }
                            return storage_incident
                except Exception:  # noqa: BLE001
                    pass

    return storage_incident


def get_process_list() -> str:
    """Find running dashboard, wsl, or vmwp processes."""
    if sys.platform == "win32":
        powershell_bin = shutil.which("powershell.exe") or shutil.which("powershell")
        if powershell_bin:
            try:
                cmd = (
                    "Get-Process | Where-Object {$_.Name -match "
                    "'python|wsl|vmwp|runner-dashboard'} | "
                    "Select-Object Id, Name, CPU, WorkingSet | "
                    "ConvertTo-Json"
                )
                res = subprocess.run(
                    [powershell_bin, "-NoProfile", "-Command", cmd],
                    capture_output=True,
                    text=True,
                    timeout=5,
                    check=False,
                )
                if res.returncode == 0 and res.stdout.strip():
                    return res.stdout.strip()
            except Exception as exc:  # noqa: BLE001
                return f"Failed to list processes on Windows: {exc}"
    else:
        # Posix systems
        if shutil.which("pgrep") and shutil.which("ps"):
            try:
                res = subprocess.run(
                    "ps -eo pid,ppid,cmd,%mem,%cpu | grep -E 'python|wsl|runner-dashboard' | grep -v grep",
                    shell=True,
                    capture_output=True,
                    text=True,
                    timeout=5,
                    check=False,
                )
                if res.returncode == 0:
                    return res.stdout.strip()
            except Exception as exc:  # noqa: BLE001
                return f"Failed to list processes: {exc}"
    return "No matching processes found or tools unavailable"


def get_recent_logs() -> str:
    """Fetch recent logs from journalctl or default log directory."""
    logs = []
    # 1. Try journalctl if on systemd
    if shutil.which("journalctl"):
        try:
            res = subprocess.run(
                ["journalctl", "-u", "runner-dashboard.service", "-n", "50", "--no-pager"],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
            if res.returncode == 0 and res.stdout.strip():
                return res.stdout.strip()
        except Exception:  # noqa: BLE001
            pass

    # 2. Try ~/.dashboard-logs directory
    log_dir = Path.home() / ".dashboard-logs"
    if log_dir.exists():
        try:
            log_files = sorted(log_dir.glob("*.log"), key=os.path.getmtime, reverse=True)
            if log_files:
                target_log = log_files[0]
                logs.append(f"--- Last 50 lines from {target_log.name} ---")
                with open(target_log, errors="ignore") as f:
                    lines = f.readlines()
                    logs.extend([line.strip() for line in lines[-50:]])
                return "\n".join(logs)
        except Exception as exc:  # noqa: BLE001
            logs.append(f"Failed to read from log directory: {exc}")

    return "No logs available"


def generate_markdown(
    sys_info: dict[str, Any],
    git_info: dict[str, Any],
    wsl_status: str,
    vhdx_status: list[dict[str, Any]],
    sharing_violations: dict[str, Any],
    process_info: str,
    logs: str,
) -> str:
    """Compile diagnostic information into a structured markdown report."""
    lines = []
    lines.append("# Runner Dashboard Crash Diagnostics Report")
    lines.append(f"Generated: {sys_info['time']}")
    lines.append(f"Hostname: {sys_info['hostname']}")
    lines.append(f"OS Platform: {sys_info['platform']}")
    lines.append(f"Python Version: {sys_info['python_version']}")
    lines.append("")

    lines.append("## Storage Incident Detection")
    if sharing_violations.get("detected"):
        lines.append("### ⚠️ ACTIVE SHARING VIOLATION DETECTED")
        lines.append(f"- Error Code: `{sharing_violations.get('error_code')}`")
        lines.append(f"- Target File: `{sharing_violations.get('target_file')}`")
        lines.append(f"- Message: {sharing_violations.get('message')}")
    else:
        lines.append("No active sharing violations or locked database/VHDX files detected.")
    lines.append("")

    lines.append("## Disk Space Summary")
    if sys_info["disk_space"]:
        for name, details in sys_info["disk_space"].items():
            lines.append(f"### {name}")
            lines.append(f"- Total: `{details['total_gb']} GB`")
            lines.append(f"- Used: `{details['used_gb']} GB` ({details['percent_used']}%)")
            lines.append(f"- Free: `{details['free_gb']} GB`")
    else:
        lines.append("Disk space information unavailable.")
    lines.append("")

    lines.append("## WSL Distributions")
    lines.append("```")
    lines.append(wsl_status.strip())
    lines.append("```")
    lines.append("")

    lines.append("## WSL VHDX Attachment Status")
    if vhdx_status:
        for _idx, item in enumerate(vhdx_status):
            lines.append(f"### Distro: {item.get('Distribution', 'unknown')}")
            lines.append(f"- Path: `{item.get('Path', 'unknown')}`")
            lines.append(f"- Attached: `{item.get('Attached')}`")
    else:
        lines.append("No WSL VHDX distributions found or powershell not available.")
    lines.append("")

    lines.append("## Git Metadata")
    lines.append(f"- Branch: `{git_info['branch']}`")
    lines.append(f"- Commit: `{git_info['commit']}`")
    lines.append(f"- Status: `{git_info['status']}`")
    if git_info["diff_summary"]:
        lines.append("### Git Diff Stat")
        lines.append("```")
        lines.append(git_info["diff_summary"])
        lines.append("```")
    lines.append("")

    lines.append("## Environment Variables (Sanitized)")
    lines.append("```")
    for k, v in sorted(os.environ.items()):
        if any(sec in k.upper() for sec in ["TOKEN", "KEY", "SECRET", "PASSWORD", "AUTH", "CRED", "PAT", "JWT"]):
            lines.append(f"{k}=********")
        else:
            lines.append(f"{k}={v}")
    lines.append("```")
    lines.append("")

    lines.append("## Process Listing")
    lines.append("```json")
    lines.append(process_info)
    lines.append("```")
    lines.append("")

    lines.append("## Recent Logs")
    lines.append("```")
    lines.append(logs.strip())
    lines.append("```")
    lines.append("")

    lines.append("## Safe Compaction Flow Instructions")
    lines.append("To safely dismount and compact the VHDX:")
    lines.append("1. Disable WSL Keepalive scheduled task: `Disable-ScheduledTask -TaskName 'WSL-Dashboard-Keepalive'`")
    lines.append("2. Stop WSL completely: `wsl --shutdown` and `Stop-Service -Name 'LxssManager'`")
    lines.append("3. Dismount the VHDX file using `diskpart` (detach vdisk) or `Dismount-VHD`")
    lines.append("4. Run `diskpart` compact command: `compact vdisk` inside `diskpart` after selecting the vdisk file.")
    lines.append("5. Restart the service: `Start-Service -Name 'LxssManager'`")
    lines.append("6. Re-enable WSL Keepalive task: `Enable-ScheduledTask -TaskName 'WSL-Dashboard-Keepalive'`")
    lines.append("")

    return "\n".join(lines)


def main() -> None:
    """Parse command line arguments and execute the diagnostics collection."""
    parser = argparse.ArgumentParser(description="Generate diagnostics artifact after a dashboard crash.")
    parser.add_argument(
        "-o",
        "--output",
        type=str,
        default="diagnostics-artifact.md",
        help="Target output markdown file path",
    )
    args = parser.parse_args()

    log.info("Starting diagnostics collection...")

    sys_info = get_system_info()
    git_info = get_git_info()
    wsl_status = get_wsl_status()
    vhdx_status = query_wsl_vhdx_status()
    sharing_violations = detect_sharing_violations(vhdx_status, wsl_status)
    process_info = get_process_list()
    logs = get_recent_logs()

    markdown_report = generate_markdown(
        sys_info,
        git_info,
        wsl_status,
        vhdx_status,
        sharing_violations,
        process_info,
        logs,
    )

    output_path = Path(args.output).resolve()
    try:
        output_path.write_text(markdown_report, encoding="utf-8")
        log.info("Successfully generated diagnostics artifact: %s", output_path)
    except Exception as exc:  # noqa: BLE001
        log.error("Failed to write diagnostics report to %s: %s", output_path, exc)
        sys.exit(1)


if __name__ == "__main__":
    main()
