"""Fetch fleet GitHub App tokens from the Desk hub for Barb VM git/gh (#3657)."""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path
from typing import TextIO

_ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(_ROOT), str(_ROOT / "clients" / "fleet")]

from fleet_validators import FleetAPIError  # noqa: E402

log = logging.getLogger("barb.github_hub_token")

_APP_TOKEN_PATH = "/api/github/app-token"


def _fleet_token() -> str | None:
    token = os.environ.get("FLEET_API_TOKEN", "").strip()
    if token:
        return token
    rd_cfg = Path(os.environ.get("RUNNER_DASHBOARD_TOKEN_DIR") or "~/.config/runner-dashboard").expanduser()
    for name in ("token", "fleet-token"):
        path = rd_cfg / name
        if path.is_file():
            return path.read_text(encoding="utf-8").strip()
    path = Path(os.environ.get("RD_THREADS_TOKEN_FILE") or "").expanduser()
    if str(path) and path.is_file():
        return path.read_text(encoding="utf-8").strip()
    return None


def fetch_hub_github_app_token(*, repository: str | None = None) -> tuple[str, float]:
    """Return ``(token, expires_at)`` from the hub; never log the token."""
    from fleet_client import FleetClient  # noqa: PLC0415

    token = _fleet_token()
    if not token:
        raise RuntimeError("FLEET_API_TOKEN (or fleet-token file) is required")
    body: dict[str, str] = {}
    if repository:
        body["repository"] = repository.strip()
    client = FleetClient(token=token)
    try:
        resp = client.request("POST", _APP_TOKEN_PATH, body=body)  # hub requires a JSON body, even empty (#3668)
    except FleetAPIError as exc:
        message = str(exc).replace(token, "[redacted]")
        raise RuntimeError(f"hub app-token request failed: {message}") from exc
    raw = resp.get("token") if isinstance(resp, dict) else None
    minted = str(raw or "").strip()
    if not minted:
        raise RuntimeError("hub app-token response missing token")
    expires_at = float(resp.get("expires_at") or 0.0) if isinstance(resp, dict) else 0.0
    return minted, expires_at


def run_git_credential_helper(
    *,
    stdin: TextIO | None = None,
    stdout: TextIO | None = None,
    repository: str | None = None,
) -> None:
    """Implement ``git credential fill`` for ``https://github.com`` via the hub."""
    inp = stdin or sys.stdin
    out = stdout or sys.stdout
    fields: dict[str, str] = {}
    for line in inp:
        line = line.strip()
        if not line:
            break
        if "=" in line:
            key, value = line.split("=", 1)
            fields[key] = value
    if fields.get("protocol") != "https" or fields.get("host") != "github.com":
        return
    repo = repository or os.environ.get("BARB_GITHUB_REPO", "").strip() or None
    token, _expires = fetch_hub_github_app_token(repository=repo)
    out.write("username=x-access-token\n")
    out.write(f"password={token}\n")


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args[:1] == ["token"]:
        try:
            repo = os.environ.get("BARB_GITHUB_REPO", "").strip() or None
            token, _expires = fetch_hub_github_app_token(repository=repo)
        except Exception as exc:  # noqa: BLE001
            log.error("barb hub token fetch failed: %s", exc)
            return 1
        sys.stdout.write(token)
        return 0
    if args and args[0] not in ("get", "fill", ""):
        return 0
    try:
        run_git_credential_helper()
    except Exception as exc:  # noqa: BLE001
        log.error("barb git credential helper failed: %s", exc)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
