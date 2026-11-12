"""Keep docs/releases.md honest about what the release pipeline actually does (#3724)."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DOC = ROOT / "docs" / "releases.md"
RELEASE_WORKFLOW = ROOT / ".github" / "workflows" / "release.yml"
DOCKER_WORKFLOW = ROOT / ".github" / "workflows" / "docker-build.yml"


def test_doc_exists() -> None:
    assert DOC.is_file(), "docs/releases.md must exist (#3724)"


def test_doc_describes_the_real_signed_artifact_flow() -> None:
    text = DOC.read_text(encoding="utf-8")
    release_text = RELEASE_WORKFLOW.read_text(encoding="utf-8")
    # The doc must point at the things release.yml actually produces.
    for marker in ("cosign", "SBOM", "attest", "VERSION"):
        assert marker.lower() in text.lower(), f"doc must mention {marker}"
        assert marker.lower() in release_text.lower(), f"release.yml must still produce {marker}"
    assert "ARTIFACT_BUILD.md" in text


def test_doc_does_not_overclaim_container_image_publication() -> None:
    """release.yml never pushes an image to a registry; the doc must say so, not imply otherwise."""
    release_text = RELEASE_WORKFLOW.read_text(encoding="utf-8").lower()
    for registry_marker in ("ghcr", "docker push", "registry"):
        assert registry_marker not in release_text, (
            f"release.yml now contains {registry_marker!r}; docs/releases.md's "
            "'no published container image yet' claim is stale and must be updated"
        )
    text = DOC.read_text(encoding="utf-8").lower()
    assert "not published" in text or "no registry" in text or "not yet published" in text


def test_doc_flags_the_multi_arch_gap_tracked_by_this_issue() -> None:
    text = DOC.read_text(encoding="utf-8")
    assert "#3724" in text
    assert "multi-arch" in text.lower() or "multi-platform" in text.lower()
    # docker-build.yml must still be the single-arch CI-only build this doc describes.
    docker_text = DOCKER_WORKFLOW.read_text(encoding="utf-8")
    assert "--platform" not in docker_text, (
        "docker-build.yml now sets --platform; docs/releases.md's single-arch "
        "claim is stale and must be updated"
    )
