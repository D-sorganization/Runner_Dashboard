import json
from pathlib import Path

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


def test_backend_only_prs_run_the_generated_api_contract_check() -> None:
    """A backend route or model change alters the snapshot, so it must trigger the check (#1819)."""
    workflow = (ROOT / ".github/workflows/frontend-tests.yml").read_text(encoding="utf-8")
    scope = workflow[workflow.index("  frontend-scope:") : workflow.index("\n  typecheck:")]
    typecheck = workflow[workflow.index("\n  typecheck:") :]
    typecheck = typecheck[: typecheck.index("npm run generate-api:check")]

    assert "run_api_contract: ${{ steps.scope.outputs.run_api_contract }}" in scope
    assert 'echo "run_api_contract=true" >> "$GITHUB_OUTPUT"' in scope
    assert 'run_api_contract = run_frontend or any(path.startswith("backend/") for path in files)' in scope
    assert "run_api_contract={" in scope
    assert "needs.frontend-scope.outputs.run_api_contract == 'true'" in typecheck


def test_committed_openapi_snapshot_and_generated_types_are_real() -> None:
    snapshot = json.loads((ROOT / "frontend/src/lib/openapi.json").read_text(encoding="utf-8"))
    types = (ROOT / "frontend/src/lib/api-types.ts").read_text(encoding="utf-8")

    assert "/" in snapshot["paths"]
    assert "/api/health" in snapshot["paths"]
    assert len(snapshot["paths"]) > 50
    assert "export interface paths" in types
    assert "export interface components" in types
    assert "Placeholder -- run `npm run generate:api`" not in types
