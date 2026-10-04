"""Signed ``pr_lifecycle`` on dispatch envelopes (issue #1874)."""

from __future__ import annotations

import pytest
from dispatch_contract import CommandEnvelope, build_envelope


def _build(**kwargs: object) -> CommandEnvelope:
    return build_envelope(
        action="agents.dispatch.adhoc",
        source="dashboard",
        target="Repository_Management",
        requested_by="op",
        **kwargs,  # type: ignore[arg-type]
    )


def test_from_dict_rejects_unsigned_pr_lifecycle_that_differs_from_signed_payload() -> None:
    wire = _build(pr_lifecycle="arm_and_exit").to_dict()
    wire["pr_lifecycle"] = "autofix"  # tampered, unsigned top-level field
    with pytest.raises(ValueError, match="pr_lifecycle"):
        CommandEnvelope.from_dict(wire)


def test_from_dict_restores_pr_lifecycle_from_signed_payload() -> None:
    wire = _build(pr_lifecycle="subscribe").to_dict()
    wire.pop("pr_lifecycle", None)
    restored = CommandEnvelope.from_dict(wire)
    assert restored.pr_lifecycle == "subscribe"
    assert restored.verify_signature()


def test_from_dict_defaults_pr_lifecycle_when_nothing_signs_it() -> None:
    wire = _build().to_dict()
    wire["payload"].pop("pr_lifecycle", None)
    wire.pop("pr_lifecycle", None)
    assert CommandEnvelope.from_dict(wire).pr_lifecycle == "arm_and_exit"


def test_from_dict_falls_back_to_top_level_for_unsigned_legacy_envelope() -> None:
    wire = _build().to_dict()
    wire["payload"].pop("pr_lifecycle", None)
    wire["pr_lifecycle"] = "subscribe"
    assert CommandEnvelope.from_dict(wire).pr_lifecycle == "subscribe"


def test_build_envelope_round_trips_payload_only_pr_lifecycle() -> None:
    env = _build(payload={"pr_lifecycle": "subscribe"})
    assert env.pr_lifecycle == "subscribe"
    assert env.payload["pr_lifecycle"] == "subscribe"
    restored = CommandEnvelope.from_dict(env.to_dict())
    assert restored.pr_lifecycle == "subscribe"
    assert restored.verify_signature()


def test_build_envelope_rejects_conflicting_pr_lifecycle_and_payload() -> None:
    with pytest.raises(ValueError, match="pr_lifecycle"):
        _build(pr_lifecycle="arm_and_exit", payload={"pr_lifecycle": "subscribe"})


def test_build_envelope_accepts_agreeing_pr_lifecycle_pair() -> None:
    env = _build(pr_lifecycle="subscribe", payload={"pr_lifecycle": "subscribe"})
    assert env.pr_lifecycle == "subscribe"
