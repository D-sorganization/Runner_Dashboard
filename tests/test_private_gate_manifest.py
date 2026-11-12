from __future__ import annotations

from pathlib import Path

import pytest

from backend.ci.private_gate_manifest import EXPECTED_IDS, load_manifest, run_gates


def test_manifest_has_pinned_versions_and_cost_order() -> None:
    gates = load_manifest()

    assert tuple(gate["id"] for gate in gates) == EXPECTED_IDS
    assert all(gate["version"] for gate in gates)
    assert all(gate["command"] for gate in gates)


def test_github_workflow_validates_the_shared_manifest() -> None:
    root = Path(__file__).resolve().parents[1]
    workflow = (root / ".github/workflows/ci-standard.yml").read_text(encoding="utf-8")

    assert "python -m backend.ci.private_gate_manifest --validate" in workflow
    secrets_workflow = (root / ".github/workflows/ci-secrets.yml").read_text(encoding="utf-8")
    assert "-m backend.ci.private_gate_manifest --validate" in secrets_workflow
    assert "feature_flag: private_ci" in (root / "ci/private-gates.yml").read_text(encoding="utf-8")


def test_manifest_rejects_changed_gate_order(tmp_path: Path) -> None:
    bad = tmp_path / "gates.yml"
    bad.write_text(
        "schema_version: 1\nfeature_flag: private_ci\ngates:\n"
        "  - {id: tests, tool: pytest, version: '8.1.1', command: [pytest]}\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="invalid ids or ordering"):
        load_manifest(bad)


def test_runner_stops_after_first_failed_gate() -> None:
    gates = [{"id": gate_id, "command": [gate_id]} for gate_id in ("lint", "types", "tests")]
    seen: list[str] = []

    def runner(command: list[str]) -> int:
        seen.append(command[0])
        return 1 if command[0] == "types" else 0

    assert run_gates(gates, runner=runner) == 1
    assert seen == ["lint", "types"]


def test_flag_off_does_not_run_manifest_commands(monkeypatch: pytest.MonkeyPatch) -> None:
    from backend.ci import private_gate_manifest

    monkeypatch.delenv("PRIVATE_CI", raising=False)
    monkeypatch.setattr(private_gate_manifest, "run_gates", lambda _gates: pytest.fail("ran"))

    assert private_gate_manifest.main(["--run"]) == 0
