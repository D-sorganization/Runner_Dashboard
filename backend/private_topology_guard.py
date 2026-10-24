"""Scan tracked content for private fleet topology that must not ship in a public repo.

Used by ``tests/test_no_private_topology_in_repo.py``. Patterns are intentionally
narrow: block known private host labels, non-example MagicDNS names, personal
email shapes, and Tailscale CGNAT addresses outside the documented example
blocks.
"""

from __future__ import annotations

import ipaddress
import re
from dataclasses import dataclass
from typing import Iterable

# Hostnames that belonged to a private fleet deployment. Case-insensitive match
# on tokens bounded by non-alphanumeric characters.
_BLOCKED_HOSTNAME_TOKENS: frozenset[str] = frozenset(
    {
        "controltower",
        "deskcomputer",
        "oglaptop",
        "brick",
        "control-tower-runner-monitoring",
        "control-tower-runner",
        "control-tower-nvme",
    }
)

# Documented example MagicDNS zone shipped in *.example files and tests.
_ALLOWED_TS_NET_SUFFIXES: tuple[str, ...] = (
    ".fleet-example.ts.net",
    ".example.ts.net",
)

# RFC 6598 example blocks — only these CGNAT subnets may appear in tracked files.
_EXAMPLE_CGNAT_NETWORKS: tuple[ipaddress.IPv4Network, ...] = (
    ipaddress.ip_network("100.64.0.0/24"),
    ipaddress.ip_network("100.64.254.0/24"),
)

_BLOCKED_EMAIL_RE = re.compile(
    r"\b[a-z0-9._%+\-]*(?:gmail|googlemail)\.com\b",
    re.IGNORECASE,
)

# Full MagicDNS hostnames ending in .ts.net (labels may include dots).
_TS_NET_HOST_RE = re.compile(
    r"\b([a-z0-9][a-z0-9.\-]*)\.ts\.net\b",
    re.IGNORECASE,
)

_ALLOWED_BARE_TS_NET_ZONES: frozenset[str] = frozenset(
    {
        "fleet-example.ts.net",
        "example.ts.net",
    }
)

_HOSTNAME_TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9\-]*", re.IGNORECASE)

_CGNAT_NET = ipaddress.ip_network("100.64.0.0/10")

_IPV4_RE = re.compile(
    r"\b(?:(?:25[0-5]|2[0-4]\d|[01]?\d\d?)\.){3}"
    r"(?:25[0-5]|2[0-4]\d|[01]?\d\d?)\b"
)


@dataclass(frozen=True, slots=True)
class TopologyFinding:
    rule: str
    detail: str


def _cgnat_is_documented_example(addr: ipaddress.IPv4Address) -> bool:
    return any(addr in net for net in _EXAMPLE_CGNAT_NETWORKS)


def _scan_line_for_hostnames(line: str) -> list[TopologyFinding]:
    findings: list[TopologyFinding] = []
    for token in _HOSTNAME_TOKEN_RE.findall(line):
        if token.lower() in _BLOCKED_HOSTNAME_TOKENS:
            findings.append(TopologyFinding("blocked_hostname", token.lower()))
    return findings


def _scan_line_for_ts_net(line: str) -> list[TopologyFinding]:
    findings: list[TopologyFinding] = []
    for match in _TS_NET_HOST_RE.finditer(line):
        host = match.group(0).lower()
        if host in _ALLOWED_BARE_TS_NET_ZONES:
            continue
        if any(host.endswith(suffix) for suffix in _ALLOWED_TS_NET_SUFFIXES):
            continue
        findings.append(TopologyFinding("ts_net_host", host))
    return findings


def _scan_line_for_cgnat_ips(line: str) -> list[TopologyFinding]:
    findings: list[TopologyFinding] = []
    for match in _IPV4_RE.finditer(line):
        text = match.group(0)
        try:
            addr = ipaddress.IPv4Address(text)
        except ValueError:
            continue
        if addr not in _CGNAT_NET:
            continue
        if _cgnat_is_documented_example(addr):
            continue
        findings.append(TopologyFinding("cgnat_ip", text))
    return findings


def _scan_line_for_email(line: str) -> list[TopologyFinding]:
    findings: list[TopologyFinding] = []
    if _BLOCKED_EMAIL_RE.search(line):
        findings.append(TopologyFinding("personal_email", "gmail/googlemail"))
    return findings


def scan_text_line(line: str) -> list[TopologyFinding]:
    """Return findings for a single line of text."""

    if "pragma: allowlist private-topology" in line:
        return []
    findings: list[TopologyFinding] = []
    findings.extend(_scan_line_for_hostnames(line))
    findings.extend(_scan_line_for_ts_net(line))
    findings.extend(_scan_line_for_cgnat_ips(line))
    findings.extend(_scan_line_for_email(line))
    return findings


def scan_text(text: str) -> list[tuple[int, TopologyFinding]]:
    """Scan multi-line text; returns (line_number, finding) pairs."""

    out: list[tuple[int, TopologyFinding]] = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        for finding in scan_text_line(line):
            out.append((lineno, finding))
    return out


def format_findings(findings: Iterable[tuple[str, int, TopologyFinding]]) -> str:
    lines = [
        f"{path}:{lineno} -> {finding.rule} ({finding.detail})"
        for path, lineno, finding in findings
    ]
    return "\n  ".join(lines)
