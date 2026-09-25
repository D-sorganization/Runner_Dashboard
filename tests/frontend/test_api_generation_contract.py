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


def test_committed_openapi_snapshot_and_generated_types_are_real() -> None:
    snapshot = json.loads((ROOT / "frontend/src/lib/openapi.json").read_text(encoding="utf-8"))
    types = (ROOT / "frontend/src/lib/api-types.ts").read_text(encoding="utf-8")

    assert "/" in snapshot["paths"]
    assert "/api/health" in snapshot["paths"]
    assert len(snapshot["paths"]) > 50
    assert "export interface paths" in types
    assert "export interface components" in types
    assert "Placeholder -- run `npm run generate:api`" not in types


def test_staff_routes_have_pydantic_response_models_in_openapi() -> None:
    snapshot = json.loads((ROOT / "frontend/src/lib/openapi.json").read_text(encoding="utf-8"))
    paths = snapshot.get("paths", {})

    staff_routes = [p for p in paths if p.startswith("/api/staff") or p.startswith("/api/v1/staff")]
    assert len(staff_routes) >= 6

    for path in staff_routes:
        methods = paths[path]
        for method, op in methods.items():
            if method.lower() not in {"get", "post", "put", "delete"}:
                continue
            if path.endswith("/stream"):
                continue  # SSE StreamingResponse
            responses = op.get("responses", {})
            assert "200" in responses or "201" in responses, f"Route {method.upper()} {path} missing 200/201 response"
            resp_200 = responses.get("200") or responses.get("201")
            content = resp_200.get("content", {})
            assert "application/json" in content, (
                f"Route {method.upper()} {path} has no application/json response schema"
            )
            schema = content["application/json"].get("schema", {})
            assert "$ref" in schema or schema.get("type") in {"object", "array"}, (
                f"Route {method.upper()} {path} has unreferenced or empty schema: {schema}"
            )


def test_staff_frontend_types_derived_from_generated_openapi() -> None:
    staff_api_ts = (ROOT / "frontend/src/pages/Staff/staffApi.ts").read_text(encoding="utf-8")
    has_import = (
        'import type { components } from "../../lib/api-types"' in staff_api_ts
        or 'import type { components } from "@/lib/api-types"' in staff_api_ts
    )
    assert has_import, "staffApi.ts must import components from generated api-types"
    assert 'components["schemas"]' in staff_api_ts


def test_deliberate_contract_drift_fails_check(tmp_path: Path) -> None:
    """Deliberate drift fixture (issue #1296): mutating snapshot must cause drift check to fail."""
    import subprocess

    snapshot_path = ROOT / "frontend/src/lib/openapi.json"
    original_bytes = snapshot_path.read_bytes()
    try:
        # Introduce deliberate drift by mutating snapshot
        drifted = json.loads(original_bytes.decode("utf-8"))
        drifted["info"]["description"] = "DELIBERATE_TEST_DRIFT_CONTRACT_MISMATCH"
        # Write bytes preserving original newline style
        newline = b"\r\n" if b"\r\n" in original_bytes else b"\n"
        drifted_text = json.dumps(drifted, indent=2)
        if newline == b"\r\n":
            drifted_text = drifted_text.replace("\n", "\r\n")
        snapshot_path.write_bytes(drifted_text.encode("utf-8"))

        # Run bash scripts/gen-api-client.sh --check
        res = subprocess.run(
            ["bash", "scripts/gen-api-client.sh", "--check"],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
        )
        assert res.returncode != 0, "Expected --check to fail when contract drifts, but it succeeded!"
    finally:
        snapshot_path.write_bytes(original_bytes)
