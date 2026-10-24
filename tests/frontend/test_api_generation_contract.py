import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

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


def test_api_generation_does_not_append_handwritten_compat_types() -> None:
    generator = (ROOT / "scripts/gen-api-client.sh").read_text(encoding="utf-8")
    assert "Client compatibility aliases" not in generator and 'cat >> "$TMP_TYPES"' not in generator
    assert "Client compatibility aliases" not in (ROOT / "frontend/src/lib/api-types.ts").read_text(
        encoding="utf-8"
    )


def test_frontend_ci_checks_generated_api_contract() -> None:
    workflow = (ROOT / ".github/workflows/frontend-tests.yml").read_text(encoding="utf-8")

    assert "npm run generate-api:check" in workflow
    assert workflow.index("npm run generate-api:check") < workflow.index("npm run typecheck")


def _api_scope_classifier() -> str:
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
    return "\n".join(lines[heredoc_start + 1 : heredoc_end])


def _run_api_scope_classifier(classifier: str, changed_files: list[str]) -> subprocess.CompletedProcess[str]:
    with tempfile.TemporaryDirectory() as temp_dir:
        temp_root = Path(temp_dir)
        files_path = temp_root / "changed_files.json"
        files_path.write_text(
            json.dumps([{"filename": path} for path in changed_files]),
            encoding="utf-8",
        )
        script_path = temp_root / "scope_classifier.py"
        script_path.write_text(
            "import io, json, urllib.request\n"
            f"with open({str(files_path)!r}, encoding='utf-8') as source:\n"
            "    payload = json.load(source)\n"
            "def fake_urlopen(request, timeout):\n"
            "    assert request.full_url == "
            "'https://api.github.com/repos/D-sorganization/Runner_Dashboard/pulls/2196/files?per_page=100'\n"
            "    assert request.get_method() == 'GET'\n"
            "    return io.BytesIO(json.dumps(payload).encode('utf-8'))\n"
            "urllib.request.urlopen = fake_urlopen\n\n"
            f"{classifier}",
            encoding="utf-8",
        )
        child_env = {
            key: value for key, value in os.environ.items() if key in {"PATH", "SYSTEMROOT", "TEMP", "TMP", "WINDIR"}
        }
        child_env.update(
            {
                "GH_TOKEN": "test-token",
                "PR_NUMBER": "2196",
                "REPOSITORY": "D-sorganization/Runner_Dashboard",
            }
        )
        return subprocess.run(
            [sys.executable, "-I", str(script_path)],
            cwd=ROOT,
            env=child_env,
            capture_output=True,
            check=False,
            text=True,
            timeout=10,
        )


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
    changed_files: list[str],
    expected: dict[str, str],
) -> None:
    result = _run_api_scope_classifier(_api_scope_classifier(), changed_files)
    assert result.returncode == 0, result.stderr
    assert dict(line.split("=", 1) for line in result.stdout.splitlines()) == expected
    assert result.stderr.strip() == f"changed_file_count={len(changed_files)}"


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
