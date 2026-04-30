"""Documentation freshness invariants (issue #394).

These tests guard against the four drifts called out in issue #394:

1. ``VERSION`` major version must be marked Supported in
   ``SECURITY.md``'s "Supported Versions" table.
2. Claims in ``CHANGELOG.md`` about CSP / security-header directives must
   match the actual ``backend/middleware.py`` content. If the changelog
   says "removed X" the directive must NOT appear in the middleware; if
   it says "added X" the directive MUST appear.
3. ``CLAUDE.md`` and ``CONTRIBUTING.md`` must not regress to the old
   "no build step / no JSX / no TypeScript" claims; the production
   frontend is a Vite + React + TypeScript SPA.

The tests use only ``pathlib`` and ``re`` so they run in any environment
that can collect pytest. They are picked up by the standard
``pytest tests/`` invocation in CI.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

VERSION_PATH = REPO_ROOT / "VERSION"
SECURITY_PATH = REPO_ROOT / "SECURITY.md"
CHANGELOG_PATH = REPO_ROOT / "CHANGELOG.md"
MIDDLEWARE_PATH = REPO_ROOT / "backend" / "middleware.py"
CLAUDE_PATH = REPO_ROOT / "CLAUDE.md"
CONTRIBUTING_PATH = REPO_ROOT / "CONTRIBUTING.md"

# Phrases that must NOT appear in CLAUDE.md / CONTRIBUTING.md after the
# Vite migration. Case-insensitive.
FORBIDDEN_DOC_PHRASES = (
    "no build step",
    "no jsx",
    "no typescript",
)

# Directives whose presence/absence in middleware.py we cross-check
# against changelog claims. Add new ones here as the CSP evolves.
CSP_DIRECTIVES = (
    "strict-dynamic",
    "unsafe-inline",
    "unsafe-eval",
    "default-src",
    "script-src",
    "style-src",
    "img-src",
    "connect-src",
    "font-src",
    "frame-ancestors",
)


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _parse_version_major(version_text: str) -> int:
    """Extract the MAJOR component from a VERSION file's contents."""
    for raw_line in version_text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        match = re.match(r"^(\d+)\.\d+\.\d+", line)
        if match:
            return int(match.group(1))
    raise AssertionError(f"VERSION file does not contain a semver MAJOR.MINOR.PATCH line:\n{version_text}")


def _supported_majors(security_text: str) -> set[int]:
    """Return MAJOR versions marked supported in SECURITY.md."""
    supported: set[int] = set()
    for raw_line in security_text.splitlines():
        # Look for table rows like "| 4.x | :white_check_mark: | ..."
        if "|" not in raw_line:
            continue
        if ":white_check_mark:" not in raw_line:
            continue
        match = re.search(r"\|\s*(\d+)\.x\s*\|", raw_line)
        if match:
            supported.add(int(match.group(1)))
    return supported


def test_version_major_is_marked_supported_in_security_md() -> None:
    """Drift #1: VERSION's major must be Supported in SECURITY.md."""
    major = _parse_version_major(_read(VERSION_PATH))
    supported = _supported_majors(_read(SECURITY_PATH))
    assert supported, "SECURITY.md has no rows marked :white_check_mark: in the Supported Versions table"
    assert major in supported, (
        f"VERSION major is {major}.x but SECURITY.md only marks {sorted(supported)} "
        "as supported. Update SECURITY.md to track the current major."
    )


def _changelog_csp_claims(changelog_text: str) -> list[tuple[str, str]]:
    """Return (verb, directive) tuples for CSP/header claims in CHANGELOG.

    A claim is a line that mentions one of CSP_DIRECTIVES alongside a
    verb in {removed, added, dropped, restored, kept, retained}. Claims
    inside the same paragraph as the word "inaccurate" or "corrected"
    are treated as historical/self-correcting and skipped.
    """
    claims: list[tuple[str, str]] = []
    # Split into paragraphs to keep "inaccurate" / "corrected" context local.
    for paragraph in re.split(r"\n\s*\n", changelog_text):
        if re.search(r"\b(inaccurate|corrected)\b", paragraph, re.IGNORECASE):
            continue
        for line in paragraph.splitlines():
            for directive in CSP_DIRECTIVES:
                if directive not in line.lower():
                    continue
                # Verb detection — keep only assertive present-state verbs.
                verb_match = re.search(
                    r"\b(removed|dropped|added|restored|kept|retained)\b",
                    line,
                    re.IGNORECASE,
                )
                if not verb_match:
                    continue
                claims.append((verb_match.group(1).lower(), directive))
    return claims


def test_changelog_csp_claims_match_middleware() -> None:
    """Drift #2: CHANGELOG claims about CSP must match middleware.py."""
    middleware = _read(MIDDLEWARE_PATH).lower()
    claims = _changelog_csp_claims(_read(CHANGELOG_PATH))

    removal_verbs = {"removed", "dropped"}
    presence_verbs = {"added", "restored", "kept", "retained"}

    for verb, directive in claims:
        if verb in removal_verbs:
            assert directive not in middleware, (
                f"CHANGELOG.md says '{verb} {directive}' but the directive "
                f"still appears in {MIDDLEWARE_PATH.relative_to(REPO_ROOT)}. "
                "Either fix the changelog or actually remove the directive."
            )
        elif verb in presence_verbs:
            assert directive in middleware, (
                f"CHANGELOG.md says '{verb} {directive}' but the directive "
                f"is NOT present in {MIDDLEWARE_PATH.relative_to(REPO_ROOT)}. "
                "Either fix the changelog or actually add the directive."
            )


def test_claude_md_does_not_claim_no_build_step_or_no_jsx() -> None:
    """Drift #3: CLAUDE.md must reflect the Vite/TS reality."""
    text = _read(CLAUDE_PATH).lower()
    for phrase in FORBIDDEN_DOC_PHRASES:
        assert phrase not in text, (
            f"CLAUDE.md contains forbidden phrase '{phrase}'. The frontend "
            "is a Vite + React + TypeScript SPA — update the docs."
        )


def test_contributing_md_does_not_claim_no_build_step_or_no_jsx() -> None:
    """Drift #4: CONTRIBUTING.md must reflect the Vite/TS reality."""
    text = _read(CONTRIBUTING_PATH).lower()
    for phrase in FORBIDDEN_DOC_PHRASES:
        assert phrase not in text, (
            f"CONTRIBUTING.md contains forbidden phrase '{phrase}'. The "
            "frontend is a Vite + React + TypeScript SPA — update the docs."
        )


def test_frontend_actually_uses_vite_and_typescript() -> None:
    """Sanity check: the truth that backs the doc claims above."""
    assert (REPO_ROOT / "vite.config.ts").is_file() or (REPO_ROOT / "vite.config.js").is_file(), (
        "Expected a vite.config.{ts,js} at repo root"
    )
    assert (REPO_ROOT / "tsconfig.json").is_file(), "Expected tsconfig.json at repo root"
    assert (REPO_ROOT / "frontend" / "src" / "main.tsx").is_file(), "Expected frontend/src/main.tsx as the Vite entry"
