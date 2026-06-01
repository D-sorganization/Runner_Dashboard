"""tests/test_frontend_typecheck_gate.py — frontend tsc gate contract (issue #823).

Asserts that the frontend CI workflow runs a TypeScript typecheck (`tsc`) gate so
real type errors cannot ship silently, and that the supporting tsconfig wiring is
in place:

  1. frontend-tests.yml defines a `typecheck` job that invokes `npm run typecheck`.
  2. The `typecheck` job is gated by frontend-scope and runs on d-sorg-fleet.
  3. package.json's `typecheck` script points tsc at tsconfig.app.json.
  4. tsconfig.app.json is composite, pulls in vite/client types (so
     `import.meta.env` typechecks), and excludes test files from the gate.
"""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FRONTEND_WORKFLOW = ROOT / ".github" / "workflows" / "frontend-tests.yml"
PACKAGE_JSON = ROOT / "package.json"
TSCONFIG_APP = ROOT / "tsconfig.app.json"


def _strip_jsonc(text: str) -> str:
    """Strip // line comments from a JSONC file so json.loads can parse it."""
    lines = []
    for line in text.splitlines():
        stripped = line.lstrip()
        if stripped.startswith("//"):
            continue
        lines.append(line)
    return "\n".join(lines)


def test_typecheck_job_present_in_frontend_workflow() -> None:
    """frontend-tests.yml must define a typecheck job that runs npm run typecheck."""
    text = FRONTEND_WORKFLOW.read_text(encoding="utf-8")
    assert "\n  typecheck:" in text, "no `typecheck:` job in frontend-tests.yml"
    idx = text.find("\n  typecheck:")
    # Window up to the next top-level job (4-space indent reset at column 2).
    window = text[idx : idx + 900]
    assert "npm run typecheck" in window, "typecheck job does not run `npm run typecheck`"
    assert "d-sorg-fleet" in window, "typecheck job must run on the self-hosted fleet"
    assert "needs: frontend-scope" in window, "typecheck job must depend on frontend-scope"


def test_package_json_typecheck_script_targets_app_tsconfig() -> None:
    """The typecheck script must run tsc against tsconfig.app.json."""
    data = json.loads(PACKAGE_JSON.read_text(encoding="utf-8"))
    script = data.get("scripts", {}).get("typecheck", "")
    assert "tsc" in script, "typecheck script must invoke tsc"
    assert "tsconfig.app.json" in script, (
        "typecheck script must target tsconfig.app.json so tests/legacy are scoped out"
    )


def test_app_tsconfig_excludes_tests_and_pulls_vite_types() -> None:
    """tsconfig.app.json must be composite, include vite/client types, exclude tests."""
    data = json.loads(_strip_jsonc(TSCONFIG_APP.read_text(encoding="utf-8")))
    opts = data.get("compilerOptions", {})
    assert opts.get("composite") is True, "tsconfig.app.json must be composite"
    assert "vite/client" in opts.get("types", []), (
        "tsconfig.app.json must include vite/client types so import.meta.env typechecks"
    )
    excludes = data.get("exclude", [])
    assert any("__tests__" in pat for pat in excludes), "test files must be excluded from the production typecheck gate"
