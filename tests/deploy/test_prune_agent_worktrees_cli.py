"""The worktree-hygiene CLI must run on the hosts' system python3.10 and survive a slow checkout.

``deploy/prune_agent_worktrees.py`` is started by a systemd unit whose interpreter falls back to
``python3`` (3.10 on the fleet hosts), outside the dashboard virtualenv. Everything it imports must
therefore stay stdlib-only and free of 3.11+ constructs, even though the backend targets 3.11.
"""

from __future__ import annotations

import ast
import importlib.util
import json
import logging
import shutil
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import agent_worktree_hygiene as hygiene
import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_BACKEND = _REPO_ROOT / "backend"
_SCRIPT = _REPO_ROOT / "deploy" / "prune_agent_worktrees.py"

_PY311_ONLY_IMPORTS = {
    "datetime": {"UTC"},
    "enum": {"StrEnum"},
    "typing": {"Self", "LiteralString", "Never", "assert_never", "reveal_type", "TypeVarTuple", "Unpack"},
}
_PY311_ONLY_MODULES = {"tomllib"}
_PY311_ONLY_NAMES = {"ExceptionGroup", "BaseExceptionGroup"}


def _module_files(name: str) -> list[Path]:
    """Backend files executed by ``import name`` (each parent package, then the module); [] if third-party."""
    files: list[Path] = []
    parts = name.split(".")
    for depth in range(1, len(parts) + 1):
        base = _BACKEND.joinpath(*parts[:depth])
        if (base / "__init__.py").is_file():
            files.append(base / "__init__.py")
        elif base.with_suffix(".py").is_file():
            files.append(base.with_suffix(".py"))
        else:
            return []
    return files


def _imported_names(tree: ast.AST) -> set[str]:
    """Absolute module names a file may import, including function-level (lazy) imports."""
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            assert node.level == 0, "the CLI import closure uses absolute imports only"
            names.add(node.module)
            names.update(f"{node.module}.{alias.name}" for alias in node.names)
    return names


def _first_party_closure(entry: Path) -> set[Path]:
    seen: set[Path] = set()
    pending = [entry]
    while pending:
        path = pending.pop()
        if path in seen:
            continue
        seen.add(path)
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for name in _imported_names(tree):
            pending.extend(_module_files(name))
    return seen


def _py311_only_constructs(path: Path) -> list[str]:
    found: list[str] = []
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.ImportFrom):
            banned = _PY311_ONLY_IMPORTS.get(node.module or "", set())
            found += [f"from {node.module} import {a.name}" for a in node.names if a.name in banned]
            if node.module in _PY311_ONLY_MODULES:
                found.append(f"from {node.module} import ...")
        elif isinstance(node, ast.Import):
            found += [f"import {a.name}" for a in node.names if a.name in _PY311_ONLY_MODULES]
        elif isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
            if node.attr in _PY311_ONLY_IMPORTS.get(node.value.id, set()):
                found.append(f"{node.value.id}.{node.attr}")
        elif isinstance(node, ast.Name) and node.id in _PY311_ONLY_NAMES:
            found.append(node.id)
        elif isinstance(node, ast.TryStar):
            found.append("except*")
    return [f"{path.relative_to(_REPO_ROOT).as_posix()}: {item}" for item in found]


def test_cli_import_closure_has_no_python311_only_constructs() -> None:
    closure = _first_party_closure(_SCRIPT)
    assert _BACKEND / "agent_worktree_hygiene.py" in closure
    offenders = sorted(item for path in closure for item in _py311_only_constructs(path))
    assert offenders == []


def test_cli_import_closure_stays_small() -> None:
    """Guards the root cause: the CLI once pulled in the whole staff engine (fastapi, ``datetime.UTC``)."""
    closure = {p.relative_to(_REPO_ROOT).as_posix() for p in _first_party_closure(_SCRIPT)}
    assert closure == {
        "deploy/prune_agent_worktrees.py",
        "backend/agent_worktree_hygiene.py",
        "backend/staff/__init__.py",
        "backend/staff/repo_roots.py",
        "backend/platform_utils/__init__.py",
        "backend/platform_utils/wsl_paths.py",
    }


def _python310() -> list[str] | None:
    for candidate in (["python3.10"], ["py", "-3.10"]):
        exe = shutil.which(candidate[0])
        if exe is None:
            continue
        probe = subprocess.run(  # noqa: S603
            [exe, *candidate[1:], "-c", "import sys; print(sys.version_info[:2])"],
            check=False,
            capture_output=True,
            text=True,
            timeout=60,
        )
        if probe.returncode == 0 and probe.stdout.strip() == "(3, 10)":
            return [exe, *candidate[1:]]
    return None


def test_cli_imports_under_real_python310() -> None:
    python310 = _python310()
    if python310 is None:
        pytest.skip("no python3.10 interpreter available")
    # ``--help`` runs every module-level import and exits before any checkout is scanned.
    res = subprocess.run(  # noqa: S603
        [*python310, "-S", str(_SCRIPT), "--help"],
        check=False,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert res.returncode == 0, res.stderr
    assert "--apply" in res.stdout


@pytest.fixture
def cli() -> ModuleType:
    spec = importlib.util.spec_from_file_location("prune_agent_worktrees_cli", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _checkout(root: Path, name: str) -> Path:
    checkout = root / name
    (checkout / ".git").mkdir(parents=True)
    return checkout


def _completed(stdout: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(args=["git"], returncode=0, stdout=stdout, stderr="")


@pytest.fixture
def two_checkouts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, object]:
    """A slow checkout whose fetch times out, then a healthy one; every git call is faked."""
    slow = _checkout(tmp_path, "Slow_Repo")
    healthy = _checkout(tmp_path, "Healthy_Repo")
    listed: list[Path] = []
    removed: list[Path] = []

    def fake_git(cwd: Path, *args: str, timeout: int = 120) -> subprocess.CompletedProcess[str]:
        if args[:2] == ("remote", "get-url"):
            return _completed("https://example.invalid/org/repo.git\n")
        if args[0] == "fetch" and cwd == slow:
            raise subprocess.TimeoutExpired(cmd=["git", "-C", str(cwd), *args], timeout=timeout)
        return _completed()

    def fake_list(checkout: Path) -> list[hygiene.WorktreeEntry]:
        listed.append(checkout)
        return [hygiene.WorktreeEntry(path=tmp_path / f"{checkout.name}-wt", branch="merged/slice")]

    monkeypatch.setattr(hygiene, "_git", fake_git)
    monkeypatch.setattr(hygiene, "_parse_worktree_list", fake_list)
    monkeypatch.setattr(hygiene, "worktree_entry_is_eligible", lambda checkout, entry, **kw: (True, "eligible"))
    monkeypatch.setattr(hygiene, "_git_worktree_remove", lambda checkout, path: removed.append(path) or True)
    return {"slow": slow, "healthy": healthy, "listed": listed, "removed": removed}


def test_fetch_timeout_skips_that_checkout_and_continues(
    two_checkouts: dict[str, object], tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    slow, healthy = two_checkouts["slow"], two_checkouts["healthy"]
    with caplog.at_level(logging.WARNING, logger="dashboard.agent_worktree_hygiene"):
        report = hygiene.plan_agent_worktree_prune([slow, healthy], apply=True, min_age_hours=72)

    assert report["skipped_checkouts"] == [
        {"repo": "Slow_Repo", "path": str(slow), "reason": "git_timeout", "command": "fetch"}
    ]
    # Stale refs must never drive a prune: the timed-out checkout is not even listed.
    assert two_checkouts["listed"] == [healthy]
    assert two_checkouts["removed"] == [tmp_path / "Healthy_Repo-wt"]
    assert report["removed"] == 1
    assert [item["repo"] for item in report["would_remove"]] == ["Healthy_Repo"]
    warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1 and "Slow_Repo" in warnings[0] and "fetch" in warnings[0]


def _timeout(cwd: Path, *args: str) -> subprocess.TimeoutExpired:
    return subprocess.TimeoutExpired(cmd=["git", "-C", str(cwd), *args], timeout=60)


def test_status_timeout_skips_that_worktree_and_continues(
    two_checkouts: dict[str, object], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A slow ``git status`` in one worktree must not abort the run or hide the other worktrees."""
    healthy = two_checkouts["healthy"]
    slow_wt, fine_wt = tmp_path / "slow-wt", tmp_path / "fine-wt"
    entries = [hygiene.WorktreeEntry(path=p, branch="merged/slice") for p in (slow_wt, fine_wt)]
    monkeypatch.setattr(hygiene, "_parse_worktree_list", lambda checkout: entries)

    def eligibility(checkout: Path, entry: hygiene.WorktreeEntry, **kw: object) -> tuple[bool, str]:
        if entry.path == slow_wt:
            raise _timeout(entry.path, "status", "--porcelain")
        return True, "eligible"

    monkeypatch.setattr(hygiene, "worktree_entry_is_eligible", eligibility)
    report = hygiene.plan_agent_worktree_prune([healthy], apply=True)

    assert report["skipped"] == [
        {"repo": "Healthy_Repo", "path": str(slow_wt), "branch": "merged/slice", "reason": "git_timeout:status"}
    ]
    assert two_checkouts["removed"] == [fine_wt] and report["removed"] == 1
    assert report["skipped_checkouts"] == []


def test_remove_timeout_is_reported_and_the_run_continues(
    two_checkouts: dict[str, object], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    healthy = two_checkouts["healthy"]
    stuck_wt, fine_wt = tmp_path / "stuck-wt", tmp_path / "fine-wt"
    entries = [hygiene.WorktreeEntry(path=p, branch="merged/slice") for p in (stuck_wt, fine_wt)]
    monkeypatch.setattr(hygiene, "_parse_worktree_list", lambda checkout: entries)

    def remove(checkout: Path, path: Path) -> bool:
        if path == stuck_wt:
            raise _timeout(checkout, "worktree", "remove", str(path))
        return True

    monkeypatch.setattr(hygiene, "_git_worktree_remove", remove)
    report = hygiene.plan_agent_worktree_prune([healthy], apply=True)

    assert report["removed"] == 1
    assert [item["path"] for item in report["would_remove"]] == [str(stuck_wt), str(fine_wt)]
    assert report["skipped"] == [
        {"repo": "Healthy_Repo", "path": str(stuck_wt), "branch": "merged/slice", "reason": "git_timeout:worktree"}
    ]


def test_other_git_failures_are_not_swallowed(
    two_checkouts: dict[str, object], monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken_list(checkout: Path) -> list[hygiene.WorktreeEntry]:
        raise OSError("git executable vanished")

    monkeypatch.setattr(hygiene, "_parse_worktree_list", broken_list)
    with pytest.raises(OSError, match="vanished"):
        hygiene.plan_agent_worktree_prune([two_checkouts["healthy"]], apply=False)


def test_report_lists_no_skipped_checkouts_on_a_clean_run(two_checkouts: dict[str, object]) -> None:
    report = hygiene.plan_agent_worktree_prune([two_checkouts["healthy"]], apply=False)
    assert report["skipped_checkouts"] == []


def test_cli_exits_zero_and_reports_a_skipped_checkout(
    cli: ModuleType,
    two_checkouts: dict[str, object],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A slow checkout is a warning, not a failure: the daily oneshot unit must not go red for it."""
    monkeypatch.delenv("AGENT_WORKTREE_HYGIENE_APPLY", raising=False)
    monkeypatch.setattr(cli, "discover_repo_checkouts", lambda extra: [two_checkouts["slow"], two_checkouts["healthy"]])

    assert cli.main([]) == 0
    assert not hasattr(cli, "EXIT_PARTIAL")

    report = json.loads(capsys.readouterr().out)
    assert [item["repo"] for item in report["skipped_checkouts"]] == ["Slow_Repo"]
    assert report["dry_run"] is True and two_checkouts["removed"] == []


def test_cli_exits_zero_when_every_checkout_was_scanned(
    cli: ModuleType, two_checkouts: dict[str, object], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("AGENT_WORKTREE_HYGIENE_APPLY", raising=False)
    monkeypatch.setattr(cli, "discover_repo_checkouts", lambda extra: [two_checkouts["healthy"]])
    assert cli.main([]) == 0


def test_discovery_does_not_load_the_staff_engine() -> None:
    code = (
        "import sys; sys.path.insert(0, sys.argv[1]); import agent_worktree_hygiene; "
        "print(sorted(m for m in sys.modules if m.startswith('staff')))"
    )
    res = subprocess.run(  # noqa: S603
        [sys.executable, "-c", code, str(_BACKEND)], check=True, capture_output=True, text=True, timeout=120
    )
    assert res.stdout.strip() == "['staff', 'staff.repo_roots']"


def test_discovery_uses_only_existing_roots(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Discovery reads the configured and built-in roots, and never a root that does not exist."""
    import staff.repo_roots as repo_roots  # noqa: PLC0415

    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    monkeypatch.setattr(repo_roots, "windows_repositories_root", lambda: tmp_path / "no-windows-profile")
    monkeypatch.delenv("STAFF_REPOS_ROOT", raising=False)
    assert hygiene.discover_repo_checkouts() == []

    configured = tmp_path / "configured"
    configured.mkdir()
    monkeypatch.setenv("STAFF_REPOS_ROOT", str(configured))
    checkout = _checkout(configured, "Some_Repo")
    assert hygiene.discover_repo_checkouts() == [checkout]
