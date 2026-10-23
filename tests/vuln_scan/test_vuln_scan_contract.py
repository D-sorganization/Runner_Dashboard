"""``.oss-scanner/`` contract rules (upstream-compatible plus fleet rules)."""

from __future__ import annotations

import pytest
from vuln_scan.contract import DEFAULT_DOCKERFILE, DEFAULT_THREAT_MODEL, check_contract

ORG = "D-sorganization"
GOOD = """\
repo: https://github.com/D-sorganization/UpstreamDrift
primary_contact: security@example.org
dockerfile: .oss-scanner/Dockerfile
threat_model: .oss-scanner/threat_model.md
"""
FILES = {DEFAULT_DOCKERFILE: 120, DEFAULT_THREAT_MODEL: 900}


def _check(text: str | None, files: dict[str, int | None] | None = None, repo: str = "UpstreamDrift"):
    return check_contract(org=ORG, repo=repo, project_yaml=text, files=FILES if files is None else files)


def test_absent_contract_is_not_present_and_not_valid() -> None:
    report = _check(None)
    assert report.present is False
    assert report.valid is False
    assert report.problems == ()


def test_good_contract_is_valid() -> None:
    report = _check(GOOD)
    assert report.problems == ()
    assert report.valid
    assert report.dockerfile == DEFAULT_DOCKERFILE
    assert report.threat_model_present is True
    assert report.to_dict()["valid"] is True


def test_defaults_apply_when_paths_omitted() -> None:
    text = "repo: https://github.com/D-sorganization/UpstreamDrift\nprimary_contact: a@b.co\n"
    report = _check(text)
    assert report.valid
    assert report.threat_model == DEFAULT_THREAT_MODEL


def test_missing_threat_model_is_allowed_but_reported() -> None:
    report = _check(GOOD, {DEFAULT_DOCKERFILE: 50, DEFAULT_THREAT_MODEL: None})
    assert report.valid
    assert report.threat_model_present is False


@pytest.mark.parametrize(
    ("extra", "needle"),
    [
        ("cadence: weekly\n", "operator setting"),
        ("budget_usd: 5\n", "operator setting"),
        ("colour: red\n", "unknown field"),
        ("disabled: maybe\n", "disabled must be true or false"),
        ("homepage: http://insecure.example\n", "homepage must be an https"),
        ("auto_ccs: me@example.org\n", "auto_ccs must be a list"),
    ],
)
def test_field_rules(extra: str, needle: str) -> None:
    report = _check(GOOD + extra)
    assert any(needle in p for p in report.problems), report.problems


def test_required_fields() -> None:
    report = _check("homepage: https://example.org\n")
    assert "missing repo" in report.problems
    assert "missing primary_contact" in report.problems


def test_repo_must_be_this_repository() -> None:
    report = _check(GOOD, repo="Tools")
    assert any("must be this repository" in p for p in report.problems)


def test_repo_pin_and_git_suffix_are_accepted() -> None:
    text = GOOD.replace("UpstreamDrift\n", "UpstreamDrift.git#main\n", 1)
    assert _check(text).valid


def test_missing_or_empty_dockerfile_fails() -> None:
    assert any("not found" in p for p in _check(GOOD, {DEFAULT_THREAT_MODEL: 9}).problems)
    assert any("is empty" in p for p in _check(GOOD, {DEFAULT_DOCKERFILE: 0}).problems)


@pytest.mark.parametrize("path", ["/etc/passwd", "../outside/Dockerfile", "has space/Dockerfile"])
def test_paths_must_stay_inside_the_repository(path: str) -> None:
    text = GOOD.replace(".oss-scanner/Dockerfile", path)
    assert any("relative path inside your repository" in p for p in _check(text).problems)


def test_pgp_rules() -> None:
    text = GOOD + "auto_ccs: [x@y.org]\npgp: not-a-key\n"
    problems = _check(text).problems
    assert any("pgp and auto_ccs" in p for p in problems)
    assert any("armored public key" in p for p in problems)


@pytest.mark.parametrize("text", ["- a\n- b\n", "repo: [unclosed\n"])
def test_malformed_yaml_never_raises(text: str) -> None:
    report = _check(text)
    assert report.present and not report.valid


def test_oversized_project_yaml_is_rejected() -> None:
    assert any("larger than" in p for p in _check(GOOD + "#" * 70_000).problems)
