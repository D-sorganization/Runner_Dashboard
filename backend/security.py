"""Security utilities extracted from server.py (issue #161).

Provides:
- Log value sanitization
- Subprocess environment scrubbing
- URL/path/command validation
- Dispatch rate limiting
"""

from __future__ import annotations

import ipaddress
import logging
import os
import re
import shlex
import sys
import time
from collections import defaultdict
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from fastapi import HTTPException

_REPO_SLUG_RE = re.compile(r"^[A-Za-z0-9._-]{1,100}$")

# Strict owner/repo format used by _normalize_repository_input (issue #326).
# Rejects anything that isn't a plain GitHub owner/repo slug pair.
_OWNER_REPO_RE = re.compile(r"^[a-zA-Z0-9._-]+/[a-zA-Z0-9._-]+$")

# ─── Log Sanitization ──────────────────────────────────────────────────────────


def sanitize_log_value(value: str) -> str:
    """Strip log-injection characters from user-controlled strings."""
    return value.replace("\n", "\\n").replace("\r", "\\r").replace("\t", "\\t")[:200]


# ─── Environment Scrubbing ─────────────────────────────────────────────────────

_PROVIDER_API_KEY_ENV_VARS = frozenset(
    {
        "ANTHROPIC_API_KEY",
        "OPENAI_API_KEY",
        "GOOGLE_API_KEY",
        "JULES_API_KEY",
        "LINEAR_API_KEY",
    }
)

_DENIED_SUBPROCESS_ENV_KEYS = frozenset(
    {
        *_PROVIDER_API_KEY_ENV_VARS,
        "GH_TOKEN",
        "GITHUB_TOKEN",
        "MAXWELL_API_TOKEN",
        "DASHBOARD_API_KEY",
        "SESSION_SECRET",
        "DISPATCH_SIGNING_SECRET",
        "LINEAR_WEBHOOK_SECRET",
    }
)

_DENIED_SUBPROCESS_ENV_PREFIXES = ("AWS_", "AZURE_")


def safe_subprocess_env() -> dict[str, str]:
    """Return os.environ with known secret-bearing keys stripped for subprocess calls."""
    return {
        key: value
        for key, value in os.environ.items()
        if key.upper() not in _DENIED_SUBPROCESS_ENV_KEYS
        and not key.upper().startswith(_DENIED_SUBPROCESS_ENV_PREFIXES)
    }


# ─── URL Validation ────────────────────────────────────────────────────────────


# Tailscale's CGNAT range — 100.64.0.0/10 per RFC 6598 — is what every
# Tailscale node IP falls under. `ipaddress.IPv4Address.is_private` does
# NOT include CGNAT (it covers 10/8, 172.16/12, 192.168/16 only). Without
# this allowance, every Tailscale-routed peer is rejected by
# validate_fleet_node_url() and fleet federation can't be configured at
# all over the canonical Tailscale transport — observed on this fleet
# 2026-05-18: machine_registry shipped 4 nodes with 100.x IPs and every
# one was rejected on auto-derive, so `/api/fleet/nodes` returned just
# the local host. See #668 follow-up for the diagnostic surface.
_TAILSCALE_CGNAT_NET = ipaddress.ip_network("100.64.0.0/10")


def validate_fleet_node_url(url: str) -> str:
    """Validate a fleet node URL to prevent SSRF (issue #28).

    Allowed address ranges:
      - RFC 1918 private (10/8, 172.16/12, 192.168/16)
      - Loopback (127/8)
      - RFC 6598 CGNAT (100.64.0.0/10) — Tailscale + carrier-grade NAT
    Allowed hostnames:
      - localhost
      - *.local
      - *.internal
      - *.ts.net (Tailscale MagicDNS — full FQDNs for federation peers)
    """
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError(f"Fleet node URL must use http or https: {url}")
    host = parsed.hostname or ""
    try:
        addr = ipaddress.ip_address(host)
        if addr.is_private or addr.is_loopback:
            return url
        if isinstance(addr, ipaddress.IPv4Address) and addr in _TAILSCALE_CGNAT_NET:
            return url
        raise ValueError(f"Fleet node URL must be a private/local address: {url}")
    except ValueError as exc:
        # If it's not an IP address check it's a hostname we trust
        if "must be" in str(exc):
            raise
        # hostname – allow localhost, .local, .internal, .ts.net (Tailscale MagicDNS)
        if not (
            host == "localhost" or host.endswith(".local") or host.endswith(".internal") or host.endswith(".ts.net")
        ):
            raise ValueError(f"Fleet node hostname not allowed: {host}") from exc
    return url


def validate_local_url(url: str, field: str = "url") -> str:
    """Validate that a URL has http/https scheme and a local host (issue #23)."""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError(f"{field} must use http or https")
    return validate_fleet_node_url(url)


def validate_repo_slug(name: str) -> str:
    """Return a GitHub repository slug safe for interpolation into gh api paths."""
    slug = str(name).strip()
    if not _REPO_SLUG_RE.fullmatch(slug):
        raise HTTPException(status_code=422, detail="repository must match ^[A-Za-z0-9._-]{1,100}$")
    return slug


def validate_owner_repo_format(value: str) -> str:
    """Validate that *value* is a strict ``owner/repo`` slug (issue #326).

    Rejects any input that does not match ``^[a-zA-Z0-9._-]+/[a-zA-Z0-9._-]+$``
    before it reaches owner-comparison or subprocess interpolation, preventing
    SSRF via malformed owner segments (null bytes, URL-encoded chars, etc.).

    Returns the stripped, validated value unchanged.
    """
    stripped = str(value).strip()
    if not _OWNER_REPO_RE.fullmatch(stripped):
        raise HTTPException(
            status_code=422,
            detail=(
                "repository must be in 'owner/repo' format matching "
                "^[a-zA-Z0-9._-]+/[a-zA-Z0-9._-]+$ with no extra path components"
            ),
        )
    return stripped


# ─── Path Validation ───────────────────────────────────────────────────────────


def validate_local_path(path_str: str, allowed_root: Path) -> Path:
    """Resolve path and ensure it stays within allowed_root (issues #23, #927).

    Both the user input AND ``allowed_root`` are fully resolved (following
    symlinks) before the containment check, so the comparison is
    resolved-vs-resolved. Previously ``allowed_root`` was compared unresolved
    while ``resolved`` followed symlinks: when ``allowed_root`` itself was (or
    contained) a symlink, legitimate paths inside the real root were rejected and,
    worse, crafted symlinks could pass a containment check they should not (#927).
    """
    resolved = Path(path_str).expanduser().resolve()
    resolved_root = Path(allowed_root).expanduser().resolve()
    try:
        resolved.relative_to(resolved_root)
    except ValueError as exc:
        raise ValueError(f"Path escapes allowed root: {path_str}") from exc
    return resolved


# ─── Command Validation ────────────────────────────────────────────────────────


def validate_health_command(cmd: str) -> list[str]:
    """Parse health command safely, rejecting shell metacharacters (issue #22)."""
    dangerous = set(";|&`$()<>")
    if any(c in cmd for c in dangerous):
        raise ValueError(f"health_command contains disallowed characters: {cmd!r}")
    return shlex.split(cmd)


# ─── Rate Limiting ───────────────────────────────────────────────────────────

_dispatch_rate: dict[str, list[float]] = defaultdict(list)
DISPATCH_LIMIT_PER_MINUTE = 10
_RATE_WINDOW_SECONDS = 60
_RATE_PRINCIPAL_TTL_SECONDS = 600  # evict inactive principals after 10 min


def _evict_stale_rate_entries(now: float) -> None:
    """Remove principals that have had no requests in the last TTL window (issue #345)."""
    stale_keys = [
        key
        for key, timestamps in _dispatch_rate.items()
        if not timestamps or (now - max(timestamps)) > _RATE_PRINCIPAL_TTL_SECONDS
    ]
    for key in stale_keys:
        del _dispatch_rate[key]


def check_dispatch_rate(client_ip: str, *, principal_id: str | None = None) -> None:
    """Enforce rate limiting for AI agent dispatch endpoints (issue #31, #345).

    Keyed on *principal_id* for authenticated callers so the limit cannot be
    bypassed by rotating IP addresses (NAT, Tailscale, etc.).  Falls back to
    *client_ip* only when no principal is available (public webhook path).
    Stale entries are evicted after ``_RATE_PRINCIPAL_TTL_SECONDS`` of inactivity
    to prevent unbounded memory growth.
    """
    rate_key = principal_id if principal_id else client_ip
    now = time.monotonic()
    _evict_stale_rate_entries(now)
    window = [t for t in _dispatch_rate[rate_key] if now - t < _RATE_WINDOW_SECONDS]
    if len(window) >= DISPATCH_LIMIT_PER_MINUTE:
        raise HTTPException(
            status_code=429,
            detail="Rate limit exceeded for agent dispatch",
            headers={"Retry-After": str(_RATE_WINDOW_SECONDS)},
        )
    window.append(now)
    _dispatch_rate[rate_key] = window


# ─── YAML Loader Security ──────────────────────────────────────────────────────
# Issue #355: Path validation, symlink checks, and file mode checks for YAML loaders

# Default allowed roots for config files
DEFAULT_ALLOWED_ROOTS = [
    Path("~/.config/runner-dashboard").expanduser(),
]


def _get_repo_root() -> Path | None:
    """Find the repository root directory by searching for .git."""
    import subprocess

    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            check=True,
            timeout=5,
        )
        return Path(result.stdout.strip())
    except (OSError, subprocess.SubprocessError, TimeoutError):
        return None


def _check_symlink(path: Path, allowed_roots: list[Path]) -> bool:
    """Check if a symlink target is within allowed roots.

    Returns True if the path is safe (not a symlink escaping allowed roots).
    Returns False if the path is a symlink pointing outside allowed roots.
    """
    if not path.is_symlink():
        return True

    # Resolve the symlink target
    try:
        resolved = path.resolve(strict=True)
    except (OSError, RuntimeError):
        # Cannot resolve - treat as unsafe
        return False

    # Check if resolved path is within any allowed root
    for root in allowed_roots:
        try:
            resolved.relative_to(root)
            return True
        except ValueError:
            continue

    # Symlink target escapes allowed roots
    return False


#: Filesystem types that cannot faithfully represent POSIX permission bits.
#: On these, ``stat.S_IWOTH`` is meaningless — Windows-backed mounts expose
#: every file as 0777 because NTFS ACLs don't map onto ``ugo+w``, and
#: remote SMB/CIFS mounts inherit the same problem from their server-side
#: ACLs. The world-writable check must skip these mounts (with a WARNING
#: for auditability) or every Windows-based contributor will hit spurious
#: rejections on pre-push. Tracked in Runner_Dashboard PR follow-up to #695.
_NON_POSIX_FS_TYPES = frozenset(
    {
        "9p",  # WSL2 mirrored / 9P over hvsock
        "drvfs",  # WSL1 Windows drive mounts
        "cifs",  # SMB shares on Linux
        "smbfs",  # macOS SMB
        "ntfs",  # NTFS read-only
        "ntfs-3g",  # NTFS read-write
        "vfat",  # FAT32
        "fat",
        "fat32",
        "exfat",
        "msdos",
    }
)


def _read_proc_mounts() -> str | None:
    """Return the contents of ``/proc/mounts``, or None if unreadable.

    Split out for test mocking; do not inline.
    """
    try:
        return Path("/proc/mounts").read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return None


def _filesystem_preserves_posix_perms(path: Path) -> bool:
    """Return True iff ``path``'s underlying filesystem honours POSIX mode bits.

    Returns False for:
      * Native Windows (``os.name == "nt"``): ``os.stat()`` returns ACL-derived
        modes that don't map to ``ugo+w`` checks.
      * WSL / Linux mounts whose ``/proc/mounts`` filesystem type is in
        :data:`_NON_POSIX_FS_TYPES` (DrvFs, 9p, CIFS, NTFS, FAT family).

    Returns True everywhere else, including when ``/proc/mounts`` is
    unreadable — failing **safe** matters more than failing convenient
    when the OS surface is ambiguous.

    Fleet note: this is the only place that "knows" about filesystem
    semantics. Do not duplicate the FS-type list elsewhere; import this
    helper or extend :data:`_NON_POSIX_FS_TYPES` here.
    """
    if os.name == "nt":
        return False
    if sys.platform != "linux":
        # macOS APFS / *BSD UFS preserve perms; unknown POSIX assumes yes.
        return True

    mounts = _read_proc_mounts()
    if mounts is None:
        return True  # fail safe

    resolved = str(path.resolve())
    best_mount_point = ""
    best_fs_type = ""
    for raw in mounts.splitlines():
        parts = raw.split()
        if len(parts) < 3:
            continue
        mount_point, fs_type = parts[1], parts[2]
        # Longest-prefix match: the most specific mount wins (matters when
        # /mnt/c is mounted under /).
        if (resolved == mount_point or resolved.startswith(mount_point.rstrip("/") + "/")) and len(mount_point) > len(
            best_mount_point
        ):
            best_mount_point = mount_point
            best_fs_type = fs_type

    return best_fs_type.lower() not in _NON_POSIX_FS_TYPES


def _check_file_mode(path: Path) -> bool:
    """Check if file has safe permissions (not world-writable).

    Returns True if file mode is safe **or** if the filesystem cannot
    represent POSIX permissions (see :func:`_filesystem_preserves_posix_perms`).
    Returns False if the file is world-writable on a POSIX-honouring
    filesystem. Logs a WARNING when skipping so audit trails stay honest.
    """
    if not _filesystem_preserves_posix_perms(path):
        logging.getLogger("security").warning(
            "Skipping world-writable mode check for %s: filesystem does not preserve POSIX permissions",
            path,
        )
        return True
    try:
        import stat

        mode = path.stat().st_mode
        # Check if world-writable bit is set
        return not bool(mode & stat.S_IWOTH)
    except (OSError, RuntimeError):
        # Cannot stat - treat as unsafe
        return False


def validate_config_path(
    path: Path,
    allowed_roots: list[Path] | None = None,
    check_symlink: bool = True,
    check_mode: bool = True,
) -> Path:
    """Validate a config file path for secure YAML loading.

    Args:
        path: The path to validate
        allowed_roots: List of allowed root directories. If None, uses defaults.
        check_symlink: Whether to check for symlink escape (default True)
        check_mode: Whether to check file mode (default True)

    Returns:
        The validated and resolved path

    Raises:
        ValueError: If path validation fails for any reason
    """
    if allowed_roots is None:
        allowed_roots = list(DEFAULT_ALLOWED_ROOTS)

    # Add repo root if available
    repo_root = _get_repo_root()
    if repo_root:
        repo_config = repo_root / "config"
        if repo_config.exists() and repo_config not in allowed_roots:
            allowed_roots.append(repo_config)
        # Also allow the repo root itself for config/ subdirectory
        if repo_root not in allowed_roots:
            allowed_roots.append(repo_root)

    # Resolve the path
    resolved = path.expanduser().resolve()

    # Check if path exists
    if not resolved.exists():
        raise ValueError(f"Config path does not exist: {path}")

    # Check if path is within allowed roots
    path_allowed = False
    for root in allowed_roots:
        try:
            resolved.relative_to(root.expanduser().resolve())
            path_allowed = True
            break
        except ValueError:
            continue

    if not path_allowed:
        raise ValueError(
            f"Config path escapes allowed roots: {resolved}. "
            f"Allowed roots: {[str(r.expanduser().resolve()) for r in allowed_roots]}"
        )

    # Check symlink safety
    if check_symlink and not _check_symlink(path, allowed_roots):
        raise ValueError(f"Config path is a symlink pointing outside allowed roots: {path} -> {resolved}")

    # Check file mode safety
    if check_mode and not _check_file_mode(resolved):
        raise ValueError(f"Config file is world-writable (insecure): {resolved}")

    return resolved


def safe_yaml_load(path: Path, allowed_roots: list[Path] | None = None) -> Any:
    """Safely load a YAML file with path validation.

    Args:
        path: Path to the YAML file
        allowed_roots: List of allowed root directories

    Returns:
        The parsed YAML content

    Raises:
        ValueError: If path validation fails or YAML parsing fails
    """
    import yaml

    # Validate the path first
    validated_path = validate_config_path(path, allowed_roots)

    # Read and parse YAML
    content = validated_path.read_text(encoding="utf-8")
    data = yaml.safe_load(content)

    return data
