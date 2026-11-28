"""RD#4709 (FC-015b): d-sorggeneralagent[bot] and d-sorggeminiagent[bot] must be in the
retry allowlist so they can authorize merge-shepherd requeues."""

from __future__ import annotations

import pytest
from coordination.merge_shepherd_requeue import _authorized_retry_author


@pytest.mark.unit
def test_d_sorggeneralagent_bot_is_authorized() -> None:
    assert _authorized_retry_author("d-sorggeneralagent[bot]") is True


@pytest.mark.unit
def test_d_sorggeminiagent_bot_is_authorized() -> None:
    assert _authorized_retry_author("d-sorggeminiagent[bot]") is True


@pytest.mark.unit
def test_d_sorggeneralagent_without_bot_suffix_is_authorized() -> None:
    assert _authorized_retry_author("d-sorggeneralagent") is True


@pytest.mark.unit
def test_d_sorggeminiagent_without_bot_suffix_is_authorized() -> None:
    assert _authorized_retry_author("d-sorggeminiagent") is True


@pytest.mark.unit
def test_openbotgit_bot_still_authorized_not_removed() -> None:
    """openbotgit is preserved; RD#4709 flags its removal as an open question only."""
    assert _authorized_retry_author("openbotgit[bot]") is True


@pytest.mark.unit
def test_unauthorized_bot_login_rejected() -> None:
    assert _authorized_retry_author("randombot[bot]") is False


@pytest.mark.unit
def test_empty_login_rejected() -> None:
    assert _authorized_retry_author("") is False


@pytest.mark.unit
def test_partial_name_match_rejected() -> None:
    """'barb' inside a different login is not authority — exact match only."""
    assert _authorized_retry_author("evil-barb[bot]") is False
