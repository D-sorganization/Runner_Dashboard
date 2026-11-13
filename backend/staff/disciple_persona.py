"""Disciple RD persona role runtime and citation-contract answer logic (#3704). Ports the real
``staff/roles/disciple.yml`` verbatim: a read-only, unscheduled role with no lease/push/PR/merge
permissions, so this module has no capsule, slot or fallback-drill plumbing (nothing to resume).
"""

from __future__ import annotations

import re
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import yaml

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from staff.provider_failover_config import FailoverStep  # noqa: E402
from staff.role_spec import RoleSpec, read_and_parse_role_file  # noqa: E402

ROLE = "disciple"

DISCIPLE_FAILOVER_CHAIN: tuple[FailoverStep, ...] = (
    FailoverStep("claude", "claude-opus-5-5"),
    FailoverStep("codex", "gpt-6.1-sol"),
    FailoverStep("cursor-agent", None),
)

DISCIPLE_REPOS: tuple[str, ...] = ("UpstreamDrift", "AffineDrift")
DISCIPLE_AUTHORITY: tuple[str, ...] = ("published", "findings", "reviews")
NOT_ESTABLISHED = "not established in our work"

_CITATION_RE = re.compile(r"^[^:]+:[^#]+#[^\s@]+ @ [^\s]+$")


def disciple_role_data() -> dict[str, Any]:
    """Canonical staff/roles/disciple.yml mapping conforming to staff/schema.json (#3704)."""
    perms = dict.fromkeys(("lease", "push_branch", "open_pr", "merge", "host_shell", "notify_user"), False)
    chain = [{"provider": s.provider, **({"model": s.model} if s.model else {})} for s in DISCIPLE_FAILOVER_CHAIN]
    return {
        "name": ROLE,
        "title": "Disciple",
        "summary": (
            "Answers from the cumulative UpstreamDrift and AffineDrift findings, citing every "
            "claim and separating current understanding from superseded results."
        ),
        "playbook": "docs/fleet-disciple.md",
        "prompt_template": None,
        "instructions": (
            "The Disciple answers questions about what the portfolio has found. Its source is "
            "the findings knowledge pack (staff/knowledge/findings.yml): AffineDrift articles, "
            "critiques and models, and UpstreamDrift research, assessments, physics and "
            "technical docs. Rules: answer only from retrieved passages, every claim carries a "
            "citation in the form repo:path#anchor @ commit; when the pack does not cover the "
            "question, say 'not established in our work' and hand off to research-scout "
            "(external literature) or vision-quest (where the frontier could go), never "
            "presenting general knowledge as our finding; passages marked superseded or "
            "retracted are never the answer, and when a current result replaced an earlier one, "
            "name both and say what changed; when sources disagree, show the disagreement and "
            "which source has the higher authority (published > findings > reviews) instead of "
            "blending. Read-only: it files nothing, leases nothing and writes nothing."
        ),
        "providers": ["claude", "codex", "cursor-agent"],
        "model": "default",
        "schedule": None,
        "window": None,
        "repos": list(DISCIPLE_REPOS),
        "scope": {
            "pack": "staff/knowledge/findings.yml",
            "answers_from": "findings knowledge pack only",
            "does_not": ["implement", "file-issues", "lease", "speculate"],
        },
        "budget": {"usd_per_run": 1.0, "usd_per_day": 10.0},
        "permissions": perms,
        "retry": {"max_attempts": 3, "failover_chain": chain},
        "reports_to": "user",
        "holds": [],
        "surface": "dashboard",
        "persona": {
            "voice": (
                "Faithful keeper of our results. Cites every claim, says plainly when something "
                "is not established, and names what superseded an old result."
            ),
            "greeting": "Disciple. Ask what our work has found and I will answer from the record, with sources.",
            "answers": [
                "what our research has established on a topic, with citations",
                "whether a result is current or was superseded, and by what",
                "where our published articles and our findings disagree",
                "which open questions our own results leave unanswered",
            ],
            "defers_to": ["research-scout", "vision-quest", "fleet-critic"],
        },
        "chat": {
            "contract": "staff/prompts/_chat_contract.md",
            "providers": ["claude", "codex", "cursor-agent"],
            "tools": ["search_knowledge", "read_repo"],
        },
    }


def write_disciple_role_yaml(directory: Path) -> Path:
    """Write ``disciple.yml`` into ``directory`` and return its path."""
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "disciple.yml"
    path.write_text(yaml.safe_dump(disciple_role_data(), sort_keys=False), encoding="utf-8")
    return path


def load_disciple_role_spec(directory: Path, *, raw: bool = False) -> Any:
    """Parse ``disciple.yml`` from ``directory`` into a ``RoleSpec`` (or raw dict when ``raw=True``)."""
    path = directory / "disciple.yml"
    return yaml.safe_load(path.read_text(encoding="utf-8")) if raw else read_and_parse_role_file(path)


def is_valid_citation(citation: str) -> bool:
    """A citation must be ``repo:path#anchor @ commit`` (disciple.yml's own required form)."""
    return bool(_CITATION_RE.match(citation.strip()))


def answer_disciple_question(
    passages: Sequence[Mapping[str, Any]],
    *,
    role: RoleSpec | None = None,
) -> dict[str, Any]:
    """Answer strictly from ``passages`` (dicts with ``text``/``citation``/``status``/``source``).

    Drops superseded/retracted passages first. With nothing left, defers per disciple.yml's own
    rule. When the survivors disagree (different ``text``), surfaces the disagreement ranked by
    ``DISCIPLE_AUTHORITY`` instead of blending them into one answer.
    """
    defers_to = tuple(role.defers_to) if role is not None else disciple_role_data()["persona"]["defers_to"]
    current = [p for p in passages if p.get("status") not in ("superseded", "retracted")]
    superseded = [p for p in passages if p.get("status") == "superseded"]
    if not current:
        return {"status": "not_established", "answer": NOT_ESTABLISHED, "defers_to": defers_to, "citations": []}
    for p in current:
        if not is_valid_citation(str(p.get("citation", ""))):
            raise ValueError(f"passage missing a valid citation: {p!r}")
    distinct_texts = {p["text"] for p in current}
    if len(distinct_texts) > 1:
        ranked = sorted(current, key=lambda p: DISCIPLE_AUTHORITY.index(p.get("source", "reviews")))
        return {
            "status": "disagreement",
            "answer": ranked[0]["text"],
            "citations": [p["citation"] for p in ranked],
            "higher_authority_source": ranked[0].get("source", "reviews"),
            "defers_to": (),
        }
    winner = current[0]
    superseded_note = f"Supersedes: {superseded[0]['text']} ({superseded[0]['citation']})" if superseded else ""
    return {
        "status": "answered",
        "answer": winner["text"],
        "citations": [winner["citation"]],
        "superseded_note": superseded_note,
        "defers_to": (),
    }
