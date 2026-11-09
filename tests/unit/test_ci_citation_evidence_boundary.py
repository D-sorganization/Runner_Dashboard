"""RM#2381: trusted evidence boundary for CI citation verification (source-only contract)."""

from __future__ import annotations

import pytest
from library.ci_citation_evidence_boundary import (
    CiCitationVerificationResult,
    CiRunnerKind,
    classify_trusted_evidence,
    deny_private_library_for_public_pr_runner,
    library_shared_only_for_ci_runner,
    verify_ci_citation_with_trusted_evidence,
)

pytestmark = pytest.mark.unit

_TRUSTED = {
    "source": "github_actions_attestation",
    "receipt_id": "gha-run-12345",
    "digest_sha256": "a" * 64,
}


def test_classify_trusted_evidence_absent_when_none() -> None:
    assert classify_trusted_evidence(None).value == "absent"


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"source": "unknown_channel"},
        {"source": "github_actions_attestation"},
        {"source": "github_actions_attestation", "receipt_id": "  "},
        {"source": "github_actions_attestation", "receipt_id": "ok", "digest_sha256": "short"},
    ],
)
def test_classify_trusted_evidence_untrusted_on_bad_shape(payload: dict[str, str]) -> None:
    assert classify_trusted_evidence(payload).value == "untrusted"


def test_classify_trusted_evidence_trusted_on_valid_receipt() -> None:
    assert classify_trusted_evidence(_TRUSTED).value == "trusted"


@pytest.mark.parametrize("evidence", [None, {"source": "blog_post"}, _TRUSTED])
def test_verify_never_reports_success_without_activation(evidence: dict[str, str] | None) -> None:
    result = verify_ci_citation_with_trusted_evidence(content="claim [ref:…]", evidence=evidence)
    assert isinstance(result, CiCitationVerificationResult)
    assert result.verified is False
    assert result.status == "unverified"


def test_verify_marks_untrusted_evidence_explicitly() -> None:
    result = verify_ci_citation_with_trusted_evidence(
        content="x",
        evidence={"source": "self_signed_blog", "receipt_id": "nope", "digest_sha256": "b" * 64},
    )
    assert result.verified is False
    assert result.status == "unverified"
    assert result.reason == "trusted_evidence_untrusted"


def test_verify_absent_evidence_is_fail_closed() -> None:
    result = verify_ci_citation_with_trusted_evidence(content="x", evidence=None)
    assert result.verified is False
    assert result.reason == "trusted_evidence_absent"


def test_public_pr_runner_is_shared_only() -> None:
    assert library_shared_only_for_ci_runner(CiRunnerKind.PUBLIC_PR) is True
    assert library_shared_only_for_ci_runner(CiRunnerKind.FLEET_HOSTED) is False


def test_public_pr_runner_denied_private_library_sources() -> None:
    assert deny_private_library_for_public_pr_runner(runner_kind=CiRunnerKind.PUBLIC_PR, source_shared=False)
    assert not deny_private_library_for_public_pr_runner(runner_kind=CiRunnerKind.PUBLIC_PR, source_shared=True)
    assert not deny_private_library_for_public_pr_runner(runner_kind=CiRunnerKind.FLEET_HOSTED, source_shared=False)
