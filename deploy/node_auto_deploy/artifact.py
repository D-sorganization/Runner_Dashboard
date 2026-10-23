"""Resolve release tarball assets for a commit on main."""

from __future__ import annotations

import re
from typing import Any

_DASHBOARD_TAR = re.compile(r"^dashboard-(?:(\d+\.\d+\.\d+)|(20\d{2}\.\d{2}\.\d{2}-[0-9a-f]{7}))\.tar\.gz$")


def pick_release_assets(releases: list[dict[str, Any]], commit_sha: str) -> dict[str, str] | None:
    """Return version, tarball_api_url, checksum_api_url for a release targeting ``commit_sha``."""
    sha = commit_sha.lower()
    for release in releases:
        if not isinstance(release, dict) or release.get("draft"):
            continue
        target = str(release.get("target_commitish") or "").lower()
        if target != sha and not target.startswith(sha[:7]):
            continue
        assets = release.get("assets")
        if not isinstance(assets, list):
            continue
        tarball_url = ""
        checksum_url = ""
        version = ""
        for asset in assets:
            if not isinstance(asset, dict):
                continue
            name = str(asset.get("name") or "")
            url = str(asset.get("url") or "")
            m = _DASHBOARD_TAR.match(name)
            if m and not tarball_url:
                version = m.group(1) or m.group(2) or ""
                tarball_url = url
            if name.endswith(".tar.gz.sha256") and not checksum_url:
                checksum_url = url
        if tarball_url and checksum_url and version:
            return {
                "version": version,
                "tarball_url": tarball_url,
                "checksum_url": checksum_url,
            }
    return None
