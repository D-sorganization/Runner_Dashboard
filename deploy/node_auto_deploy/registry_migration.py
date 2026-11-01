"""Stdlib-only machine registry migration for auto-deploy state carry (#3309).

The dashboard ``machine_registry`` module imports ``security``, which depends on
FastAPI. Auto-deploy may run under a dashboard venv interpreter that is not yet
installed or under a minimal interpreter during release switch; keep migration
logic here so deploy transactions never import backend packages.
"""

from __future__ import annotations

import logging
import os
import shutil
from pathlib import Path
from typing import Literal

log = logging.getLogger("node_auto_deploy.registry_migration")

MigrationOutcome = Literal[
    "migrated",
    "skipped_env_override",
    "skipped_local_exists",
    "skipped_no_legacy",
]


def _local_registry_path() -> Path:
    return Path("~/.config/runner-dashboard/machine_registry.yml").expanduser()


def migrate_legacy_registry_to_local_config(
    *,
    deploy_backend_dir: Path | str | None = None,
) -> MigrationOutcome:
    """Copy ``backend/machine_registry.yml`` into the canonical local config path."""
    if os.environ.get("MACHINE_REGISTRY_PATH", "").strip():
        return "skipped_env_override"
    local_path = _local_registry_path()
    if local_path.is_file():
        return "skipped_local_exists"

    legacy_candidates: list[Path] = []
    if deploy_backend_dir is not None:
        legacy_candidates.append(Path(deploy_backend_dir) / "machine_registry.yml")

    legacy_path: Path | None = None
    for candidate in legacy_candidates:
        if candidate.is_file() and candidate.stat().st_size > 0:
            legacy_path = candidate
            break
    if legacy_path is None:
        return "skipped_no_legacy"

    local_path.parent.mkdir(parents=True, exist_ok=True)
    local_path.parent.chmod(0o700)
    staging = local_path.with_suffix(local_path.suffix + ".migrate.tmp")
    shutil.copy2(legacy_path, staging)
    staging.chmod(0o600)
    os.replace(staging, local_path)
    log.warning(
        "Migrated fleet machine registry from %s to %s (mode 600). "
        "Future loads use the local config path; keep this file out of git.",
        legacy_path,
        local_path,
    )
    return "migrated"
