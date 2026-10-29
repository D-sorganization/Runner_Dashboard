"""Unit tests for the private-topology scanner used by the repo-wide CI guard."""

from __future__ import annotations

from private_topology_guard import TopologyFinding, format_findings, scan_text, scan_text_line


def test_blocked_hostname_detected() -> None:
    findings = scan_text_line("ssh deskcomputer.local")
    assert any(f.rule == "blocked_hostname" and f.detail == "deskcomputer" for f in findings)


def test_allowed_example_ts_net_host_is_clean() -> None:
    assert not scan_text_line("https://hub-a.fleet-example.ts.net:8321/api")


def test_blocked_ts_net_host_detected() -> None:
    findings = scan_text_line("https://hub-a.oglaptop.ts.net:8321")
    assert any(f.rule == "ts_net_host" for f in findings)


def test_documented_example_cgnat_ip_is_clean() -> None:
    assert not scan_text_line("endpoint: 100.64.0.5")


def test_live_cgnat_ip_detected() -> None:
    findings = scan_text_line("endpoint: 100.95.177.68")
    assert any(f.rule == "cgnat_ip" and f.detail == "100.95.177.68" for f in findings)


def test_personal_gmail_address_detected() -> None:
    findings = scan_text_line("notify: dieterolson@gmail.com")
    assert any(f.rule == "personal_email" for f in findings)


def test_pragma_allowlist_comment_suppresses_findings() -> None:
    assert not scan_text_line("controltower  # pragma: allowlist private-topology")


def test_scan_text_reports_line_numbers() -> None:
    text = "clean line\nssh brick\nanother clean line"
    results = scan_text(text)
    assert results == [(2, TopologyFinding("blocked_hostname", "brick"))]


def test_format_findings_renders_path_line_and_rule() -> None:
    rendered = format_findings([("backend/host_volume.py", 6, TopologyFinding("blocked_hostname", "controltower"))])
    assert rendered == "backend/host_volume.py:6 -> blocked_hostname (controltower)"
