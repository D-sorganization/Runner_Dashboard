#!/usr/bin/env python3
"""Select the root-owned Python 3.12 that seeds the qualified scheduler runtime (#1839).

Contract
--------
Input:
    An optional explicit interpreter path, then the fixed search order
    ``SEARCH_ORDER``. Every path must be absolute.
Accepted only if all of the following hold, checked on the candidate's own
path components and again on its resolved realpath:
    * no component lies in a user-controlled location (``/home``, ``/tmp`` ...);
    * every component, the file included, is owned by root;
    * no component is group- or other-writable;
    * the file is an executable regular file; and
    * it reports exactly Python 3.12.x.
Ownership and location are proven *before* the interpreter is executed, so a
user-owned runtime is never run, not even to read its version.
Failure:
    ``NoQualifiedPythonError`` listing every rejected candidate and the operator
    action. There is deliberately no fallback to Python 3.14 or any user runtime.
Postcondition:
    The chosen interpreter is logged with its realpath, owner uid and version.

The module is stdlib-only and runs under whatever ``/usr/bin/python3`` the host
has (for example 3.14), because it only *selects* an interpreter; it never
becomes the scheduler runtime itself.
"""

from __future__ import annotations

import argparse
import logging
import os
import posixpath
import re
import stat
import subprocess
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

LOG = logging.getLogger("root_python_selector")

REQUIRED_MINOR = "3.12"
VERSION_PATTERN = re.compile(r"3\.12\.\d+")
SEARCH_ORDER: tuple[str, ...] = (
    "/usr/bin/python3.12",
    "/usr/local/bin/python3.12",
    "/usr/bin/python3",
)
# Trees a non-root user can write to or control; never a valid interpreter home.
USER_LOCATION_PREFIXES: tuple[str, ...] = (
    "/home/",
    "/root/",
    "/tmp/",
    "/var/tmp/",
    "/run/user/",
    "/mnt/",
)
USER_RUNTIME_SEGMENTS = frozenset({".local", ".pyenv", ".asdf", ".conda", ".venv", "venv"})
GROUP_OTHER_WRITE = 0o022
ANY_EXECUTE = 0o111
VERSION_PROBE_TIMEOUT_SECONDS = 10


class NoQualifiedPythonError(RuntimeError):
    """No candidate satisfied the root-owned Python 3.12 contract."""


@dataclass(frozen=True)
class PathFacts:
    """What the selector needs to know about one path component."""

    uid: int
    mode: int
    kind: str  # "dir" | "file" | "symlink" | "other"


@dataclass(frozen=True)
class Selection:
    """The accepted interpreter."""

    path: str
    realpath: str
    owner_uid: int
    version: str


class HostProbe(Protocol):
    """Filesystem and interpreter facts, injected so policy is testable offline."""

    def realpath(self, path: str) -> str: ...

    def lstat(self, path: str) -> PathFacts | None: ...

    def interpreter_version(self, path: str) -> str | None: ...


class OsProbe:
    """The real host."""

    def realpath(self, path: str) -> str:
        return os.path.realpath(path)

    def lstat(self, path: str) -> PathFacts | None:
        try:
            info = os.lstat(path)
        except OSError:
            return None
        mode = info.st_mode
        if stat.S_ISLNK(mode):
            kind = "symlink"
        elif stat.S_ISDIR(mode):
            kind = "dir"
        elif stat.S_ISREG(mode):
            kind = "file"
        else:
            kind = "other"
        return PathFacts(uid=info.st_uid, mode=stat.S_IMODE(mode), kind=kind)

    def interpreter_version(self, path: str) -> str | None:
        try:
            result = subprocess.run(  # noqa: S603 - path proven root-owned before this call
                [path, "-I", "-c", "import platform; print(platform.python_version())"],
                capture_output=True,
                text=True,
                timeout=VERSION_PROBE_TIMEOUT_SECONDS,
                check=False,
                env={"PATH": "/usr/bin:/bin"},
            )
        except (OSError, subprocess.SubprocessError):
            return None
        return result.stdout.strip() if result.returncode == 0 else None


def _ancestors(path: str) -> list[str]:
    """Return ``/``, then each parent, down to ``path`` itself."""
    parts = [part for part in path.split("/") if part]
    chain = ["/"]
    for index in range(len(parts)):
        chain.append("/" + "/".join(parts[: index + 1]))
    return chain


def _user_location_reason(path: str) -> str | None:
    if any(path.startswith(prefix) for prefix in USER_LOCATION_PREFIXES):
        return f"user-owned runtime location {path}"
    bad = USER_RUNTIME_SEGMENTS.intersection(path.split("/"))
    if bad:
        return f"user-managed runtime directory {sorted(bad)[0]} in {path}"
    return None


def _component_reason(component: str, facts: PathFacts | None, *, is_leaf: bool, strict: bool) -> str | None:
    if facts is None:
        return f"{component} is absent or unreadable"
    if facts.uid != 0:
        return f"user-owned: {component} is owned by uid {facts.uid}"
    if facts.kind == "symlink":
        # A root-owned link is only legal on the unresolved path; the realpath
        # pass (strict) re-checks every real component.
        return f"{component} is a symlink in the resolved path" if strict else None
    if facts.mode & GROUP_OTHER_WRITE:
        return f"{component} is group/other-writable (mode {facts.mode:o})"
    if is_leaf and (facts.kind != "file" or not facts.mode & ANY_EXECUTE):
        return f"{component} is not an executable regular file"
    if not is_leaf and facts.kind != "dir":
        return f"{component} is not a directory"
    return None


def _chain_reason(probe: HostProbe, path: str, *, strict: bool) -> str | None:
    chain = _ancestors(path)
    for component in chain:
        reason = _component_reason(
            component,
            probe.lstat(component),
            is_leaf=component == chain[-1],
            strict=strict,
        )
        if reason:
            return reason
    return None


def _rejection_reason(probe: HostProbe, candidate: str) -> tuple[str | None, str]:
    """Return ``(reason, realpath)``; ``reason`` is None when trust checks pass."""
    resolved = probe.realpath(candidate)
    for label, path in (("candidate", candidate), ("realpath", resolved)):
        reason = _user_location_reason(path)
        if reason:
            suffix = " (symlink escapes into a user location)" if label == "realpath" and resolved != candidate else ""
            return reason + suffix, resolved
    reason = _chain_reason(probe, candidate, strict=False) or _chain_reason(probe, resolved, strict=True)
    return reason, resolved


def _evaluate(probe: HostProbe, candidate: str) -> tuple[Selection | None, str]:
    reason, resolved = _rejection_reason(probe, candidate)
    if reason:
        return None, reason
    version = probe.interpreter_version(resolved)
    if version is None:
        return None, f"version probe of {resolved} failed"
    if not VERSION_PATTERN.fullmatch(version):
        return None, f"version {version} is not Python {REQUIRED_MINOR}.x"
    facts = probe.lstat(resolved)
    owner = facts.uid if facts else 0
    return Selection(path=candidate, realpath=resolved, owner_uid=owner, version=version), ""


def select_root_python(probe: HostProbe, explicit: str | None = None) -> Selection:
    """Return the first candidate meeting the contract, else fail closed."""
    candidates = ([explicit] if explicit else []) + [c for c in SEARCH_ORDER if c != explicit]
    for candidate in candidates:
        if not posixpath.isabs(candidate):
            raise ValueError(f"interpreter path must be absolute: {candidate!r}")
    rejections: list[str] = []
    for candidate in candidates:
        selection, reason = _evaluate(probe, candidate)
        if selection:
            LOG.info(
                "selected scheduler interpreter %s realpath=%s uid=%d version=%s",
                selection.path,
                selection.realpath,
                selection.owner_uid,
                selection.version,
            )
            return selection
        rejections.append(f"  - {candidate}: {reason}")
        LOG.warning("rejected %s: %s", candidate, reason)
    raise NoQualifiedPythonError(
        f"no root-owned Python {REQUIRED_MINOR} is available for the scheduler runtime.\n"
        + "\n".join(rejections)
        + f"\nOperator action: root-install a vetted Python {REQUIRED_MINOR} "
        "(root-owned, not group/other-writable, e.g. /usr/bin/python3.12 from the distribution "
        "or a reviewed package), then re-run the deployment. Python 3.14 and user-owned "
        "runtimes are never used."
    )


def main(argv: Sequence[str] | None = None, probe: HostProbe | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("--python", default=None, help="explicit absolute interpreter path to try first")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, stream=sys.stderr, format="[root-python] %(message)s")
    try:
        chosen = select_root_python(probe or OsProbe(), explicit=args.python)
    except (NoQualifiedPythonError, ValueError) as error:
        sys.stderr.write(f"[FAIL] {error}\n")
        return 1
    sys.stdout.write(chosen.realpath + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
