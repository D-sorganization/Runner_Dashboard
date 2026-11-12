# CI citation evidence boundary (RM #2381)

Board decision 2026-11-05: **accept** (unanimous). This document is the Runner_Dashboard
**source-only** consumer contract. Activation (workflow wiring, catalog export, credentials)
is explicitly deferred to a follow-up PR.

## Purpose

CI citation verification must be **fail-closed**. When trusted evidence of a CI attestation
or fleet receipt is absent or malformed, verification returns `unverified` and never reports
citation success.

## Trusted evidence shape

| Field | Requirement |
|-------|-------------|
| `source` | One of `github_actions_attestation`, `fleet_ci_receipt` |
| `receipt_id` | Non-empty string identifying the attestation/run receipt |
| `digest_sha256` | Lowercase 64-hex SHA-256 of the trusted receipt payload |

## Verifier interface

Implementation: `backend/library/ci_citation_evidence_boundary.py`

- `classify_trusted_evidence(payload) -> EvidenceTrust`
- `verify_ci_citation_with_trusted_evidence(...)` → `CiCitationVerificationResult`

Until `activation_enabled=True` in a future change, `verified` remains `False` even when
evidence classifies as `trusted` (contract-only mode).

## Public PR runners

`CiRunnerKind.PUBLIC_PR` (`public_pr_runner`) is **shared-library-only**:

- `library_shared_only_for_ci_runner` is `True`
- `deny_private_library_for_public_pr_runner` denies non-shared sources

## Out of scope (this contract)

- GitHub Actions workflow steps
- Evidence catalog export
- Provisioning attestation credentials
- Changing live `/api/v1/library/verify` behavior

Tests: `tests/unit/test_ci_citation_evidence_boundary.py`
