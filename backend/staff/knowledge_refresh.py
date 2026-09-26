"""Staff Console knowledge pack refresh daemon and utilities (Issue #1479).

Builds each manifest in $STAFF_RM_ROOT/staff/knowledge/*.yml from the local checkouts
under ~/Repositories into ~/.local/share/runner-dashboard/knowledge/<id>.sqlite.
Rebuilds only when KnowledgePack.is_stale() is True.
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Mapping
from pathlib import Path

from knowledge_pack import (
    KnowledgePack,
    PackInfo,
    PackManifest,
    build_pack,
    load_manifest,
    manifest_from_dict,
)
from staff.workspace import find_repo_checkout, repos_roots
from staff.workspace import rm_root as get_default_rm_root

log = logging.getLogger("dashboard.staff.knowledge_refresh")

DEFAULT_KNOWLEDGE_DIR = Path("~/.local/share/runner-dashboard/knowledge").expanduser()


def get_knowledge_dir() -> Path:
    """Return configured knowledge pack storage directory."""
    configured = os.environ.get("STAFF_KNOWLEDGE_DIR")
    if configured:
        return Path(configured).expanduser()
    return DEFAULT_KNOWLEDGE_DIR


def find_knowledge_manifests(rm_root: Path | None = None) -> list[Path]:
    """Locate all YAML manifests in staff/knowledge/*.yml."""
    root = rm_root
    if root is None:
        configured = os.environ.get("STAFF_RM_ROOT")
        if configured:
            root = Path(configured).expanduser()
        else:
            root = get_default_rm_root()
    if root is None or not root.is_dir():
        log.warning("No Repository_Management root found for knowledge manifests")
        return []

    knowledge_dir = root / "staff" / "knowledge"
    if not knowledge_dir.is_dir():
        return []

    manifests: list[Path] = []
    for ext in ("*.yml", "*.yaml"):
        manifests.extend(knowledge_dir.glob(ext))
    return sorted(manifests)


def resolve_manifest_roots(
    manifest: PackManifest,
    explicit_roots: Mapping[str, Path] | None = None,
) -> dict[str, Path] | None:
    """Resolve checkout directory for each repository required by manifest.

    Precondition: manifest is a PackManifest.
    Postcondition: returns dict of existing directories, or None if any repository is missing.
    """
    roots: dict[str, Path] = {}
    for repo in manifest.repos:
        if explicit_roots and repo in explicit_roots:
            p = Path(explicit_roots[repo]).resolve()
            if p.is_dir():
                roots[repo] = p
                continue
            return None

        found = find_repo_checkout(repo)
        if found is not None and found.is_dir():
            roots[repo] = found.resolve()
            continue

        for r_root in repos_roots():
            cand = r_root / repo
            if cand.is_dir():
                roots[repo] = cand.resolve()
                break
        if repo not in roots:
            log.warning("Repository '%s' for pack '%s' not found", repo, manifest.id)
            return None

    return roots


def pack_is_stale(pack: KnowledgePack) -> bool:
    """Check whether an already-open pack's source manifest reports it stale.

    Precondition: pack is an open KnowledgePack.
    Postcondition: returns False (never raises) if the manifest is missing,
    unparseable, or its repository checkouts cannot be resolved locally.
    """
    try:
        meta = pack._meta()
        raw_manifest = meta.get("manifest")
        if not raw_manifest:
            return False
        manifest = manifest_from_dict(json.loads(raw_manifest))
        roots = resolve_manifest_roots(manifest)
        if roots is None:
            return False
        return pack.is_stale(roots)
    except Exception as exc:  # noqa: BLE001
        log.warning("Staleness check failed for pack: %s", exc)
        return False


def refresh_pack(
    manifest_path: Path,
    roots: Mapping[str, Path] | None = None,
    knowledge_dir: Path | None = None,
    force: bool = False,
) -> tuple[bool, PackInfo | None]:
    """Rebuild a pack if missing, corrupt, or stale.

    Returns (rebuilt, pack_info).
    """
    manifest_path = Path(manifest_path)
    if not manifest_path.is_file():
        log.warning("Manifest not found: %s", manifest_path)
        return False, None

    manifest = load_manifest(manifest_path)
    resolved_roots = resolve_manifest_roots(manifest, roots)
    if resolved_roots is None:
        log.warning("Skipping pack '%s': missing repository checkouts", manifest.id)
        return False, None

    target_dir = knowledge_dir or get_knowledge_dir()
    target_dir.mkdir(parents=True, exist_ok=True)
    out_file = target_dir / f"{manifest.id}.sqlite"

    if out_file.is_file() and not force:
        try:
            pack = KnowledgePack.open(out_file)
            if not pack.is_stale(resolved_roots):
                log.info("Knowledge pack '%s' is up-to-date; skipping build", manifest.id)
                return False, pack.info()
            log.info("Knowledge pack '%s' is stale; rebuilding", manifest.id)
        except Exception as exc:  # noqa: BLE001
            log.warning("Failed opening knowledge pack '%s' (%s); rebuilding", manifest.id, exc)

    log.info("Building knowledge pack '%s' -> %s", manifest.id, out_file)
    info = build_pack(manifest, resolved_roots, out_file)
    log.info(
        "Built knowledge pack '%s': %d passages from %d files",
        info.pack_id,
        info.passages,
        info.files,
    )
    return True, info


def refresh_all_packs(
    rm_root: Path | None = None,
    knowledge_dir: Path | None = None,
    force: bool = False,
) -> list[tuple[str, bool, PackInfo | None]]:
    """Refresh all discovered knowledge manifests."""
    manifests = find_knowledge_manifests(rm_root)
    results: list[tuple[str, bool, PackInfo | None]] = []
    for m in manifests:
        rebuilt, info = refresh_pack(m, knowledge_dir=knowledge_dir, force=force)
        pack_id = info.pack_id if info else m.stem
        results.append((pack_id, rebuilt, info))
    return results


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    log.info("Starting knowledge pack refresh sweep")
    results = refresh_all_packs()
    for pack_id, rebuilt, info in results:
        status = "rebuilt" if rebuilt else ("unchanged" if info else "failed")
        log.info("Pack %s: %s", pack_id, status)
    log.info("Knowledge pack refresh sweep complete (%d packs)", len(results))


if __name__ == "__main__":
    main()
