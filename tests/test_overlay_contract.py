"""Contract tests for the core/private-overlay contract (issue #3079)."""

from __future__ import annotations

import ast
import copy
import json
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
_BACKEND = _ROOT / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

import overlay_contract as oc  # noqa: E402

MOCK = _ROOT / "tests" / "fixtures" / "overlay" / "mock-overlay.json"
SENTINEL = "SENTINEL-private-value-do-not-echo"


def _mock() -> dict:
    return json.loads(MOCK.read_text(encoding="utf-8"))


def _write(tmp_path: Path, data: object) -> Path:
    path = tmp_path / f"{SENTINEL}-overlay.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def _assert_no_echo(exc: pytest.ExceptionInfo[oc.OverlayError]) -> None:
    assert SENTINEL not in str(exc.value)


def test_mock_overlay_is_valid() -> None:
    overlay = oc.load_overlay(MOCK)
    assert overlay.schema_version == oc.OVERLAY_SCHEMA_VERSION
    assert overlay.host_names() == ("mock-hub", "mock-node")
    assert all(h.address.endswith(".example.invalid") for h in overlay.hosts)


def test_unset_path_is_backward_compatible() -> None:
    assert oc.get_overlay({}) is None
    oc.assert_overlay_ready({})  # no-op: behaviour unchanged until cutover


def test_required_flag_without_path_refuses_to_start() -> None:
    with pytest.raises(oc.OverlayError, match=oc.OVERLAY_PATH_ENV):
        oc.assert_overlay_ready({oc.OVERLAY_REQUIRED_ENV: "1"})


def test_env_path_loads_through_accessor() -> None:
    overlay = oc.get_overlay({oc.OVERLAY_PATH_ENV: str(MOCK)})
    assert overlay is not None and overlay.roles_dir == "roles"


def test_missing_file_fails_closed_without_echoing_path(tmp_path: Path) -> None:
    env = {oc.OVERLAY_PATH_ENV: str(tmp_path / f"{SENTINEL}.json")}
    with pytest.raises(oc.OverlayError, match="does not exist") as exc:
        oc.assert_overlay_ready(env)
    _assert_no_echo(exc)


def test_invalid_json_fails_closed(tmp_path: Path) -> None:
    path = tmp_path / "bad.json"
    path.write_text("{" + SENTINEL, encoding="utf-8")
    with pytest.raises(oc.OverlayError, match="not valid JSON") as exc:
        oc.load_overlay(path)
    _assert_no_echo(exc)


@pytest.mark.parametrize("version", [0, 2, "1", True, None])
def test_wrong_schema_version_fails_closed(tmp_path: Path, version: object) -> None:
    data = _mock()
    data["schema_version"] = version
    with pytest.raises(oc.OverlayError, match="schema_version"):
        oc.load_overlay(_write(tmp_path, data))


@pytest.mark.parametrize("key", sorted(set(oc._TOP_LEVEL_KEYS) - {"schema_version"}))
def test_missing_key_fails_closed(tmp_path: Path, key: str) -> None:
    data = _mock()
    del data[key]
    with pytest.raises(oc.OverlayError, match=f"missing required key.*{key}"):
        oc.load_overlay(_write(tmp_path, data))


def test_unknown_key_fails_closed_and_names_key_only(tmp_path: Path) -> None:
    data = _mock()
    data["extra"] = SENTINEL
    with pytest.raises(oc.OverlayError, match="unknown key.*extra") as exc:
        oc.load_overlay(_write(tmp_path, data))
    _assert_no_echo(exc)


@pytest.mark.parametrize(
    ("mutate", "match"),
    [
        (lambda d: d.update(roles_dir=7), "roles_dir"),
        (lambda d: d.update(roles_dir=""), "non-empty"),
        (lambda d: d.update(hosts={}), "hosts"),
        (lambda d: d["hosts"].append(SENTINEL), r"hosts\[2\]"),
        (lambda d: d["hosts"][0].update(address=1), "address"),
        (lambda d: d["hosts"][0].update(port=SENTINEL), "unknown key.*port"),
        (lambda d: d["hosts"].append(copy.deepcopy(d["hosts"][0])), "duplicate"),
        (lambda d: d["agent_token_paths"].update(x=""), "agent_token_paths"),
    ],
)
def test_invalid_values_fail_closed_without_echo(tmp_path: Path, mutate, match: str) -> None:
    data = _mock()
    mutate(data)
    with pytest.raises(oc.OverlayError, match=match) as exc:
        oc.load_overlay(_write(tmp_path, data))
    _assert_no_echo(exc)


def test_top_level_must_be_object() -> None:
    with pytest.raises(oc.OverlayError, match="JSON object"):
        oc.parse_overlay([SENTINEL])


def test_overlay_is_immutable() -> None:
    overlay = oc.load_overlay(MOCK)
    with pytest.raises(AttributeError):
        overlay.roles_dir = "x"  # type: ignore[misc]


def test_only_the_accessor_reads_the_overlay() -> None:
    """LoD/DRY: no other core module names the overlay env vars or mock values."""
    mock_values = {h["address"] for h in _mock()["hosts"]} | set(_mock()["agent_token_paths"].values())
    offenders = []
    for path in _BACKEND.rglob("*.py"):
        if path.name in {"overlay_contract.py", "private_mode.py"}:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        if oc.OVERLAY_PATH_ENV in text or oc.OVERLAY_REQUIRED_ENV in text:
            offenders.append(f"{path.relative_to(_ROOT)}: reads overlay env directly")
        offenders += [f"{path.relative_to(_ROOT)}: hard-codes an overlay value" for v in mock_values if v in text]
    assert offenders == []


def test_server_startup_runs_the_overlay_gate() -> None:
    tree = ast.parse((_BACKEND / "server.py").read_text(encoding="utf-8"))
    startup = next(n for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef) and n.name == "_startup")
    calls = {n.func.id for n in ast.walk(startup) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
    assert "assert_overlay_ready" in calls
