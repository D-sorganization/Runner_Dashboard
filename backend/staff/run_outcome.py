"""Pure run-outcome classification shared by report cards and the shadow router (epic #2117).

Exactly one bucket per run:

* ``success``      status succeeded AND verification ``verified``.
* ``no_op``        neutral: succeeded with no change / not applicable / no verdict.
* ``quality_fail`` the agent or its output failed (incl. verification ``failed``).
* ``infra_fail``   rate limit, quota, auth, capacity, environment: not the model's fault.
* ``cancelled`` / ``in_flight`` never decided / not yet decided.

Quality scores use ``success`` vs ``quality_fail`` only; ``no_op``, ``cancelled`` and
``infra_fail`` are excluded from that denominator (infra is reported as availability).
"""

from __future__ import annotations

BUCKETS = ("success", "no_op", "quality_fail", "infra_fail", "cancelled", "in_flight")

# Failure classes that say nothing about the agent's quality (provider/host/auth/capacity).
INFRA_FAILURE_CLASSES = frozenset(
    {
        "auth_expired",
        "cli_missing",
        "cli_outdated",
        "config",
        "provider_capacity",
        "provider_error",
        "providers_exhausted",
        "quota_exhausted",
        "rate_limited",
        "lease_blocked",
        "orphaned",
        "workspace_error",
        "environment_error",
        "sandbox_error",
        "unkillable",
        "upstream_unavailable",
        "bridge_unavailable",
        "chat_capacity",
        "launch_failed",
        "dispatch_failed",
    }
)
_IN_FLIGHT = frozenset({"queued", "preparing", "running", "needs_input"})
_NEUTRAL_VERIFICATION = frozenset({"", "no_change", "not_applicable", "unverified"})


def classify_outcome(status: str, verification: str = "", failure_class: str = "") -> str:
    """Map one run to exactly one bucket in :data:`BUCKETS`."""
    status = (status or "").strip()
    verification = (verification or "").strip()
    failure_class = (failure_class or "").strip()
    if status in _IN_FLIGHT:
        return "in_flight"
    if status == "cancelled":
        return "cancelled"
    if status == "blocked":
        return "infra_fail"
    if status in ("succeeded", "completed"):
        if verification == "verified":
            return "success"
        if verification in _NEUTRAL_VERIFICATION:
            return "no_op"
        return "quality_fail"  # failed / unverified_output / anything else non-verified
    if failure_class in INFRA_FAILURE_CLASSES:
        return "infra_fail"
    return "quality_fail"
