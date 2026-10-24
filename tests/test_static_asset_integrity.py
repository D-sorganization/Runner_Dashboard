from __future__ import annotations

import hashlib
import io
import sys
from pathlib import Path
from typing import Any

import pytest

_BACKEND = Path(__file__).resolve().parents[1] / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "deploy"))
import artifact_metadata  # noqa: E402
from static_asset_integrity import inspect_static_asset_integrity  # noqa: E402


def _manifest(path: str, data: bytes) -> dict[str, Any]:
    return {
        "schema": "runner-dashboard-static-assets-v1",
        "entries": [{"path": path, "size_bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}],
    }


def test_integrity_matches_expected_static_bytes(tmp_path: Path) -> None:
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_bytes(b"dashboard")

    result = inspect_static_asset_integrity(_manifest("index.html", b"dashboard"), dist)

    assert result == {"status": "match", "checked_files": 1, "changed": [], "missing": []}


def test_integrity_reports_changed_bytes_without_absolute_paths(tmp_path: Path) -> None:
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_bytes(b"tampered!")

    result = inspect_static_asset_integrity(_manifest("index.html", b"dashboard"), dist)

    assert result["status"] == "drift"
    assert result["changed"] == ["index.html"]
    assert str(tmp_path) not in str(result)


def test_integrity_reports_missing_expected_file_and_allows_extras(tmp_path: Path) -> None:
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "old-hash.js").write_text("retained", encoding="utf-8")

    result = inspect_static_asset_integrity(_manifest("index.html", b"dashboard"), dist)

    assert result["status"] == "drift"
    assert result["missing"] == ["index.html"]


def test_integrity_allows_unlisted_retained_assets(tmp_path: Path) -> None:
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_bytes(b"dashboard")
    (dist / "retained-old.js").write_bytes(b"old")

    assert inspect_static_asset_integrity(_manifest("index.html", b"dashboard"), dist)["status"] == "match"


@pytest.mark.parametrize("manifest", [None, {}, {"schema": "wrong", "entries": []}])
def test_integrity_marks_legacy_or_malformed_manifest_unavailable(tmp_path: Path, manifest: Any) -> None:
    dist = tmp_path / "dist"
    dist.mkdir()

    result = inspect_static_asset_integrity(manifest, dist)

    assert result["status"] == "unavailable"


@pytest.mark.parametrize("path", ["/etc/passwd", "../outside", "assets/../../outside", r"assets\\app.js"])
def test_integrity_rejects_unsafe_manifest_paths(tmp_path: Path, path: str) -> None:
    result = inspect_static_asset_integrity(_manifest(path, b"x"), tmp_path)

    assert result["status"] == "unavailable"
    assert str(tmp_path) not in str(result)


def test_integrity_bounds_manifest_entry_count(tmp_path: Path) -> None:
    manifest = {
        "schema": "runner-dashboard-static-assets-v1",
        "entries": [{"path": f"asset-{n}.js", "size_bytes": 0, "sha256": "0" * 64} for n in range(513)],
    }

    assert inspect_static_asset_integrity(manifest, tmp_path)["status"] == "unavailable"


def test_manifest_reader_stops_when_asset_grows_past_byte_limit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dist = tmp_path / "dist"
    dist.mkdir()
    asset = dist / "app.js"
    asset.write_bytes(b"1234")
    monkeypatch.setattr(artifact_metadata, "_MAX_STATIC_ASSET_FILE_BYTES", 4)
    original_open = Path.open
    reads: list[int] = []

    class GrowingStream:
        def __init__(self) -> None:
            with open(asset, "ab") as disk:
                disk.write(b"x" * 100)
            self.stream = io.BytesIO(b"1234" + b"x" * 100)

        def __enter__(self) -> GrowingStream:
            return self

        def __exit__(self, *_args: object) -> None:
            self.stream.close()

        def read(self, size: int = -1) -> bytes:
            chunk = self.stream.read(size)
            reads.append(len(chunk))
            return chunk

    def growing_open(path: Path, *args: Any, **kwargs: Any) -> Any:
        if path == asset:
            return GrowingStream()
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", growing_open)

    with pytest.raises(ValueError, match="changed|limit"):
        artifact_metadata.build_static_asset_manifest(dist)

    assert sum(reads) <= 5


def test_integrity_does_not_follow_symlink_parent_outside_dist(tmp_path: Path) -> None:
    dist = tmp_path / "dist"
    outside = tmp_path / "outside"
    dist.mkdir()
    outside.mkdir()
    (outside / "app.js").write_bytes(b"dashboard")
    (dist / "assets").symlink_to(outside, target_is_directory=True)

    result = inspect_static_asset_integrity(_manifest("assets/app.js", b"dashboard"), dist)

    assert result["status"] == "drift"
    assert result["changed"] == ["assets/app.js"]
    assert str(outside) not in str(result)


def test_integrity_reports_unreadable_asset_as_unavailable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    dist = tmp_path / "dist"
    dist.mkdir()
    asset = dist / "index.html"
    asset.write_bytes(b"dashboard")
    original_open = Path.open

    def denied_open(path: Path, *args: Any, **kwargs: Any) -> Any:
        if path == asset:
            raise PermissionError("fixture")
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", denied_open)

    result = inspect_static_asset_integrity(_manifest("index.html", b"dashboard"), dist)

    assert result["status"] == "unavailable"
    assert result["reason"] == "asset_unavailable"
    assert str(tmp_path) not in str(result)


@pytest.mark.parametrize("sizes", [[32 * 1024 * 1024 + 1], [32 * 1024 * 1024] * 9])
def test_integrity_bounds_declared_hash_work(tmp_path: Path, sizes: list[int]) -> None:
    manifest = {
        "schema": "runner-dashboard-static-assets-v1",
        "entries": [
            {"path": f"asset-{index}.js", "size_bytes": size, "sha256": "0" * 64} for index, size in enumerate(sizes)
        ],
    }

    result = inspect_static_asset_integrity(manifest, tmp_path)

    assert result["status"] == "unavailable"
    assert result["reason"] == "invalid_manifest"
