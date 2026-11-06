"""Tests for post_agent_lease subprocess fleet-App identity resolution (#4083)."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

BOT_STATUS = (
    "github.com\n"
    "  ✓ Logged in to github.com account d-sorggeneralagent[bot] (GH_TOKEN)\n"
    "  - Active account: true\n"
    "  ✓ Logged in to github.com account dieterolson (hosts.yml)\n"
    "  - Active account: false\n"
)
PERSONAL_STATUS = "github.com\n  ✓ Logged in to github.com account dieterolson (hosts.yml)\n  - Active account: true\n"
FORBIDDEN_403 = "gh: Resource not accessible by integration (HTTP 403)"


class FakeExec:
    """Mock gh executor recording calls."""

    def __init__(
        self,
        status: str = BOT_STATUS,
        user_rc: int = 1,
        user_out: str = FORBIDDEN_403,
        mutate_rc: int = 0,
        mutate_out: str = "https://github.com/D-sorganization/AffineDrift/issues/5175#issuecomment-1",
    ) -> None:
        self.status = status
        self.user_rc = user_rc
        self.user_out = user_out
        self.mutate_rc = mutate_rc
        self.mutate_out = mutate_out
        self.calls: list[tuple[list[str], dict[str, str]]] = []

    def __call__(self, cmd: list[str], env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
        cmd_list = list(cmd)
        self.calls.append((cmd_list, dict(env or {})))
        args = cmd_list[1:]
        if len(args) >= 2 and args[0] == "auth" and args[1] == "status":
            return subprocess.CompletedProcess(cmd_list, 0, self.status, "")
        if len(args) >= 2 and args[0] == "api" and args[1] == "user":
            if self.user_rc == 0:
                return subprocess.CompletedProcess(cmd_list, 0, self.user_out, "")
            return subprocess.CompletedProcess(cmd_list, self.user_rc, "", self.user_out)
        # Any other command (e.g. issue comment / issue edit / label)
        if self.mutate_rc == 0:
            return subprocess.CompletedProcess(cmd_list, 0, self.mutate_out, "")
        return subprocess.CompletedProcess(cmd_list, self.mutate_rc, "", self.mutate_out)

    @property
    def mutations(self) -> list[list[str]]:
        return [c for c, _ in self.calls if len(c) >= 2 and c[1:3] not in (["auth", "status"], ["api", "user"])]


def test_post_agent_lease_refuses_when_only_personal_account_present(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """When /usr/bin/gh is logged in as dieterolson, post_agent_lease must refuse and exit 3 with zero writes."""
    from scripts import post_agent_lease as pal

    usr_bin_gh = tmp_path / "usr_bin" / "gh"
    usr_bin_gh.parent.mkdir(parents=True)
    usr_bin_gh.touch(mode=0o755)

    fake = FakeExec(status=PERSONAL_STATUS, user_rc=0, user_out="dieterolson\n")
    monkeypatch.setattr(pal, "_exec_gh", fake)
    monkeypatch.setenv("PATH", str(usr_bin_gh.parent))

    rc = pal.main(
        [
            "--repo",
            "AffineDrift",
            "--issue",
            "5175",
            "--agent",
            "antigravity",
            "--session",
            "agy-test-session",
        ]
    )

    assert rc == pal.EXIT_IDENTITY_REFUSED  # Must exit non-zero (3)
    assert len(fake.mutations) == 0  # Zero writes/mutations

    out = capsys.readouterr().out
    payload = json.loads(out.strip())
    assert payload.get("ok") is False
    assert payload.get("identity_refused") is True


def test_post_agent_lease_prefers_fleet_wrapper_over_personal_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """When ~/.local/bin/gh or FLEET_GH_BIN is present alongside /usr/bin/gh, it resolves the fleet wrapper."""
    from scripts import post_agent_lease as pal

    local_bin = tmp_path / ".local" / "bin"
    local_bin.mkdir(parents=True)
    fleet_gh = local_bin / "gh"
    fleet_gh.touch(mode=0o755)

    usr_bin = tmp_path / "usr_bin"
    usr_bin.mkdir(parents=True)
    personal_gh = usr_bin / "gh"
    personal_gh.touch(mode=0o755)

    fake = FakeExec(status=BOT_STATUS, user_rc=1, user_out=FORBIDDEN_403)
    monkeypatch.setattr(pal, "_exec_gh", fake)
    monkeypatch.setenv("PATH", f"{usr_bin}:{local_bin}")
    monkeypatch.setenv("FLEET_GH_BIN", str(fleet_gh))

    resolved = pal.resolve_fleet_gh(env={"PATH": f"{usr_bin}:{local_bin}", "FLEET_GH_BIN": str(fleet_gh)})
    assert resolved == str(fleet_gh)

    rc = pal.main(
        [
            "--repo",
            "AffineDrift",
            "--issue",
            "5175",
            "--agent",
            "antigravity",
            "--session",
            "agy-test-session",
        ]
    )
    assert rc == 0
    out = capsys.readouterr().out
    payload = json.loads(out.strip())
    assert payload.get("ok") is True
    assert len(fake.mutations) > 0


def test_rm_env_resolves_local_bin_and_fleet_gh_bin(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """backend.coordination.rm_scripts._rm_env resolves FLEET_GH_BIN and ~/.local/bin/gh."""
    from coordination.rm_scripts import _rm_env

    local_bin = tmp_path / ".local" / "bin"
    local_bin.mkdir(parents=True)
    fleet_gh = local_bin / "gh"
    fleet_gh.touch(mode=0o755)

    monkeypatch.setenv("FLEET_GH_BIN", str(fleet_gh))
    monkeypatch.setenv("PATH", "/usr/bin:/bin")

    env = _rm_env()
    assert env.get("FLEET_GH_APP") == "general"
    assert env.get("PATH", "").startswith(str(local_bin))


def test_post_agent_lease_rejects_line_breaks(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """CR/LF injection in session or intent must exit 2 with zero writes."""
    from scripts import post_agent_lease as pal

    rc = pal.main(
        [
            "--repo",
            "AffineDrift",
            "--issue",
            "5175",
            "--agent",
            "antigravity",
            "--session",
            "line\nbreak",
        ]
    )
    assert rc == 2
    out = capsys.readouterr().out
    payload = json.loads(out.strip())
    assert payload.get("ok") is False
    assert "line breaks" in payload.get("errors", [""])[0]


def test_post_agent_lease_refuses_when_gh_missing(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """When gh executable is absent from PATH and known wrapper paths, refuse with exit 3."""
    from scripts import post_agent_lease as pal

    monkeypatch.setattr(pal, "resolve_fleet_gh", lambda env=None: None)

    rc = pal.main(
        [
            "--repo",
            "AffineDrift",
            "--issue",
            "5175",
            "--agent",
            "antigravity",
            "--session",
            "agy-test-session",
        ]
    )
    assert rc == pal.EXIT_IDENTITY_REFUSED
    out = capsys.readouterr().out
    payload = json.loads(out.strip())
    assert payload.get("ok") is False
    assert payload.get("identity_refused") is True
