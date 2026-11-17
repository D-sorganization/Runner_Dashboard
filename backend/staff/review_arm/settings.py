"""Default-off configuration, read only by the hub integration boundary."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from staff.review_arm.policy import DEFAULT_REPOS, HELD_PRS, Candidate

_FALSE = {"0", "false", "no", "off"}
DEFAULT_LEDGER = Path.home() / ".config" / "runner-dashboard" / "review-arm-ledger.sqlite3"


def _bounded_int(env: Mapping[str, str], key: str, default: int, low: int, high: int) -> int:
    value = int(env.get(key, str(default)))
    if not low <= value <= high:
        raise ValueError(f"{key} must be {low}..{high}")
    return value


@dataclass(frozen=True)
class Settings:
    enabled: bool = False
    dry_run: bool = True
    interval_seconds: int = 600
    repos: tuple[str, ...] = DEFAULT_REPOS
    held_prs: frozenset[tuple[str, int]] = HELD_PRS
    host: str = ""
    max_dispatch: int = 5
    stall_minutes: int = 60
    ledger_path: Path = DEFAULT_LEDGER
    box_state_dir: Path | None = None

    @classmethod
    def from_env(cls, env: Mapping[str, str], *, machine: str) -> Settings:
        """``RD_REVIEW_ARM_HOST`` is the enable flag: only that machine runs the loop.

        Missing/blank host disables it; ``RD_REVIEW_ARM_ENABLED=0`` is a kill switch
        that wins over the host. Dry-run stays on unless explicitly disabled.
        """
        killed = env.get("RD_REVIEW_ARM_ENABLED", "").strip().casefold() in _FALSE
        # Fail closed: only an explicit opt-out leaves dry-run; blank or misspelled stays dry.
        dry_run = env.get("RD_REVIEW_ARM_DRY_RUN", "").strip().casefold() not in _FALSE
        interval = _bounded_int(env, "RD_REVIEW_ARM_INTERVAL_MINUTES", 10, 1, 1440)
        repos = tuple(
            s.strip() for s in env.get("RD_REVIEW_ARM_REPOS", ",".join(DEFAULT_REPOS)).split(",") if s.strip()
        )
        if not repos or len(set(repos)) != len(repos):
            raise ValueError("review-arm repos must be nonempty and unique")
        for repo in repos:
            Candidate(repo, 1, "0" * 40)
        # Seeds cannot be removed by configuration. Additional holds use repo#PR.
        held = set(HELD_PRS)
        for entry in env.get("RD_REVIEW_ARM_HELD_PRS", "").split(","):
            if not entry.strip():
                continue
            repo, number = entry.strip().rsplit("#", 1)
            Candidate(repo, int(number), "0" * 40)
            held.add((repo, int(number)))
        # Deployment supplies the approved hub name through host-local configuration.
        # Missing/blank host authorization must never enable the service.
        host = env.get("RD_REVIEW_ARM_HOST", "").strip().casefold()
        box_dir = env.get("RD_REVIEW_ARM_BOX_STATE_DIR", "").strip()
        ledger = env.get("RD_REVIEW_ARM_LEDGER", "").strip()
        return cls(
            enabled=bool(host and not killed and machine.casefold() == host),
            dry_run=dry_run,
            interval_seconds=interval * 60,
            repos=repos,
            held_prs=frozenset(held),
            host=host,
            max_dispatch=_bounded_int(env, "RD_REVIEW_ARM_MAX_DISPATCH", 5, 0, 60),
            stall_minutes=_bounded_int(env, "RD_REVIEW_ARM_STALL_MINUTES", 60, 1, 1440),
            ledger_path=Path(ledger).expanduser() if ledger else DEFAULT_LEDGER,
            box_state_dir=Path(box_dir).expanduser() if box_dir else None,
        )
