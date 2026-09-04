"""Lightweight in-repo grep for credential patterns.

Issue #396 / AC4: complement the gitleaks + detect-secrets gates in CI with a
fast pytest that runs in every developer's local test loop. The intent is *not*
to replace those scanners, but to give an immediate signal when an obvious
token shape (GitHub PAT, AWS key, OpenAI key, Anthropic key, private-key
header) is staged into a tracked file.

Design by contract:

* PRECONDITION: invoked from inside a git working tree (otherwise the test is
  skipped — it has nothing to scan).
* INVARIANT: the patterns below match only well-known credential prefixes that
  upstream issuers publish. We deliberately do NOT include high-entropy
  heuristics here; that is the job of detect-secrets / gitleaks.
* POSTCONDITION: zero matches across all tracked files (after path-allowlist
  filtering) implies the test passes; one or more matches fails the test with
  a redacted location list.

Allowlist conventions:

* Files explicitly in `_ALLOWED_PATHS` are skipped wholesale (test fixtures,
  example configs, lockfiles, the secrets baseline itself).
* Inline `# pragma: allowlist secret` (the detect-secrets convention) on the
  same line suppresses a single match.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent

# ---------------------------------------------------------------------------
# Patterns
# ---------------------------------------------------------------------------
# Each pattern targets a documented credential prefix. Keep this list narrow:
# false positives here block CI and erode trust in the gate.
# GitHub token bodies: the classic shape is 36+ alphanumerics, but App
# installation tokens now arrive as ``ghs_<app_id>_<base64url-JWT>``, so the
# body class must admit ``_``, ``.`` and ``-``. The body is anchored to start
# with an alphanumeric so a bare ``ghs_|`` inside a validation regex cannot
# match, and deliberately carries no trailing ``\b``: base64url payloads can
# end in ``-`` or ``_``, where a word boundary would fail.
_GITHUB_TOKEN_BODY = r"[A-Za-z0-9][A-Za-z0-9_.\-]{35,254}"

_TOKEN_PATTERNS: dict[str, re.Pattern[str]] = {
    "github_pat": re.compile(r"\bghp_" + _GITHUB_TOKEN_BODY),
    "github_oauth": re.compile(r"\bgho_" + _GITHUB_TOKEN_BODY),
    "github_user_to_server": re.compile(r"\bghu_" + _GITHUB_TOKEN_BODY),
    "github_server_to_server": re.compile(r"\bghs_" + _GITHUB_TOKEN_BODY),
    "github_refresh": re.compile(r"\bghr_" + _GITHUB_TOKEN_BODY),
    # AWS access key id — strict 20-char alnum after AKIA/ASIA prefix.
    "aws_access_key": re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"),
    # OpenAI keys (sk-...). Require enough length to dodge `sk-test` placeholders.
    "openai_key": re.compile(r"\bsk-[A-Za-z0-9]{40,}\b"),
    # Anthropic keys.
    "anthropic_key": re.compile(r"\bsk-ant-[A-Za-z0-9_-]{40,}\b"),
    # Slack bot/user tokens.
    "slack_token": re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b"),
    # PEM private-key headers — any of RSA / EC / OPENSSH / PGP.
    "private_key_block": re.compile(r"-----BEGIN (?:RSA |EC |DSA |OPENSSH |PGP )?PRIVATE KEY-----"),
}

# Files that may legitimately contain credential-shaped strings.
_ALLOWED_PATHS: frozenset[str] = frozenset(
    {
        # The detect-secrets baseline stores hashes of audited findings.
        ".secrets.baseline",
        # The gitleaks config itself documents allowlisted patterns.
        ".gitleaks.toml",
        # This very test file enumerates token regexes for matching.
        "tests/test_no_secrets_in_repo.py",
    }
)

# Path prefixes (directories) that are skipped wholesale.
_ALLOWED_PREFIXES: tuple[str, ...] = (
    "node_modules/",
    ".venv/",
    ".git/",
    "package-lock.json",
    "uv.lock",
)

# Inline marker following detect-secrets convention.
_INLINE_ALLOW = "pragma: allowlist secret"


def _git_tracked_files() -> list[Path]:
    """Return all git-tracked files relative to the repo root.

    Uses `git ls-files` rather than walking the filesystem so we never scan
    untracked artefacts (e.g. local `.venv/`, build outputs).
    """

    try:
        out = subprocess.check_output(
            ["git", "ls-files"],
            cwd=REPO_ROOT,
            text=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        return []
    return [REPO_ROOT / line for line in out.splitlines() if line.strip()]


def _is_allowed(rel_path: str) -> bool:
    if rel_path in _ALLOWED_PATHS:
        return True
    return any(rel_path.startswith(prefix) for prefix in _ALLOWED_PREFIXES)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_repo_has_tracked_files() -> None:
    """Sanity check: we are inside a populated git repo."""

    files = _git_tracked_files()
    if not files:
        pytest.skip("No git-tracked files (running outside a git checkout).")
    assert any(p.suffix == ".py" for p in files), "Expected at least one Python file in the repo; got none."


def test_no_known_credential_patterns_in_tracked_files() -> None:
    """Fail loud if any known credential prefix slips into a tracked file.

    See module docstring for allowlist semantics.
    """

    files = _git_tracked_files()
    if not files:
        pytest.skip("No git-tracked files (running outside a git checkout).")

    findings: list[tuple[str, str, int]] = []

    for path in files:
        rel = path.relative_to(REPO_ROOT).as_posix()
        if _is_allowed(rel):
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except (OSError, UnicodeDecodeError):
            # Binary or unreadable — skip; gitleaks handles binaries.
            continue

        for lineno, line in enumerate(text.splitlines(), start=1):
            if _INLINE_ALLOW in line:
                continue
            for name, pattern in _TOKEN_PATTERNS.items():
                if pattern.search(line):
                    findings.append((rel, name, lineno))

    assert not findings, (
        "Suspected credential-shaped strings found in tracked files. Either "
        "rotate the secret, replace it with a placeholder, or annotate the "
        "line with '# pragma: allowlist secret' if the value is genuinely "
        "fake. Findings (path, pattern, line):\n  " + "\n  ".join(f"{p}:{ln} -> {name}" for p, name, ln in findings)
    )


# ---------------------------------------------------------------------------
# Pattern-shape regression tests
#
# The patterns above must match the token formats this fleet actually issues,
# not just the classic 36-char alphanumeric shape. The dashboard's agents
# authenticate with GitHub App *installation* tokens minted by
# ~/.claude/mint-gh-token.ps1, which arrive as
# ``ghs_<app_id>_<base64url-JWT>`` -- underscores and dots inside the body.
# ``\bghs_[A-Za-z0-9]{36,255}\b`` stops dead at the first underscore, so the
# gate was blind to the one credential shape most likely to reach a file here.
# ---------------------------------------------------------------------------

# Structurally accurate, cryptographically meaningless: the signature segment is
# literal filler, and no such installation exists.
#
# Assembled from parts so that no single source line carries a complete
# token-shaped string. detect-secrets reports the line the match lands on, not
# the line the statement starts on, so a `pragma: allowlist secret` on the
# assignment does not cover a value split across continuations -- keeping the
# shape off every individual line is sturdier than annotating each one.
_FAKE_TOKEN_PREFIX = "ghs_"
_FAKE_APP_INSTALLATION_TOKEN = (
    _FAKE_TOKEN_PREFIX + "0000000_eyJhbGciOiJFUzI1NiIsInR5cCI6IkpXVCJ9."
    "eyJhdWQiOiJleGFtcGxlIiwiZXhwIjoxLCJpYXQiOjEsImlzcyI6ImdpdGh1YiJ9."
    "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
)


def test_github_pattern_matches_app_installation_token_format() -> None:
    """The ghs_ pattern must catch the JWT-shaped installation tokens we mint."""

    pattern = _TOKEN_PATTERNS["github_server_to_server"]
    assert pattern.search(_FAKE_APP_INSTALLATION_TOKEN), (
        "ghs_ pattern does not match the ghs_<app_id>_<jwt> installation-token "
        "format this fleet mints; a leaked agent token would pass the gate"
    )


def test_github_pattern_still_matches_classic_token_format() -> None:
    """Widening the body character class must not lose the original shape."""

    classic = "ghs_" + "a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6q7R8"  # pragma: allowlist secret
    assert _TOKEN_PATTERNS["github_server_to_server"].search(classic)


def test_github_patterns_do_not_match_prefix_mentions() -> None:
    """Bare prefixes in prose and validation regexes must not trip the gate.

    ``deploy/lib.sh`` and friends legitimately name these prefixes when
    validating operator-supplied tokens; matching those would make the gate
    unusable.
    """

    for benign in (
        'if [[ ! "$token" =~ ^(ghp_|github_pat_|ghs_|gho_)[A-Za-z0-9_]{20,}$ ]]; then',
        "#                    ghs_ (GitHub Apps installation), gho_ (OAuth)",
        "ghs_short",
    ):
        for name, pattern in _TOKEN_PATTERNS.items():
            assert not pattern.search(benign), f"{name} false-positives on: {benign}"
