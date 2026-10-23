from __future__ import annotations

from pathlib import Path

import pytest

from scripts import register_desk_x9_library as registration


def _inventory(drive: str = "E") -> tuple[list[dict[str, object]], ...]:
    return (
        [
            {
                "DriveLetter": drive,
                "FileSystemLabel": "Crucial X9",
                "UniqueId": r"\\?\Volume{stable-volume-id}",
            }
        ],
        [{"DriveLetter": drive, "DiskNumber": 3}],
        [
            {
                "Number": 3,
                "FriendlyName": "Crucial X9 SSD",
                "SerialNumber": "SERIAL1234",
                "BusType": "USB",
            }
        ],
    )


def test_detects_only_verified_mounted_crucial_x9(tmp_path: Path) -> None:
    mount_root = tmp_path / "mnt"
    drive = mount_root / "e"
    drive.mkdir(parents=True)
    candidate = registration.find_x9_mount(
        *_inventory(), mount_root=mount_root, mount_checker=lambda path: path == drive
    )

    assert candidate.mount_path == drive.resolve()
    assert candidate.model == "Crucial X9 SSD"
    assert candidate.bus_type == "USB"
    assert candidate.serial_suffix == "1234"
    assert len(candidate.volume_fingerprint) == 12


def test_rejects_label_only_match(tmp_path: Path) -> None:
    volumes, partitions, disks = _inventory()
    disks[0]["FriendlyName"] = "Generic USB Storage"

    with pytest.raises(ValueError, match="identity could not be verified"):
        registration.find_x9_mount(
            volumes,
            partitions,
            disks,
            mount_root=tmp_path,
            mount_checker=lambda _path: True,
        )


def test_rejects_unmounted_volume(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="identity could not be verified"):
        registration.find_x9_mount(*_inventory(), mount_root=tmp_path, mount_checker=lambda _path: False)


def test_rejects_ambiguous_matching_x9_mounts(tmp_path: Path) -> None:
    volumes, partitions, disks = _inventory()
    (tmp_path / "e").mkdir()
    (tmp_path / "f").mkdir()
    volumes.append({**volumes[0], "DriveLetter": "F", "UniqueId": "other-volume"})
    partitions.append({"DriveLetter": "F", "DiskNumber": 4})
    disks.append({**disks[0], "Number": 4, "SerialNumber": "SERIAL5678"})

    with pytest.raises(ValueError, match="multiple mounted Crucial X9 volumes"):
        registration.find_x9_mount(volumes, partitions, disks, mount_root=tmp_path, mount_checker=lambda _path: True)


def test_requires_matching_physical_confirmation() -> None:
    candidate = registration.MountCandidate(
        drive_letter="E",
        mount_path=Path("/mnt/e"),
        label="Crucial X9",
        model="Crucial X9 SSD",
        bus_type="USB",
        serial_number="SERIAL1234",
        volume_id="stable-id",
    )

    registration.verify_confirmation(candidate, "1234", candidate.volume_fingerprint)
    with pytest.raises(ValueError, match="confirmation did not match"):
        registration.verify_confirmation(candidate, "9999", candidate.volume_fingerprint)


def test_library_root_must_be_existing_directory_below_mount(tmp_path: Path) -> None:
    mount = tmp_path / "mnt" / "e"
    library = mount / "Library"
    library.mkdir(parents=True)
    candidate = registration.MountCandidate(
        drive_letter="E",
        mount_path=mount,
        label="Crucial X9",
        model="Crucial X9 SSD",
        bus_type="USB",
        serial_number="SERIAL1234",
        volume_id="stable-id",
    )

    assert registration.library_root(candidate, "Library") == library.resolve()
    with pytest.raises(ValueError, match="must already exist"):
        registration.library_root(candidate, "Missing")


def test_detects_verified_crucial_x9_pro_product_code(tmp_path: Path) -> None:
    mount_root = tmp_path / "mnt"
    drive = mount_root / "d"
    drive.mkdir(parents=True)
    volumes = [{"DriveLetter": "D", "FileSystemLabel": "X9 Pro", "UniqueId": r"\\?\Volume{x9-pro}"}]
    partitions = [{"DriveLetter": "D", "DiskNumber": 9}]
    disks = [
        {
            "Number": 9,
            "FriendlyName": "Micron CT4000X9PROSSD9",
            "SerialNumber": "PRIVATE-SERIAL-1234",
            "BusType": "USB",
        }
    ]

    candidate = registration.find_x9_mount(
        volumes,
        partitions,
        disks,
        mount_root=mount_root,
        mount_checker=lambda path: path == drive,
    )

    assert candidate.label == "X9 Pro"
    assert candidate.model == "Micron CT4000X9PROSSD9"
    assert candidate.serial_suffix == "1234"


@pytest.mark.parametrize(
    ("label", "model"),
    [
        ("X9 Pro", "Generic USB Storage"),
        ("Archive", "Micron CT4000X9PROSSD9"),
        ("Crucial X9 Pro", "Micron CT5000X9PROSSD9"),
    ],
)
def test_rejects_unverified_x9_pro_identity(tmp_path: Path, label: str, model: str) -> None:
    mount_root = tmp_path / "mnt"
    (mount_root / "d").mkdir(parents=True)
    volumes = [{"DriveLetter": "D", "FileSystemLabel": label, "UniqueId": "volume-id"}]
    partitions = [{"DriveLetter": "D", "DiskNumber": 9}]
    disks = [{"Number": 9, "FriendlyName": model, "SerialNumber": "SERIAL1234", "BusType": "USB"}]

    with pytest.raises(ValueError, match="identity could not be verified"):
        registration.find_x9_mount(
            volumes,
            partitions,
            disks,
            mount_root=mount_root,
            mount_checker=lambda _path: True,
        )
