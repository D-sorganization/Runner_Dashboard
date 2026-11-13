"""Code reviewer author detection from branch prefix."""

from __future__ import annotations

import pytest
from staff.review import detect_author_provider, select_reviewer_provider
from staff.role_spec import parse_role
from staff.role_provider_chains import enrich_role_provider_policy

pytestmark = pytest.mark.unit


def test_detect_author_from_branch_when_store_empty() -> None:
    assert (
        detect_author_provider(
            "Runner_Dashboard",
            9,
            branch="cursor/rd-role-providers-87d5",
            store=None,
            gh_probe=None,
        )
        == "cursor-agent"
    )


def test_reviewer_skips_author_provider_in_chain() -> None:
    spec = parse_role(
        enrich_role_provider_policy(
            {"name": "code-reviewer", "title": "CR", "providers": ["codex", "claude", "cursor-agent"]}
        ),
        "code-reviewer.yml",
    )
    sel = select_reviewer_provider("codex", list(spec.providers), role_spec=spec)
    assert sel.provider == "claude"
    assert not sel.same_provider
