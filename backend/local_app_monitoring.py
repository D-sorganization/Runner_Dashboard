from __future__ import annotations

import json
import logging
import os
import shlex
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

log = logging.getLogger("dashboard.local_app_monitoring")

_DANGEROUS_SHELL_CHARS = set(";|&`$()<>")

MANIFEST_FILENAME = "local_apps.json"
DEFAULT_DEPLOYMENT_FILENAME = "deployment.json"
DEFAULT_DRIFT_REF = "origin/main"

_EXCLUDED_ENV_KEYS = {
    "GH_TOKEN",
    "GITHUB_TOKEN",
    "ANTHROPIC_API_KEY",
    "DASHBOARD_API_KEY",
    "SECRET",
    "PASSWORD",
    "TOKEN",
}


def _safe_subprocess_env() -> dict[str, str]:
    """Return env dict with secrets stripped (issue #29)."""
    return {k: v for k, v in os.environ.items() if not any(exc in k.upper() for exc in _EXCLUDED_ENV_KEYS)}


def _validate_health_command(cmd: list[str]) -> list[str]:
    """Reject health commands containing shell metacharacters (issue #22)."""
    for part in cmd:
        if any(c in part for c in _DANGEROUS_SHELL_CHARS):
            raise ValueError(f"health_command part contains disallowed characters: {part!r}")
    return cmd


@dataclass(frozen=True)
class LocalAppSpec:
    """A monitored local tool from the manifest."""

    name: str
    path: Path
    artifact_source: Any | None = None
    service: Any | None = None
    health_url: str | None = None
    health_command: list[str] | None = None
    rollback_strategy: str | None = None
    owner: str | None = None
    deployment_file: Path | None = None
    version_file: Path | None = None
    drift_ref: str = DEFAULT_DRIFT_REF

    @property
    def install_path(self) -> Path:
        return self.path

    @property
    def service_definition(self) -> Any | None:
        return self.service


def manifest_path(root: Path | None = None) -> Path:
    base = root or Path(__file__).resolve().parent.parent
    return base / MANIFEST_FILENAME


def _first_manifest_entries(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, dict):
        for key in ("tools", "apps"):
            entries = payload.get(key)
            if isinstance(entries, list) and entries:
                return list(entries)
        entries = payload.get("tools")
        if isinstance(entries, list):
            return list(entries)
        entries = payload.get("apps")
        if isinstance(entries, list):
            return list(entries)
        return []
    if isinstance(payload, list):
        return list(payload)
    raise ValueError("local tools manifest must contain a list of tools")


def _coerce_command(value: Any, *, field: str, index: int) -> list[str]:
    if isinstance(value, str):
        # Validate raw string before splitting (issue #22)
        if any(c in value for c in _DANGEROUS_SHELL_CHARS):
            raise ValueError(f"local tools manifest entry {index} {field} contains disallowed characters")
        command = shlex.split(value)
    elif isinstance(value, (list, tuple)):
        command = []
        for item in value:
            if not isinstance(item, str):
                raise ValueError(f"local tools manifest entry {index} has invalid {field}")
            text = item.strip()
            if text:
                command.append(text)
        _validate_health_command(command)
    else:
        raise ValueError(f"local tools manifest entry {index} has invalid {field}")
    if not command:
        raise ValueError(f"local tools manifest entry {index} has invalid {field}")
    return command


def _coerce_path(value: str, *, base: Path | None = None) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute() and base is not None:
        return (base / path).expanduser()
    return path


def _manifest_install_root(raw_entry: dict[str, Any], manifest_dir: Path) -> Path:
    install_path = raw_entry.get("install_path", raw_entry.get("path"))
    if not isinstance(install_path, str) or not install_path.strip():
        raise ValueError("missing install path")
    return _coerce_path(install_path, base=manifest_dir)


def _deployment_file_for(raw_entry: dict[str, Any], install_path: Path) -> Path | None:
    deployment_file = raw_entry.get("deployment_file")
    if deployment_file is None:
        return install_path / DEFAULT_DEPLOYMENT_FILENAME
    if not isinstance(deployment_file, str) or not deployment_file.strip():
        raise ValueError("invalid deployment_file")
    return _coerce_path(deployment_file, base=install_path)


def _version_file_for(raw_entry: dict[str, Any], install_path: Path) -> Path | None:
    version_file = raw_entry.get("version_file")
    if version_file is None:
        return None
    if not isinstance(version_file, str) or not version_file.strip():
        raise ValueError("invalid version_file")
    return _coerce_path(version_file, base=install_path)


def _validate_local_app_entry(entry: dict[str, Any], index: int) -> None:
    """Validate required fields in a local app manifest entry (issue #44)."""
    required = {"name"}
    missing = required - set(entry.keys())
    if missing:
        raise ValueError(f"local_app entry {index} missing required fields: {missing}")


def load_manifest(path: Path | None = None) -> list[LocalAppSpec]:
    """Load and validate the local tools manifest."""

    manifest = path or manifest_path()
    try:
        payload = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        log.warning("Failed to load local apps manifest at %s: %s", manifest, exc)
        raise
    entries = _first_manifest_entries(payload)

    apps: list[LocalAppSpec] = []
    manifest_dir = manifest.parent
    for index, raw_entry in enumerate(entries):
        if not isinstance(raw_entry, dict):
            raise ValueError(f"local tools manifest entry {index} is not an object")
        _validate_local_app_entry(raw_entry, index)
        name = raw_entry.get("name")
        if not isinstance(name, str) or not name.strip():
            raise ValueError(f"local tools manifest entry {index} is missing name")
        install_path = _manifest_install_root(raw_entry, manifest_dir)
        artifact_source = raw_entry.get("artifact_source")
        service = raw_entry.get("service")
        if service is not None and not isinstance(service, (str, dict)):
            raise ValueError(f"local tools manifest entry {index} has invalid service")
        health_url = raw_entry.get("health_url")
        if health_url is not None and not isinstance(health_url, str):
            raise ValueError(f"local tools manifest entry {index} has invalid health_url")
        health_command = raw_entry.get("health_command")
        if health_command is not None:
            health_command = _coerce_command(health_command, field="health_command", index=index)
        rollback_strategy = raw_entry.get("rollback_strategy")
        if rollback_strategy is not None and not isinstance(rollback_strategy, str):
            raise ValueError(f"local tools manifest entry {index} has invalid rollback_strategy")
        owner = raw_entry.get("owner")
        if owner is not None and not isinstance(owner, str):
            raise ValueError(f"local tools manifest entry {index} has invalid owner")
        drift_ref = raw_entry.get("drift_ref", DEFAULT_DRIFT_REF)
        if not isinstance(drift_ref, str) or not drift_ref.strip():
            raise ValueError(f"local tools manifest entry {index} has invalid drift_ref")
        deployment_file = _deployment_file_for(raw_entry, install_path)
        version_file = _version_file_for(raw_entry, install_path)
        apps.append(
            LocalAppSpec(
                name=name.strip(),
                path=install_path,
                artifact_source=artifact_source,
                service=service,
                health_url=health_url.strip() if isinstance(health_url, str) else None,
                health_command=health_command,
                rollback_strategy=rollback_strategy.strip() if isinstance(rollback_strategy, str) else None,
                owner=owner.strip() if isinstance(owner, str) else None,
                deployment_file=deployment_file,
                version_file=version_file,
                drift_ref=drift_ref.strip(),
            )
        )
    return apps


def build_dirty_command(app: LocalAppSpec) -> list[str]:
    return [
        "git",
        "-C",
        str(app.path),
        "status",
        "--porcelain=v1",
        "--untracked-files=all",
    ]


def build_drift_command(app: LocalAppSpec) -> list[str]:
    return [
        "git",
        "-C",
        str(app.path),
        "rev-list",
        "--left-right",
        "--count",
        f"HEAD...{app.drift_ref}",
    ]


def build_service_command(app: LocalAppSpec) -> list[str] | None:
    if not app.service:
        return None
    if isinstance(app.service, str):
        return ["systemctl", "is-active", app.service]
    service_command = app.service.get("state_command") or app.service.get("command")
    if service_command is not None:
        return _coerce_command(service_command, field="service.state_command", index=0)
    service_name = app.service.get("name")
    if isinstance(service_name, str) and service_name.strip():
        return ["systemctl", "is-active", service_name.strip()]
    raise ValueError("service definition must provide a name or command")


def build_health_command(app: LocalAppSpec) -> list[str] | None:
    if app.health_command:
        return list(app.health_command)
    return None


def _read_text_version(path: Path | None) -> str | None:
    if path is None:
        return None
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if stripped and not stripped.startswith("#"):
                return stripped
    except (FileNotFoundError, OSError):
        return None
    return None


def _load_deployment_payload(path: Path | None) -> dict[str, Any]:
    if path is None:
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def probe_deployment(app: LocalAppSpec) -> dict[str, Any]:
    payload = _load_deployment_payload(app.deployment_file)
    deployed_version = str(payload.get("version") or payload.get("deployed_version") or "").strip()
    deployed_version_source = "deployment-file" if deployed_version else None
    if not deployed_version:
        version_file = _read_text_version(app.version_file)
        if version_file:
            deployed_version = version_file
            deployed_version_source = "version-file"
    if not deployed_version and payload.get("git_sha"):
        deployed_version = str(payload.get("git_sha"))
        deployed_version_source = "deployment-file"
    return {
        "available": bool(deployed_version or payload),
        "version": deployed_version or None,
        "version_source": deployed_version_source,
        "source": payload.get("source"),
        "artifact_source": app.artifact_source,
        "deployment_file": str(app.deployment_file) if app.deployment_file else None,
        "git_sha": payload.get("git_sha"),
        "git_branch": payload.get("git_branch"),
        "git_dirty": payload.get("git_dirty"),
        "deployed_at": payload.get("deployed_at"),
        "hostname": payload.get("hostname"),
        "compatibility": payload.get("compatibility"),
        "raw": payload or None,
    }


def probe_health_command(command: list[str] | None) -> dict[str, Any]:
    if not command:
        return {"available": False, "status": "not-configured"}
    result = run_command(command, timeout=10)
    payload: dict[str, Any] = {}
    if result.stdout.strip():
        try:
            parsed = json.loads(result.stdout)
        except ValueError:
            parsed = {}
        if isinstance(parsed, dict):
            payload = parsed
    if result.returncode == 0:
        return {
            "available": True,
            "ok": True,
            "status": payload.get("status") or "healthy",
            "command": command,
            "stdout": result.stdout.strip(),
            "payload": payload,
        }
    return {
        "available": True,
        "ok": False,
        "status": payload.get("status") or "unhealthy",
        "command": command,
        "stdout": result.stdout.strip(),
        "stderr": result.stderr.strip(),
        "payload": payload,
    }


def run_command(command: list[str], timeout: int = 10) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(  # noqa: S603
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=timeout,
            env=_safe_subprocess_env(),
        )
    except FileNotFoundError as exc:
        return subprocess.CompletedProcess(command, 127, "", str(exc))
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout.decode("utf-8", errors="replace") if exc.stdout else ""
        stderr = exc.stderr.decode("utf-8", errors="replace") if exc.stderr else ""
        message = stderr or f"Command timed out after {timeout} seconds"
        return subprocess.CompletedProcess(command, 124, stdout, message)
    except OSError as exc:
        return subprocess.CompletedProcess(command, 1, "", str(exc))


def probe_health(app: LocalAppSpec) -> dict[str, Any]:
    if app.health_url:
        health_url = app.health_url
    else:
        health_url = None
    if health_url:
        try:
            response = httpx.get(health_url, timeout=5.0)
            payload: dict[str, Any]
            try:
                payload = response.json()
            except ValueError:
                payload = {}
            return {
                "available": True,
                "ok": response.status_code == 200,
                "status_code": response.status_code,
                "status": payload.get("status") or ("healthy" if response.status_code == 200 else "degraded"),
                "payload": payload,
                "url": health_url,
            }
        except Exception as exc:
            return {
                "available": True,
                "ok": False,
                "status": "unreachable",
                "error": str(exc),
                "url": health_url,
            }
    return probe_health_command(build_health_command(app))


def probe_local_app(app: LocalAppSpec) -> dict[str, Any]:
    deployment = probe_deployment(app)
    dirty_result = run_command(build_dirty_command(app))
    dirty_available = dirty_result.returncode == 0
    dirty_entries = [line for line in dirty_result.stdout.splitlines() if line.strip()] if dirty_available else []
    drift_result = run_command(build_drift_command(app))
    drift_ahead = drift_behind = None
    if drift_result.returncode == 0:
        parts = drift_result.stdout.strip().split()
        if len(parts) == 2 and all(part.isdigit() for part in parts):
            ahead, behind = (int(part) for part in parts)
            drift_ahead = ahead
            drift_behind = behind

    service_result = None
    service_command = None
    service_error = None
    try:
        service_command = build_service_command(app)
    except ValueError as exc:
        service_error = str(exc)
    if service_command is not None:
        service_result = run_command(service_command)

    service_status = "not-configured"
    if service_result is not None:
        service_status = "active" if service_result.returncode == 0 else "inactive"
    if service_error is not None:
        service_status = "invalid"

    deployed_version = deployment.get("version")
    dirty = bool(dirty_entries) if dirty_available else None
    drift = {
        "ahead": drift_ahead,
        "behind": drift_behind,
        "ref": app.drift_ref,
        "available": drift_result.returncode == 0,
    }
    if drift_result.returncode != 0:
        drift["error"] = drift_result.stderr.strip() or "drift probe failed"

    report = {
        "name": app.name,
        "path": str(app.path),
        "install_path": str(app.install_path),
        "artifact_source": app.artifact_source,
        "service": app.service,
        "service_definition": app.service_definition,
        "health_url": app.health_url,
        "health_command": app.health_command,
        "rollback_strategy": app.rollback_strategy,
        "owner": app.owner,
        "deployment": deployment,
        "deployed_version": deployed_version,
        "dirty": dirty,
        "dirty_available": dirty_available,
        "dirty_files": dirty_entries,
        "drift": drift,
        "service_status": service_status,
        "service_error": service_error,
        "health": probe_health(app),
    }
    if not dirty_available:
        report["dirty_error"] = dirty_result.stderr.strip() or "dirty probe failed"
    return report


def collect_local_apps(path: Path | None = None) -> dict[str, Any]:
    apps = load_manifest(path)
    reports = [probe_local_app(app) for app in apps]
    return {
        "manifest_path": str(path or manifest_path()),
        "count": len(apps),
        "tool_count": len(apps),
        "apps": reports,
        "tools": reports,
    }
