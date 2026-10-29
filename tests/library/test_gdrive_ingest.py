from __future__ import annotations

import json
import sqlite3
import sys
import types
from pathlib import Path
from typing import Any

import pytest

if "pypdf" not in sys.modules:
    sys.modules["pypdf"] = types.SimpleNamespace(PdfReader=object)  # type: ignore[assignment]

from library.dispatch_refs import attach_library_references  # noqa: E402
from library.gdrive_ingest import (  # noqa: E402
    DriveFile,
    ingest_gdrive_source,
    ingest_gdrive_uri,
    set_default_drive_client,
)
from library.ingest import ingest_sources  # noqa: E402
from library.search import SearchOptions, fetch_passage, search_passages  # noqa: E402
from library.sources import add_source, update_source  # noqa: E402

_FOLDER_A = "1AgentSharedFolderId999999"
_FOLDER_B = "2PrivateDriveFolderId88888"


class MockDriveClient:
    def __init__(
        self, folders: dict[str, list[DriveFile | dict[str, Any]]], blobs: dict[str, bytes] | None = None
    ) -> None:
        self.folders, self.blobs = folders, blobs or {}
        self.listed_folders: list[str] = []
        self.fetched_files: list[str] = []

    def list_files(self, folder_id: str) -> list[DriveFile | dict[str, Any]]:
        self.listed_folders.append(folder_id)
        return list(self.folders.get(folder_id, []))

    def fetch_file(self, file_id: str) -> bytes:
        self.fetched_files.append(file_id)
        return self.blobs[file_id]


def _reg(db: Path, sid: str, fid: str, shared: bool, repos: list[str] | None = None, root: Path | None = None) -> None:
    bt = "google_drive_local" if root else "google_drive_connector"
    kw: dict[str, Any] = {
        "root_path": root,
        "backend_type": bt,
        "drive_folder_id": fid,
        "shared": shared,
        "repos": repos,
    }
    add_source(db, source_id=sid, label=sid, **kw)


def test_ingest_gdrive_source_fetches_indexes_and_cites_with_mocked_drive_client(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = tmp_path / "collections.yml"
    cfg.write_text("repos:\n  UpstreamDrift:\n    topics: [Golf]\ntopics:\n  Golf:\n    folders: [Golf]\n")
    monkeypatch.setenv("LIBRARY_COLLECTIONS_PATH", str(cfg))
    db = tmp_path / "library.sqlite"
    _reg(db, "agent-drive", _FOLDER_A, True, ["UpstreamDrift"])
    nb_bytes = json.dumps({"cells": [{"cell_type": "markdown", "source": ["# Swing\n", "Pelvis velocity."]}]}).encode()
    md_bytes = b"# Pelvis Rotation\nKinematic sequence of pelvis rotation and hip torque in downswing."
    client = MockDriveClient(
        folders={
            _FOLDER_A: [
                DriveFile("drv_md_1", "Golf/Kwon - Pelvis Kinematics 2024.md", mtime=1700000100.0, content=md_bytes),
                DriveFile("drv_txt_2", "Golf/Nesbit - Hub Torque 2023.txt", mtime=1700000200.0),
                DriveFile("drv_nb_3", "Golf/Swing_Notebook_2025.ipynb", content=nb_bytes),
            ]
        },
        blobs={"drv_txt_2": b"Hip joint inverse dynamics and ground reaction moment in golf swing."},
    )
    stats = ingest_sources(db, drive_client=client)["agent-drive"]
    assert (stats["files_seen"], stats["files_indexed"], stats["files_rejected"]) == (3, 3, 0)
    assert client.listed_folders == [_FOLDER_A] and client.fetched_files == ["drv_txt_2"]
    with sqlite3.connect(db) as conn:
        q = "SELECT rel_path, drive_id, source_root, status FROM files WHERE source_id='agent-drive' ORDER BY rel_path"
        row = conn.execute(q).fetchone()
    assert row == ("Golf/Kwon - Pelvis Kinematics 2024.md", "drv_md_1", f"gdrive://{_FOLDER_A}", "present")
    hits = search_passages(db, "pelvis rotation kinematic sequence", options=SearchOptions(repo="UpstreamDrift"))
    assert hits and "Kwon" in hits[0].citation and ":agent-drive:w" in hits[0].cite_key
    h = hits[0]
    kw = {
        "work_id": h.work_id,
        "passage_id": h.passage_id,
        "sha_prefix": h.sha8,
        "page_start": h.page_start,
        "page_end": h.page_end,
    }
    assert fetch_passage(db, source_id="agent-drive", shared_only=True, **kw).cite_key == h.cite_key
    block, work_ids = attach_library_references(db, repo="UpstreamDrift", query="pelvis rotation kinematic sequence")
    assert h.work_id in work_ids and h.citation in block and h.cite_key in block


def test_ingest_gdrive_respects_classification_and_scope(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = tmp_path / "collections.yml"
    cfg.write_text(
        "repos:\n  UpstreamDrift:\n    topics: [Golf]\n  AffineDrift:\n    topics: [Physics]\n"
        "topics:\n  Golf:\n    folders: [Golf]\n  Physics:\n    folders: [Physics]\n"
    )
    monkeypatch.setenv("LIBRARY_COLLECTIONS_PATH", str(cfg))
    db = tmp_path / "library.sqlite"
    _reg(db, "drive-upstream", _FOLDER_A, True, ["UpstreamDrift"])
    _reg(db, "drive-private", _FOLDER_B, False, ["Runner_Dashboard"])
    client = MockDriveClient(
        folders={
            _FOLDER_A: [
                {"id": "s1", "name": "tok.md", "content": b"API_KEY = sk-proj-1234567890abcdefghijklmnop"},
                {"id": "s2", "name": "priv.md", "content": b"<<PRIVATE:SENSITIVE>> Internal salary deliberation."},
                {"id": "s3", "name": "memo.md", "classification": "confidential_work", "content": b"Confidential."},
                DriveFile("u1", "Golf/Lagrange - Golf 2021.md", content=b"Generalized coordinates lag."),
            ],
            _FOLDER_B: [DriveFile("p1", "Fleet Runbook 2024.md", content=b"Generalized coordinates internal queue.")],
        }
    )
    set_default_drive_client(client)
    try:
        stats = ingest_gdrive_uri(f"gdrive://{_FOLDER_A}", db_path=db)
        ingest_gdrive_source(db, source_id="drive-private")
    finally:
        set_default_drive_client(None)
    assert (stats["files_seen"], stats["files_indexed"], stats["files_rejected"]) == (4, 1, 3)
    assert search_passages(db, "salary deliberation") == []
    up = search_passages(db, "Generalized coordinates", options=SearchOptions(repo="UpstreamDrift"))
    assert [h.source_id for h in up] == ["drive-upstream"]
    assert search_passages(db, "Generalized coordinates", options=SearchOptions(repo="AffineDrift")) == []
    sh = search_passages(db, "Generalized coordinates", options=SearchOptions(shared_only=True))
    assert [h.source_id for h in sh] == ["drive-upstream"]
    mirror = tmp_path / "mirror"
    mirror.mkdir()
    _reg(db, "drive-upstream-mirror", _FOLDER_A, False, root=mirror)
    assert search_passages(db, "Generalized coordinates", options=SearchOptions(shared_only=True)) == []
    update_source(db, "drive-upstream-mirror", shared=True)
    assert len(search_passages(db, "Generalized coordinates", options=SearchOptions(shared_only=True))) == 1
