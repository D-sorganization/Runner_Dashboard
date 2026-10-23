"""Compare staff role GitHub App permission needs against the live installation (#2038)."""

from __future__ import annotations

import asyncio
import logging
import os
import time
from collections.abc import Mapping
from typing import Any

from github_app_auth import (
    compare_installation_permissions,
    fetch_installation_permissions,
    github_app_auth_configured,
    normalize_permissions_for_mint,
)
from staff.github_run_env import github_permissions_for_role
from staff.roles import RoleSpec

log = logging.getLogger("dashboard.staff.github_app_preflight")

_PREFLIGHT_INTERVAL_SECONDS = float(os.environ.get("STAFF_GITHUB_APP_PREFLIGHT_INTERVAL_SECONDS", "900"))

_cache: dict[str, Any] = {
    "checked_at": None,
    "ok": True,
    "configured": False,
    "mismatches": [],
    "error": None,
}


def preflight_cache_snapshot() -> dict[str, Any]:
    """Last preflight result for summary and liveness surfaces."""
    return dict(_cache)


def preflight_github_app_for_roles(roles: Mapping[str, RoleSpec]) -> dict[str, Any]:
    """Compare every non-retired role's requested permissions to the installation grant.

    Post: never logs tokens; returns ``ok`` when GitHub App auth is off or every role matches.
    """
    if not github_app_auth_configured():
        return {"ok": True, "configured": False, "mismatches": [], "checked_at": None, "error": None}

    try:
        granted = fetch_installation_permissions()
    except Exception as exc:  # noqa: BLE001
        return {
            "ok": False,
            "configured": True,
            "mismatches": [],
            "checked_at": time.time(),
            "error": f"could not read installation permissions: {exc}",
        }

    mismatches: list[dict[str, Any]] = []
    for name, role in sorted(roles.items()):
        if role.retired:
            continue
        requested = normalize_permissions_for_mint(github_permissions_for_role(role))
        missing = compare_installation_permissions(requested, granted)
        if missing:
            mismatches.append({"role": name, "requested": requested, "missing": missing})

    return {
        "ok": not mismatches,
        "configured": True,
        "mismatches": mismatches,
        "granted": granted,
        "checked_at": time.time(),
        "error": None,
    }


def attention_items_from_preflight(report: dict[str, Any]) -> list[dict[str, Any]]:
    """Turn a preflight report into staff summary ``attention`` rows."""
    if report.get("ok"):
        return []
    if report.get("error"):
        return [
            {
                "id": "github-app-preflight-error",
                "role": "hub",
                "status": "blocked",
                "failure_class": "github_app_permissions",
                "error": report["error"],
                "remediation": "Verify GITHUB_APP_* credentials on the hub and GitHub App installation health.",
            }
        ]
    items: list[dict[str, Any]] = []
    for row in report.get("mismatches") or []:
        role = str(row.get("role") or "unknown")
        missing = row.get("missing") or []
        items.append(
            {
                "id": f"github-app-perms-{role}",
                "role": role,
                "status": "blocked",
                "failure_class": "github_app_permissions",
                "error": f"GitHub App installation lacks permissions for role {role}: {', '.join(missing)}",
                "remediation": (
                    "Grant the listed permissions on the GitHub App installation for D-sorganization, "
                    "or trim the role's requested scopes in github_permissions_for_role after verifying code paths."
                ),
            }
        )
    return items


def run_preflight_and_cache(roles: Mapping[str, RoleSpec]) -> dict[str, Any]:
    report = preflight_github_app_for_roles(roles)
    _cache.clear()
    _cache.update(report)
    if report.get("configured") and not report.get("ok"):
        log.warning(
            "staff github app preflight: %d role(s) request permissions the installation does not grant",
            len(report.get("mismatches") or []),
        )
    return report


async def periodic_github_app_preflight_loop(roles_loader: Any) -> None:
    """Hub background task: refresh installation permission mismatches before scheduled runs."""
    await asyncio.sleep(5)
    while True:
        try:
            roles = roles_loader()
            await asyncio.to_thread(run_preflight_and_cache, roles)
        except Exception as exc:  # noqa: BLE001
            log.warning("staff github app preflight loop failed: %s", exc)
        await asyncio.sleep(_PREFLIGHT_INTERVAL_SECONDS)
