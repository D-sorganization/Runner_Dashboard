"""rd-barb living guidelines doc (RD #3065): 8 KB cap, fail-closed hold/secret lint, ``#durable``-safe compaction."""

from __future__ import annotations

import logging
import os
import re
import threading
from collections.abc import Callable
from datetime import UTC, date, datetime
from pathlib import Path

from staff.rd_barb_policy import HoldLint, LockedPolicy
from staff.redaction import redact_sensitive_content
from staff.store import _config_dir

log = logging.getLogger("dashboard.staff.rd_barb_memory")

MAX_BYTES = 8 * 1024
MAX_ENTRY_CHARS = 1000
DURABLE_TAG = "#durable"
SECTIONS: tuple[str, ...] = ("Durable procedures", "Dated observations", "Unresolved questions")
PROCEDURES, OBSERVATIONS, QUESTIONS = SECTIONS
_COMPACTED_RE = re.compile(r"^<!-- compacted_at: (\S+) -->$")
_CONFIDENTIAL_RE = re.compile(r"security[-_ ]confidential", re.IGNORECASE)


class MemoryRefused(RuntimeError):
    """A write was refused (locked policy, secret, or cap); nothing was written."""


def rd_barb_state_dir() -> Path:
    """``$RUNNER_DASHBOARD_STATE_DIR/rd-barb`` (config dir fallback); every rd-barb state file lives here."""
    state = os.environ.get("RUNNER_DASHBOARD_STATE_DIR", "").strip()
    return (Path(state).expanduser() if state else _config_dir()) / "rd-barb"


def default_guidelines_path() -> Path:
    """``$RUNNER_DASHBOARD_STATE_DIR/rd-barb/guidelines.md`` (config dir fallback)."""
    return rd_barb_state_dir() / "guidelines.md"


def _check_entry(text: str) -> str:
    """DbC precondition: one non-empty line of bounded length."""
    entry = (text or "").strip()
    if not entry or "\n" in entry or "\r" in entry or len(entry) > MAX_ENTRY_CHARS:
        raise ValueError(f"entry must be one non-empty line of at most {MAX_ENTRY_CHARS} chars")
    return entry


class GuidelinesDoc:
    """Bounded guidelines doc. The locked policy is an injected, frozen, read-only input."""

    def __init__(self, path: Path, policy: LockedPolicy, clock: Callable[[], datetime] = lambda: datetime.now(UTC)):
        self.path, self.policy, self._clock = path, policy, clock
        self._lint = HoldLint(policy)
        self._lock = threading.Lock()
        self.compacted_at: str | None = None
        self._sections: dict[str, list[str]] = {name: [] for name in SECTIONS}
        self._load()

    def add_procedure(self, text: str) -> None:
        entry = _check_entry(text)
        self._append(PROCEDURES, entry if DURABLE_TAG in entry else f"{entry} {DURABLE_TAG}")

    def append_observation(self, text: str, *, source: str, review: str) -> None:
        entry, src, review_on = _check_entry(text), _check_entry(source), date.fromisoformat(review).isoformat()
        self._append(OBSERVATIONS, f"{self._clock().date().isoformat()} {entry} (source: {src}; review: {review_on})")

    def add_question(self, text: str) -> None:
        self._append(QUESTIONS, f"{self._clock().date().isoformat()} {_check_entry(text)}")

    def compact(self, target: int = MAX_BYTES) -> int:
        """Drop oldest non-durable observations until the doc fits ``target``; returns lines dropped."""
        with self._lock:
            dropped = self._compact(target)
            self._write()
            return dropped

    def render(self) -> str:
        marker = [f"<!-- compacted_at: {self.compacted_at} -->"] if self.compacted_at else []
        lines = ["# rd-barb guidelines", *marker]
        for name in SECTIONS:
            lines += ["", f"## {name}", *(f"- {item}" for item in self._sections[name])]
        return "\n".join(lines) + "\n"

    def size_bytes(self) -> int:
        return len(self.render().encode("utf-8"))

    def _refuse(self, section: str, reason: str) -> MemoryRefused:
        log.warning("rd-barb memory write refused (%s): %s", section, reason)
        return MemoryRefused(reason)

    def _append(self, section: str, line: str) -> None:
        reason = self._lint.violation(line)
        if not reason and (_CONFIDENTIAL_RE.search(line) or redact_sensitive_content(line) != line):
            reason = "entry carries a secret, address or confidential marker"
        if reason:
            raise self._refuse(section, reason)
        with self._lock:
            saved = {k: list(v) for k, v in self._sections.items()}, self.compacted_at
            self._sections[section].append(line)
            self._compact(MAX_BYTES)
            if self.size_bytes() > MAX_BYTES:
                self._sections, self.compacted_at = saved
                raise self._refuse(section, f"durable content would exceed {MAX_BYTES} bytes")
            self._write()

    def _compact(self, target: int) -> int:
        """O(N) streaming filter over observations, oldest first; ``#durable`` lines always kept."""
        excess, kept, dropped = self.size_bytes() - target, [], 0
        for line in self._sections[OBSERVATIONS]:
            if excess > 0 and DURABLE_TAG not in line:
                excess -= len(f"- {line}\n".encode())
                dropped += 1
            else:
                kept.append(line)
        if dropped:
            self._sections[OBSERVATIONS] = kept
            self.compacted_at = self._clock().isoformat().replace("+00:00", "Z")
        return dropped

    def _write(self) -> None:
        text = self.render()
        if len(text.encode("utf-8")) > MAX_BYTES:  # DbC postcondition
            raise MemoryRefused(f"postcondition failed: doc exceeds {MAX_BYTES} bytes")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        (tmp := self.path.with_suffix(".md.tmp")).write_text(text, encoding="utf-8")
        os.replace(tmp, self.path)

    def _load(self) -> None:
        current: str | None = None
        for raw in self.path.read_text(encoding="utf-8").splitlines() if self.path.exists() else []:
            if match := _COMPACTED_RE.match(raw):
                self.compacted_at = match.group(1)
            elif raw.startswith("## ") and raw[3:] in self._sections:
                current = raw[3:]
            elif current and raw.startswith("- "):
                self._sections[current].append(raw[2:])
