import io
import json
from pathlib import Path
from urllib import request

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]


def test_api_generation_has_single_canonical_script_and_output() -> None:
    package = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))
    scripts = package["scripts"]

    assert scripts["generate-api"] == "bash scripts/gen-api-client.sh"
    assert scripts["generate-api:check"] == "bash scripts/gen-api-client.sh --check"
    assert "generate:api" not in scripts
    assert package["devDependencies"]["prettier"] == "3.6.2"
    assert not (ROOT / "frontend/src/types/api.d.ts").exists()


def test_api_generation_formats_openapi_snapshot_before_diff() -> None:
    generator = (ROOT / "scripts/gen-api-client.sh").read_text(encoding="utf-8")

    assert 'npx prettier --parser json --write "$TMP_SNAPSHOT"' in generator
    assert generator.index('npx prettier --parser json --write "$TMP_SNAPSHOT"') < generator.index(
        'npx openapi-typescript "$TMP_SNAPSHOT" --output "$TMP_TYPES"'
    )


def test_frontend_ci_checks_generated_api_contract() -> None:
    workflow = (ROOT / ".github/workflows/frontend-tests.yml").read_text(encoding="utf-8")

    assert "npm run generate-api:check" in workflow
    assert workflow.index("npm run generate-api:check") < workflow.index("npm run typecheck")


def _assert_api_scope_outputs(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    changed_files: list[str],
    expected: dict[str, str],
) -> None:
    """Execute the workflow classifier for backend, unrelated, and frontend changes."""
    workflow = yaml.safe_load((ROOT / ".github/workflows/frontend-tests.yml").read_text(encoding="utf-8"))
    scope_job = workflow["jobs"]["frontend-scope"]
    scope_step = next(step for step in scope_job["steps"] if step.get("id") == "scope")
    typecheck = workflow["jobs"]["typecheck"]
    assert scope_job["outputs"]["run_api_contract"] == "${{ steps.scope.outputs.run_api_contract }}"
    assert any(
        step.get("if") == "needs.frontend-scope.outputs.run_api_contract == 'true'"
        and "npm run generate-api:check" in step.get("run", "")
        for step in typecheck["steps"]
    )

    lines = scope_step["run"].splitlines()
    heredoc_start = next(index for index, line in enumerate(lines) if "- <<'EOF'" in line)
    heredoc_end = next(index for index in range(heredoc_start + 1, len(lines)) if lines[index] == "EOF")
    classifier = "\n".join(lines[heredoc_start + 1 : heredoc_end])

    payload = json.dumps([{"filename": path} for path in changed_files]).encode()
    monkeypatch.setenv("REPOSITORY", "D-sorganization/Runner_Dashboard")
    monkeypatch.setenv("PR_NUMBER", "2196")
    monkeypatch.setenv("GH_TOKEN", "test-token")
    monkeypatch.setattr(request, "urlopen", lambda _request, timeout: io.BytesIO(payload))
    exec(compile(classifier, "frontend-tests.yml:scope", "exec"), {})

    captured = capsys.readouterr()
    assert dict(line.split("=", 1) for line in captured.out.splitlines()) == expected
    assert captured.err.strip() == f"changed_file_count={len(changed_files)}"


@pytest.mark.parametrize(
    ("changed_files", "expected"),
    [
        (
            ["backend/routers/example.py"],
            {
                "run_frontend_tests": "false",
                "run_staff_e2e": "false",
                "run_api_contract": "true",
            },
        ),
        (
            ["docs/operations/readme.md"],
            {
                "run_frontend_tests": "false",
                "run_staff_e2e": "false",
                "run_api_contract": "false",
            },
        ),
        (
            ["frontend/src/App.tsx"],
            {
                "run_frontend_tests": "true",
                "run_staff_e2e": "true",
                "run_api_contract": "true",
            },
        ),
    ],
)
def test_api_scope_classifier_outputs(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    changed_files: list[str],
    expected: dict[str, str],
) -> None:
    _assert_api_scope_outputs(monkeypatch, capsys, changed_files, expected)


def test_committed_openapi_snapshot_and_generated_types_are_real() -> None:
    snapshot = json.loads((ROOT / "frontend/src/lib/openapi.json").read_text(encoding="utf-8"))
    types = (ROOT / "frontend/src/lib/api-types.ts").read_text(encoding="utf-8")

    assert "/" in snapshot["paths"]
    assert "/api/health" in snapshot["paths"]
    assert "/api/v1/metrics/summary" in snapshot["paths"]
    assert "/api/v1/metrics/trends" in snapshot["paths"]
    assert "/api/v1/metrics/pull" in snapshot["paths"]
    assert len(snapshot["paths"]) > 50
    assert "export interface paths" in types
    assert "export interface components" in types
    assert "Placeholder -- run `npm run generate:api`" not in types
