"""Portable handoffs exercise the public CLI without launching providers."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "session_handoff.py"


def run_cli(*args):
    return subprocess.run([sys.executable, str(SCRIPT), *map(str, args)], capture_output=True, text=True, check=False)


def context():
    return {
        "work_item": "example/project#12",
        "context": "Resume the Unicode café test.",
        "decisions": ["Use a bounded cache"],
        "evidence": [{"ref": "tests/test_cache.py", "summary": "3 targeted tests passed"}],
        "next_steps": ["Verify expiry"],
    }


@pytest.mark.parametrize("source,target", [("codex", "claude"), ("claude", "codex")])
def test_cross_provider_round_trip(tmp_path, source, target):
    original = tmp_path / "context.json"
    package = tmp_path / "handoff.json"
    imported = tmp_path / "session.json"
    original.write_text(json.dumps(context()), encoding="utf-8")
    exported = run_cli("export", "--source-provider", source, "--input", original, "--output", package)
    assert exported.returncode == 0, exported.stderr
    result = run_cli("import", "--provider", target, "--input", package, "--output", imported)
    assert result.returncode == 0, result.stderr
    session = json.loads(imported.read_text())
    assert session["provider"] == target
    assert session["handoff"] == json.loads(package.read_text())
    assert session["handoff"]["payload"] == context()
    assert json.loads(session["prompt"].split("\n", 1)[1]) == context()


@pytest.mark.parametrize(
    "invalid",
    [
        {"work_item": "x"},
        {**context(), "decisions": "not a list"},
        {**context(), "evidence": [{"ref": "x"}]},
        {**context(), "context": " "},
        {**context(), "session_id": "native-session"},
    ],
)
def test_invalid_context_fails_without_creating_output(tmp_path, invalid):
    source, destination = tmp_path / "input.json", tmp_path / "output.json"
    source.write_text(json.dumps(invalid))
    result = run_cli("export", "--source-provider", "codex", "--input", source, "--output", destination)
    assert result.returncode == 2
    assert "handoff:" in result.stderr
    assert not destination.exists()


@pytest.mark.parametrize(
    "payload", ["{broken", "[]", '{"version": 99}', "x" * 262145], ids=["malformed", "array", "version", "oversize"]
)
def test_invalid_package_fails_closed(tmp_path, payload):
    source, destination = tmp_path / "input.json", tmp_path / "output.json"
    source.write_text(payload)
    result = run_cli("import", "--provider", "claude", "--input", source, "--output", destination)
    assert result.returncode == 2
    assert "handoff:" in result.stderr
    assert not destination.exists()


def test_does_not_overwrite_existing_output_or_echo_input(tmp_path):
    source, destination = tmp_path / "input.json", tmp_path / "output.json"
    source.write_text(json.dumps(context()))
    destination.write_text("keep existing")
    result = run_cli("export", "--source-provider", "codex", "--input", source, "--output", destination)
    assert result.returncode == 2
    assert "handoff:" in result.stderr
    assert destination.read_text() == "keep existing"
    assert "café" not in result.stderr


def test_rejects_non_utf8_json(tmp_path):
    source, destination = tmp_path / "input.json", tmp_path / "output.json"
    source.write_bytes(json.dumps(context()).encode("utf-16"))
    result = run_cli("export", "--source-provider", "codex", "--input", source, "--output", destination)
    assert result.returncode == 2
    assert not destination.exists()
