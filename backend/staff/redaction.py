"""Secret, token, and network address redaction hook for staff conversations (SC-B2, Issue #1305).

Ensures sensitive secrets and internal network topology do not leak into
persisted message history, transcripts, or logs.
"""

from __future__ import annotations

import re

# GitHub personal access tokens, OAuth tokens, and fine-grained tokens
_GITHUB_TOKEN_RE = re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9_]{35,254}|github_pat_[A-Za-z0-9_]{22,254})\b")

# AWS Access Key IDs
_AWS_KEY_RE = re.compile(r"\b(?:AKIA|ASIA|AROA)[0-9A-Z]{16}\b")

# AWS Secret Access Key patterns (e.g., aws_secret_access_key = '...')
_AWS_SECRET_RE = re.compile(
    r"(?i)\b(?:aws_secret_access_key|aws_secret_key|secret_key)\s*[:=]\s*['\"]?([A-Za-z0-9/+=]{40})['\"]?"
)

# LLM API keys: OpenAI sk-..., Anthropic sk-ant-...
_API_KEY_RE = re.compile(r"\b(?:sk-ant-[A-Za-z0-9_-]{20,}|sk-[A-Za-z0-9_-]{20,})\b")

# Private Keys (PEM format)
_PRIVATE_KEY_RE = re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----")

# Bearer authorization tokens
_BEARER_TOKEN_RE = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9_\-\.]{20,}\b")

# Private LAN IPv4 addresses (10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16, 100.64.0.0/10)
# Matches valid 0-255 octets within the specified private CIDR ranges
_OCTET = r"(?:25[0-5]|2[0-4][0-9]|1[0-9]{2}|[1-9]?[0-9])"

_PRIVATE_IP_RE = re.compile(
    r"\b(?:"
    # 10.0.0.0/8
    rf"10\.(?:{_OCTET}\.){{2}}{_OCTET}"
    r"|"
    # 172.16.0.0/12 -> 172.16.0.0 - 172.31.255.255
    rf"172\.(?:1[6-9]|2[0-9]|3[0-1])\.{_OCTET}\.{_OCTET}"
    r"|"
    # 192.168.0.0/16
    rf"192\.168\.{_OCTET}\.{_OCTET}"
    r"|"
    # 100.64.0.0/10 (CGNAT) -> 100.64.0.0 - 100.127.255.255
    rf"100\.(?:6[4-9]|[7-9][0-9]|1[0-1][0-9]|12[0-7])\.{_OCTET}\.{_OCTET}"
    r")\b"
)


def redact_sensitive_content(text: str) -> str:
    """Apply redaction hooks to text before writing to persistent storage.

    Redacts:
    - GitHub tokens (ghp_, gho_, ghu_, ghs_, ghr_, github_pat_) -> [REDACTED_GITHUB_TOKEN]
    - AWS access keys (AKIA/ASIA/AROA) -> [REDACTED_AWS_KEY]
    - AWS secret keys -> [REDACTED_AWS_SECRET_KEY]
    - API keys (sk-..., sk-ant-...) -> [REDACTED_API_KEY]
    - PEM private keys -> [REDACTED_PRIVATE_KEY]
    - Bearer tokens -> Bearer [REDACTED_TOKEN]
    - RFC 1918 & RFC 6598 private LAN IPv4 addresses -> [REDACTED_IP]
    """
    if not text:
        return text

    # Redact private keys first since they may span multiple lines
    s = _PRIVATE_KEY_RE.sub("[REDACTED_PRIVATE_KEY]", text)

    # Redact AWS secrets before generic API keys
    def _sub_aws_secret(match: re.Match[str]) -> str:
        full = match.group(0)
        secret = match.group(1)
        return full.replace(secret, "[REDACTED_AWS_SECRET_KEY]")

    s = _AWS_SECRET_RE.sub(_sub_aws_secret, s)

    # Redact GitHub tokens
    s = _GITHUB_TOKEN_RE.sub("[REDACTED_GITHUB_TOKEN]", s)

    # Redact AWS access keys
    s = _AWS_KEY_RE.sub("[REDACTED_AWS_KEY]", s)

    # Redact API keys
    s = _API_KEY_RE.sub("[REDACTED_API_KEY]", s)

    # Redact Bearer tokens
    s = _BEARER_TOKEN_RE.sub("Bearer [REDACTED_TOKEN]", s)

    # Redact Private LAN IPv4 addresses
    s = _PRIVATE_IP_RE.sub("[REDACTED_IP]", s)

    return s
