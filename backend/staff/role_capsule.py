"""Persona role capsule store, Barbara mirror, fallback epoch+1 ingest, and SENSITIVE projection (#3295 S5, #3296).

Contract (DbC):
- RD is canonical. Each role capsule is capped at ``MAX_CAPSULE_BYTES`` (8 KB) and secret-linted fail-closed.
- Every ``save_capsule`` or ``ingest_fallback_capsule`` enforces monotonic ``epoch`` fencing and mirrors
  ``emit_shared(capsule)`` to ``capsules/<role>.json`` (via an injected Barbara mirror writer or local mirror dir).
- ``SENSITIVE`` capsules emit a strict metadata-only projection through ``emit_shared()`` (no open-item text,
  URLs, or cursor values leave the canonical store).
"""

from __future__ import annotations

import json
import math
import os
import re
import sys
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from datetime import date
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from staff.redaction import redact_sensitive_content  # noqa: E402

from scripts import rd_barb_notify as _notify  # noqa: E402

MAX_CAPSULE_BYTES = 8 * 1024
VALID_CLASSIFICATIONS = frozenset({"PUBLIC", "INTERNAL", "SENSITIVE"})
VALID_SOURCES = frozenset({"rd", "grok-fallback"})
_CONFIDENTIAL_RE = re.compile(r"security[-_ ]confidential", re.IGNORECASE)

MirrorWriter = Callable[[str, dict[str, Any]], None]


class CapsuleError(RuntimeError):
    """A capsule violated a size, schema, or secret-redaction invariant; nothing was written."""


class CapsuleStaleEpochError(CapsuleError):
    """A capsule write or fallback ingest carried a non-monotonic (stale) epoch."""


def default_capsules_dir() -> Path:
    from staff.rd_barb_memory import rd_barb_state_dir  # noqa: PLC0415

    return rd_barb_state_dir() / "capsules"


def barbara_mirror_relpath(role: str) -> str:
    return f"capsules/{_notify.normalize_role(role)}.json"


def _validate_items(items: Sequence[Mapping[str, Any]] | None) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for raw in items or []:
        if not isinstance(raw, Mapping):
            raise ValueError("each open_item must be a mapping")
        item_id = str(raw.get("id") or "").strip()
        title = str(raw.get("title") or "").strip()
        if not item_id or not title:
            raise ValueError("each open_item requires non-empty 'id' and 'title'")
        entry = {"id": item_id, "title": title}
        if raw.get("url"):
            entry["url"] = str(raw["url"]).strip()
        out.append(entry)
    return out


def _validate_cursors(cursors: Mapping[str, Any] | None) -> dict[str, str]:
    if cursors is None:
        return {}
    if not isinstance(cursors, Mapping):
        raise ValueError("cursors must be a mapping")
    return {str(k).strip(): str(v) for k, v in cursors.items() if str(k).strip()}


def _validate_slots(slots: Sequence[str] | None) -> list[str]:
    return [str(s).strip() for s in (slots or []) if str(s).strip()]


def _lint_serialized(raw_json: str) -> None:
    encoded = raw_json.encode("utf-8")
    if len(encoded) > MAX_CAPSULE_BYTES:
        raise CapsuleError(f"capsule size {len(encoded)} bytes exceeds 8 KB ({MAX_CAPSULE_BYTES} bytes) cap")
    if _CONFIDENTIAL_RE.search(raw_json) or redact_sensitive_content(raw_json) != raw_json:
        raise CapsuleError("capsule refused: payload contains a secret, private token, or confidential marker")


def emit_shared(capsule: Mapping[str, Any]) -> dict[str, Any]:
    """Return the mirror-safe projection of ``capsule``; ``SENSITIVE`` roles get metadata-only (#3295 S5)."""
    role = _notify.normalize_role(str(capsule.get("role") or "rd-barb"))
    classification = str(capsule.get("classification") or "INTERNAL").upper()
    if classification not in VALID_CLASSIFICATIONS:
        raise ValueError(f"classification must be one of {sorted(VALID_CLASSIFICATIONS)}")
    epoch = int(capsule.get("epoch") or 1)
    if epoch < 1:
        raise ValueError("epoch must be >= 1")
    open_items = list(capsule.get("open_items") or [])
    cursors = dict(capsule.get("cursors") or {})
    next_slots = _validate_slots(capsule.get("next_slots"))
    base: dict[str, Any] = {
        "role": role,
        "classification": classification,
        "epoch": epoch,
        "source": str(capsule.get("source") or "rd"),
        "last_scorecard_date": capsule.get("last_scorecard_date"),
        "next_slots": next_slots,
        "updated_at": float(capsule.get("updated_at") or 0.0),
    }
    if classification == "SENSITIVE":
        return {
            **base,
            "open_item_count": len(open_items),
            "cursor_keys": sorted(str(k) for k in cursors),
            "projection": "metadata_only",
        }
    return {
        **base,
        "open_items": open_items,
        "cursors": cursors,
        "projection": "full",
    }


class RoleCapsuleStore:
    """Canonical RD capsule store with atomic writes, 8 KB cap, and Barbara mirror projection."""

    def __init__(self, root_dir: Path | None = None, *, mirror_writer: MirrorWriter | None = None) -> None:
        self._dir = default_capsules_dir() if root_dir is None else root_dir
        self._mirror_writer = mirror_writer
        self._lock = threading.Lock()

    def load_capsule(self, role: str) -> dict[str, Any] | None:
        norm = _notify.normalize_role(role)
        path = self._dir / f"{norm}.json"
        with self._lock:
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except FileNotFoundError:
                return None
            except (OSError, ValueError) as exc:
                raise CapsuleError(f"cannot read capsule for {norm}: {exc}") from exc
        return data if isinstance(data, dict) else None

    def save_capsule(
        self,
        role: str,
        *,
        epoch: int,
        classification: str = "INTERNAL",
        last_scorecard_date: str | None = None,
        open_items: Sequence[Mapping[str, Any]] | None = None,
        cursors: Mapping[str, Any] | None = None,
        next_slots: Sequence[str] | None = None,
        source: str = "rd",
        now: float | None = None,
    ) -> dict[str, Any]:
        norm = _notify.normalize_role(role)
        ep = int(epoch)
        if ep < 1:
            raise ValueError("epoch must be >= 1")
        cls = str(classification or "INTERNAL").upper()
        if cls not in VALID_CLASSIFICATIONS:
            raise ValueError(f"classification must be one of {sorted(VALID_CLASSIFICATIONS)}")
        if source not in VALID_SOURCES:
            raise ValueError(f"source must be one of {sorted(VALID_SOURCES)}")
        if last_scorecard_date is not None:
            try:
                date.fromisoformat(str(last_scorecard_date))
            except ValueError as exc:
                raise ValueError(f"last_scorecard_date must be YYYY-MM-DD: {last_scorecard_date!r}") from exc
        clock = time.time() if now is None else float(now)
        if not math.isfinite(clock) or clock < 0:
            raise ValueError("now must be a non-negative finite timestamp")

        capsule: dict[str, Any] = {
            "role": norm,
            "epoch": ep,
            "classification": cls,
            "source": source,
            "last_scorecard_date": last_scorecard_date,
            "open_items": _validate_items(open_items),
            "cursors": _validate_cursors(cursors),
            "next_slots": _validate_slots(next_slots),
            "updated_at": clock,
        }
        raw_json = json.dumps(capsule, sort_keys=True)
        _lint_serialized(raw_json)
        shared = emit_shared(capsule)
        _lint_serialized(json.dumps(shared, sort_keys=True))

        with self._lock:
            existing = self._read_unlocked(norm)
            if existing is not None and ep < int(existing.get("epoch") or 1):
                raise CapsuleStaleEpochError(
                    f"stale capsule epoch {ep} for {norm} (current epoch is {existing.get('epoch')})"
                )
            self._write_atomic(self._dir / f"{norm}.json", raw_json)
            self._mirror(norm, shared)
        return capsule

    def ingest_fallback_capsule(
        self, role: str, payload: Mapping[str, Any], *, now: float | None = None
    ) -> dict[str, Any]:
        """Ingest a Grok-fallback capsule on RD resume; requires epoch >= stored_epoch + 1 (#3295 S5)."""
        norm = _notify.normalize_role(role)
        incoming_role = _notify.normalize_role(str(payload.get("role") or norm))
        if incoming_role != norm:
            raise ValueError(f"fallback capsule role {incoming_role!r} does not match target {norm!r}")
        incoming_epoch = int(payload.get("epoch") or 0)
        with self._lock:
            existing = self._read_unlocked(norm)
            min_epoch = (int(existing.get("epoch") or 0) + 1) if existing is not None else 1
            if incoming_epoch < min_epoch:
                raise CapsuleStaleEpochError(
                    f"fallback capsule epoch {incoming_epoch} for {norm} must be >= {min_epoch}"
                )
            cls = str(payload.get("classification") or (existing or {}).get("classification") or "INTERNAL")
        return self.save_capsule(
            norm,
            epoch=incoming_epoch,
            classification=cls,
            last_scorecard_date=payload.get("last_scorecard_date"),
            open_items=payload.get("open_items"),
            cursors=payload.get("cursors"),
            next_slots=payload.get("next_slots"),
            source="grok-fallback",
            now=now,
        )

    def _read_unlocked(self, norm: str) -> dict[str, Any] | None:
        path = self._dir / f"{norm}.json"
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        return data if isinstance(data, dict) else None

    def _mirror(self, norm: str, shared: dict[str, Any]) -> None:
        rel = barbara_mirror_relpath(norm)
        if self._mirror_writer is not None:
            self._mirror_writer(rel, shared)
            return
        mirror_path = self._dir / "barbara-mirror" / rel
        self._write_atomic(mirror_path, json.dumps(shared, sort_keys=True))

    @staticmethod
    def _write_atomic(path: Path, content: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(f".{os.getpid()}.tmp")
        tmp.write_text(content, encoding="utf-8")
        tmp.replace(path)
