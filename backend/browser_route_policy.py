"""Exact method/path allowlist for human browser ingress."""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class BrowserRoutePolicy:
    """Access required after Cloudflare Access validation."""

    access: str


_ID = r"[A-Za-z0-9_-]{1,128}"
_SHELL_PATHS = {
    "/",
    "/staff",
    "/work",
    "/fleet",
    "/settings",
    "/offline.html",
    "/manifest.webmanifest",
    "/icon.svg",
    "/favicon.ico",
    "/sw.js",
}
_SPA = re.compile(r"/(?:t|staff|work|fleet|settings)/" + _ID + r"\Z")
_STATIC = re.compile(r"/(?:assets|icons)/[A-Za-z0-9_./-]+\Z")
_OWNER_GETS = {
    "/api/runs",
    "/api/queue",
    "/api/queue/diagnose",
    "/api/runners",
    "/api/fleet/status",
    "/api/events",
    "/api/agents/providers",
    "/api/auth/me",
    "/api/stats",
    "/api/usage",
    "/api/v1/staff/roster",
    "/api/v1/staff/roles",
    "/api/v1/staff/providers",
    "/api/v1/staff/schedule",
    "/api/v1/staff/board",
    "/api/v1/staff/runs",
    "/api/v1/staff/inbox",
    "/api/v1/staff/threads",
    "/api/v1/staff/holds",
    "/api/v1/staff/summary",
    "/api/v1/staff/audit",
    "/api/v1/staff/outcomes",
    "/api/v1/staff/proposals",
    "/api/v1/staff/work-items",
    "/api/v1/staff/panels/presets",
}
_OWNER_GET_PATTERNS = tuple(
    re.compile(pattern)
    for pattern in (
        r"/api/v1/staff/threads/" + _ID + r"\Z",
        r"/api/v1/staff/threads/" + _ID + r"/(?:events|stream)\Z",
        r"/api/v1/staff/panels/" + _ID + r"\Z",
        r"/api/v1/staff/proposals/" + _ID + r"\Z",
        r"/api/v1/staff/runs/" + _ID + r"\Z",
        r"/api/v1/staff/runs/" + _ID + r"/(?:stream|references)\Z",
        r"/api/v1/staff/work-items/" + _ID + r"\Z",
        r"/api/v1/staff/groups/" + _ID + r"/cost-estimate\Z",
    )
)
_THREAD_POSTS = tuple(
    re.compile(pattern)
    for pattern in (
        r"/api/v1/staff/threads\Z",
        r"/api/v1/staff/panels\Z",
        r"/api/v1/staff/threads/" + _ID + r"/(?:messages|read)\Z",
        r"/api/v1/staff/threads/" + _ID + r"/runs/" + _ID + r"/answer\Z",
    )
)
_AUTH_ACCESS_GETS = {"/api/auth/github", "/api/auth/callback"}


def classify_browser_route(method: str, path: str) -> BrowserRoutePolicy | None:
    """Classify a route; callers deny None and enforce the returned access level."""
    if not isinstance(method, str) or not isinstance(path, str):
        return None
    method = method.upper()
    if not path.startswith("/") or "//" in path or "\\" in path or any(part in {".", ".."} for part in path.split("/")):
        return None

    if method == "GET" and (path in _SHELL_PATHS or _SPA.fullmatch(path) or _STATIC.fullmatch(path)):
        return BrowserRoutePolicy("shell")
    if method == "GET" and path in _AUTH_ACCESS_GETS:
        return BrowserRoutePolicy("access")
    if method == "POST" and path in {"/api/auth/logout", "/api/auth/refresh"}:
        return BrowserRoutePolicy("auth_mutation")
    if method == "POST" and path == "/api/auth/browser-csrf":
        return BrowserRoutePolicy("owner")
    if method == "GET" and path in _OWNER_GETS:
        return BrowserRoutePolicy("owner")
    if method == "GET" and any(pattern.fullmatch(path) for pattern in _OWNER_GET_PATTERNS):
        return BrowserRoutePolicy("owner")
    if method == "PATCH" and re.fullmatch(r"/api/v1/staff/threads/" + _ID, path):
        return BrowserRoutePolicy("messages")
    if method == "POST" and any(pattern.fullmatch(path) for pattern in _THREAD_POSTS):
        return BrowserRoutePolicy(
            "dispatches" if path in {"/api/v1/staff/threads", "/api/v1/staff/panels"} else "messages"
        )
    return None
