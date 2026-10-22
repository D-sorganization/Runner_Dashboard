"""Main CI auto-release naming, gate, retention, and artifact matching."""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "deploy"))

from main_auto_release.gate import commit_passes_main_ci_gate, select_green_commit_with_release  # noqa: E402
from main_auto_release.naming import (  # noqa: E402
    auto_release_tag_for_commit,
    auto_release_version_for_commit,
    is_auto_release_tag,
)
from main_auto_release.release_index import commit_has_release, re_semver_tag  # noqa: E402
from main_auto_release.retention import select_releases_to_delete  # noqa: E402
from node_auto_deploy.artifact import pick_release_assets  # noqa: E402

_GREEN_SHA = "a474ee662a55f9437677116a4d269f74946a8d10"  # pragma: allowlist secret
_FAIL_SHA = "87fa0117aacefbf7b02ca72d52d207599fedd305"  # pragma: allowlist secret
_CANCEL_SHA = "3ee37e4a41514213222cb3730d44e189e3b81a15"  # pragma: allowlist secret
_COMMIT_DATE = "2026-10-22T02:11:34Z"


def _runs(path: Path, sha: str) -> list[dict[str, object]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return [{**run, "head_sha": sha} for run in payload["check_runs"]]


def _minimal_failing_runs(sha: str, *, failing_name: str, failing_conclusion: str) -> list[dict[str, object]]:
    return [
        {"head_sha": sha, "name": failing_name, "status": "completed", "conclusion": failing_conclusion},
        {"head_sha": sha, "name": "quality-gate", "status": "completed", "conclusion": "success"},
    ]


def test_fixture_a474ee66_is_ci_green() -> None:
    runs = _runs(REPO / "tests/deploy/fixtures/a474ee6_check_runs.json", _GREEN_SHA)
    ok, detail = commit_passes_main_ci_gate(runs, _GREEN_SHA)
    assert ok is True
    assert _GREEN_SHA[:7] in detail


def test_vitest_failure_blocks_release() -> None:
    runs = _minimal_failing_runs(_FAIL_SHA, failing_name="Vitest + Coverage", failing_conclusion="failure")
    ok, detail = commit_passes_main_ci_gate(runs, _FAIL_SHA)
    assert ok is False
    assert "Vitest" in detail


def test_cancelled_docker_blocks_release() -> None:
    runs = _minimal_failing_runs(
        _CANCEL_SHA,
        failing_name="Build and scan image",
        failing_conclusion="cancelled",
    )
    ok, detail = commit_passes_main_ci_gate(runs, _CANCEL_SHA)
    assert ok is False
    assert "Build and scan image" in detail


def test_auto_release_tag_is_deterministic() -> None:
    tag = auto_release_tag_for_commit(_GREEN_SHA, _COMMIT_DATE)
    assert tag == "v2026.10.22-a474ee6"
    assert is_auto_release_tag(tag)
    assert auto_release_version_for_commit(_GREEN_SHA, _COMMIT_DATE) == "2026.10.22-a474ee6"


def test_governed_v4100_semver_tag_still_recognized() -> None:
    sha = "a" * 40
    assert re_semver_tag("v4.10.0")
    releases = [{"draft": False, "tag_name": "v4.10.0", "target_commitish": sha, "assets": []}]
    assert commit_has_release(releases, sha)


def test_pick_release_assets_supports_auto_version_string() -> None:
    releases = [
        {
            "draft": False,
            "target_commitish": _GREEN_SHA,
            "assets": [
                {"name": "dashboard-2026.10.22-a474ee6.tar.gz", "url": "https://example/tar"},
                {"name": "dashboard-2026.10.22-a474ee6.tar.gz.sha256", "url": "https://example/sum"},
            ],
        }
    ]
    picked = pick_release_assets(releases, _GREEN_SHA)
    assert picked is not None
    assert picked["version"] == "2026.10.22-a474ee6"


def test_select_green_commit_with_release_skips_head_without_release() -> None:
    head = _runs(REPO / "tests/deploy/fixtures/a474ee6_check_runs.json", _GREEN_SHA)
    older_sha = "b" * 40
    older = [
        {"head_sha": older_sha, "name": "quality-gate", "status": "completed", "conclusion": "success"},
        {"head_sha": older_sha, "name": "tests (3.11)", "status": "completed", "conclusion": "success"},
    ]
    releases = [
        {
            "draft": False,
            "tag_name": "v2026.10.21-bbbbbbb",
            "target_commitish": older_sha,
            "assets": [
                {"name": "dashboard-2026.10.21-bbbbbbb.tar.gz", "url": "u"},
                {"name": "dashboard-2026.10.21-bbbbbbb.tar.gz.sha256", "url": "u"},
            ],
        }
    ]

    def fetch(sha: str) -> list[dict[str, object]]:
        return head if sha == _GREEN_SHA else older

    chosen, _ = select_green_commit_with_release([_GREEN_SHA, older_sha], fetch, releases)
    assert chosen == older_sha


def test_retention_never_deletes_protected_shas() -> None:
    rows = [
        {
            "draft": False,
            "tag_name": f"v2026.10.{i:02d}-abc1234",
            "target_commitish": f"{i:040x}",
            "published_at": datetime(2026, 10, 1, tzinfo=UTC).isoformat(),
        }
        for i in range(35)
    ]
    protected = {rows[30]["target_commitish"], rows[31]["target_commitish"]}
    doomed = select_releases_to_delete(rows, keep_count=30, protected_shas=protected)
    assert protected.isdisjoint({r["target_commitish"] for r in doomed})

