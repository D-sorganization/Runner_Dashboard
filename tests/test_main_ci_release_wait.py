"""Bounded publisher-wait behavior using fake GitHub and sleep commands."""

from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "deploy"))
from main_auto_release.publish_cli import _load_compare  # noqa: E402
from main_auto_release.publish_cli import main as publish_cli_main  # noqa: E402

SHA = "a474ee662a55f9437677116a4d269f74946a8d10"  # pragma: allowlist secret
SCRIPT = ROOT / "deploy" / "publish-main-ci-release.sh"
WORKFLOW = ROOT / ".github" / "workflows" / "main-ci-auto-release.yml"


def _runs(*records: dict[str, object]) -> dict[str, object]:
    return {
        "total_count": len(records),
        "check_runs": [{"id": 1001 + index, **record} for index, record in enumerate(records)],
    }


def _record(status: str, conclusion: str | None) -> dict[str, object]:
    return {
        "head_sha": SHA,
        "name": "quality-gate",
        "status": status,
        "conclusion": conclusion,
        "started_at": "2026-10-22T00:00:00Z",
        "check_suite": {"id": 1},
        "app": {"id": 1, "slug": "github-actions"},
    }


def _posix_path(path: Path) -> str:
    rendered = path.as_posix()
    if len(rendered) >= 3 and rendered[1] == ":" and rendered[2] == "/":
        return f"/mnt/{rendered[0].lower()}{rendered[2:]}"
    return rendered


def _run_publisher(
    tmp_path: Path,
    checks: list[dict[str, object]],
    *,
    main_sha: str = SHA,
    main_shas: list[str] | None = None,
    require_main_ancestor: bool = False,
    compare_payloads: list[dict[str, object]] | None = None,
    releases: list[dict[str, object]] | None = None,
    release_payloads: list[list[dict[str, object]]] | None = None,
    release_compare_payloads: list[dict[str, object]] | None = None,
    fail_check_api: bool = False,
    extra_args: tuple[str, ...] = (),
) -> subprocess.CompletedProcess[str]:
    if os.name == "nt":
        wsl = shutil.which("wsl.exe")
        if not wsl:
            pytest.skip("WSL is required for publisher shell integration tests on Windows")
        shell_command = [wsl, "--exec", "/bin/bash", "-c"]
    else:
        bash = shutil.which("bash")
        if not bash:
            pytest.skip("bash is required for publisher shell integration tests")
        shell_command = [bash, "-c"]

    sequence_file = tmp_path / "checks.json"
    sequence_file.write_text(json.dumps(checks), encoding="utf-8")
    main_sequence_file = tmp_path / "main-shas.json"
    main_sequence_file.write_text(json.dumps(main_shas or [main_sha]), encoding="utf-8")
    count_file = tmp_path / "check-count.txt"
    count_file.write_text("0", encoding="utf-8")
    main_count_file = tmp_path / "main-count.txt"
    main_count_file.write_text("0", encoding="utf-8")
    compare_sequence_file = tmp_path / "compare-responses.json"
    compare_sequence_file.write_text(json.dumps(compare_payloads or []), encoding="utf-8")
    compare_count_file = tmp_path / "compare-count.txt"
    compare_count_file.write_text("0", encoding="utf-8")
    release_compare_sequence_file = tmp_path / "release-compare-responses.json"
    release_compare_sequence_file.write_text(json.dumps(release_compare_payloads or []), encoding="utf-8")
    release_compare_count_file = tmp_path / "release-compare-count.txt"
    release_compare_count_file.write_text("0", encoding="utf-8")
    releases_file = tmp_path / "fake-releases.json"
    releases_file.write_text(json.dumps(release_payloads or [releases or []]), encoding="utf-8")
    release_fetch_count_file = tmp_path / "release-fetch-count.txt"
    release_fetch_count_file.write_text("0", encoding="utf-8")
    sleep_log = tmp_path / "sleep.log"
    gh = tmp_path / "fake-gh.py"
    gh.write_text(
        "import json, os, sys\n"
        "from pathlib import Path\n"
        "args = sys.argv[1:]\n"
        "endpoint = next((arg for arg in args if arg.startswith('repos/')), '')\n"
        "if endpoint.endswith('/commits/main'):\n"
        "    count_path = Path(os.environ['FAKE_MAIN_COUNT'])\n"
        "    index = int(count_path.read_text())\n"
        "    count_path.write_text(str(index + 1))\n"
        "    rows = json.loads(Path(os.environ['FAKE_MAIN_SEQUENCE']).read_text())\n"
        "    print(rows[min(index, len(rows) - 1)])\n"
        "elif '/compare/' in endpoint:\n"
        "    if not endpoint.endswith('...main'):\n"
        "        count_path = Path(os.environ['FAKE_RELEASE_COMPARE_COUNT'])\n"
        "        index = int(count_path.read_text())\n"
        "        count_path.write_text(str(index + 1))\n"
        "        rows = json.loads(Path(os.environ['FAKE_RELEASE_COMPARE_SEQUENCE']).read_text())\n"
        "        if not rows:\n"
        "            print('missing fake release compare response', file=sys.stderr)\n"
        "            raise SystemExit(2)\n"
        "        print(json.dumps(rows[min(index, len(rows) - 1)]))\n"
        "        raise SystemExit(0)\n"
        "    count_path = Path(os.environ['FAKE_COMPARE_COUNT'])\n"
        "    index = int(count_path.read_text())\n"
        "    count_path.write_text(str(index + 1))\n"
        "    rows = json.loads(Path(os.environ['FAKE_COMPARE_SEQUENCE']).read_text())\n"
        "    if not rows:\n"
        "        print('missing fake compare response', file=sys.stderr)\n"
        "        raise SystemExit(2)\n"
        "    print(json.dumps(rows[min(index, len(rows) - 1)]))\n"
        "elif '/check-runs?' in endpoint:\n"
        "    if '--paginate' not in args or '--slurp' not in args:\n"
        "        print('check-runs request must paginate and slurp', file=sys.stderr)\n"
        "        raise SystemExit(2)\n"
        "    if 'filter=all' not in endpoint:\n"
        "        print('check-runs request must include filter=all', file=sys.stderr)\n"
        "        raise SystemExit(2)\n"
        "    if os.environ.get('FAKE_CHECK_API_FAIL') == '1':\n"
        "        print('fake API unavailable', file=sys.stderr)\n"
        "        raise SystemExit(1)\n"
        "    count_path = Path(os.environ['FAKE_CHECK_COUNT'])\n"
        "    index = int(count_path.read_text())\n"
        "    count_path.write_text(str(index + 1))\n"
        "    rows = json.loads(Path(os.environ['FAKE_CHECK_SEQUENCE']).read_text())\n"
        "    print(json.dumps([rows[min(index, len(rows) - 1)]]))\n"
        "elif endpoint.endswith('/releases?per_page=100'):\n"
        "    count_path = Path(os.environ['FAKE_RELEASE_FETCH_COUNT'])\n"
        "    index = int(count_path.read_text())\n"
        "    count_path.write_text(str(index + 1))\n"
        "    rows = json.loads(Path(os.environ['FAKE_RELEASES']).read_text())\n"
        "    print(json.dumps(rows[min(index, len(rows) - 1)]))\n"
        "else:\n"
        "    print('unexpected fake gh endpoint: ' + endpoint, file=sys.stderr)\n"
        "    raise SystemExit(2)\n",
        encoding="utf-8",
    )
    bash_env = tmp_path / "bash-env.sh"
    bash_env.write_bytes(
        (
            "gh() { python3 " + shlex.quote(_posix_path(gh)) + ' "$@"; }\n'
            'sleep() { printf \'%s\\n\' "$*" >> "$FAKE_SLEEP_LOG"; }\n'
        ).encode("utf-8")
    )

    env = dict(os.environ)
    env.update(
        {
            "BASH_ENV": _posix_path(bash_env),
            "GITHUB_REPOSITORY": "D-sorganization/Runner_Dashboard",
            "FAKE_MAIN_SEQUENCE": _posix_path(main_sequence_file),
            "FAKE_MAIN_COUNT": _posix_path(main_count_file),
            "FAKE_COMPARE_SEQUENCE": _posix_path(compare_sequence_file),
            "FAKE_COMPARE_COUNT": _posix_path(compare_count_file),
            "FAKE_RELEASE_COMPARE_SEQUENCE": _posix_path(release_compare_sequence_file),
            "FAKE_RELEASE_COMPARE_COUNT": _posix_path(release_compare_count_file),
            "FAKE_RELEASES": _posix_path(releases_file),
            "FAKE_RELEASE_FETCH_COUNT": _posix_path(release_fetch_count_file),
            "FAKE_CHECK_SEQUENCE": _posix_path(sequence_file),
            "FAKE_CHECK_COUNT": _posix_path(count_file),
            "FAKE_CHECK_API_FAIL": "1" if fail_check_api else "0",
            "FAKE_SLEEP_LOG": _posix_path(sleep_log),
            "GATE_ATTEMPTS": "3",
            "GATE_SLEEP_SECS": "1",
            "RUNNER_TEMP": _posix_path(tmp_path),
            "GITHUB_OUTPUT": _posix_path(tmp_path / "github-output.txt"),
        }
    )
    wsl_variables = [
        "BASH_ENV",
        "GITHUB_REPOSITORY",
        "FAKE_MAIN_SEQUENCE",
        "FAKE_MAIN_COUNT",
        "FAKE_COMPARE_SEQUENCE",
        "FAKE_COMPARE_COUNT",
        "FAKE_RELEASE_COMPARE_SEQUENCE",
        "FAKE_RELEASE_COMPARE_COUNT",
        "FAKE_RELEASES",
        "FAKE_RELEASE_FETCH_COUNT",
        "FAKE_CHECK_SEQUENCE",
        "FAKE_CHECK_COUNT",
        "FAKE_CHECK_API_FAIL",
        "FAKE_SLEEP_LOG",
        "GATE_ATTEMPTS",
        "GATE_SLEEP_SECS",
        "RUNNER_TEMP",
        "GITHUB_OUTPUT",
    ]
    existing_wsl_env = env.get("WSLENV", "")
    env["WSLENV"] = ":".join(filter(None, [existing_wsl_env, *wsl_variables]))
    mode_args = ("--require-main-ancestor",) if require_main_ancestor else ("--require-current-main",)
    command = (
        "exec bash "
        + shlex.quote(_posix_path(SCRIPT))
        + " "
        + " ".join(
            shlex.quote(arg)
            for arg in (
                "--sha",
                SHA,
                "--committer-date",
                "2026-10-22T00:00:00Z",
                "--dry-run",
                *mode_args,
                *extra_args,
            )
        )
    )
    return subprocess.run(
        [*shell_command, command],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def _compare_payload(*, base: str, head: str, merge_base: str, status: str = "ahead") -> dict[str, object]:
    commits = [{"sha": head}] if status in {"ahead", "diverged"} else []
    return {
        "status": status,
        "base_commit": {"sha": base},
        "head_commit": {"sha": head},
        "merge_base_commit": {"sha": merge_base},
        "total_commits": len(commits),
        "commits": commits,
    }


def test_terminal_failure_stops_without_sleeping_or_releasing(tmp_path: Path) -> None:
    result = _run_publisher(
        tmp_path,
        [_runs(_record("completed", "failure"))],
    )

    assert result.returncode == 0, result.stderr
    assert "terminal" in result.stdout.lower()
    assert not (tmp_path / "sleep.log").exists()
    assert (tmp_path / "check-count.txt").read_text(encoding="utf-8") == "1"
    assert "DRY RUN" not in result.stdout


def test_verify_cli_distinguishes_terminal_failure_from_pending(tmp_path: Path) -> None:
    terminal_path = tmp_path / "terminal.json"
    terminal_path.write_text(json.dumps(_runs(_record("completed", "failure"))), encoding="utf-8")
    pending_path = tmp_path / "pending.json"
    pending_path.write_text(json.dumps(_runs(_record("in_progress", None))), encoding="utf-8")

    assert publish_cli_main(["verify-gate", "--publisher", "--sha", SHA, "--check-runs-json", str(terminal_path)]) == 2
    assert publish_cli_main(["verify-gate", "--publisher", "--sha", SHA, "--check-runs-json", str(pending_path)]) == 1


def test_verify_cli_fails_closed_on_invalid_payload(tmp_path: Path) -> None:
    malformed_path = tmp_path / "malformed.json"
    malformed_path.write_text('{"check_runs": ["not a check run"]}', encoding="utf-8")

    assert publish_cli_main(["verify-gate", "--publisher", "--sha", SHA, "--check-runs-json", str(malformed_path)]) == 3


def test_verify_cli_fails_closed_when_pagination_is_incomplete(tmp_path: Path) -> None:
    partial_path = tmp_path / "partial.json"
    partial_path.write_text(json.dumps([{"total_count": 2, "check_runs": [_record("completed", "success")]}]))

    assert publish_cli_main(["verify-gate", "--publisher", "--sha", SHA, "--check-runs-json", str(partial_path)]) == 3


def test_verify_main_ancestor_uses_authoritative_github_compare_result(tmp_path: Path) -> None:
    compare_path = tmp_path / "compare.json"
    compare_path.write_text(
        json.dumps(_compare_payload(base=SHA, head="b" * 40, merge_base=SHA)),
        encoding="utf-8",
    )

    assert publish_cli_main(["verify-main-ancestor", "--sha", SHA, "--compare-json", str(compare_path)]) == 0


def test_verify_main_ancestor_accepts_identical_compare_without_head_commit(tmp_path: Path) -> None:
    compare_path = tmp_path / "identical-compare.json"
    compare_path.write_text(
        json.dumps(
            {
                "status": "identical",
                "base_commit": {"sha": SHA},
                "head_commit": None,
                "merge_base_commit": {"sha": SHA},
                "total_commits": 0,
                "commits": [],
            }
        ),
        encoding="utf-8",
    )

    assert publish_cli_main(["verify-main-ancestor", "--sha", SHA, "--compare-json", str(compare_path)]) == 0
    assert _load_compare(compare_path, SHA, SHA) == ("identical", SHA, SHA, SHA)


def test_verify_main_ancestor_binds_documented_compare_head_to_last_commit(tmp_path: Path) -> None:
    head_sha = "b" * 40
    compare_path = tmp_path / "ahead-compare.json"
    compare_path.write_text(
        json.dumps(
            {
                "status": "ahead",
                "base_commit": {"sha": SHA},
                "merge_base_commit": {"sha": SHA},
                "total_commits": 2,
                "commits": [{"sha": "c" * 40}, {"sha": head_sha}],
            }
        ),
        encoding="utf-8",
    )

    assert publish_cli_main(["verify-main-ancestor", "--sha", SHA, "--compare-json", str(compare_path)]) == 0


@pytest.mark.parametrize(
    ("base_sha", "merge_base_sha", "status"),
    [
        ("b" * 40, SHA, "identical"),
        (SHA, "b" * 40, "identical"),
        (SHA, SHA, "ahead"),
        (SHA, SHA, "diverged"),
    ],
)
def test_identical_compare_without_head_commit_requires_consistent_proof(
    tmp_path: Path, base_sha: str, merge_base_sha: str, status: str
) -> None:
    compare_path = tmp_path / "contradictory-identical-compare.json"
    compare_path.write_text(
        json.dumps(
            {
                "status": status,
                "base_commit": {"sha": base_sha},
                "head_commit": None,
                "merge_base_commit": {"sha": merge_base_sha},
                "total_commits": 0,
                "commits": [],
            }
        ),
        encoding="utf-8",
    )

    assert publish_cli_main(["verify-main-ancestor", "--sha", SHA, "--compare-json", str(compare_path)]) == 3


def test_release_order_accepts_behind_compare_without_head_when_merge_base_binds_release(tmp_path: Path) -> None:
    released_sha = "d" * 40
    compare_path = tmp_path / "behind-compare-without-head.json"
    compare_path.write_text(
        json.dumps(
            {
                "status": "behind",
                "base_commit": {"sha": SHA},
                "head_commit": None,
                "merge_base_commit": {"sha": released_sha},
                "total_commits": 0,
                "commits": [],
            }
        ),
        encoding="utf-8",
    )

    assert (
        publish_cli_main(
            ["verify-release-order", "--sha", SHA, "--released-sha", released_sha, "--compare-json", str(compare_path)]
        )
        == 0
    )


def test_release_order_rejects_missing_head_when_merge_base_does_not_bind_release(tmp_path: Path) -> None:
    released_sha = "d" * 40
    compare_path = tmp_path / "unbound-behind-compare.json"
    compare_path.write_text(
        json.dumps(
            {
                "status": "behind",
                "base_commit": {"sha": SHA},
                "head_commit": None,
                "merge_base_commit": {"sha": "c" * 40},
                "total_commits": 0,
                "commits": [],
            }
        ),
        encoding="utf-8",
    )

    assert (
        publish_cli_main(
            ["verify-release-order", "--sha", SHA, "--released-sha", released_sha, "--compare-json", str(compare_path)]
        )
        == 3
    )


def test_release_order_rejects_explicit_head_that_disagrees_with_requested_release(tmp_path: Path) -> None:
    released_sha = "d" * 40
    compare_path = tmp_path / "mismatched-release-head.json"
    compare_path.write_text(
        json.dumps(_compare_payload(base=SHA, head="c" * 40, merge_base=released_sha, status="behind")),
        encoding="utf-8",
    )

    assert (
        publish_cli_main(
            ["verify-release-order", "--sha", SHA, "--released-sha", released_sha, "--compare-json", str(compare_path)]
        )
        == 3
    )


def test_compare_rejects_commit_list_longer_than_reported_total(tmp_path: Path) -> None:
    compare_path = tmp_path / "inconsistent-commit-count.json"
    compare_path.write_text(
        json.dumps(
            {
                "status": "ahead",
                "base_commit": {"sha": SHA},
                "merge_base_commit": {"sha": SHA},
                "total_commits": 1,
                "commits": [{"sha": "b" * 40}, {"sha": "c" * 40}],
            }
        ),
        encoding="utf-8",
    )

    assert publish_cli_main(["verify-main-ancestor", "--sha", SHA, "--compare-json", str(compare_path)]) == 3


@pytest.mark.parametrize(("total_commits", "returned_count"), [(2, 1), (260, 1)])
def test_compare_rejects_incomplete_unpaged_commit_window(
    tmp_path: Path, total_commits: int, returned_count: int
) -> None:
    compare_path = tmp_path / "incomplete-compare.json"
    compare_path.write_text(
        json.dumps(
            {
                "status": "ahead",
                "base_commit": {"sha": SHA},
                "merge_base_commit": {"sha": SHA},
                "total_commits": total_commits,
                "commits": [{"sha": f"{index + 1:040x}"} for index in range(returned_count)],
            }
        ),
        encoding="utf-8",
    )

    assert _load_compare(compare_path, SHA) is None


def test_compare_accepts_complete_250_commit_window_when_total_is_larger(tmp_path: Path) -> None:
    compare_path = tmp_path / "capped-compare.json"
    commits = [{"sha": f"{index + 1:040x}"} for index in range(250)]
    compare_path.write_text(
        json.dumps(
            {
                "status": "ahead",
                "base_commit": {"sha": SHA},
                "merge_base_commit": {"sha": SHA},
                "total_commits": 260,
                "commits": commits,
            }
        ),
        encoding="utf-8",
    )

    assert _load_compare(compare_path, SHA) == ("ahead", SHA, commits[-1]["sha"], SHA)


def test_verify_main_ancestor_rejects_diverged_current_main(tmp_path: Path) -> None:
    compare_path = tmp_path / "compare.json"
    compare_path.write_text(
        json.dumps(_compare_payload(base=SHA, head="b" * 40, merge_base="c" * 40, status="diverged")),
        encoding="utf-8",
    )

    assert publish_cli_main(["verify-main-ancestor", "--sha", SHA, "--compare-json", str(compare_path)]) == 1


def test_latest_release_order_guard_skips_an_older_candidate(tmp_path: Path) -> None:
    compare_path = tmp_path / "compare.json"
    compare_path.write_text(
        json.dumps(_compare_payload(base=SHA, head="d" * 40, merge_base=SHA)),
        encoding="utf-8",
    )

    assert (
        publish_cli_main(
            ["verify-release-order", "--sha", SHA, "--released-sha", "d" * 40, "--compare-json", str(compare_path)]
        )
        == 1
    )


def test_latest_release_order_guard_allows_a_newer_candidate(tmp_path: Path) -> None:
    released_sha = "d" * 40
    compare_path = tmp_path / "compare.json"
    compare_path.write_text(
        json.dumps(_compare_payload(base=SHA, head=released_sha, merge_base=released_sha, status="behind")),
        encoding="utf-8",
    )

    assert (
        publish_cli_main(
            ["verify-release-order", "--sha", SHA, "--released-sha", released_sha, "--compare-json", str(compare_path)]
        )
        == 0
    )


def test_release_order_guard_fails_closed_on_unrelated_release_history(tmp_path: Path) -> None:
    released_sha = "d" * 40
    compare_path = tmp_path / "compare.json"
    compare_path.write_text(
        json.dumps(_compare_payload(base=SHA, head=released_sha, merge_base="c" * 40, status="diverged")),
        encoding="utf-8",
    )

    assert (
        publish_cli_main(
            ["verify-release-order", "--sha", SHA, "--released-sha", released_sha, "--compare-json", str(compare_path)]
        )
        == 3
    )


@pytest.mark.parametrize(
    "mutate",
    [
        lambda runs: runs[0].update(id=runs[1]["id"]),
        lambda runs: runs[0].update(id=0),
        lambda runs: runs[0].update(head_sha="not-a-sha"),
        lambda runs: runs[0].update(name="  "),
    ],
)
def test_verify_cli_rejects_duplicate_or_malformed_check_run_identity(tmp_path: Path, mutate) -> None:
    runs = [_record("completed", "success"), _record("completed", "success")]
    runs[1]["id"] = 1002
    mutate(runs)
    payload = tmp_path / "invalid-identity.json"
    payload.write_text(json.dumps(_runs(*runs)), encoding="utf-8")

    assert publish_cli_main(["verify-gate", "--publisher", "--sha", SHA, "--check-runs-json", str(payload)]) == 3


def test_verify_cli_flattens_complete_paginated_payload(tmp_path: Path) -> None:
    complete_path = tmp_path / "complete.json"
    complete_path.write_text(
        json.dumps(
            [
                {"total_count": 2, "check_runs": [{"id": 1001, **_record("completed", "success")}]},
                {
                    "total_count": 2,
                    "check_runs": [
                        {
                            "id": 1002,
                            "head_sha": SHA,
                            "name": "frontend-tests",
                            "status": "completed",
                            "conclusion": "success",
                            "started_at": "2026-10-22T00:00:00Z",
                            "check_suite": {"id": 2},
                            "app": {"id": 1, "slug": "github-actions"},
                        }
                    ],
                },
            ]
        ),
        encoding="utf-8",
    )

    assert publish_cli_main(["verify-gate", "--publisher", "--sha", SHA, "--check-runs-json", str(complete_path)]) == 0


def test_publisher_normalizes_latest_check_attempt_like_node(tmp_path: Path) -> None:
    payload = [
        {
            "total_count": 2,
            "check_runs": [
                {"id": 1001, **_record("completed", "failure")},
                {
                    "id": 1002,
                    **{
                        **_record("completed", "success"),
                        "started_at": "2026-10-21T23:59:00Z",
                    },
                },
            ],
        }
    ]
    attempts_path = tmp_path / "same-suite-attempts.json"
    attempts_path.write_text(json.dumps(payload), encoding="utf-8")

    assert publish_cli_main(["verify-gate", "--publisher", "--sha", SHA, "--check-runs-json", str(attempts_path)]) == 0


def test_publisher_rejects_raw_inventory_over_5000_before_normalizing(tmp_path: Path) -> None:
    attempts = [{"id": 1001 + i, **_record("completed", "cancelled")} for i in range(5001)]
    payload_path = tmp_path / "over-cap-checks.json"
    payload_path.write_text(json.dumps([{"total_count": 5001, "check_runs": attempts}]), encoding="utf-8")

    assert publish_cli_main(["verify-gate", "--publisher", "--sha", SHA, "--check-runs-json", str(payload_path)]) == 3


@pytest.mark.parametrize("bad_record", ["foreign_sha", "missing_conclusion"])
def test_publisher_rejects_bad_older_attempt_before_normalizing(tmp_path: Path, bad_record: str) -> None:
    older = {"id": 1001, **_record("completed", "failure")}
    if bad_record == "foreign_sha":
        older["head_sha"] = "b" * 40
    else:
        older.pop("conclusion")
    payload = [
        {
            "total_count": 2,
            "check_runs": [
                older,
                {
                    "id": 1002,
                    **{
                        **_record("completed", "success"),
                        "started_at": "2026-10-22T00:01:00Z",
                    },
                },
            ],
        }
    ]
    payload_path = tmp_path / f"bad-older-{bad_record}.json"
    payload_path.write_text(json.dumps(payload), encoding="utf-8")

    assert publish_cli_main(["verify-gate", "--publisher", "--sha", SHA, "--check-runs-json", str(payload_path)]) == 3


def test_github_api_error_is_a_failed_publisher_job(tmp_path: Path) -> None:
    result = _run_publisher(
        tmp_path,
        [_runs(_record("completed", "success"))],
        fail_check_api=True,
    )

    assert result.returncode != 0
    assert (tmp_path / "check-count.txt").read_text(encoding="utf-8") == "0"
    assert "DRY RUN" not in result.stdout


def test_pending_gate_waits_then_passes_current_main_dry_run(tmp_path: Path) -> None:
    result = _run_publisher(
        tmp_path,
        [
            _runs(_record("in_progress", None)),
            _runs(_record("completed", "success")),
        ],
    )

    assert result.returncode == 0, result.stderr
    assert "CI gate waiting" in result.stdout
    assert "DRY RUN: would publish" in result.stdout
    assert len((tmp_path / "sleep.log").read_text(encoding="utf-8").splitlines()) == 1


def test_pending_gate_exhausts_bound_without_release(tmp_path: Path) -> None:
    result = _run_publisher(
        tmp_path,
        [_runs(_record("in_progress", None))],
    )

    assert result.returncode == 0, result.stderr
    assert "after 3 attempts" in result.stdout
    assert (tmp_path / "check-count.txt").read_text(encoding="utf-8") == "3"
    assert len((tmp_path / "sleep.log").read_text(encoding="utf-8").splitlines()) == 2
    assert "DRY RUN" not in result.stdout


def test_superseded_automatic_commit_skips_before_polling(tmp_path: Path) -> None:
    result = _run_publisher(
        tmp_path,
        [_runs(_record("completed", "success"))],
        main_sha="b" * 40,
    )

    assert result.returncode == 0, result.stderr
    assert "superseded" in result.stdout.lower()
    assert (tmp_path / "check-count.txt").read_text(encoding="utf-8") == "0"
    assert "DRY RUN" not in result.stdout


def test_superseded_automatic_commit_skips_before_publication(tmp_path: Path) -> None:
    result = _run_publisher(
        tmp_path,
        [_runs(_record("completed", "success"))],
        main_shas=[SHA, "b" * 40],
    )

    assert result.returncode == 0, result.stderr
    assert "superseded" in result.stdout.lower()
    assert (tmp_path / "check-count.txt").read_text(encoding="utf-8") == "1"
    assert "DRY RUN" not in result.stdout


def test_automatic_release_accepts_green_candidate_that_is_ancestor_of_current_main(tmp_path: Path) -> None:
    current_main = "b" * 40
    result = _run_publisher(
        tmp_path,
        [_runs(_record("completed", "success"))],
        require_main_ancestor=True,
        compare_payloads=[
            _compare_payload(base=SHA, head=current_main, merge_base=SHA),
        ],
    )

    assert result.returncode == 0, result.stderr
    assert "DRY RUN: would publish" in result.stdout
    assert (tmp_path / "check-count.txt").read_text(encoding="utf-8") == "1"
    assert int((tmp_path / "compare-count.txt").read_text(encoding="utf-8")) >= 2


def test_automatic_release_skips_candidate_not_in_current_main_history(tmp_path: Path) -> None:
    current_main = "b" * 40
    unrelated_merge_base = "c" * 40
    result = _run_publisher(
        tmp_path,
        [_runs(_record("completed", "success"))],
        require_main_ancestor=True,
        compare_payloads=[
            _compare_payload(base=SHA, head=current_main, merge_base=unrelated_merge_base, status="diverged"),
        ],
    )

    assert result.returncode == 0, result.stderr
    assert "not an ancestor" in result.stdout.lower()
    assert (tmp_path / "check-count.txt").read_text(encoding="utf-8") == "0"
    assert "DRY RUN" not in result.stdout


def test_automatic_release_does_not_publish_behind_latest_auto_release(tmp_path: Path) -> None:
    current_main = "b" * 40
    newest_release = "d" * 40
    result = _run_publisher(
        tmp_path,
        [_runs(_record("completed", "success"))],
        require_main_ancestor=True,
        compare_payloads=[
            _compare_payload(base=SHA, head=current_main, merge_base=SHA),
        ],
        releases=[
            {
                "draft": False,
                "tag_name": "v2026.10.23-ddddddd",
                "target_commitish": newest_release,
            }
        ],
        release_compare_payloads=[
            _compare_payload(base=SHA, head=newest_release, merge_base=SHA),
        ],
    )

    assert result.returncode == 0, result.stderr
    assert "newer auto-release" in result.stdout.lower()
    assert (tmp_path / "check-count.txt").read_text(encoding="utf-8") == "0"
    assert int((tmp_path / "release-compare-count.txt").read_text(encoding="utf-8")) == 1


def test_automatic_release_rechecks_ancestry_before_dry_run_publication(tmp_path: Path) -> None:
    result = _run_publisher(
        tmp_path,
        [_runs(_record("completed", "success"))],
        require_main_ancestor=True,
        compare_payloads=[
            _compare_payload(base=SHA, head="b" * 40, merge_base=SHA),
            _compare_payload(base=SHA, head="b" * 40, merge_base=SHA),
            _compare_payload(base=SHA, head="c" * 40, merge_base="d" * 40, status="diverged"),
        ],
    )

    assert result.returncode == 0, result.stderr
    assert "not an ancestor" in result.stdout.lower()
    assert int((tmp_path / "compare-count.txt").read_text(encoding="utf-8")) == 3
    assert "DRY RUN" not in result.stdout


def test_automatic_release_rechecks_newer_release_before_publication(tmp_path: Path) -> None:
    newer_release = "d" * 40
    latest = {
        "draft": False,
        "tag_name": "v2026.10.23-ddddddd",
        "target_commitish": newer_release,
    }
    result = _run_publisher(
        tmp_path,
        [_runs(_record("completed", "success"))],
        require_main_ancestor=True,
        compare_payloads=[
            _compare_payload(base=SHA, head="b" * 40, merge_base=SHA),
        ],
        release_payloads=[[], [], [], [latest]],
        release_compare_payloads=[
            _compare_payload(base=SHA, head=newer_release, merge_base=SHA),
        ],
    )

    assert result.returncode == 0, result.stderr
    assert "newer auto-release" in result.stdout.lower()
    assert (tmp_path / "check-count.txt").read_text(encoding="utf-8") == "1"
    assert int((tmp_path / "release-compare-count.txt").read_text(encoding="utf-8")) == 1


def _gate_output(tmp_path: Path) -> str:
    return (tmp_path / "github-output.txt").read_text(encoding="utf-8").strip()


def test_gate_only_reports_ok_true_without_publishing(tmp_path: Path) -> None:
    result = _run_publisher(
        tmp_path,
        [_runs(_record("in_progress", None)), _runs(_record("completed", "success"))],
        require_main_ancestor=True,
        compare_payloads=[_compare_payload(base=SHA, head="b" * 40, merge_base=SHA)],
        extra_args=("--gate-only",),
    )

    assert result.returncode == 0, result.stderr
    assert "handing off to the publish job" in result.stdout
    assert "DRY RUN" not in result.stdout
    assert _gate_output(tmp_path) == "ok=true"


def test_gate_only_reports_ok_false_on_exhausted_wait_and_terminal_failure(tmp_path: Path) -> None:
    pending = _run_publisher(
        tmp_path,
        [_runs(_record("in_progress", None))],
        require_main_ancestor=True,
        compare_payloads=[_compare_payload(base=SHA, head="b" * 40, merge_base=SHA)],
        extra_args=("--gate-only",),
    )
    assert pending.returncode == 0, pending.stderr
    assert "after 3 attempts" in pending.stdout
    assert _gate_output(tmp_path) == "ok=false"

    failed_dir = tmp_path / "failed"
    failed_dir.mkdir()
    failed = _run_publisher(
        failed_dir,
        [_runs(_record("completed", "failure"))],
        require_main_ancestor=True,
        compare_payloads=[_compare_payload(base=SHA, head="b" * 40, merge_base=SHA)],
        extra_args=("--gate-only",),
    )
    assert failed.returncode == 0, failed.stderr
    assert _gate_output(failed_dir) == "ok=false"


def test_gate_wait_exits_early_when_newer_commit_is_released_mid_wait(tmp_path: Path) -> None:
    newer_release = "d" * 40
    latest = {"draft": False, "tag_name": "v2026.10.23-ddddddd", "target_commitish": newer_release}
    result = _run_publisher(
        tmp_path,
        [_runs(_record("in_progress", None))],
        require_main_ancestor=True,
        compare_payloads=[_compare_payload(base=SHA, head="b" * 40, merge_base=SHA)],
        release_payloads=[[], [latest]],
        release_compare_payloads=[_compare_payload(base=SHA, head=newer_release, merge_base=SHA)],
        extra_args=("--gate-only",),
    )

    assert result.returncode == 0, result.stderr
    assert "newer auto-release" in result.stdout.lower()
    # Exited on the second iteration instead of burning the remaining attempts.
    assert (tmp_path / "check-count.txt").read_text(encoding="utf-8") == "1"
    assert len((tmp_path / "sleep.log").read_text(encoding="utf-8").splitlines()) == 1
    assert _gate_output(tmp_path) == "ok=false"


def test_publish_only_takes_one_gate_look_and_rechecks_order(tmp_path: Path) -> None:
    result = _run_publisher(
        tmp_path,
        [_runs(_record("in_progress", None))],
        require_main_ancestor=True,
        compare_payloads=[_compare_payload(base=SHA, head="b" * 40, merge_base=SHA)],
        extra_args=("--publish-only",),
    )

    assert result.returncode == 0, result.stderr
    assert "after 1 attempts" in result.stdout
    assert (tmp_path / "check-count.txt").read_text(encoding="utf-8") == "1"
    assert not (tmp_path / "sleep.log").exists()


def test_publish_only_refuses_when_newer_release_landed_after_gate(tmp_path: Path) -> None:
    newer_release = "d" * 40
    latest = {"draft": False, "tag_name": "v2026.10.23-ddddddd", "target_commitish": newer_release}
    result = _run_publisher(
        tmp_path,
        [_runs(_record("completed", "success"))],
        require_main_ancestor=True,
        compare_payloads=[_compare_payload(base=SHA, head="b" * 40, merge_base=SHA)],
        release_payloads=[[latest]],
        release_compare_payloads=[_compare_payload(base=SHA, head=newer_release, merge_base=SHA)],
        extra_args=("--publish-only",),
    )

    assert result.returncode == 0, result.stderr
    assert "newer auto-release" in result.stdout.lower()
    assert "DRY RUN" not in result.stdout


def test_gate_only_and_publish_only_are_mutually_exclusive(tmp_path: Path) -> None:
    result = _run_publisher(
        tmp_path,
        [_runs(_record("completed", "success"))],
        extra_args=("--gate-only", "--publish-only"),
    )
    assert result.returncode == 1
    assert "choose either --gate-only or --publish-only" in result.stderr


def test_gate_job_is_cancellable_and_publish_job_is_not() -> None:
    config = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    jobs = config["jobs"]
    gate, publish, dispatch = (
        jobs["gate"]["concurrency"],
        jobs["publish"]["concurrency"],
        jobs["publish-dispatch"]["concurrency"],
    )
    assert gate["cancel-in-progress"] is True
    assert publish["cancel-in-progress"] is False
    assert dispatch["cancel-in-progress"] is False
    # Uploads from both paths share one never-cancelled lane; the gate has its own.
    assert publish["group"] == dispatch["group"]
    assert gate["group"] != publish["group"]
    # No workflow-wide shared lane: a waiting candidate must not block newer ones.
    assert config["concurrency"]["group"] != "main-ci-auto-release"
    assert "run_id" in config["concurrency"]["group"]
    assert jobs["publish"]["needs"] == "gate"
    assert "needs.gate.outputs.ok == 'true'" in str(jobs["publish"]["if"])
    assert jobs["gate"]["outputs"]["ok"] == "${{ steps.gate.outputs.ok }}"


def test_workflow_run_gate_and_publisher_require_successful_trigger_and_ancestor_main() -> None:
    config = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    condition = str(config["jobs"]["gate"]["if"])
    assert "github.event.workflow_run.conclusion == 'success'" in condition
    gate_run = config["jobs"]["gate"]["steps"][-1]["run"]
    assert "--gate-only" in gate_run
    assert config["jobs"]["gate"]["steps"][-1]["env"]["GATE_ATTEMPTS"] == "45"
    auto_run = config["jobs"]["publish"]["steps"][-1]["run"]
    assert "--publish-only" in auto_run
    dispatch_run = config["jobs"]["publish-dispatch"]["steps"][-1]["run"]
    assert "--require-main-ancestor" in auto_run
    assert "--require-current-main" not in auto_run
    assert "--require-main-ancestor" not in dispatch_run  # Manual dispatch keeps explicit-SHA semantics.


def test_release_regression_tests_run_in_the_issue_2116_smoke_job() -> None:
    workflow = yaml.safe_load(
        (ROOT / ".github" / "workflows" / "ci-auto-deploy-regression.yml").read_text(encoding="utf-8")
    )
    config = workflow if isinstance(workflow, dict) else {}
    triggers = config.get("on", config.get(True, {}))
    pull_paths = triggers.get("pull_request", {}).get("paths", [])
    assert "tests/test_main_ci_release_wait.py" in pull_paths
    run_command = config["jobs"]["auto-deploy-regression"]["steps"][-1]["run"]
    assert "tests/test_main_ci_release_wait.py" in run_command
