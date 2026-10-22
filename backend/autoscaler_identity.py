"""Fail-closed installation identity checks for opt-in WSL deployments."""

from __future__ import annotations

import json
import os
import re
import stat
from collections.abc import Callable
from pathlib import Path
from typing import Any

RECEIPT_PATH = Path("/etc/runner-dashboard/autoscaler-installation.json")
MACHINE_ID_PATH = Path("/etc/machine-id")
MAX_RECEIPT_BYTES = 4096
MAX_JSON_DEPTH = 16


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate key")
        result[key] = value
    return result


def _read_machine_id() -> str:
    value = MACHINE_ID_PATH.read_text(encoding="ascii").strip()
    if not re.fullmatch(r"[0-9a-fA-F]{32}", value):
        raise ValueError("invalid machine id")
    return value.lower()


def _validate_receipt_metadata(parent_info: os.stat_result, receipt_info: os.stat_result) -> None:
    if stat.S_ISLNK(parent_info.st_mode) or not stat.S_ISDIR(parent_info.st_mode):
        raise ValueError("receipt directory is not a real directory")
    if stat.S_ISLNK(receipt_info.st_mode) or not stat.S_ISREG(receipt_info.st_mode):
        raise ValueError("receipt is not a regular file")
    if parent_info.st_uid != 0 or stat.S_IMODE(parent_info.st_mode) & 0o022:
        raise ValueError("receipt directory ownership or permissions are unsafe")
    if receipt_info.st_uid != 0 or stat.S_IMODE(receipt_info.st_mode) & 0o022:
        raise ValueError("receipt ownership or permissions are unsafe")


def _read_receipt() -> tuple[bytes, os.stat_result, os.stat_result]:
    parent_info = RECEIPT_PATH.parent.lstat()
    receipt_info = RECEIPT_PATH.lstat()
    _validate_receipt_metadata(parent_info, receipt_info)
    with RECEIPT_PATH.open("rb") as receipt_file:
        payload = receipt_file.read(MAX_RECEIPT_BYTES + 1)
    if len(payload) > MAX_RECEIPT_BYTES:
        raise ValueError("receipt exceeds maximum size")
    return payload, parent_info, receipt_info


def _parse_receipt(payload: bytes) -> dict[str, Any]:
    if not isinstance(payload, bytes):
        raise ValueError("receipt payload must be bytes")
    if len(payload) > MAX_RECEIPT_BYTES:
        raise ValueError("receipt exceeds maximum size")
    try:
        parsed = json.loads(payload.decode("utf-8"), object_pairs_hook=_reject_duplicate_keys)
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError, TypeError) as exc:
        raise ValueError("receipt JSON is invalid") from exc
    pending = [(parsed, 0)]
    while pending:
        value, depth = pending.pop()
        if depth > MAX_JSON_DEPTH:
            raise ValueError("receipt JSON nesting exceeds maximum depth")
        if isinstance(value, dict):
            pending.extend((child, depth + 1) for child in value.values())
        elif isinstance(value, list):
            pending.extend((child, depth + 1) for child in value)
    if not isinstance(parsed, dict):
        raise ValueError("receipt must be an object")
    return parsed


def verify_installation_identity(
    expected_distro: str,
    *,
    live_distro: str | None = None,
    receipt_reader: Callable[[], tuple[bytes, os.stat_result, os.stat_result]] = _read_receipt,
    machine_id_reader: Callable[[], str] = _read_machine_id,
) -> None:
    """Require a trusted installation receipt before touching local runner state.

    A missing live WSL_DISTRO_NAME is accepted only when the root-owned receipt
    binds this installation to the current machine-id. This is not proof of
    current Windows registration; it is installation-time attestation.
    """
    if not expected_distro:
        return
    if live_distro is None:
        live_distro = os.environ.get("WSL_DISTRO_NAME")
    if live_distro and live_distro != expected_distro:
        raise ValueError("live WSL distro does not match configured expected distro")
    payload, parent_info, receipt_info = receipt_reader()
    _validate_receipt_metadata(parent_info, receipt_info)
    receipt = _parse_receipt(payload)
    if set(receipt) != {"schema_version", "expected_wsl_distro", "machine_id"}:
        raise ValueError("receipt schema is invalid")
    if type(receipt["schema_version"]) is not int or receipt["schema_version"] != 1:
        raise ValueError("receipt schema version is invalid")
    if receipt["expected_wsl_distro"] != expected_distro:
        raise ValueError("receipt does not match configured expected distro")
    machine_id = receipt["machine_id"]
    if not isinstance(machine_id, str) or not re.fullmatch(r"[0-9a-fA-F]{32}", machine_id):
        raise ValueError("receipt machine id is invalid")
    if machine_id.lower() != machine_id_reader().lower():
        raise ValueError("receipt belongs to a different machine id")
