"""Tests for the root-owned Python 3.12 selector (#1839).

The selector decides which interpreter may seed the root-owned scheduler venv. It
accepts only a root-owned, non-writable, exactly-3.12 interpreter and fails closed
otherwise; it never falls back to a user-owned runtime or to Python 3.14. All
filesystem facts come through an injected probe, so no test touches the real host.
"""

from __future__ import annotations

import importlib.util
import logging
import sys
from dataclasses import dataclass, field
from pathlib import Path

import pytest

SELECTOR_PATH = Path(__file__).resolve().parents[2] / "deploy" / "root_python_selector.py"
spec = importlib.util.spec_from_file_location("root_python_selector", SELECTOR_PATH)
assert spec is not None and spec.loader is not None
selector = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = selector
spec.loader.exec_module(selector)

pytestmark = pytest.mark.unit

ROOT_DIR = selector.PathFacts(uid=0, mode=0o755, kind="dir")
ROOT_EXE = selector.PathFacts(uid=0, mode=0o755, kind="file")
USER_UID = 1000


@dataclass
class FakeProbe:
    """In-memory stand-in for the host: facts, symlinks and interpreter versions."""

    facts: dict[str, object] = field(default_factory=dict)
    links: dict[str, str] = field(default_factory=dict)
    versions: dict[str, str] = field(default_factory=dict)
    executed: list[str] = field(default_factory=list)

    def realpath(self, path: str) -> str:
        return self.links.get(path, path)

    def lstat(self, path: str) -> object | None:
        if path in self.links:
            return selector.PathFacts(uid=0, mode=0o777, kind="symlink")
        return self.facts.get(path)

    def interpreter_version(self, path: str) -> str | None:
        self.executed.append(path)
        return self.versions.get(path)


def _root_tree(probe: FakeProbe, *dirs: str) -> None:
    for directory in ("/", *dirs):
        probe.facts[directory] = ROOT_DIR


def _system_python(probe: FakeProbe, path: str = "/usr/bin/python3.12", version: str = "3.12.3") -> None:
    _root_tree(probe, "/usr", "/usr/bin", "/usr/local", "/usr/local/bin")
    probe.facts[path] = ROOT_EXE
    probe.versions[path] = version


def test_root_owned_python312_is_accepted() -> None:
    probe = FakeProbe()
    _system_python(probe)

    chosen = selector.select_root_python(probe)

    assert chosen.path == "/usr/bin/python3.12"
    assert chosen.realpath == "/usr/bin/python3.12"
    assert chosen.owner_uid == 0
    assert chosen.version == "3.12.3"


def test_explicit_candidate_is_tried_before_search_order() -> None:
    probe = FakeProbe()
    _system_python(probe)
    _system_python(probe, "/usr/local/bin/python3.12", "3.12.9")

    chosen = selector.select_root_python(probe, explicit="/usr/local/bin/python3.12")

    assert chosen.path == "/usr/local/bin/python3.12"


def test_user_owned_runtime_is_rejected_with_reason() -> None:
    probe = FakeProbe()
    _root_tree(probe, "/home")
    probe.facts["/home/fleet-operator"] = selector.PathFacts(uid=USER_UID, mode=0o755, kind="dir")
    probe.facts["/home/fleet-operator/.local"] = selector.PathFacts(uid=USER_UID, mode=0o755, kind="dir")
    probe.facts["/home/fleet-operator/.local/bin"] = selector.PathFacts(uid=USER_UID, mode=0o755, kind="dir")
    exe = "/home/fleet-operator/.local/bin/python3.12"
    probe.facts[exe] = selector.PathFacts(uid=USER_UID, mode=0o755, kind="file")
    probe.versions[exe] = "3.12.14"

    with pytest.raises(selector.NoQualifiedPythonError) as excinfo:
        selector.select_root_python(probe, explicit=exe)

    assert "user-owned" in str(excinfo.value)
    assert exe in str(excinfo.value)
    assert exe not in probe.executed, "an untrusted interpreter must never be executed"


def test_group_writable_parent_directory_is_rejected() -> None:
    probe = FakeProbe()
    _system_python(probe)
    probe.facts["/usr/bin"] = selector.PathFacts(uid=0, mode=0o775, kind="dir")

    with pytest.raises(selector.NoQualifiedPythonError) as excinfo:
        selector.select_root_python(probe)

    assert "writable" in str(excinfo.value)
    assert probe.executed == []


def test_group_writable_interpreter_is_rejected() -> None:
    probe = FakeProbe()
    _system_python(probe)
    probe.facts["/usr/bin/python3.12"] = selector.PathFacts(uid=0, mode=0o775, kind="file")

    with pytest.raises(selector.NoQualifiedPythonError) as excinfo:
        selector.select_root_python(probe)

    assert "writable" in str(excinfo.value)


def test_symlink_into_home_is_rejected() -> None:
    probe = FakeProbe()
    _system_python(probe, "/usr/local/bin/python3.12")
    target = "/home/fleet-operator/.local/bin/python3.12"
    probe.links["/usr/local/bin/python3.12"] = target
    probe.facts[target] = ROOT_EXE  # even if the target looks root-owned, $HOME is denied
    probe.versions[target] = "3.12.14"

    with pytest.raises(selector.NoQualifiedPythonError) as excinfo:
        selector.select_root_python(probe, explicit="/usr/local/bin/python3.12")

    assert "escapes" in str(excinfo.value) or "user" in str(excinfo.value)
    assert probe.executed == []


def test_symlink_to_user_writable_location_outside_home_is_rejected() -> None:
    probe = FakeProbe()
    _system_python(probe, "/usr/local/bin/python3.12")
    probe.links["/usr/local/bin/python3.12"] = "/opt/pyenv/bin/python3.12"
    probe.facts["/opt"] = ROOT_DIR
    probe.facts["/opt/pyenv"] = selector.PathFacts(uid=USER_UID, mode=0o755, kind="dir")
    probe.facts["/opt/pyenv/bin"] = selector.PathFacts(uid=USER_UID, mode=0o755, kind="dir")
    probe.facts["/opt/pyenv/bin/python3.12"] = selector.PathFacts(uid=USER_UID, mode=0o755, kind="file")
    probe.versions["/opt/pyenv/bin/python3.12"] = "3.12.1"

    with pytest.raises(selector.NoQualifiedPythonError):
        selector.select_root_python(probe, explicit="/usr/local/bin/python3.12")

    assert probe.executed == []


def test_root_owned_symlink_to_root_owned_target_is_accepted() -> None:
    probe = FakeProbe()
    _system_python(probe, "/usr/bin/python3.12.3")
    probe.links["/usr/bin/python3.12"] = "/usr/bin/python3.12.3"

    chosen = selector.select_root_python(probe)

    assert chosen.path == "/usr/bin/python3.12"
    assert chosen.realpath == "/usr/bin/python3.12.3"


def test_python314_is_rejected_and_never_used_as_fallback() -> None:
    probe = FakeProbe()
    _system_python(probe, "/usr/bin/python3", "3.14.4")

    with pytest.raises(selector.NoQualifiedPythonError) as excinfo:
        selector.select_root_python(probe)

    assert "3.14.4" in str(excinfo.value)


def test_wrong_minor_in_the_python312_slot_is_rejected() -> None:
    probe = FakeProbe()
    _system_python(probe, "/usr/bin/python3.12", "3.13.1")

    with pytest.raises(selector.NoQualifiedPythonError):
        selector.select_root_python(probe)


def test_no_candidate_fails_closed_naming_the_operator_action() -> None:
    probe = FakeProbe()
    _root_tree(probe, "/usr", "/usr/bin", "/usr/local", "/usr/local/bin")

    with pytest.raises(selector.NoQualifiedPythonError) as excinfo:
        selector.select_root_python(probe)

    message = str(excinfo.value)
    assert "root-owned Python 3.12" in message
    assert "/usr/bin/python3.12" in message


def test_rejected_explicit_candidate_does_not_hide_later_valid_one() -> None:
    probe = FakeProbe()
    _system_python(probe)
    probe.facts["/home/u/python3.12"] = selector.PathFacts(uid=USER_UID, mode=0o755, kind="file")

    chosen = selector.select_root_python(probe, explicit="/home/u/python3.12")

    assert chosen.path == "/usr/bin/python3.12"


def test_relative_explicit_path_is_a_precondition_violation() -> None:
    with pytest.raises(ValueError, match="absolute"):
        selector.select_root_python(FakeProbe(), explicit="python3.12")


def test_selection_is_logged_with_realpath_owner_and_version(caplog: pytest.LogCaptureFixture) -> None:
    probe = FakeProbe()
    _system_python(probe)

    with caplog.at_level(logging.INFO, logger="root_python_selector"):
        selector.select_root_python(probe)

    line = " ".join(record.getMessage() for record in caplog.records)
    assert "/usr/bin/python3.12" in line
    assert "uid=0" in line
    assert "3.12.3" in line
