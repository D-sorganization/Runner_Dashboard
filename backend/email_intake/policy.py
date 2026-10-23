"""Mail intake sender policy (parsed from RM staff/mail_policy.yml)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import yaml


class MailPolicyError(ValueError):
    """Policy YAML is invalid."""


@dataclass(frozen=True)
class AllowedSender:
    address: str
    enabled: bool


@dataclass(frozen=True)
class MailPolicy:
    intake_enabled: bool
    require_dmarc_pass: bool
    max_age_hours: int
    rate_limit_per_sender_per_hour: int
    max_message_bytes: int
    allowed_senders: tuple[AllowedSender, ...]

    def sender_enabled(self, address: str) -> bool:
        needle = address.strip().lower()
        for row in self.allowed_senders:
            if row.address == needle:
                return row.enabled
        return False

    def to_dict(self) -> dict[str, Any]:
        return {
            "intake_enabled": self.intake_enabled,
            "require_dmarc_pass": self.require_dmarc_pass,
            "max_age_hours": self.max_age_hours,
            "rate_limit_per_sender_per_hour": self.rate_limit_per_sender_per_hour,
            "max_message_bytes": self.max_message_bytes,
            "allowed_senders": [{"address": s.address, "enabled": s.enabled} for s in self.allowed_senders],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> MailPolicy:
        senders = tuple(
            AllowedSender(address=str(row["address"]).lower(), enabled=bool(row["enabled"]))
            for row in data.get("allowed_senders", [])
        )
        return cls(
            intake_enabled=bool(data.get("intake_enabled", True)),
            require_dmarc_pass=bool(data.get("require_dmarc_pass", True)),
            max_age_hours=int(data.get("max_age_hours", 72)),
            rate_limit_per_sender_per_hour=int(data.get("rate_limit_per_sender_per_hour", 12)),
            max_message_bytes=int(data.get("max_message_bytes", 262_144)),
            allowed_senders=senders,
        )


_DEFAULTS = {
    "intake_enabled": True,
    "require_dmarc_pass": True,
    "max_age_hours": 72,
    "rate_limit_per_sender_per_hour": 12,
    "max_message_bytes": 262_144,
}


def _email(value: Any, *, field: str) -> str:
    if not isinstance(value, str) or "@" not in value:
        raise MailPolicyError(f"{field} must be an email address")
    return value.strip().lower()


def parse_mail_policy_yaml(text: str) -> MailPolicy:
    """Parse policy document; missing file should be handled by caller."""
    try:
        raw = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise MailPolicyError(f"YAML syntax error: {exc}") from exc
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise MailPolicyError("top level must be a mapping")
    merged = {**_DEFAULTS, **{k: raw[k] for k in _DEFAULTS if k in raw}}
    if "intake_enabled" in raw:
        merged["intake_enabled"] = bool(raw["intake_enabled"])
    if "require_dmarc_pass" in raw:
        merged["require_dmarc_pass"] = bool(raw["require_dmarc_pass"])
    senders_raw = raw.get("allowed_senders", [])
    if not isinstance(senders_raw, list):
        raise MailPolicyError("allowed_senders must be a list")
    senders: list[AllowedSender] = []
    for index, item in enumerate(senders_raw):
        if not isinstance(item, dict):
            raise MailPolicyError(f"allowed_senders[{index}] must be a mapping")
        senders.append(
            AllowedSender(
                address=_email(item.get("address"), field=f"allowed_senders[{index}].address"),
                enabled=bool(item.get("enabled", True)),
            )
        )
    for key in ("max_age_hours", "rate_limit_per_sender_per_hour", "max_message_bytes"):
        val = merged[key]
        if not isinstance(val, int) or val < 1:
            raise MailPolicyError(f"{key} must be a positive integer")
    return MailPolicy(
        intake_enabled=bool(merged["intake_enabled"]),
        require_dmarc_pass=bool(merged["require_dmarc_pass"]),
        max_age_hours=int(merged["max_age_hours"]),
        rate_limit_per_sender_per_hour=int(merged["rate_limit_per_sender_per_hour"]),
        max_message_bytes=int(merged["max_message_bytes"]),
        allowed_senders=tuple(senders),
    )
