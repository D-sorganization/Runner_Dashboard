from __future__ import annotations

import json
import os
import stat
from types import SimpleNamespace
from unittest.mock import Mock

import autoscaler_identity as identity
import pytest


def _stat(mode: int, uid: int = 0) -> os.stat_result:
    return os.stat_result((mode, 1, 0, 1, uid, 0, 0, 0, 0, 0))


def _receipt_reader(receipt: dict[str, object] | None = None):
    payload = receipt or {
        "schema_version": 1,
        "expected_wsl_distro": "Ubuntu-22.04",
        "machine_id": "a" * 32,
    }
    return lambda: (
        json.dumps(payload).encode("utf-8"),
        _stat(stat.S_IFDIR | 0o755),
        _stat(stat.S_IFREG | 0o644),
    )


def test_identity_requires_receipt_and_machine_id_binding() -> None:
    identity.verify_installation_identity(
        "Ubuntu-22.04",
        live_distro="Ubuntu-22.04",
        receipt_reader=_receipt_reader(),
        machine_id_reader=lambda: "a" * 32,
    )


def test_identity_accepts_receipt_when_systemd_has_no_live_distro_name() -> None:
    identity.verify_installation_identity(
        "Ubuntu-22.04",
        live_distro="",
        receipt_reader=_receipt_reader(),
        machine_id_reader=lambda: "a" * 32,
    )


def test_identity_rejects_mismatched_live_distro() -> None:
    with pytest.raises(ValueError):
        identity.verify_installation_identity(
            "Ubuntu-22.04",
            live_distro="Other",
            receipt_reader=_receipt_reader(),
            machine_id_reader=lambda: "a" * 32,
        )


@pytest.mark.parametrize(
    "receipt",
    [
        {"schema_version": 1, "expected_wsl_distro": "Other", "machine_id": "a" * 32},
        {"schema_version": 2, "expected_wsl_distro": "Ubuntu-22.04", "machine_id": "a" * 32},
        {"schema_version": True, "expected_wsl_distro": "Ubuntu-22.04", "machine_id": "a" * 32},
        {"schema_version": 1, "expected_wsl_distro": "Ubuntu-22.04", "machine_id": "b" * 32},
    ],
)
def test_identity_rejects_wrong_schema_target_or_machine(receipt: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        identity.verify_installation_identity(
            "Ubuntu-22.04",
            live_distro="",
            receipt_reader=_receipt_reader(receipt),
            machine_id_reader=lambda: "a" * 32,
        )


def test_identity_rejects_untrusted_receipt_directory_and_file() -> None:
    for parent, receipt in [
        (_stat(stat.S_IFDIR | 0o777, uid=1000), _stat(stat.S_IFREG | 0o644)),
        (_stat(stat.S_IFDIR | 0o755), _stat(stat.S_IFREG | 0o666)),
    ]:
        with pytest.raises(ValueError):
            identity.verify_installation_identity(
                "Ubuntu-22.04",
                live_distro="",
                receipt_reader=lambda p=parent, r=receipt: (_receipt_reader()()[0], p, r),
                machine_id_reader=lambda: "a" * 32,
            )


def test_identity_rejects_symlinks_and_unreadable_receipt() -> None:
    for parent, receipt in [
        (_stat(stat.S_IFLNK | 0o777), _stat(stat.S_IFREG | 0o644)),
        (_stat(stat.S_IFDIR | 0o755), _stat(stat.S_IFLNK | 0o777)),
    ]:
        with pytest.raises(ValueError):
            identity.verify_installation_identity(
                "Ubuntu-22.04",
                live_distro="",
                receipt_reader=lambda p=parent, r=receipt: (_receipt_reader()()[0], p, r),
                machine_id_reader=lambda: "a" * 32,
            )
    with pytest.raises(PermissionError):
        identity.verify_installation_identity(
            "Ubuntu-22.04",
            live_distro="",
            receipt_reader=lambda: (_ for _ in ()).throw(PermissionError("denied")),
            machine_id_reader=lambda: "a" * 32,
        )


def test_receipt_metadata_is_validated_before_open(monkeypatch: pytest.MonkeyPatch) -> None:
    open_receipt = Mock(side_effect=AssertionError("untrusted receipt must not be read"))
    path = SimpleNamespace(
        parent=SimpleNamespace(lstat=lambda: _stat(stat.S_IFDIR | 0o777, uid=1000)),
        lstat=lambda: _stat(stat.S_IFREG | 0o644),
        open=open_receipt,
    )
    monkeypatch.setattr(identity, "RECEIPT_PATH", path)
    with pytest.raises(ValueError, match="directory ownership or permissions"):
        identity._read_receipt()
    open_receipt.assert_not_called()


def test_receipt_parser_rejects_duplicate_keys_and_extra_schema_fields() -> None:
    with pytest.raises(ValueError, match="duplicate key"):
        identity._reject_duplicate_keys([("schema_version", 1), ("schema_version", 1)])
    receipt = json.dumps(
        {
            "schema_version": 1,
            "expected_wsl_distro": "Ubuntu-22.04",
            "machine_id": "a" * 32,
            "unexpected": True,
        }
    ).encode("utf-8")
    with pytest.raises(ValueError, match="schema"):
        identity.verify_installation_identity(
            "Ubuntu-22.04",
            live_distro="",
            receipt_reader=lambda: (receipt, _stat(stat.S_IFDIR | 0o755), _stat(stat.S_IFREG | 0o644)),
            machine_id_reader=lambda: "a" * 32,
        )


def test_unconfigured_identity_preserves_legacy_behavior() -> None:
    identity.verify_installation_identity("", receipt_reader=lambda: pytest.fail("receipt read"))


def test_receipt_parser_bounds_size_and_handles_deep_json() -> None:
    with pytest.raises(ValueError, match="maximum size"):
        identity._parse_receipt(b" " * (identity.MAX_RECEIPT_BYTES + 1))
    with pytest.raises(ValueError, match="nesting exceeds"):
        identity._parse_receipt(b"[" * 1500 + b"0" + b"]" * 1500)


def test_receipt_parser_maps_recursion_and_json_errors_separately(monkeypatch: pytest.MonkeyPatch) -> None:
    def recursion_error(*_args: object, **_kwargs: object) -> None:
        raise RecursionError("decoder recursion limit")

    monkeypatch.setattr(identity.json, "loads", recursion_error)
    with pytest.raises(ValueError, match="nesting exceeds"):
        identity._parse_receipt(b"{}")

    def invalid_json(*_args: object, **_kwargs: object) -> None:
        raise json.JSONDecodeError("bad json", "{}", 0)

    monkeypatch.setattr(identity.json, "loads", invalid_json)
    with pytest.raises(ValueError, match="receipt JSON is invalid"):
        identity._parse_receipt(b"{}")
