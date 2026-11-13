"""Metric and log hygiene for Private Mode PM-3 (#3253).

Prometheus label allowlisting, optional route templating (``RD_METRICS_ROUTE_TEMPLATES``),
level-aware log filtering, and traceback scrubbing at SENSITIVE classification.

The ``ClassificationLevel`` enum here is a minimal stand-in until PM-0 (#3250) lands;
callers should import only from this module (LoD).
"""

from __future__ import annotations

import contextvars
import logging
import os
import re
import sys
from collections.abc import Mapping
from enum import IntEnum
from typing import Any

_TRUTHY = frozenset({"1", "true", "yes", "on"})
METRICS_ROUTE_TEMPLATES_ENV = "RD_METRICS_ROUTE_TEMPLATES"
MAX_LABEL_VALUE_LEN = 120

# Single allowlist table (DRY): metric name -> permitted label names.
METRIC_LABEL_ALLOWLIST: dict[str, frozenset[str]] = {
    "dashboard_http_requests_total": frozenset({"method", "endpoint", "status_code"}),
    "dashboard_http_request_duration_seconds": frozenset({"method", "endpoint"}),
    "dashboard_github_api_calls_total": frozenset({"method", "endpoint"}),
    "dashboard_github_api_duration_seconds": frozenset({"method", "endpoint"}),
    "dashboard_cache_hits_total": frozenset({"cache"}),
    "dashboard_cache_misses_total": frozenset({"cache"}),
    "dashboard_health_checks_total": frozenset({"status", "github_api"}),
    "dashboard_health_check_duration_seconds": frozenset({"status", "github_api"}),
    "autoscaler_scaling_actions_total": frozenset({"action", "reason"}),
    "autoscaler_systemd_action_errors_total": frozenset({"action", "unit"}),
    "autoscaler_busy_check_duration_seconds": frozenset({"strategy"}),
}


class ClassificationLevel(IntEnum):
    """Ordered classification levels (PM-0 will own the canonical enum)."""

    PUBLIC = 0
    INTERNAL = 1
    SENSITIVE = 2


classification_level_ctx: contextvars.ContextVar[ClassificationLevel] = contextvars.ContextVar(
    "classification_level",
    default=ClassificationLevel.PUBLIC,
)

_GH_ISSUES = re.compile(r"/issues/\d+")
_GH_PULLS = re.compile(r"/pulls/\d+")
_NUM_SEGMENT = re.compile(r"/\d+")
_HEX_ID_SEGMENT = re.compile(r"/[0-9a-f]{8,}")
_REPO_SLUG_PAIR = re.compile(r"/repos/([^/]+)/([^/]+)")
_SCRUB_FORMATTED_EXC_RE = re.compile(
    r"(?i)(?:repo|issue|token|secret|password|path)=[^\s)\"']+|"
    r"/repos/[^/\s]+/[^/\s]+|"
    r"issues/\d+"
)


def metrics_route_templates_enabled(env: Mapping[str, str] | None = None) -> bool:
    """Return whether fleet-wide metric route templating is enabled."""
    env_map = os.environ if env is None else env
    return env_map.get(METRICS_ROUTE_TEMPLATES_ENV, "").strip().lower() in _TRUTHY


def _bound_len(value: str) -> str:
    if len(value) <= MAX_LABEL_VALUE_LEN:
        return value
    return value[: MAX_LABEL_VALUE_LEN - 3] + "..."


def template_http_metric_endpoint(
    route_template: str | None,
    raw_path: str,
    *,
    enabled: bool,
) -> str:
    """Resolve the HTTP ``endpoint`` label for Prometheus (DbC: raw_path non-empty)."""
    assert raw_path, "raw_path must be non-empty"
    if not enabled:
        return route_template if route_template else raw_path
    if route_template:
        return route_template
    templated = _HEX_ID_SEGMENT.sub("/{id}", raw_path)
    templated = _NUM_SEGMENT.sub("/{id}", templated)
    return _bound_len(templated)


def template_github_api_endpoint(endpoint: str, *, enabled: bool) -> str:
    """Normalize a GitHub REST path for metric labels (DbC: endpoint starts with /)."""
    assert endpoint.startswith("/"), "endpoint must be an absolute path"
    if not enabled:
        return endpoint
    path = endpoint
    path = _REPO_SLUG_PAIR.sub("/repos/{owner}/{repo}", path)
    path = _GH_ISSUES.sub("/issues/{issue_number}", path)
    path = _GH_PULLS.sub("/pulls/{pull_number}", path)
    path = _NUM_SEGMENT.sub("/{id}", path)
    return _bound_len(path)


def sanitize_metric_labels(
    metric_name: str,
    labels: Mapping[str, str],
    *,
    templates_enabled: bool,
) -> dict[str, str]:
    """Filter to the allowlist and scrub high-cardinality label values."""
    assert metric_name, "metric_name must be non-empty"
    allowed = METRIC_LABEL_ALLOWLIST.get(metric_name)
    if allowed is None:
        return {}
    out: dict[str, str] = {}
    for key, value in labels.items():
        if key not in allowed:
            continue
        if key == "endpoint" and templates_enabled:
            if value.startswith("/repos/"):
                value = template_github_api_endpoint(value, enabled=True)
            else:
                value = template_http_metric_endpoint(None, value, enabled=True)
        elif templates_enabled and key in {"repo", "issue", "issue_number", "repository"}:
            continue
        out[key] = _bound_len(value)
    return out


def format_exception_for_log(
    exc_type: type[BaseException] | None,
    exc_value: BaseException | None,
    exc_tb: Any,
) -> str:
    """Format an exception for logs, scrubbing values at SENSITIVE level."""
    if exc_type is None or exc_value is None:
        return ""
    level = classification_level_ctx.get()
    if level < ClassificationLevel.SENSITIVE:
        return "".join(logging.Formatter().formatException((exc_type, exc_value, exc_tb)))
    redacted = RuntimeError("[REDACTED_EXCEPTION]")
    formatted = "".join(logging.Formatter().formatException((exc_type, redacted, exc_tb)))
    return _SCRUB_FORMATTED_EXC_RE.sub("[REDACTED]", formatted)


class ClassificationLevelLogFilter(logging.Filter):
    """Drop sub-WARNING records while classification is SENSITIVE."""

    def filter(self, record: logging.LogRecord) -> bool:
        level = classification_level_ctx.get()
        if level >= ClassificationLevel.SENSITIVE and record.levelno < logging.WARNING:
            return False
        return True


def log_exception_scrubbed(logger: logging.Logger, message: str) -> None:
    """Log the active exception without leaking sensitive argument values."""
    exc_info = sys.exc_info()
    if exc_info[0] is None:
        logger.error(message)
        return
    level = classification_level_ctx.get()
    if level >= ClassificationLevel.SENSITIVE:
        logger.error("%s: [REDACTED_EXCEPTION]", message)
        return
    logger.exception(message)


def install_observability_hygiene(root_logger: logging.Logger | None = None) -> None:
    """Attach PM-3 log filters to the root logger (safe when flag is off)."""
    root = root_logger or logging.getLogger()
    filt = ClassificationLevelLogFilter()
    if not any(isinstance(f, ClassificationLevelLogFilter) for f in root.filters):
        root.addFilter(filt)
    for handler in root.handlers:
        if not any(isinstance(f, ClassificationLevelLogFilter) for f in handler.filters):
            handler.addFilter(filt)
