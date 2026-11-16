"""The ``.github/scripts`` JavaScript helpers load and their tests run (#4268, H-20).

The root ``package.json`` declares ``"type": "module"``, so Node treats every
``.js`` file below it as an ES module. A CommonJS helper named ``*.js`` then
fails at ``require`` time with "require is not defined in ES module scope".
That broke ``labels-sync.yml`` on main, and nothing noticed because the two
``node:test`` files for these helpers never ran in CI.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / ".github" / "scripts"
WORKFLOWS = ROOT / ".github" / "workflows"

_COMMONJS_MARKERS = re.compile(r"\brequire\(|\bmodule\.exports\b")
_WORKFLOW_REQUIRE = re.compile(r"require\(\s*['\"]\./(\.github/scripts/[^'\"]+)['\"]\s*\)")
_RELATIVE_REQUIRE = re.compile(r"require\(\s*['\"](\./[^'\"]+)['\"]\s*\)")


def _node_scripts() -> list[Path]:
    return sorted(p for p in SCRIPTS.iterdir() if p.suffix in {".js", ".cjs", ".mjs"})


def _node_tests() -> list[Path]:
    return [p for p in _node_scripts() if ".test." in p.name]


@pytest.mark.unit
def test_root_package_is_an_es_module_package() -> None:
    """The premise of this file; if it changes, the extension rule below can go."""
    package = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))
    assert package.get("type") == "module"


@pytest.mark.unit
def test_commonjs_github_scripts_use_the_cjs_extension() -> None:
    offenders = [
        path.name
        for path in _node_scripts()
        if path.suffix == ".js" and _COMMONJS_MARKERS.search(path.read_text(encoding="utf-8"))
    ]
    assert offenders == [], f"CommonJS helpers that Node will load as ES modules: {offenders}"


@pytest.mark.unit
def test_workflows_require_github_scripts_that_exist() -> None:
    missing: list[str] = []
    for workflow in sorted(WORKFLOWS.glob("*.yml")):
        for target in _WORKFLOW_REQUIRE.findall(workflow.read_text(encoding="utf-8")):
            if not (ROOT / target).is_file():
                missing.append(f"{workflow.name}: {target}")
    assert missing == []


@pytest.mark.unit
def test_relative_requires_between_github_scripts_name_a_real_file() -> None:
    """``require('./x')`` does not try ``x.cjs``, so the extension must be spelled out."""
    missing: list[str] = []
    for script in _node_scripts():
        for target in _RELATIVE_REQUIRE.findall(script.read_text(encoding="utf-8")):
            if not (script.parent / target).is_file():
                missing.append(f"{script.name}: {target}")
    assert missing == []


@pytest.mark.unit
def test_there_are_github_script_tests_to_run() -> None:
    assert [p.name for p in _node_tests()] == ["labels-sync.test.cjs", "panel-review.test.cjs"]


@pytest.mark.unit
@pytest.mark.parametrize("test_file", [p.name for p in _node_tests()] or ["<none>"])
def test_github_script_node_tests_pass(test_file: str) -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")
    result = subprocess.run(
        [node, "--test", str(SCRIPTS / test_file)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        cwd=ROOT,
        timeout=120,
        check=False,
    )
    assert result.returncode == 0, result.stdout[-4000:] + result.stderr[-2000:]
