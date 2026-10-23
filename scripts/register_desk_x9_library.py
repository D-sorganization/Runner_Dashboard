"""Safely inspect or register the DeskComputer Crucial X9 library source."""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import re
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class MountCandidate:
    drive_letter: str
    mount_path: Path
    label: str
    model: str
    bus_type: str
    serial_number: str
    volume_id: str

    @property
    def serial_suffix(self) -> str:
        return self.serial_number[-4:]

    @property
    def volume_fingerprint(self) -> str:
        return hashlib.sha256(self.volume_id.encode("utf-8")).hexdigest()[:12]


def _rows(value: Any) -> list[dict[str, Any]]:
    return [] if value is None else value if isinstance(value, list) else [value]


def _powershell_json(command: str) -> list[dict[str, Any]]:
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", command],
        check=True,
        capture_output=True,
        text=True,
        timeout=20,
    )
    return _rows(json.loads(result.stdout)) if result.stdout.strip() else []


def read_windows_inventory() -> tuple[list[dict[str, Any]], ...]:
    """Read Windows volume, partition and physical-disk identity."""
    return (
        _powershell_json("Get-Volume | Select-Object DriveLetter,FileSystemLabel,UniqueId | ConvertTo-Json -Compress"),
        _powershell_json("Get-Partition | Select-Object DriveLetter,DiskNumber | ConvertTo-Json -Compress"),
        _powershell_json(
            "Get-Disk | Select-Object Number,FriendlyName,SerialNumber,BusType | ConvertTo-Json -Compress"
        ),
    )


def find_x9_mount(
    volumes: list[dict[str, Any]],
    partitions: list[dict[str, Any]],
    disks: list[dict[str, Any]],
    *,
    mount_root: Path = Path("/mnt"),
    mount_checker: Callable[[Path], bool] | None = None,
) -> MountCandidate:
    """Require a unique, physically identified X9 on an actual WSL mount."""
    checker = mount_checker or Path.is_mount
    partition_by_letter = {str(row.get("DriveLetter", "")).upper(): row for row in partitions if row.get("DriveLetter")}
    disk_by_number = {str(row.get("Number")): row for row in disks}
    found = []
    for volume in volumes:
        letter = str(volume.get("DriveLetter", "")).upper()
        part = partition_by_letter.get(letter)
        disk = disk_by_number.get(str(part.get("DiskNumber"))) if part else None
        if not disk or not re.search(r"\bCrucial\s+X9\b", str(disk.get("FriendlyName", "")), re.I):
            continue
        label, serial, volume_id = (
            str(volume.get("FileSystemLabel", "")),
            str(disk.get("SerialNumber", "")).strip(),
            str(volume.get("UniqueId", "")).strip(),
        )
        if (
            label.casefold() != "crucial x9"
            or str(disk.get("BusType", "")).casefold() != "usb"
            or len(letter) != 1
            or not serial
            or not volume_id
        ):
            continue
        mount = mount_root / letter.lower()
        if mount.is_symlink() or not mount.is_dir() or not checker(mount):
            continue
        found.append(
            MountCandidate(letter, mount.resolve(), label, str(disk["FriendlyName"]), "USB", serial, volume_id)
        )
    if len(found) != 1:
        if len(found) > 1:
            raise ValueError("multiple mounted Crucial X9 volumes found; refusing registration")
        raise ValueError("X9 identity could not be verified on a mounted volume")
    return found[0]


def verify_confirmation(candidate: MountCandidate, serial_suffix: str, volume_fingerprint: str) -> None:
    if serial_suffix != candidate.serial_suffix or volume_fingerprint != candidate.volume_fingerprint:
        raise ValueError("physical identity confirmation did not match the current mounted X9")


def library_root(candidate: MountCandidate, subdir: str) -> Path:
    relative = Path(subdir)
    if relative.is_absolute() or not relative.parts or any(part in {".", ".."} for part in relative.parts):
        raise ValueError("library subdirectory must be a safe relative path")
    root = candidate.mount_path
    for part in relative.parts:
        root = root / part
        if root.is_symlink():
            raise ValueError("library path must not contain symlinks")
    if not root.is_dir():
        raise ValueError("library directory must already exist on the mounted volume")
    resolved = root.resolve()
    if not resolved.is_relative_to(candidate.mount_path):
        raise ValueError("library directory must remain beneath the mounted volume")
    return resolved


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--library-subdir", default="Library")
    parser.add_argument("--apply", action="store_true", help="register after matching identity confirmation")
    parser.add_argument("--confirm-serial-suffix", default="")
    parser.add_argument("--confirm-volume-fingerprint", default="")
    return parser


def main() -> int:
    args = _parser().parse_args()
    try:
        candidate = find_x9_mount(*read_windows_inventory())
        root = library_root(candidate, args.library_subdir)
        logger.info(
            "Verified candidate: drive=%s label=%s model=%s serial_suffix=%s volume_fingerprint=%s path=%s",
            candidate.drive_letter,
            candidate.label,
            candidate.model,
            candidate.serial_suffix,
            candidate.volume_fingerprint,
            root,
        )
        if not args.apply:
            logger.info("Inspection only; no library registration was written. Review identity physically first.")
            return 0
        verify_confirmation(candidate, args.confirm_serial_suffix, args.confirm_volume_fingerprint)
        from library.db import library_db_path
        from library.sources import add_source

        add_source(
            library_db_path(),
            source_id="desk-x9-library",
            label="DeskComputer Crucial X9",
            root_path=str(root),
            shared=False,
            created_by="register_desk_x9_library",
        )
        logger.info("Registered DeskComputer Crucial X9 as a private library source.")
        return 0
    except (OSError, ValueError, subprocess.SubprocessError, json.JSONDecodeError) as exc:
        logger.error("X9 source registration stopped: %s", exc)
        return 2


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    raise SystemExit(main())
