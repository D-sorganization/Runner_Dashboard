#!/usr/bin/env python3
"""Run a fleet restic restore drill and append a verified event (#4013)."""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

from machine_record import (
    DEFAULT_RECORD_PATH,
    DrillFailedError,
    load_or_init_record,
    post_hub_drill_failure_alert,
    run_restore_drill_once,
    save_record,
    verified_restore_drill_count,
)

log = logging.getLogger("restic_restore_drill")

RunnerFn = Callable[[list[str]], int]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_probe_manifest(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("probe manifest must be a JSON object")
    files = data.get("files")
    if not isinstance(files, list) or not files:
        raise ValueError("probe manifest requires a non-empty files list")
    return data


def verify_restored_probe(manifest: dict[str, Any], restore_root: Path) -> bool:
    for raw in manifest.get("files", []):
        if not isinstance(raw, dict):
            return False
        rel = str(raw.get("path") or "").strip()
        expected = str(raw.get("sha256") or "").strip().lower()
        if not rel or not expected:
            return False
        target = restore_root / rel
        if not target.is_file():
            log.error("probe missing after restore: %s", rel)
            return False
        if sha256_file(target) != expected:
            log.error("probe hash mismatch: %s", rel)
            return False
    return True


def _includes_from_manifest(manifest: dict[str, Any]) -> list[str]:
    includes: list[str] = []
    for raw in manifest.get("files", []):
        if isinstance(raw, dict) and raw.get("path"):
            includes.append(str(raw["path"]))
    return includes


def _run_restic_restore(
    *,
    restic_binary: str,
    snapshot: str,
    restore_root: Path,
    includes: list[str],
    runner: RunnerFn,
) -> int:
    argv = [restic_binary, "restore", snapshot, "--target", str(restore_root)]
    for include in includes:
        argv.extend(["--include", include])
    return runner(argv)


def run_drill(
    *,
    record_path: Path,
    machine: str,
    snapshot: str,
    restore_root: Path,
    probe_manifest: Path,
    restic_binary: str = "restic",
    runner: RunnerFn | None = None,
    alert: Callable[[str], None] | None = None,
    hub_origin: str | None = None,
    hub_token: str | None = None,
) -> int:
    manifest = load_probe_manifest(probe_manifest)
    includes = _includes_from_manifest(manifest)
    restore_root.mkdir(parents=True, exist_ok=True)
    run = runner or (lambda argv: subprocess.call(argv))

    def verify() -> bool:
        rc = _run_restic_restore(
            restic_binary=restic_binary,
            snapshot=snapshot,
            restore_root=restore_root,
            includes=includes,
            runner=run,
        )
        if rc != 0:
            log.error("restic restore exited %s", rc)
            return False
        return verify_restored_probe(manifest, restore_root)

    record = load_or_init_record(record_path, machine)

    def persist(updated: Any) -> None:
        save_record(record_path, updated)

    def default_alert(message: str) -> None:
        log.error("%s", message)
        if hub_origin:
            post_hub_drill_failure_alert(
                hub_origin=hub_origin,
                machine=machine,
                detail=message,
                token=hub_token,
            )

    alert_fn = alert or default_alert

    try:
        run_restore_drill_once(record, verify=verify, alert=alert_fn, persist=persist)
    except DrillFailedError:
        return 3

    count = verified_restore_drill_count(load_or_init_record(record_path, machine))
    log.info("verified restore drills on record: %s", count)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Fleet restic restore drill (#4013)")
    parser.add_argument("--record", default=str(DEFAULT_RECORD_PATH))
    parser.add_argument("--machine", required=True)
    parser.add_argument("--snapshot", required=True, help="restic snapshot id or selector")
    parser.add_argument("--target", required=True, type=Path, help="empty directory for restore")
    parser.add_argument("--probe-manifest", required=True, type=Path)
    parser.add_argument("--restic", default="restic", dest="restic_binary")
    parser.add_argument("--hub-origin", default=os.environ.get("HUB_URL", "").strip() or None)
    parser.add_argument("--hub-token", default=os.environ.get("HUB_FLEET_TOKEN", "").strip() or None)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    try:
        return run_drill(
            record_path=Path(args.record),
            machine=args.machine,
            snapshot=args.snapshot,
            restore_root=args.target,
            probe_manifest=args.probe_manifest,
            restic_binary=args.restic_binary,
            hub_origin=args.hub_origin,
            hub_token=args.hub_token,
        )
    except (ValueError, OSError, json.JSONDecodeError) as exc:
        log.error("%s", exc)
        return 1


if __name__ == "__main__":
    sys.exit(main())
