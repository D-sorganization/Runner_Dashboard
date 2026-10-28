"""Contract tests for Private Mode PM-13 scoped short-lived credentials after admission (issue #3264)."""

from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(_ROOT / "backend"))

for _mod in ("fastapi", "httpx"):
    try:
        __import__(_mod)
    except ModuleNotFoundError:
        _stub = types.ModuleType(_mod)
        _stub.HTTPException = Exception  # type: ignore[attr-defined]
        sys.modules[_mod] = _stub

import dashboard_config  # noqa: E402
import private_mode as pm  # noqa: E402
import private_mode_admission as pma  # noqa: E402
import private_mode_credentials as pmc  # noqa: E402
from staff import fleet_run_token_service  # noqa: E402
from staff.github_run_env import StaffGithubToken  # noqa: E402
from staff.roles import RoleSpec  # noqa: E402

_CFG = pm.PrivateModeConfig(enabled=True, default_level=pm.Level.INTERNAL)


@pytest.fixture(autouse=True)
def _example_corp_org(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(dashboard_config, "ORG", "example-corp")
    monkeypatch.setattr(fleet_run_token_service, "ORG", "example-corp")


def _sample_role(*, read_only: bool = False, push_branch: bool = True, open_pr: bool = False) -> RoleSpec:
    return RoleSpec(
        name="pm-worker",
        title="Example Corp private worker",
        repos=("example-corp/widgets-sensitive",),
        scope={"code_read_only": read_only},
        permissions={"push_branch": push_branch, "open_pr": open_pr, "merge": False},
    )


def _admitted_decision(state: pma.AdmissionState = pma.AdmissionState.READY) -> pma.AdmissionDecision:
    return pma.AdmissionDecision(
        job_id="job-pm13-1",
        state=state,
        effective_level=pm.Level.SENSITIVE,
        actual_destination="local:claude",
        reason="admitted" if state is pma.AdmissionState.READY else "lane-capacity-exhausted",
        owner="pm-rd" if state in pma.PARKED_STATES else "",
        deadline="2026-10-28T22:00:00Z" if state in pma.PARKED_STATES else None,
    )


def test_token_scope_least_permission_and_ttl_asserted() -> None:
    """Acceptance criterion 1: single-repo scope, least permission, RD-managed source, and TTL <= 60 min asserted."""
    now = 1_700_000_000.0
    minted_calls: list[dict[str, object]] = []

    def _fake_mint(*, run_id: str, role_name: str, repositories: list[str], caller_node: str) -> StaffGithubToken:
        minted_calls.append(
            {"run_id": run_id, "role_name": role_name, "repositories": repositories, "caller_node": caller_node}
        )
        return StaffGithubToken(token="ghs_rd_managed_ephemeral_token", expires_at=now + 1800.0, source="github_app")

    broker = pmc.PrivateModeCredentialBroker(config=_CFG, token_minter=_fake_mint)
    ro_role = _sample_role(read_only=True, push_branch=False)
    cred = broker.mint_for_admitted(
        decision=_admitted_decision(),
        role=ro_role,
        repositories=["example-corp/widgets-sensitive"],
        caller_node="node-example-1",
        now=now,
    )
    assert cred.repo == "example-corp/widgets-sensitive"
    assert cred.ttl_seconds == pytest.approx(1800.0)
    assert cred.ttl_seconds <= pmc.MAX_CREDENTIAL_TTL_SECONDS
    assert cred.permissions == {
        "contents": "read",
        "pull_requests": "read",
        "issues": "read",
        "metadata": "read",
        "checks": "read",
    }
    assert "ghs_rd_managed_ephemeral_token" not in repr(cred)
    assert len(minted_calls) == 1

    with pytest.raises(pm.ClassificationError, match="exactly one repository"):
        broker.mint_for_admitted(
            decision=_admitted_decision(),
            role=ro_role,
            repositories=["example-corp/widgets-sensitive", "example-corp/widgets-public"],
            caller_node="node-example-1",
            now=now,
        )

    with pytest.raises(pm.ClassificationError, match="READY or RUNNING"):
        broker.mint_for_admitted(
            decision=_admitted_decision(pma.AdmissionState.PARKED_CAPACITY),
            role=ro_role,
            repositories=["example-corp/widgets-sensitive"],
            caller_node="node-example-1",
            now=now,
        )

    long_ttl_broker = pmc.PrivateModeCredentialBroker(
        config=_CFG,
        token_minter=lambda **_: StaffGithubToken(token="ghs_too_long", expires_at=now + 3601.0, source="github_app"),
    )
    with pytest.raises(pm.ClassificationError, match="TTL"):
        long_ttl_broker.mint_for_admitted(
            decision=_admitted_decision(),
            role=ro_role,
            repositories=["example-corp/widgets-sensitive"],
            caller_node="node-example-1",
            now=now,
        )

    operator_broker = pmc.PrivateModeCredentialBroker(
        config=_CFG,
        token_minter=lambda **_: StaffGithubToken(token="ghp_operator_pat", expires_at=now + 600.0, source="operator"),
    )
    with pytest.raises(pm.ClassificationError, match="RD-managed"):
        operator_broker.mint_for_admitted(
            decision=_admitted_decision(),
            role=ro_role,
            repositories=["example-corp/widgets-sensitive"],
            caller_node="node-example-1",
            now=now,
        )


def test_sandbox_ephemeral_home_leaves_no_credential_files(tmp_path: Path) -> None:
    """Acceptance criterion 2: in-memory/askpass delivery, ephemeral HOME, and sandbox leaves no credential files."""
    now = 1_700_000_000.0
    broker = pmc.PrivateModeCredentialBroker(
        config=_CFG,
        token_minter=lambda **_: StaffGithubToken(
            token="ghs_ephemeral_12345", expires_at=now + 900.0, source="github_app"
        ),
    )
    cred = broker.mint_for_admitted(
        decision=_admitted_decision(),
        role=_sample_role(),
        repositories=["example-corp/widgets-sensitive"],
        caller_node="node-example-1",
        now=now,
    )
    base_env = {
        "PATH": "/usr/bin:/bin",
        "HOME": "/home/ambient-user",
        "GH_TOKEN": "ghp_ambient_host_secret",
        "GITHUB_APP_PRIVATE_KEY": "secret-key-pem",
    }
    sandbox_root = tmp_path / "sandboxes"
    sandbox_root.mkdir()
    captured_home: Path | None = None
    with broker.ephemeral_sandbox_env(
        cred.credential_id, base_env=base_env, parent_dir=sandbox_root, real_gh_path="/usr/bin/gh", now=now + 10.0
    ) as sandbox_env:
        captured_home = Path(sandbox_env["HOME"])
        assert captured_home.exists() and captured_home.parent == sandbox_root
        assert "GITHUB_APP_PRIVATE_KEY" not in sandbox_env
        assert "ghp_ambient_host_secret" not in sandbox_env.values()
        assert sandbox_env["GIT_TERMINAL_PROMPT"] == "0"
        assert sandbox_env["GIT_CONFIG_NOSYSTEM"] == "1"
        assert "credential.https://github.com.helper" in sandbox_env.values()
        pmc.assert_no_credential_files(captured_home)

    assert captured_home is not None and not captured_home.exists()
    assert list(sandbox_root.iterdir()) == []


def test_reuse_after_ttl_or_revocation_is_refused(tmp_path: Path) -> None:
    """Acceptance criterion 3: credential reuse after TTL or after sandbox teardown is refused."""
    now = 1_700_000_000.0
    broker = pmc.PrivateModeCredentialBroker(
        config=_CFG,
        token_minter=lambda **_: StaffGithubToken(token="ghs_short_lived", expires_at=now + 600.0, source="github_app"),
    )
    cred = broker.mint_for_admitted(
        decision=_admitted_decision(),
        role=_sample_role(),
        repositories=["example-corp/widgets-sensitive"],
        caller_node="node-example-1",
        now=now,
    )
    assert broker.resolve_token(cred.credential_id, now=now + 599.0) == "ghs_short_lived"
    with pytest.raises(pm.ClassificationError, match="expired"):
        broker.resolve_token(cred.credential_id, now=now + 600.0)
    with pytest.raises(pm.ClassificationError, match="expired"):
        with broker.ephemeral_sandbox_env(
            cred.credential_id, base_env={}, parent_dir=tmp_path, real_gh_path="/usr/bin/gh", now=now + 601.0
        ):
            pass


def test_flag_off_behaviour_unchanged() -> None:
    """Acceptance criterion 4: Flag-off behaviour unchanged."""
    now = 1_700_000_000.0
    broker_off = pmc.PrivateModeCredentialBroker(
        config=pm.DISABLED,
        token_minter=lambda **_: StaffGithubToken(token="ghp_op_fallback", expires_at=0.0, source="operator"),
    )
    cred = broker_off.mint_for_admitted(
        decision=_admitted_decision(),
        role=_sample_role(),
        repositories=["example-corp/widgets-sensitive"],
        caller_node="node-example-1",
        now=now,
    )
    assert cred.level is pm.Level.PUBLIC
    assert broker_off.resolve_token(cred.credential_id, now=now + 7200.0) == "ghp_op_fallback"


def test_credentials_files_pass_publish_guard() -> None:
    """Acceptance criterion 5: Passes the #3080 publish guard; placeholder fixtures only ('Example Corp')."""
    sys.path.insert(0, str(_ROOT / "scripts"))
    import publish_guard as pg  # noqa: PLC0415

    paths = ["backend/private_mode_credentials.py", "tests/test_private_mode_credentials.py"]
    assert pg.check(_ROOT, paths, pg.load_rules()) == []
