"""Private Mode PM-13: scoped short-lived credentials minted after admission (issue #3264, epic #3249)."""

from __future__ import annotations

import secrets
import shutil
import tempfile
import threading
import time
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from private_mode import DISABLED, ClassificationError, Level, PrivateModeConfig
from private_mode_admission import AdmissionDecision, AdmissionState
from staff.fleet_run_token_service import assert_repositories_allowed_for_role, mint_fleet_run_github_token
from staff.github_run_env import StaffGithubToken, build_staff_run_env, github_permissions_for_role
from staff.roles import RoleSpec

MAX_CREDENTIAL_TTL_SECONDS: float = 3600.0
_ADMITTED_STATES: frozenset[AdmissionState] = frozenset({AdmissionState.READY, AdmissionState.RUNNING})
_RD_MANAGED_SOURCES: frozenset[str] = frozenset({"github_app", "rd_managed"})
_FORBIDDEN_CREDENTIAL_FILENAMES: frozenset[str] = frozenset(
    {".git-credentials", ".netrc", "_netrc", "hosts.yml", "id_rsa", "id_ed25519"}
)


class TokenMinterProtocol(Protocol):
    def __call__(
        self, *, run_id: str, role_name: str, repositories: list[str], caller_node: str
    ) -> StaffGithubToken: ...


@dataclass(frozen=True)
class ScopedRunCredential:
    """Short-lived single-repo credential metadata (token value is excluded from ``repr``)."""

    credential_id: str
    job_id: str
    repo: str
    level: Level
    permissions: Mapping[str, str]
    issued_at: float
    expires_at: float
    ttl_seconds: float
    source: str
    _token: str = field(repr=False)


def assert_no_credential_files(root_dir: Path) -> None:
    """Assert that *root_dir* contains no persisted Git/GitHub/SSH credential files."""
    base = Path(root_dir)
    if not base.exists():
        return
    for path in base.rglob("*"):
        if path.is_file() and path.name in _FORBIDDEN_CREDENTIAL_FILENAMES:
            raise ClassificationError(f"sandbox credential file violation: {path.name}")


class PrivateModeCredentialBroker:
    """Mints and brokers in-memory, single-repo, short-lived credentials after PM-10 admission."""

    def __init__(
        self,
        *,
        config: PrivateModeConfig = DISABLED,
        token_minter: Callable[..., StaffGithubToken] = mint_fleet_run_github_token,
    ) -> None:
        if not isinstance(config, PrivateModeConfig):
            raise ClassificationError("PrivateModeCredentialBroker: config must be a PrivateModeConfig")
        self._config = config
        self._token_minter = token_minter
        self._lock = threading.RLock()
        self._store: dict[str, ScopedRunCredential] = {}
        self._revoked: set[str] = set()

    def mint_for_admitted(
        self,
        *,
        decision: AdmissionDecision,
        role: RoleSpec,
        repositories: Sequence[str],
        caller_node: str,
        now: float | None = None,
    ) -> ScopedRunCredential:
        """Mint a single-repo, least-permission credential via ``fleet_run_token_service`` after admission."""
        if not isinstance(decision, AdmissionDecision) or decision.state not in _ADMITTED_STATES:
            raise ClassificationError("mint_for_admitted: admission decision must be READY or RUNNING")
        if not isinstance(role, RoleSpec):
            raise ClassificationError("mint_for_admitted: role must be a RoleSpec")
        repo_list = [str(r).strip() for r in repositories if str(r).strip()]
        if len(repo_list) != 1:
            raise ClassificationError("mint_for_admitted: exactly one repository is allowed per credential")
        if not isinstance(caller_node, str) or not caller_node.strip():
            raise ClassificationError("mint_for_admitted: caller_node must be a non-empty str")

        try:
            primary_repo = assert_repositories_allowed_for_role(role, repo_list)
        except Exception as exc:
            raise ClassificationError(f"mint_for_admitted: repository validation failed: {exc}") from exc

        issued_at = float(time.time() if now is None else now)
        minted = self._token_minter(
            run_id=decision.job_id,
            role_name=role.name,
            repositories=[primary_repo],
            caller_node=caller_node.strip(),
        )
        if not isinstance(minted, StaffGithubToken) or not minted.token.strip():
            raise ClassificationError("mint_for_admitted: token_minter returned an empty token")

        eff_level = decision.effective_level if self._config.enabled else Level.PUBLIC
        if self._config.enabled:
            if minted.source not in _RD_MANAGED_SOURCES:
                raise ClassificationError("mint_for_admitted: only RD-managed credentials are permitted")
            ttl = float(minted.expires_at) - issued_at
            if ttl <= 0.0 or ttl > MAX_CREDENTIAL_TTL_SECONDS:
                raise ClassificationError(
                    f"mint_for_admitted: credential TTL must be in (0, {int(MAX_CREDENTIAL_TTL_SECONDS)}] seconds"
                )
            expires_at = float(minted.expires_at)
        else:
            expires_at = float(minted.expires_at) if minted.expires_at > 0.0 else issued_at + MAX_CREDENTIAL_TTL_SECONDS
            ttl = max(0.0, expires_at - issued_at)

        cred_id = f"pmcred-{secrets.token_hex(8)}"
        cred = ScopedRunCredential(
            credential_id=cred_id,
            job_id=decision.job_id,
            repo=primary_repo,
            level=eff_level,
            permissions=dict(github_permissions_for_role(role)),
            issued_at=issued_at,
            expires_at=expires_at,
            ttl_seconds=ttl,
            source=minted.source,
            _token=minted.token,
        )
        with self._lock:
            self._store[cred_id] = cred
        return cred

    def resolve_token(self, credential_id: str, *, now: float | None = None) -> str:
        """Resolve an active in-memory token for askpass/credential-helper use; refuse after TTL or revoke."""
        if not isinstance(credential_id, str) or not credential_id.strip():
            raise ClassificationError("resolve_token: credential_id must be a non-empty str")
        check_now = float(time.time() if now is None else now)
        with self._lock:
            cred = self._store.get(credential_id.strip())
            if cred is None or credential_id.strip() in self._revoked:
                raise ClassificationError("resolve_token: credential is unknown, expired, or revoked")
            if self._config.enabled and check_now >= cred.expires_at:
                self._revoked.add(credential_id.strip())
                raise ClassificationError("resolve_token: credential expired; reuse after TTL is refused")
            return cred._token

    def revoke(self, credential_id: str) -> None:
        """Immediately revoke an in-memory credential so subsequent resolves fail closed."""
        with self._lock:
            self._revoked.add(credential_id.strip())
            self._store.pop(credential_id.strip(), None)

    @contextmanager
    def ephemeral_sandbox_env(
        self,
        credential_id: str,
        *,
        base_env: Mapping[str, str],
        parent_dir: Path | None = None,
        real_gh_path: str = "/usr/bin/gh",
        now: float | None = None,
    ) -> Iterator[dict[str, str]]:
        """Provide an ephemeral ``HOME`` and in-memory/askpass Git env; scrub and verify on exit."""
        token = self.resolve_token(credential_id, now=now)
        with self._lock:
            cred = self._store[credential_id.strip()]
        eph_home = Path(tempfile.mkdtemp(prefix="rd-pm-home-", dir=str(parent_dir) if parent_dir else None))
        try:
            env = build_staff_run_env(
                base_env,
                token,
                run_id=cred.job_id,
                real_gh_path=real_gh_path,
            )
            xdg_cfg = eph_home / ".config"
            xdg_cache = eph_home / ".cache"
            gnupg_dir = eph_home / ".gnupg"
            for d in (xdg_cfg, xdg_cache, gnupg_dir):
                d.mkdir(parents=True, exist_ok=True)
            env["HOME"] = str(eph_home)
            env["XDG_CONFIG_HOME"] = str(xdg_cfg)
            env["XDG_CACHE_HOME"] = str(xdg_cache)
            env["GNUPGHOME"] = str(gnupg_dir)
            env["GIT_CONFIG_NOSYSTEM"] = "1"
            env["GIT_CONFIG_GLOBAL"] = "/dev/null"
            yield env
            assert_no_credential_files(eph_home)
        finally:
            self.revoke(credential_id)
            shutil.rmtree(eph_home, ignore_errors=True)
