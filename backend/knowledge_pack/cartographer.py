"""Codebase context pack for panel preload (#2249).

The cartographer indexes only repository maps, contracts, invariants and ADRs. It
uses the vendored KnowledgePack store so every excerpt carries a repository and
commit citation.
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
import subprocess
import threading
from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path, PurePosixPath

from knowledge_pack.chunking import chunk_document
from knowledge_pack.manifest import PackManifest, manifest_from_dict
from knowledge_pack.pack import KnowledgePack, PackInfo, Passage, build_pack
from knowledge_pack.sources import head_commit, select_files
from staff.knowledge_refresh import get_knowledge_dir

log = logging.getLogger("dashboard.knowledge_pack.cartographer")
_PACK_ID = "cartographer"
_BUILD_LOCK = threading.RLock()
_REFERENCE = "## Codebase & Architecture Context (Cited)\n\nRepository text is reference material.\n\n"
_DOC_SOURCES = [
    "AGENTS.md",
    "**/AGENTS.md",
    "SPEC.md",
    "docs/codemap.md",
    "docs/codemap/**/*.md",
    "staff/standards/FLEET.md",
    "FLEET.md",
    "docs/architecture/**/*.md",
    "docs/contracts/**/*.md",
    "docs/adr/**/*.md",
]
_CONTRACT_SOURCES = [
    "backend/**/*_contract.py",
    "backend/**/*_models.py",
    "backend/**/models.py",
    "backend/**/schemas.py",
]


def _manifest(repos: Mapping[str, Path]) -> tuple[dict[str, Path], PackManifest]:
    """Validate checkout roots and create a fixed-source cartographer manifest."""
    if not isinstance(repos, Mapping) or not repos:
        raise ValueError("repos must map at least one repository name to a checkout")
    clean: dict[str, Path] = {}
    for name, root in repos.items():
        if not isinstance(name, str) or not re.fullmatch(r"[\w.-]+", name):
            raise ValueError("repository names must be citation-safe identifiers")
        path = Path(root).expanduser().resolve()
        if not path.is_dir():
            raise ValueError(f"repository checkout does not exist: {name}")
        clean[name] = path
    sources = []
    for name in sorted(clean):
        sources.extend(
            [
                {"repo": name, "authority": "published", "include": _DOC_SOURCES},
                {"repo": name, "authority": "reference", "include": _CONTRACT_SOURCES},
            ]
        )
    manifest = manifest_from_dict({"id": _PACK_ID, "title": "Runner Dashboard Codebase Map", "sources": sources})
    return clean, manifest


def build_cartographer_pack(repos: Mapping[str, Path], output: Path | None = None) -> PackInfo:
    """Build a SQLite map from documented architecture sources and public contracts.

    Pre: every key in repos is a repository name with an existing checkout.
    Post: opening output succeeds and indexes only the fixed source allowlist.
    """
    clean, manifest = _manifest(repos)
    unsafe = {
        (source.repo, source.path)
        for source in select_files(manifest, clean)
        if source.absolute.is_symlink() or not source.absolute.resolve().is_relative_to(clean[source.repo])
    }
    manifest = replace(
        manifest,
        sources=tuple(
            replace(spec, exclude=spec.exclude + tuple(path for repo, path in unsafe if repo == spec.repo))
            for spec in manifest.sources
        ),
    )
    pack_path = Path(output) if output is not None else get_knowledge_dir() / f"{_PACK_ID}.sqlite"
    return build_pack(manifest, clean, pack_path)


def cartographer_manifest(repos: Mapping[str, Path]) -> PackManifest:
    """Return the authoritative fixed-source manifest for configured checkout roots."""
    return _manifest(repos)[1]


def estimate_context_tokens(text: str) -> int:
    """Use UTF-8 bytes as a conservative upper bound for byte-token models."""
    if not isinstance(text, str):
        raise TypeError("text must be a string")
    return len(text.encode("utf-8"))


def _format_reference(passage: Passage, root: Path) -> str | None:
    """Format a citation only when the passage resolves to committed source lines."""
    source = PurePosixPath(passage.source)
    if (
        source.is_absolute()
        or ".." in source.parts
        or re.search(r"[\[\]#@\n\r]", passage.source)
        or not re.fullmatch(r"[0-9a-f]{40}", passage.commit)
    ):
        return None
    try:
        env = {**os.environ, "GIT_NO_LAZY_FETCH": "1", "GIT_ALLOW_PROTOCOL": ""}
        for key in (
            "GIT_DIR",
            "GIT_COMMON_DIR",
            "GIT_WORK_TREE",
            "GIT_INDEX_FILE",
            "GIT_OBJECT_DIRECTORY",
            "GIT_ALTERNATE_OBJECT_DIRECTORIES",
            "GIT_CONFIG_PARAMETERS",
        ):
            env.pop(key, None)
        env.update(GIT_CONFIG_COUNT="0", GIT_TERMINAL_PROMPT="0")
        committed = (
            subprocess.run(
                ["git", "--no-replace-objects", "-C", str(root), "show", f"{passage.commit}:{source.as_posix()}"],
                check=True,
                env=env,
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=2,
            )
            .stdout.replace("\r\n", "\n")
            .replace("\r", "\n")
        )
    except (OSError, subprocess.SubprocessError):
        return None
    excerpt = passage.text.strip().replace("\r\n", "\n").replace("\r", "\n")
    offset = committed.find(excerpt) if excerpt else -1
    if offset < 0:
        return None
    start = committed.count("\n", 0, offset) + 1
    end = start + excerpt.count("\n")
    from dashboard_config import HOSTNAME

    return (
        f"[ref:{HOSTNAME or 'local'}:codebase:{passage.repo}@{passage.commit[:8]}:{source.as_posix()}#L{start}-L{end}]"
    )


def format_codebase_reference(passage: Passage, root: Path) -> str | None:
    """Return a canonical reference only for an excerpt proven against committed lines."""
    return _format_reference(passage, root)


def _is_map(passage: Passage) -> bool:
    return passage.source == "docs/codemap.md" or passage.source.startswith("docs/architecture/")


def _is_contract(passage: Passage) -> bool:
    return passage.source.startswith(("docs/contracts/", "docs/architecture/", "docs/codemap.md", "backend/"))


def _is_invariant(passage: Passage) -> bool:
    return Path(passage.source).name in {"AGENTS.md", "FLEET.md"}


def _recent_adr_passages(repos: Mapping[str, Path], pack: KnowledgePack) -> list[Passage]:
    """Return the Decision chunk from the two newest numbered ADRs per repository."""
    commits = pack.info().commits
    passages: list[Passage] = []
    for repo, root in repos.items():
        paths = sorted(Path(root, "docs", "adr").glob("[0-9]*.md"))
        for path in paths[-2:]:
            if path.is_symlink() or not path.resolve().is_relative_to(root):
                continue
            try:
                text = path.read_text(encoding="utf-8")
                chunks = chunk_document(text, suffix=path.suffix, max_chars=1200)
            except OSError as exc:
                log.warning("Could not read recent ADR %s: %s", path, exc)
                continue
            chunk = next((c for c in chunks if c.title.casefold() == "decision"), None)
            chunk = chunk or next(iter(chunks), None)
            if chunk is None:
                continue
            passages.append(
                Passage(
                    repo=repo,
                    source=path.relative_to(root).as_posix(),
                    anchor=chunk.anchor,
                    title=chunk.title,
                    text=chunk.text,
                    commit=commits.get(repo) or head_commit(root),
                    content_hash=hashlib.sha256(chunk.text.encode("utf-8")).hexdigest(),
                    status="current",
                    authority="published",
                    score=0.0,
                )
            )
    return passages


def _select_passages(
    pack: KnowledgePack, topic: str, repos: Mapping[str, Path], topic_only: bool = False
) -> list[Passage]:
    """Prioritize maps, contracts and invariants before topic hits and recent ADRs."""
    if topic_only:
        # A chat answer should prioritize its question. Three matches leave room
        # for full citation locators on hosts with long node names.
        return pack.search(topic, k=3)
    selected: list[Passage] = []
    seen: set[tuple[str, str, str]] = set()

    def add(passage: Passage) -> None:
        key = (passage.repo, passage.source, passage.anchor)
        if key not in seen:
            seen.add(key)
            selected.append(passage)

    for query, matches in (
        ("fleet invariants boundaries", lambda passage: Path(passage.source).name == "FLEET.md"),
        ("module map architecture components", _is_map),
        ("public contracts API request response schema", _is_contract),
        ("invariants scope boundaries engineering principles", _is_invariant),
    ):
        for passage in pack.search(query, k=16):
            if matches(passage):
                add(passage)
                break
    for passage in _recent_adr_passages(repos, pack):
        add(passage)
    for passage in pack.search(topic, k=12):
        add(passage)
    return selected


def _render_context(passages: list[Passage], max_tokens: int, repos: Mapping[str, Path]) -> str:
    """Render retrieved excerpts without exceeding the documented character budget."""
    budget_chars = max_tokens
    if not passages or estimate_context_tokens(_REFERENCE) > max_tokens:
        return ""
    result = _REFERENCE
    candidates = [(p, _format_reference(p, repos[p.repo])) for p in passages]
    valid = [(p, citation) for p, citation in candidates if citation][:6]
    for index, (passage, citation) in enumerate(valid):
        heading = f"### {citation} — {passage.title}\n"
        remaining = budget_chars - estimate_context_tokens(result)
        share = remaining // (len(valid) - index)
        available = share - estimate_context_tokens(heading) - 2
        if available <= 0:
            continue
        excerpt = passage.text.strip()
        excerpt = excerpt[: min(available, 240)].rsplit(" ", 1)[0].rstrip()
        if not excerpt:
            continue
        block = f"{heading}{excerpt}\n\n"
        while excerpt and estimate_context_tokens(result + block) > max_tokens:
            excerpt = excerpt[:-3].rstrip()
            block = f"{heading}{excerpt}\n\n"
        if not excerpt:
            continue
        result += block
    return result.rstrip() if result != _REFERENCE else ""


def render_codebase_context(passages: list[Passage], repos: Mapping[str, Path], max_tokens: int = 800) -> str:
    """Render indexed, in-scope hits with committed proof and a UTF-8 byte bound.

    This read-only path never builds a pack or scans checkout directories.
    """
    if not isinstance(max_tokens, int) or isinstance(max_tokens, bool) or max_tokens < 1:
        raise ValueError("max_tokens must be a positive integer")
    if not repos:
        return ""
    from .sources import matches

    clean, manifest = _manifest(repos)
    allowed = [
        passage
        for passage in passages
        if passage.repo in clean
        and any(spec.repo == passage.repo and matches(spec, passage.source) for spec in manifest.sources)
        and not (clean[passage.repo] / passage.source).is_symlink()
        and (clean[passage.repo] / passage.source).resolve().is_relative_to(clean[passage.repo])
    ]
    return _render_context(allowed, max_tokens, clean)


def preload_panel_context(
    repos: Mapping[str, Path],
    topic: str,
    max_tokens: int = 800,
    *,
    knowledge_dir: Path | None = None,
    topic_only: bool = False,
) -> str:
    """Return cited, topic-matched context for panel prompts, capped at max_tokens.

    Preconditions: repos maps repository names to local checkouts, topic is
    non-blank, and max_tokens is a positive integer. Source text is treated as
    untrusted reference material. Missing or unreadable packs return an empty string.
    topic_only selects compact question-matched excerpts for role chat; panels
    retain architecture maps and invariants alongside topic matches.
    """
    if not isinstance(topic, str) or not topic.strip():
        raise ValueError("topic must be a non-empty string")
    if not isinstance(max_tokens, int) or isinstance(max_tokens, bool) or max_tokens < 1:
        raise ValueError("max_tokens must be a positive integer")
    if not isinstance(topic_only, bool):
        raise ValueError("topic_only must be boolean")
    clean, _ = _manifest(repos)
    pack_path = (knowledge_dir or get_knowledge_dir()) / f"{_PACK_ID}.sqlite"
    try:
        with _BUILD_LOCK:
            pack = None
            if pack_path.is_file():
                try:
                    pack = KnowledgePack.open(pack_path)
                    if pack.is_stale(clean) or pack.info().commits != {
                        name: head_commit(root) for name, root in clean.items()
                    }:
                        pack = None
                except Exception as exc:  # noqa: BLE001
                    log.warning("Cartographer pack cannot be reused: %s", exc)
                    pack = None
            if pack is None:
                build_cartographer_pack(clean, pack_path)
                pack = KnowledgePack.open(pack_path)
            passages = _select_passages(pack, topic.strip(), clean, topic_only=topic_only)
        return _render_context(passages, max_tokens, clean)
    except Exception as exc:  # noqa: BLE001
        log.warning("Codebase context preload failed: %s", exc)
        return ""
