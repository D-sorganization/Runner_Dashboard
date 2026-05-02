"""Structural tests for log shipping observability config (issue #418)."""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent


def test_vector_toml_exists() -> None:
    path = REPO_ROOT / "deploy" / "observability" / "vector.toml"
    assert path.exists(), "deploy/observability/vector.toml must exist"
    content = path.read_text(encoding="utf-8")
    assert "[sources." in content, "vector.toml must define at least one source"
    assert "[sinks.loki]" in content, "vector.toml must have a Loki sink"


def test_vector_toml_has_retention_transform() -> None:
    path = REPO_ROOT / "deploy" / "observability" / "vector.toml"
    content = path.read_text(encoding="utf-8")
    assert "7d" in content or "retention" in content, "vector.toml must reference 7-day retention"


def test_journald_retention_conf_exists() -> None:
    path = REPO_ROOT / "deploy" / "observability" / "journald-retention.conf"
    assert path.exists(), "deploy/observability/journald-retention.conf must exist"
    content = path.read_text(encoding="utf-8")
    assert "MaxRetentionSec" in content, "journald config must set MaxRetentionSec"
    assert "SystemMaxUse" in content, "journald config must set SystemMaxUse"


def test_journald_retention_has_1gb_limit() -> None:
    path = REPO_ROOT / "deploy" / "observability" / "journald-retention.conf"
    content = path.read_text(encoding="utf-8")
    assert re.search(r"SystemMaxUse\s*=\s*1G", content), "journald SystemMaxUse must be 1G"


def test_docker_compose_exists() -> None:
    path = REPO_ROOT / "docker" / "docker-compose.yml"
    assert path.exists(), "docker/docker-compose.yml must exist"
    content = path.read_text(encoding="utf-8")
    assert "vector" in content.lower(), "docker-compose.yml must include vector sidecar"
    assert "dashboard" in content.lower(), "docker-compose.yml must include dashboard service"


def test_docker_compose_has_log_rotation() -> None:
    path = REPO_ROOT / "docker" / "docker-compose.yml"
    content = path.read_text(encoding="utf-8")
    assert "max-file" in content, "docker-compose.yml must configure log rotation (max-file)"
    assert "max-size" in content, "docker-compose.yml must configure log rotation (max-size)"


def test_log_retention_runbook_exists() -> None:
    path = REPO_ROOT / "docs" / "runbooks" / "log-retention.md"
    assert path.exists(), "docs/runbooks/log-retention.md must exist"
    content = path.read_text(encoding="utf-8")
    assert "7 days" in content or "7d" in content, "runbook must document 7-day retention"
    assert "Vector" in content, "runbook must mention Vector"
    assert "Loki" in content, "runbook must mention Loki"
