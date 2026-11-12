#!/usr/bin/env python3
"""Validate host-local HUB_URL against machine_registry.yml before deploy (#2058)."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

log = logging.getLogger(__name__)


def _parse_env_file(path: Path) -> tuple[str | None, bool]:
    hub_url: str | None = None
    override = False
    if not path.is_file():
        return hub_url, override
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line.removeprefix("export ").strip()
        if line.startswith("HUB_URL="):
            hub_url = line.split("=", 1)[1].strip().strip('"').strip("'")
        elif line.startswith("HUB_URL_REGISTRY_OVERRIDE="):
            val = line.split("=", 1)[1].strip().strip('"').strip("'").lower()
            override = val in {"1", "true", "yes", "on"}
    return hub_url, override


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--secrets-file",
        default=str(Path.home() / ".config/runner-dashboard/env"),
        help="Path to runner-dashboard secrets env file",
    )
    parser.add_argument(
        "--allow-registry-override",
        action="store_true",
        help="Treat setup --allow-hub-url-override as consent for this run",
    )
    parser.add_argument(
        "--repo-root",
        default=None,
        help="Repository root (defaults to parent of deploy/)",
    )
    args = parser.parse_args(argv)

    secrets = Path(args.secrets_file).expanduser()
    hub_url, file_override = _parse_env_file(secrets)
    allow = args.allow_registry_override or file_override

    repo_root = Path(args.repo_root or Path(__file__).resolve().parent.parent)
    backend = repo_root / "backend"
    registry_path = backend / "machine_registry.yml"
    if not registry_path.is_file():
        log.error("machine_registry.yml not found at %s", registry_path)
        return 1

    try:
        import yaml
    except ImportError:
        log.error("PyYAML is required for fleet-env-preflight (install backend deps)")
        return 1

    sys.path.insert(0, str(backend))
    from fleet_hub_env_guard import assert_hub_url_env_matches_registry

    try:
        registry = yaml.safe_load(registry_path.read_text(encoding="utf-8"))
        if not isinstance(registry, dict):
            raise RuntimeError("machine_registry.yml must parse to a mapping")
        assert_hub_url_env_matches_registry(
            registry,
            hub_url_env=hub_url,
            allow_registry_override=allow,
        )
    except RuntimeError as exc:
        log.error("%s", exc)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
