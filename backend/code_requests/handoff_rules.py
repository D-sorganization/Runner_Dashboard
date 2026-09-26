"""Fleet handoff content rules, vendored for turnover-document validation (CR-4, #1285).

Vendored from Repository_Management ``shared_scripts/handoff_validator.py``
(``validate_handoff_content``, commit f430564c). The dashboard never imports a sibling
repo at runtime, so the content rules are copied, not called. Keep them in step with the
source: tests/code_requests/test_handoff_rules_drift.py compares the two whenever a
Repository_Management checkout sits next to this one.
"""

from __future__ import annotations

import re

REQUIRED_HEADINGS = (
    ("## Identity", re.compile(r"^##\s+Identity\s*$", re.MULTILINE)),
    ("## Objective and status", re.compile(r"^##\s+Objective\s+and\s+status\s*$", re.MULTILINE | re.IGNORECASE)),
    ("## Files and decisions", re.compile(r"^##\s+Files\s+and\s+decisions\s*$", re.MULTILINE | re.IGNORECASE)),
    ("## Validation", re.compile(r"^##\s+Validation\s*$", re.MULTILINE)),
    ("## Blockers and risks", re.compile(r"^##\s+Blockers\s+and\s+risks\s*$", re.MULTILINE | re.IGNORECASE)),
    ("## Next steps", re.compile(r"^##\s+Next\s+steps\s*$", re.MULTILINE | re.IGNORECASE)),
    ("## Change log", re.compile(r"^##\s+Change\s*log\s*$", re.MULTILINE | re.IGNORECASE)),
)

REQUIRED_IDENTITY_FIELDS = (
    "Repository",
    "Working directory",
    "Branch",
    "Baseline commit",
    "Implementation commit",
    "Pull request",
    "Governing issue/epic",
)

PLACEHOLDER_PATTERN = re.compile(r"<[^>\n]+>")
HEX_COMMIT_PATTERN = re.compile(r"^[0-9a-fA-F]{7,40}$")

SECRET_PATTERNS = (
    (re.compile(r"(?:ghp|gho|ghu|ghs|ghr)_[a-zA-Z0-9]{36}"), "GitHub token"),
    (re.compile(r"github_pat_[a-zA-Z0-9_]{82}"), "GitHub fine-grained PAT"),
    (re.compile(r"\bsk-[a-zA-Z0-9]{20,}\b"), "API secret key"),
    (re.compile(r"-----BEGIN (?:RSA |EC |DSA |OPENSSH )?PRIVATE KEY-----"), "Private cryptographic key"),
)


def handoff_findings(content: str) -> list[str]:
    """Return one message per rule the handoff document breaks (empty when valid)."""
    findings: list[str] = []
    lines = content.splitlines()

    if not content.startswith("# ") and not re.search(r"^#\s+.*Handoff", content, re.MULTILINE):
        findings.append("Document must start with '# Implementation Handoff'.")

    for heading_title, pattern in REQUIRED_HEADINGS:
        if not pattern.search(content):
            findings.append(f"Missing required section '{heading_title}'.")

    identity_match = re.search(r"^##\s+Identity\s*\n(.*?)(?=\n##|\Z)", content, re.DOTALL | re.MULTILINE)
    if identity_match:
        identity_text = identity_match.group(1)
        for field in REQUIRED_IDENTITY_FIELDS:
            if not re.search(rf"^-\s+{re.escape(field)}:", identity_text, re.MULTILINE | re.IGNORECASE):
                findings.append(f"Missing required Identity field '- {field}:'.")
        commit_match = re.search(r"^-\s+Implementation commit:\s*([^\n]+)", identity_text, re.MULTILINE | re.IGNORECASE)
        if commit_match:
            first_token = commit_match.group(1).strip().split()[0].strip("`'\",")
            if first_token != "SELF" and not HEX_COMMIT_PATTERN.match(first_token):
                findings.append(f"Implementation commit '{first_token}' is not 'SELF' or a valid SHA.")

    for idx, line in enumerate(lines, start=1):
        if line.strip().startswith("<!--") or line.strip().startswith("```"):
            continue
        for match in PLACEHOLDER_PATTERN.finditer(line):
            findings.append(f"Unedited template placeholder on line {idx}: {match.group(0)}")

    changelog_match = re.search(
        r"^##\s+Change\s*log\s*\n(.*?)(?=\n##|\Z)", content, re.DOTALL | re.MULTILINE | re.IGNORECASE
    )
    if changelog_match:
        bullets = [ln for ln in changelog_match.group(1).strip().splitlines() if ln.strip().startswith(("- ", "* "))]
        if not bullets:
            findings.append("Change log must contain at least one bullet entry.")

    for idx, line in enumerate(lines, start=1):
        for pattern, secret_type in SECRET_PATTERNS:
            if pattern.search(line):
                findings.append(f"Potential {secret_type} detected on line {idx}.")

    return findings
