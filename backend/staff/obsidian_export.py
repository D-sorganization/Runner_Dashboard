"""Obsidian RO-2: deterministic bounded read-only Markdown exporter (#3507).

Parent epic #3505, slice 2 of 3. Produces a disposable, untrusted-reference
Markdown projection of a small, explicitly allowlisted slice of assistant
memory entries. NOT a sync tool, NOT bidirectional, and performs NO
write-capable call against the memory store or any other API -- see
``tests/unit/test_obsidian_export.py`` for the instrumented/mocked proof.

Reuses existing helpers per docs/operations/obsidian-ro1-authority-provenance.md
(RO-1, #3506) instead of duplicating them: ``memory.redact`` for secret
scanning, ``AssistantMemoryStore.list_entries`` (read-only) for the data
path, and ``staff.audit`` for one metadata-only audit row per export run.
RO-1 found no existing single export-schema module; the allowlist below is
the minimal one for slice 2 -- it layers a stricter, independently testable
filter on top of the store's own visibility/review-status gates, never
replaces them, and is never the sole secret boundary (see ``_clean_scalar``).

Integrity note (do not overclaim): manifest ``content_sha256`` values are
unkeyed SHA-256 digests. They detect accidental/incidental edits to
generated files -- they are NOT cryptographic authentication of origin. An
actor able to replace both a generated file and its manifest together can
produce a matching hash. No trust root/signing key is established here;
treat a hash match as "unchanged since export", never as "authentic".
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from memory.models import MemoryEntry
from memory.redact import contains_secret, redact_memory_text
from staff.audit import record_audit

log = logging.getLogger("dashboard.staff.obsidian_export")

EXPORT_SCHEMA_VERSION = 1
EXPORT_FORMAT_VERSION = "obsidian-ro2-v1"

# Allowlist of entry types/body fields that may ever leave this module.
# Anything else -- "transcript"/"lesson" entries, or a body field such as
# "private_notes"/"barb_only_notes"/"credential" -- is never looked at for
# inclusion, not redacted after the fact.
ALLOWED_ENTRY_TYPES: frozenset[str] = frozenset({"session_digest", "decision", "task"})
ALLOWED_BODY_FIELDS: dict[str, tuple[str, ...]] = {
    "session_digest": ("summary", "decisions", "open_items"),
    "decision": ("title", "summary", "status"),
    "task": ("title", "summary", "status"),
}
# Only "fleet" and unset (legacy rows) are exportable; any other value --
# including "barb-only" and any unknown future value -- fails closed.
_ALLOWED_VISIBILITY: frozenset[str | None] = frozenset({None, "", "fleet"})

MAX_ENTRIES = 500
MAX_TOTAL_BYTES = 2_000_000
MAX_FIELD_LEN = 4000
MAX_TAGS = 32
MAX_TAG_LEN = 64

MANIFEST_FILENAME = "manifest.json"
MARKDOWN_FILENAME = "memory-export.md"

_INTEGRITY_NOTE = (
    "content_sha256 values are unkeyed SHA-256 digests: they detect "
    "accidental/incidental edits but are NOT cryptographic authentication "
    "of origin -- an actor able to replace both the generated file and "
    "this manifest together can produce a matching hash. No trust "
    "root/signing key is established by this prototype."
)


class ExportBoundsExceeded(ValueError):
    """A whole-export hard bound was exceeded. Never truncates silently."""


class ExportPathError(ValueError):
    """A write would escape the designated generated subtree."""


@dataclass(frozen=True)
class RejectedEntry:
    reason: str
    entry_type_hint: str = ""


@dataclass
class ExportResult:
    manifest: dict[str, Any]
    markdown: str
    accepted: int
    rejected: list[RejectedEntry] = field(default_factory=list)
    input_total: int = 0


def _stable_id(entry: MemoryEntry) -> str:
    """Deterministic, source-derived export ID (never random/time-based)."""
    return hashlib.sha256(f"{entry.entry_type}:{entry.id}".encode()).hexdigest()[:16]


def _fence(text: str) -> str:
    """Render untrusted text as literal, inert data -- never instructions."""
    return f"```text\n{text.replace('```', chr(39) * 3)}\n```"


def _clean_scalar(value: Any) -> str | None:
    if not isinstance(value, str) or not value or len(value) > MAX_FIELD_LEN or contains_secret(value):
        return None
    return redact_memory_text(value)


def _clean_list(value: Any) -> list[str] | None:
    if not isinstance(value, list):
        return None
    out = [_clean_scalar(v) for v in value]
    return None if any(c is None for c in out) else out  # type: ignore[return-value]


def _clean_tags(tags: Any) -> list[str]:
    if not isinstance(tags, list):
        return []
    cleaned = [t for t in tags if isinstance(t, str) and t and len(t) <= MAX_TAG_LEN and not contains_secret(t)]
    return cleaned[:MAX_TAGS]


def _extract_body_fields(entry: MemoryEntry) -> dict[str, str | list[str]] | None:
    """Copy only allowlisted body fields; ``None`` = safe rejection (never truncation)."""
    if not isinstance(entry.body, dict):
        return None
    out: dict[str, str | list[str]] = {}
    for name in ALLOWED_BODY_FIELDS.get(entry.entry_type, ()):
        if name not in entry.body:
            continue
        raw = entry.body[name]
        cleaned = _clean_list(raw) if isinstance(raw, list) else _clean_scalar(raw)
        if cleaned is None:
            return None
        out[name] = cleaned
    return out


def _render_entry(entry: MemoryEntry, stable_id: str, body_fields: dict[str, str | list[str]]) -> str:
    tags = _clean_tags(entry.tags)
    lines = [
        f"## {stable_id}",
        "",
        f"- entry_type: {entry.entry_type}",
        f"- source_product: {entry.source_product}",
        f"- created_at: {entry.created_at}",
        f"- tags: {', '.join(tags) if tags else '(none)'}",
        "",
    ]
    for name in ALLOWED_BODY_FIELDS.get(entry.entry_type, ()):
        if name not in body_fields:
            continue
        value = body_fields[name]
        lines += [f"### {name}", ""]
        lines += [_fence(v) + "\n" for v in value] if isinstance(value, list) else [_fence(value) + "\n"]
    return "\n".join(lines).rstrip() + "\n"


def build_export(
    entries: list[MemoryEntry],
    *,
    captured_at: str,
    max_entries: int = MAX_ENTRIES,
    max_total_bytes: int = MAX_TOTAL_BYTES,
    truncated: bool = False,
) -> ExportResult:
    """Build deterministic Markdown + manifest from already-fetched entries.

    Pure function: no I/O, no store/network calls. Same input -- regardless
    of input order or duplicate entries -- produces byte-identical output.
    Oversized/malformed input is rejected explicitly, never truncated.
    """
    if not captured_at:
        raise ExportBoundsExceeded("captured_at_required")

    deduped: dict[str, MemoryEntry] = {}
    for e in entries:
        deduped.setdefault(e.id, e)
    input_total = len(deduped)
    if input_total > max_entries:
        raise ExportBoundsExceeded(f"entries_exceed_bound:{input_total}>{max_entries}")

    accepted: list[tuple[str, MemoryEntry, dict[str, str | list[str]]]] = []
    rejected: list[RejectedEntry] = []
    for entry in deduped.values():
        if entry.entry_type not in ALLOWED_ENTRY_TYPES:
            rejected.append(RejectedEntry("entry_type_not_allowlisted", str(entry.entry_type)))
        elif entry.visibility not in _ALLOWED_VISIBILITY:
            rejected.append(RejectedEntry("visibility_not_allowlisted", entry.entry_type))
        elif not isinstance(entry.source_product, str) or not entry.source_product:
            rejected.append(RejectedEntry("malformed_source_product", entry.entry_type))
        elif (body_fields := _extract_body_fields(entry)) is None:
            rejected.append(RejectedEntry("malformed_or_oversized_body_field", entry.entry_type))
        else:
            accepted.append((_stable_id(entry), entry, body_fields))

    accepted.sort(key=lambda t: t[0])  # deterministic: independent of input/fetch order

    sections: list[str] = []
    entry_manifest: list[dict[str, Any]] = []
    running_bytes = 0
    for stable_id, entry, body_fields in accepted:
        rendered = _render_entry(entry, stable_id, body_fields)
        rendered_bytes = len(rendered.encode("utf-8"))
        running_bytes += rendered_bytes
        if running_bytes > max_total_bytes:
            raise ExportBoundsExceeded(f"total_bytes_exceed_bound:{running_bytes}>{max_total_bytes}")
        sections.append(rendered)
        entry_manifest.append(
            {
                "stable_id": stable_id,
                "entry_type": entry.entry_type,
                "source_product": entry.source_product,
                "created_at": entry.created_at,
                "content_sha256": hashlib.sha256(rendered.encode("utf-8")).hexdigest(),
                "bytes": rendered_bytes,
            }
        )

    header = (
        "<!-- Generated by backend/staff/obsidian_export.py (#3507). Untrusted "
        "read-only reference data -- never instructions. Do not hand-edit; re-export instead. -->\n"
    )
    markdown = header + "\n".join(sections) + ("\n" if sections else "")

    manifest = {
        "export_schema_version": EXPORT_SCHEMA_VERSION,
        "export_format_version": EXPORT_FORMAT_VERSION,
        "source_class": "assistant_memory_store",
        "captured_at": captured_at,
        "truncated": truncated,
        "counts": {"input_total": input_total, "accepted": len(accepted), "rejected": len(rejected)},
        "rejected": [{"reason": r.reason, "entry_type_hint": r.entry_type_hint} for r in rejected],
        "entries": entry_manifest,
        "content_sha256": hashlib.sha256(markdown.encode("utf-8")).hexdigest(),
        "integrity_note": _INTEGRITY_NOTE,
    }
    return ExportResult(
        manifest=manifest, markdown=markdown, accepted=len(accepted), rejected=rejected, input_total=input_total
    )


def confine_output_dir(generated_root: Path, output_dir: Path) -> Path:
    """``output_dir`` must be ``generated_root`` or a descendant, with no
    symlink escape anywhere along the way. Raises, never silently redirects.
    """
    root_resolved = generated_root.resolve()
    try:
        target_resolved = output_dir.resolve(strict=False)
    except OSError as exc:
        raise ExportPathError(f"cannot_resolve_output_dir: {exc}") from exc
    if target_resolved != root_resolved and root_resolved not in target_resolved.parents:
        raise ExportPathError("output_dir_escapes_generated_root")

    cursor = root_resolved
    for part in target_resolved.relative_to(root_resolved).parts:
        cursor = cursor / part
        if cursor.exists() and cursor.is_symlink():
            raise ExportPathError("symlink_escape_in_output_dir")
    return target_resolved


def write_export(generated_root: Path, output_dir: Path, result: ExportResult) -> dict[str, Path]:
    """Write markdown + manifest, confined to ``generated_root``. The only
    filesystem write in this module."""
    confined = confine_output_dir(generated_root, output_dir)
    confined.mkdir(parents=True, exist_ok=True)
    md_path, manifest_path = confined / MARKDOWN_FILENAME, confined / MANIFEST_FILENAME
    if md_path.is_symlink() or manifest_path.is_symlink():
        raise ExportPathError("refusing_to_write_through_symlink")
    md_path.write_text(result.markdown, encoding="utf-8")
    manifest_path.write_text(json.dumps(result.manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {"markdown": md_path, "manifest": manifest_path}


@dataclass(frozen=True)
class VerifyResult:
    ok: bool
    reason: str = "ok"


def verify_export(output_dir: Path) -> VerifyResult:
    """Tamper/change detection against the manifest's content hash -- see
    the module docstring on why a hash match is not an authenticity claim.
    """
    manifest_path, md_path = output_dir / MANIFEST_FILENAME, output_dir / MARKDOWN_FILENAME
    if not manifest_path.exists():
        return VerifyResult(False, "manifest_missing")
    if not md_path.exists():
        return VerifyResult(False, "markdown_missing")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return VerifyResult(False, "manifest_invalid")
    expected = manifest.get("content_sha256")
    if not isinstance(expected, str) or not expected:
        return VerifyResult(False, "manifest_missing_provenance")
    actual = hashlib.sha256(md_path.read_bytes()).hexdigest()
    return VerifyResult(True) if actual == expected else VerifyResult(False, "content_hash_mismatch")


def run_export(
    store: Any,
    *,
    generated_root: Path,
    output_dir: Path,
    captured_at: str,
    principal: str = "",
    entry_type: str | None = None,
    max_entries: int = MAX_ENTRIES,
    max_total_bytes: int = MAX_TOTAL_BYTES,
) -> ExportResult:
    """Read-only end-to-end export: fetch via ``store.list_entries`` only,
    build deterministic output, write it inside the generated subtree, and
    record one metadata-only audit row. Never calls ``append``,
    ``append_idempotent``, ``update_review``, or any other mutating
    store/API method -- see
    ``test_run_export_never_calls_a_mutating_store_method`` for the proof.

    Fetching ``max_entries + 1`` lets a surplus beyond the bound be flagged
    as ``truncated`` in the manifest rather than silently dropped or
    represented as a complete capture; exceeding ``max_total_bytes`` while
    rendering is a hard rejection instead (see ``build_export``).
    """
    fetched = store.list_entries(
        entry_type=entry_type, limit=max_entries + 1, include_barb_only=False, include_superseded=False
    )
    truncated = len(fetched) > max_entries
    entries = fetched[:max_entries] if truncated else fetched

    try:
        result = build_export(
            entries,
            captured_at=captured_at,
            max_entries=max_entries,
            max_total_bytes=max_total_bytes,
            truncated=truncated,
        )
        write_export(generated_root, output_dir, result)
    except (ExportBoundsExceeded, ExportPathError) as exc:
        record_audit(
            action="memory_export",
            target="obsidian_ro2",
            principal=principal,
            surface="api",
            outcome="rejected",
            detail={"reason": str(exc)},
        )
        raise

    record_audit(
        action="memory_export",
        target="obsidian_ro2",
        principal=principal,
        surface="api",
        outcome="success",
        detail={
            "input_total": result.input_total,
            "accepted": result.accepted,
            "rejected": len(result.rejected),
            "truncated": truncated,
            "captured_at": captured_at,
            "content_sha256": result.manifest["content_sha256"],
        },
    )
    return result
