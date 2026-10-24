"""Contract for expert panels (#1634, epic #1633): 3-4 experts discuss a topic in turns.

A panel is a ``kind="panel"`` thread. Each expert is a read-only chat seat with a
perspective; the moderator (the reserved name ``Moderator``) writes the closing
synthesis. The request model is the single boundary check for the API and the engine.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Literal

import provider_switch
from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, field_validator, model_validator
from staff.adapter_policies import _CHAT_READ_ONLY_FLAGS

MODERATOR_NAME = "Moderator"
PANEL_MODES = ("debate", "brainstorm")
STANCES = ("agree", "partly", "disagree")
# Providers that can run a read-only chat turn: a panel seat never gets write tools.
PANEL_PROVIDERS: tuple[str, ...] = tuple(sorted(_CHAT_READ_ONLY_FLAGS))
DEFAULT_TURN_TIMEOUT_SECONDS = 300.0
MAX_ACTIVE_PANELS = 2
CODEBASE_REPOS = (
    "UpstreamDrift",
    "AffineDrift",
    "Gasification_Model",
    "Tools",
    "Repository_Management",
    "Runner_Dashboard",
)


def enabled_panel_providers() -> tuple[str, ...]:
    """Panel providers not switched off on this node (#1597); read per call so the env file governs."""
    return tuple(p for p in PANEL_PROVIDERS if not provider_switch.is_disabled(p))


# A model id is appended to the provider CLI argv (``--model <id>``); this shape can never be a flag
# (no leading '-'), a shell fragment or whitespace, so it cannot inject arguments.
MODEL_ID_RE = re.compile(r"^[A-Za-z0-9._:/][A-Za-z0-9._:/-]{0,79}$")


def _check_model(model: str | None, seat: str) -> str | None:
    if model is not None and not MODEL_ID_RE.fullmatch(model):
        raise ValueError(
            f"seat {seat!r}: model id must match [A-Za-z0-9._:/-]{{1,80}} and not start with '-' (got {model[:40]!r})"
        )
    return model


def default_quorum(seats: int) -> int:
    """A majority of ``seats``."""
    return seats // 2 + 1


def _check_provider(provider: str) -> str:
    if provider not in PANEL_PROVIDERS:
        raise ValueError(f"provider {provider!r} has no read-only chat mode; use one of {', '.join(PANEL_PROVIDERS)}")
    if provider_switch.is_disabled(provider):
        raise ValueError(f"provider {provider!r} is {provider_switch.DISABLED_DETAIL}")
    return provider


class PanelExpert(BaseModel):
    """One panelist: a display name, the perspective it argues from, and the CLI seat that speaks for it."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=40, pattern=r"^[A-Za-z][\w .-]*$")
    perspective: str = Field(min_length=1, max_length=600)
    provider: str = "claude"
    model: str | None = None  # shape + length checked by _safe_model

    @field_validator("provider")
    @classmethod
    def _read_only_provider(cls, v: str, info: ValidationInfo) -> str:
        try:
            return _check_provider(v)
        except ValueError as exc:
            raise ValueError(f"seat {info.data.get('name', '?')!r}: {exc}") from exc

    @field_validator("model")
    @classmethod
    def _safe_model(cls, v: str | None, info: ValidationInfo) -> str | None:
        return _check_model(v, str(info.data.get("name", "?")))


class PanelCreateRequest(BaseModel):
    """``POST /api/v1/staff/panels`` body.

    Pre: 3-4 experts with unique names (case-insensitive, never ``Moderator``), 1-6 rounds,
    a non-blank topic, and read-only providers for every seat.
    """

    model_config = ConfigDict(extra="forbid")

    topic: str = Field(min_length=1, max_length=4000)
    experts: list[PanelExpert] = Field(min_length=3, max_length=4)
    rounds: int = Field(default=3, ge=1, le=6)
    mode: Literal["debate", "brainstorm"] = "debate"
    title: str | None = Field(default=None, max_length=120)
    confirm_cost: bool = False
    moderator_provider: str = "claude"
    moderator_model: str | None = None  # shape + length checked by _safe_model
    # Seats that must take part for the panel to count as quorate; default is a majority of the seats.
    quorum: int | None = Field(default=None, ge=1, le=4)
    pack_id: Literal["cartographer"] | None = "cartographer"
    repos: list[str] | None = Field(default=None, min_length=1, max_length=6)
    preload_codebase: bool = True
    # With ``repos``: seats read a read-only snapshot of origin/main for those repos (#2511).
    ground_repos: bool = True

    @field_validator("repos")
    @classmethod
    def _known_repositories(cls, repos: list[str] | None) -> list[str] | None:
        if repos is not None and (len(set(repos)) != len(repos) or any(repo not in CODEBASE_REPOS for repo in repos)):
            raise ValueError("repos must contain unique configured fleet repository names")
        return repos

    @field_validator("moderator_provider")
    @classmethod
    def _read_only_moderator(cls, v: str) -> str:
        return _check_provider(v)

    @field_validator("moderator_model")
    @classmethod
    def _safe_moderator_model(cls, v: str | None) -> str | None:
        return _check_model(v, MODERATOR_NAME)

    @field_validator("topic")
    @classmethod
    def _topic_not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("topic must not be blank")
        return v.strip()

    @model_validator(mode="after")
    def _unique_names(self) -> PanelCreateRequest:
        names = [e.name.strip().lower() for e in self.experts]
        if len(set(names)) != len(names):
            raise ValueError("expert names must be unique (case-insensitive)")
        if MODERATOR_NAME.lower() in names:
            raise ValueError(f"{MODERATOR_NAME!r} is reserved for the synthesis turn")
        if self.quorum is not None and self.quorum > len(self.experts):
            raise ValueError(f"quorum {self.quorum} exceeds the {len(self.experts)} seats")
        return self


@dataclass(frozen=True)
class PanelSpeaker:
    """Who speaks a turn: an expert or the moderator. Flat so a turn runner needs nothing else."""

    name: str
    perspective: str
    provider: str
    model: str | None = None


@dataclass(frozen=True)
class TurnOutcome:
    """What a turn runner returns: the reply text, or why there is none."""

    ok: bool
    text: str = ""
    error: str | None = None


def _expert(name: str, perspective: str) -> dict[str, Any]:
    return {"name": name, "perspective": perspective}


PANEL_PRESETS: tuple[dict[str, Any], ...] = (
    {
        "id": "engineering-review",
        "title": "Engineering design review",
        "experts": [
            _expert("Architect", "System architecture, interfaces and long-term maintainability."),
            _expert("Skeptic", "Failure modes, hidden assumptions and what could go wrong."),
            _expert("Operator", "Day-to-day operation, cost, monitoring and recovery."),
        ],
    },
    {
        "id": "science",
        "title": "Scientific method",
        "experts": [
            _expert("Theorist", "First principles, governing equations and physical plausibility."),
            _expert("Experimentalist", "Measurement, uncertainty, controls and what the data can show."),
            _expert("Statistician", "Sample sizes, bias, confounders and strength of evidence."),
            _expert("Reviewer", "Prior literature, novelty and how a critical referee would respond."),
        ],
    },
    {
        "id": "product",
        "title": "Product and strategy",
        "experts": [
            _expert("User Advocate", "What the user actually needs and how they will experience it."),
            _expert("Engineer", "Feasibility, effort, technical risk and sequencing."),
            _expert("Strategist", "Market, cost-benefit, opportunity cost and timing."),
        ],
    },
)
