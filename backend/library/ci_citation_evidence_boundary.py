"""Fail-closed trusted-evidence boundary for CI citation verification (RM#2381).

Source-only contract: documents the verifier interface and classification rules.
No workflow hooks, catalog export, or credential provisioning belong in this module.

Activation (wiring ``verify_content`` after attested CI receipts) is a separate PR.
Until activation is enabled explicitly, this boundary never reports citation success.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

_TRUSTED_EVIDENCE_SOURCES = frozenset({"github_actions_attestation", "fleet_ci_receipt"})
_DIGEST_SHA256 = re.compile(r"^[a-f0-9]{64}\Z")


class EvidenceTrust(StrEnum):
    ABSENT = "absent"
    UNTRUSTED = "untrusted"
    TRUSTED = "trusted"


class CiRunnerKind(StrEnum):
    """Classification for callers that may invoke CI citation verification."""

    FLEET_HOSTED = "fleet_hosted"
    PUBLIC_PR = "public_pr_runner"


@dataclass(frozen=True, slots=True)
class CiCitationVerificationResult:
    """Result envelope for CI citation verification under the evidence boundary."""

    verified: bool
    status: str
    reason: str
    citations: tuple[dict[str, Any], ...] = ()


def classify_trusted_evidence(payload: Mapping[str, Any] | None) -> EvidenceTrust:
    """Classify whether ``payload`` carries trusted CI citation evidence (format only)."""
    if payload is None:
        return EvidenceTrust.ABSENT
    source = payload.get("source")
    if not isinstance(source, str) or source not in _TRUSTED_EVIDENCE_SOURCES:
        return EvidenceTrust.UNTRUSTED
    receipt_id = payload.get("receipt_id")
    digest = payload.get("digest_sha256")
    if not isinstance(receipt_id, str) or not receipt_id.strip():
        return EvidenceTrust.UNTRUSTED
    if not isinstance(digest, str) or not _DIGEST_SHA256.match(digest.lower()):
        return EvidenceTrust.UNTRUSTED
    return EvidenceTrust.TRUSTED


def verify_ci_citation_with_trusted_evidence(
    *,
    content: str,
    evidence: Mapping[str, Any] | None,
    runner_kind: CiRunnerKind = CiRunnerKind.FLEET_HOSTED,
    activation_enabled: bool = False,
) -> CiCitationVerificationResult:
    """Verify a CI citation claim only when trusted evidence is present (fail-closed).

    Pre: ``content`` is the text under verification (unused until activation).
    Post: ``verified`` is never ``True`` in the source-only contract; missing or
    malformed evidence yields ``unverified`` with an explicit reason.
    """
    _ = content  # reserved for activation PR
    if runner_kind is CiRunnerKind.PUBLIC_PR:
        # Public PR runners are library-shared-only by policy; evidence cannot widen that.
        pass
    trust = classify_trusted_evidence(evidence)
    if trust is not EvidenceTrust.TRUSTED:
        reason = f"trusted_evidence_{trust.value}"
        return CiCitationVerificationResult(verified=False, status="unverified", reason=reason)
    if not activation_enabled:
        return CiCitationVerificationResult(
            verified=False,
            status="unverified",
            reason="ci_citation_evidence_boundary_not_activated",
        )
    return CiCitationVerificationResult(
        verified=False,
        status="unverified",
        reason="ci_citation_evidence_boundary_not_activated",
    )


def library_shared_only_for_ci_runner(runner_kind: CiRunnerKind) -> bool:
    """Public PR runners must not select private library corpora."""
    return runner_kind is CiRunnerKind.PUBLIC_PR


def deny_private_library_for_public_pr_runner(*, runner_kind: CiRunnerKind, source_shared: bool) -> bool:
    """Return ``True`` when library access must be denied (fail closed)."""
    return runner_kind is CiRunnerKind.PUBLIC_PR and not source_shared
