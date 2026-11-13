"""Tests for scripts/bootstrap_public_repo.py (#3083, public-repo slice 5, epic #3077).

Offline throughout: the "mock GitHub API" acceptance criterion is a fake
``github_get`` callable that only ever receives GET requests, so dry-run
proves it changes nothing without touching the network. The export checks
reuse slice 2's publish_guard and inject a fake secret scanner instead of
shelling out to the real gitleaks binary.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

_ROOT = Path(__file__).parent.parent
_SCRIPTS = _ROOT / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import bootstrap_public_repo as bootstrap  # noqa: E402

_HOLDS_POLICY = {
    "holds": [
        {"id": "rd-2657", "aliases": ["#2657"]},
        {"id": "rd-2658-disarmed", "aliases": ["#2658"]},
        {"id": "rd-2880-auto-merge-off", "aliases": ["#2880"]},
    ],
}

_CHECKS_POLICY = {
    "required_contexts": [{"context": "quality-gate"}, {"context": "tests"}],
    "merge_queue": {"parameters": {"merge_method": "SQUASH", "max_entries_to_merge": 5}},
}


# DbC preconditions


def test_require_zero_open_prs_raises_when_nonzero() -> None:
    bootstrap.require_zero_open_prs(0)  # passes
    try:
        bootstrap.require_zero_open_prs(3)
    except bootstrap.PreconditionError as exc:
        assert "3" in str(exc)
    else:
        raise AssertionError("expected PreconditionError")


def test_require_clean_guard_raises_when_dirty() -> None:
    bootstrap.require_clean_guard(False)  # passes
    try:
        bootstrap.require_clean_guard(True)
    except bootstrap.PreconditionError:
        pass
    else:
        raise AssertionError("expected PreconditionError")


def test_apply_preconditions_raises_on_first_violation_and_passes_when_clean() -> None:
    try:
        bootstrap.apply_preconditions(open_pr_count=1, is_dirty=False, admin_session_confirmed=True)
    except bootstrap.PreconditionError as exc:
        assert "open PR" in str(exc)
    else:
        raise AssertionError("expected PreconditionError")
    try:
        bootstrap.apply_preconditions(open_pr_count=0, is_dirty=False, admin_session_confirmed=False)
    except bootstrap.PreconditionError as exc:
        assert "admin-session" in str(exc)
    else:
        raise AssertionError("expected PreconditionError")
    bootstrap.apply_preconditions(open_pr_count=0, is_dirty=False, admin_session_confirmed=True)


# Hold re-filing verifier


def test_verify_holds_refiled_all_missing_when_export_has_no_policy() -> None:
    missing = bootstrap.verify_holds_refiled(_HOLDS_POLICY, None)
    assert missing == ["rd-2657", "rd-2658-disarmed", "rd-2880-auto-merge-off"]


def test_verify_holds_refiled_flags_one_missing_hold() -> None:
    exported = {"holds": [h for h in _HOLDS_POLICY["holds"] if h["id"] != "rd-2657"]}
    assert bootstrap.verify_holds_refiled(_HOLDS_POLICY, exported) == ["rd-2657"]


def test_verify_holds_refiled_empty_when_all_present() -> None:
    assert bootstrap.verify_holds_refiled(_HOLDS_POLICY, _HOLDS_POLICY) == []


# Settings plan (reads required_status_checks_policy.json, DRY)


def test_build_settings_plan_includes_required_checks_and_merge_queue() -> None:
    settings = {
        "license": {"spdx": "Apache-2.0", "files": ["LICENSE"]},
        "actions_permissions": {"fork_pr_approval_required": True},
        "variables": ["CI_RUNNER_MODE"],
        "environments": [{"name": "self-hosted-heavy"}],
    }
    plan = bootstrap.build_settings_plan(settings, _CHECKS_POLICY)
    assert plan["required_status_checks"] == [{"context": "quality-gate"}, {"context": "tests"}]
    assert plan["merge_queue"] == {"merge_method": "SQUASH", "max_entries_to_merge": 5}
    assert plan["variables"] == ["CI_RUNNER_MODE"]
    assert plan["environments"] == [{"name": "self-hosted-heavy"}]


# Export checks: allowlist (slice 2) + secret scan (slice 3)


def test_check_export_clean_tree_has_no_findings(tmp_path: Path) -> None:
    (tmp_path / "backend").mkdir()
    (tmp_path / "backend" / "server.py").write_text("print('ok')\n", encoding="utf-8")
    result = bootstrap.check_export(tmp_path, ["backend/server.py"], secret_scanner=lambda root: [])
    assert result.ok
    assert result.publish_findings == ()
    assert result.secret_findings == ()


def test_check_export_flags_path_outside_allowlist(tmp_path: Path) -> None:
    (tmp_path / "secret-ops").mkdir()
    (tmp_path / "secret-ops" / "topology.yml").write_text("hub: internal\n", encoding="utf-8")
    result = bootstrap.check_export(tmp_path, ["secret-ops/topology.yml"], secret_scanner=lambda root: [])
    assert not result.ok
    assert any("outside-allowlist" in f for f in result.publish_findings)


def test_check_export_flags_secret_scanner_finding(tmp_path: Path) -> None:
    (tmp_path / "backend").mkdir()
    (tmp_path / "backend" / "server.py").write_text("print('ok')\n", encoding="utf-8")
    fake_finding = {"rule": "generic-api-key", "file": "backend/server.py"}
    result = bootstrap.check_export(tmp_path, ["backend/server.py"], secret_scanner=lambda root: [fake_finding])
    assert not result.ok
    assert result.secret_findings == ("generic-api-key",)


# Dry-run against a mock GitHub API


class _RecordingGitHubGet:
    """Fake GitHub client: only ever answers GET, records every call it saw."""

    def __init__(self, responses: dict[str, object]) -> None:
        self.responses = responses
        self.calls: list[tuple[str, str]] = []

    def __call__(self, method: str, url: str, *, token: str | None = None, body: object = None) -> object:
        self.calls.append((method, url))
        if method != "GET":
            raise AssertionError(f"dry-run must never issue a {method} request")
        if body is not None:
            raise AssertionError("a GET request must not carry a body")
        for suffix, response in self.responses.items():
            if url.endswith(suffix):
                return response
        return {}


def _write_dry_run_fixtures(tmp_path: Path) -> dict[str, Path]:
    settings_path = tmp_path / "public_repo_settings.json"
    settings_path.write_text(
        json.dumps(
            {
                "license": {"spdx": "Apache-2.0", "files": ["LICENSE"]},
                "actions_permissions": {"fork_pr_approval_required": True},
                "variables": ["CI_RUNNER_MODE"],
                "environments": [{"name": "self-hosted-heavy"}],
            }
        ),
        encoding="utf-8",
    )
    checks_path = tmp_path / "required_status_checks_policy.json"
    checks_path.write_text(json.dumps(_CHECKS_POLICY), encoding="utf-8")
    return {"settings": settings_path, "checks": checks_path}


_REPO = "D-sorganization/Runner_Dashboard"


def _run_dry(paths: dict[str, Path], fake: _RecordingGitHubGet) -> dict[str, object]:
    return bootstrap.dry_run(
        settings_path=paths["settings"], checks_policy_path=paths["checks"], repo=_REPO, token="x", github_get=fake
    )


def test_dry_run_issues_only_get_requests_and_returns_diff(tmp_path: Path) -> None:
    paths = _write_dry_run_fixtures(tmp_path)
    fake = _RecordingGitHubGet(
        {"/rulesets": [], "/actions/variables": {"variables": []}, "/environments": {"environments": []}}
    )
    result = _run_dry(paths, fake)
    assert fake.calls and all(method == "GET" for method, _ in fake.calls)
    assert result["diff"]["variables"]["add"] == ["CI_RUNNER_MODE"]
    assert result["diff"]["environments"]["add"] == ["self-hosted-heavy"]


def test_dry_run_diff_is_empty_when_current_already_matches(tmp_path: Path) -> None:
    paths = _write_dry_run_fixtures(tmp_path)
    fake = _RecordingGitHubGet(
        {
            "/rulesets": [],
            "/actions/variables": {"variables": [{"name": "CI_RUNNER_MODE"}]},
            "/environments": {"environments": [{"name": "self-hosted-heavy"}]},
        }
    )
    result = _run_dry(paths, fake)
    assert result["diff"]["variables"]["add"] == []
    assert result["diff"]["environments"]["add"] == []
