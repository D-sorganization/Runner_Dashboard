import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_api_generation_has_single_canonical_script_and_output() -> None:
    package = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))
    scripts = package["scripts"]

    assert scripts["generate-api"] == "bash scripts/gen-api-client.sh"
    assert scripts["generate-api:check"] == "bash scripts/gen-api-client.sh --check"
    assert "generate:api" not in scripts
    assert not (ROOT / "frontend/src/types/api.d.ts").exists()


def test_frontend_ci_checks_generated_api_contract() -> None:
    workflow = (ROOT / ".github/workflows/frontend-tests.yml").read_text(encoding="utf-8")

    assert "npm run generate-api:check" in workflow
    assert workflow.index("npm run generate-api:check") < workflow.index("npm run typecheck")


def test_committed_openapi_snapshot_and_generated_types_are_real() -> None:
    snapshot = json.loads((ROOT / "frontend/src/lib/openapi.json").read_text(encoding="utf-8"))
    types = (ROOT / "frontend/src/lib/api-types.ts").read_text(encoding="utf-8")

    assert "/" in snapshot["paths"]
    assert "/api/health" in snapshot["paths"]
    assert len(snapshot["paths"]) > 50
    assert "export interface paths" in types
    assert "export interface components" in types
    assert "Placeholder -- run `npm run generate:api`" not in types
