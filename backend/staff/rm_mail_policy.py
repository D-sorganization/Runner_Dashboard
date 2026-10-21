"""Load mail intake policy from Repository_Management YAML."""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from email_intake.policy import MailPolicy, MailPolicyError, parse_mail_policy_yaml
from staff.store import _config_dir

log = logging.getLogger("dashboard.staff.rm_mail_policy")

POLICY_RELATIVE = ("staff", "mail_policy.yml")
CACHE_NAME = "mail_policy_cache.json"


@dataclass(frozen=True)
class MailPolicySnapshot:
    policy: MailPolicy
    source_commit: str
    loaded_at: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "policy": self.policy.to_dict(),
            "source_commit": self.source_commit,
            "loaded_at": self.loaded_at,
        }


def mail_policy_yaml_path(rm_root: Path) -> Path:
    return rm_root.joinpath(*POLICY_RELATIVE)


def _cache_path() -> Path:
    return _config_dir() / CACHE_NAME


def load_mail_policy_yaml(path: Path) -> MailPolicy:
    if not path.is_file():
        return parse_mail_policy_yaml("")
    return parse_mail_policy_yaml(path.read_text(encoding="utf-8"))


def refresh_mail_policy_cache(rm_root: Path, commit_sha: str) -> MailPolicySnapshot | None:
    """Read RM YAML and persist a local snapshot for the request path."""
    path = mail_policy_yaml_path(rm_root)
    try:
        if path.is_file():
            policy = parse_mail_policy_yaml(path.read_text(encoding="utf-8"))
        else:
            policy = parse_mail_policy_yaml("")
    except MailPolicyError as exc:
        log.error("mail_policy.yml invalid (fail closed): %s", exc)
        return None
    import time

    snap = MailPolicySnapshot(policy=policy, source_commit=commit_sha, loaded_at=time.time())
    cache = _cache_path()
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(snap.to_dict(), indent=2) + "\n", encoding="utf-8")
    return snap


def get_cached_mail_policy() -> MailPolicy | None:
    """Return the last reconciled policy, or None when missing/invalid."""
    path = _cache_path()
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        policy_dict = raw.get("policy") or {}
        return MailPolicy.from_dict(policy_dict)
    except (OSError, ValueError, MailPolicyError, TypeError, KeyError):
        return None


def policy_from_rm_root(rm_root: Path | None = None) -> MailPolicy:
    """Best-effort policy for tests and startup; empty allowlist when unset."""
    configured = rm_root
    if configured is None:
        env = os.environ.get("STAFF_RM_ROOT")
        configured = Path(env).expanduser() if env else None
    if configured is None:
        return parse_mail_policy_yaml("allowed_senders: []")
    path = mail_policy_yaml_path(configured)
    if not path.is_file():
        return parse_mail_policy_yaml("allowed_senders: []")
    return load_mail_policy_yaml(path)
