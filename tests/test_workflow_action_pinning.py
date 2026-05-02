"""Issue #390: enforce action SHA pinning uniformity across workflows.

These tests guard against two forms of drift in `.github/workflows/*.yml`:

1. Two different 40-char SHAs pinned for the same `actions/<name>@`
   reference (the bug that motivated this issue: `actions/checkout`
   pinned to v4 in agent workflows but v6 in CI).
2. The same SHA carrying inconsistent `# vN` version comments across
   files (the `# v7` vs `# v9` mislabel on `actions/github-script`).

The tests also verify that `pyproject.toml`'s dev pins for `ruff` and
`mypy` exactly match the versions in `.pre-commit-config.yaml`, so a
local `uv sync` cannot install a newer linter/type-checker than CI.
"""

from __future__ import annotations

import re
from collections import defaultdict
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent
WORKFLOWS_DIR = REPO_ROOT / ".github" / "workflows"

# `uses: <ref>@<sha> # <comment>` — only match 40-char SHAs (already-pinned).
_USES_LINE = re.compile(
    r"^\s*-?\s*(?:name:.*\n\s*)?uses:\s+(?P<ref>[A-Za-z0-9._/-]+)@(?P<sha>[0-9a-f]{40})\s*(?:#\s*(?P<comment>.+?))?\s*$"
)

# Actions covered by the uniformity guard. Local repo-relative refs
# (`./.github/...`) are intentionally excluded.
_GUARDED_ACTIONS = (
    "actions/checkout",
    "actions/setup-python",
    "actions/setup-node",
    "actions/github-script",
    "actions/upload-artifact",
    "actions/download-artifact",
    "actions/create-github-app-token",
)


def _collect_uses() -> list[tuple[Path, int, str, str, str]]:
    """Return (path, lineno, ref, sha, comment) for every pinned `uses:` line."""
    rows: list[tuple[Path, int, str, str, str]] = []
    for wf in sorted(WORKFLOWS_DIR.glob("*.yml")):
        for lineno, raw in enumerate(wf.read_text(encoding="utf-8").splitlines(), start=1):
            m = _USES_LINE.match(raw)
            if not m:
                continue
            rows.append(
                (
                    wf,
                    lineno,
                    m.group("ref"),
                    m.group("sha"),
                    (m.group("comment") or "").strip(),
                )
            )
    return rows


def test_workflows_directory_exists() -> None:
    assert WORKFLOWS_DIR.is_dir(), f"missing workflows dir: {WORKFLOWS_DIR}"


@pytest.mark.parametrize("action", _GUARDED_ACTIONS)
def test_action_sha_uniform_per_action(action: str) -> None:
    """Each guarded action must resolve to a single SHA across all workflows."""
    by_sha: dict[str, list[tuple[Path, int]]] = defaultdict(list)
    for path, lineno, ref, sha, _comment in _collect_uses():
        if ref == action:
            by_sha[sha].append((path, lineno))

    if len(by_sha) <= 1:
        return  # 0 or 1 SHAs: trivially uniform.

    detail_lines = [f"{sha}:" for sha in by_sha]
    for sha, sites in by_sha.items():
        for path, lineno in sites:
            detail_lines.append(f"  {sha}  {path.relative_to(REPO_ROOT)}:{lineno}")
    pytest.fail(f"Inconsistent SHA pinning for {action} across workflows:\n" + "\n".join(detail_lines))


@pytest.mark.parametrize("action", _GUARDED_ACTIONS)
def test_action_version_comment_uniform_per_action(action: str) -> None:
    """A single SHA must always carry the same `# vN` comment.

    This catches the `actions/github-script@<sha> # v9` vs `# v7`
    inconsistency reported in issue #390 — the SHA was identical but
    the comment claimed two different major versions.
    """
    by_sha: dict[str, dict[str, list[tuple[Path, int]]]] = defaultdict(lambda: defaultdict(list))
    for path, lineno, ref, sha, comment in _collect_uses():
        if ref != action:
            continue
        by_sha[sha][comment].append((path, lineno))

    for sha, comments in by_sha.items():
        if len(comments) <= 1:
            continue
        detail = [f"{action}@{sha} carries multiple version comments:"]
        for comment, sites in comments.items():
            label = comment or "(no comment)"
            detail.append(f"  {label}")
            for path, lineno in sites:
                detail.append(f"    {path.relative_to(REPO_ROOT)}:{lineno}")
        pytest.fail("\n".join(detail))


def test_no_floating_action_versions() -> None:
    """No workflow may pin to a floating tag like `@v4` or `@main`."""
    floating = re.compile(r"^\s*-?\s*uses:\s+(?P<ref>[\w./-]+)@(?P<tag>v\d+|main|latest)\s*$")
    offenders: list[str] = []
    for wf in sorted(WORKFLOWS_DIR.glob("*.yml")):
        for lineno, raw in enumerate(wf.read_text(encoding="utf-8").splitlines(), start=1):
            m = floating.match(raw)
            if m:
                offenders.append(f"{wf.relative_to(REPO_ROOT)}:{lineno}  {m.group('ref')}@{m.group('tag')}")
    assert not offenders, "Floating action versions found (pin to SHA):\n" + "\n".join(offenders)


# ---------------------------------------------------------------------------
# pyproject.toml dev pins must match .pre-commit-config.yaml
# ---------------------------------------------------------------------------

_PRECOMMIT_REV = re.compile(
    r"-\s*repo:\s*https://github\.com/(?P<repo>[^\s]+)\s*\n\s*rev:\s*v?(?P<rev>[0-9][0-9A-Za-z.\-+]*)"
)
_PYPROJECT_DEV_PIN = re.compile(r'"\s*(?P<name>[A-Za-z0-9_.\-]+)\s*==\s*(?P<rev>[0-9][0-9A-Za-z.\-+]*)\s*"')


def _parse_precommit_revs() -> dict[str, str]:
    """Map pre-commit repo slug → rev (without leading `v`)."""
    text = (REPO_ROOT / ".pre-commit-config.yaml").read_text()
    return {m.group("repo").rstrip("/"): m.group("rev") for m in _PRECOMMIT_REV.finditer(text)}


def _parse_pyproject_dev_pins() -> dict[str, str]:
    """Map dev-group package name → exact `==` pin (or empty if not exact)."""
    text = (REPO_ROOT / "pyproject.toml").read_text()
    # Narrow to `[dependency-groups]` block to avoid pulling project deps.
    block = re.search(
        r"\[dependency-groups\](?P<body>.*?)(?:\n\[|\Z)",
        text,
        flags=re.DOTALL,
    )
    if not block:
        return {}
    return {m.group("name").lower(): m.group("rev") for m in _PYPROJECT_DEV_PIN.finditer(block.group("body"))}


def test_ruff_version_matches_pre_commit() -> None:
    precommit = _parse_precommit_revs()
    pyproject = _parse_pyproject_dev_pins()
    expected = precommit.get("astral-sh/ruff-pre-commit")
    assert expected, "ruff missing from .pre-commit-config.yaml"
    actual = pyproject.get("ruff")
    assert actual == expected, (
        f"ruff version drift: pyproject [dependency-groups.dev] pins "
        f"{actual!r}, .pre-commit-config.yaml pins v{expected}. "
        f"Use exact `ruff=={expected}` in pyproject.toml."
    )


def test_mypy_version_matches_pre_commit() -> None:
    precommit = _parse_precommit_revs()
    pyproject = _parse_pyproject_dev_pins()
    expected = precommit.get("pre-commit/mirrors-mypy")
    assert expected, "mypy missing from .pre-commit-config.yaml"
    actual = pyproject.get("mypy")
    assert actual == expected, (
        f"mypy version drift: pyproject [dependency-groups.dev] pins "
        f"{actual!r}, .pre-commit-config.yaml pins v{expected}. "
        f"Use exact `mypy=={expected}` in pyproject.toml."
    )
