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
_TOKEN_PATTERNS: dict[str, re.Pattern[str]] = {
    "github_pat": re.compile(r"\bghp_[A-Za-z0-9]{36,255}\b"),
    "github_oauth": re.compile(r"\bgho_[A-Za-z0-9]{36,255}\b"),
    "github_user_to_server": re.compile(r"\bghu_[A-Za-z0-9]{36,255}\b"),
    "github_server_to_server": re.compile(r"\bghs_[A-Za-z0-9]{36,255}\b"),
    "github_refresh": re.compile(r"\bghr_[A-Za-z0-9]{36,255}\b"),
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
