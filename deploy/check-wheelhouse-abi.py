#!/usr/bin/env python3
"""Fail when an artifact wheelhouse holds wheels the declared runtime cannot install (#1212).

The dashboard artifact declares ``compatibility.python_minor`` in
``deployment.json`` and installs offline from ``backend/wheels``. A wheel built
for another CPython ABI (``cp311`` in a ``3.12`` artifact) or another OS makes
that offline install fail on the target host, after the operator has already
started a deploy. This check runs at packaging time and again in the installer
preflight, before anything live is touched.

Accepted per wheel (PEP 425 tags, any compressed tag set may match):

* pure wheels: interpreter ``pyN``/``pyNM`` with ABI ``none``;
* ``abi3`` wheels whose ``cpXY`` floor is at or below the declared minor;
* CPython wheels whose ABI tag is exactly the declared ``cpXY``;

and the platform tag must be ``any`` or a Linux tag (the artifact targets
Linux/WSL hosts only).

Usage::

    check-wheelhouse-abi.py --python-minor 3.12 --wheel-dir stage/backend/wheels
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path

_MINOR_RE = re.compile(r"^3\.(\d{1,2})$")
_CP_RE = re.compile(r"^cp3(\d{1,2})$")
_PY_RE = re.compile(r"^py3\d{0,2}$")


@dataclass(frozen=True)
class WheelTags:
    """Compressed PEP 425 tag sets parsed from a wheel filename."""

    filename: str
    interpreters: tuple[str, ...]
    abis: tuple[str, ...]
    platforms: tuple[str, ...]


def parse_python_minor(value: str) -> int:
    """Return the minor number of a ``3.X`` string, or raise ``ValueError``."""
    match = _MINOR_RE.match(value.strip())
    if not match:
        raise ValueError(f"python_minor must look like '3.12', got {value!r}")
    return int(match.group(1))


def parse_wheel_filename(filename: str) -> WheelTags:
    """Split ``name-ver[-build]-py-abi-plat.whl`` into its tag sets."""
    if not filename.endswith(".whl"):
        raise ValueError(f"not a wheel: {filename}")
    parts = filename[: -len(".whl")].split("-")
    if len(parts) not in (5, 6):
        raise ValueError(f"malformed wheel filename: {filename}")
    py_tag, abi_tag, plat_tag = parts[-3:]
    return WheelTags(
        filename=filename,
        interpreters=tuple(py_tag.split(".")),
        abis=tuple(abi_tag.split(".")),
        platforms=tuple(plat_tag.split(".")),
    )


def _cp_minor(tag: str) -> int | None:
    match = _CP_RE.match(tag)
    return int(match.group(1)) if match else None


def _abi_compatible(tags: WheelTags, minor: int) -> bool:
    for interpreter in tags.interpreters:
        for abi in tags.abis:
            if abi == "none" and _PY_RE.match(interpreter):
                return True
            floor = _cp_minor(interpreter)
            if floor is None:
                continue
            if abi == "abi3" and floor <= minor:
                return True
            if abi == "none" and floor == minor:
                return True
            if _cp_minor(abi) == minor and floor == minor:
                return True
    return False


def _platform_compatible(tags: WheelTags) -> bool:
    return any(plat == "any" or "linux" in plat for plat in tags.platforms)


def wheel_problem(filename: str, minor: int) -> str | None:
    """Return why *filename* cannot install on CPython 3.<minor>/Linux, else ``None``."""
    try:
        tags = parse_wheel_filename(filename)
    except ValueError as exc:
        return str(exc)
    if not _abi_compatible(tags, minor):
        interp = ".".join(tags.interpreters)
        abi = ".".join(tags.abis)
        return f"{filename}: ABI {interp}-{abi} does not match declared python_minor 3.{minor}"
    if not _platform_compatible(tags):
        return f"{filename}: platform {'.'.join(tags.platforms)} is not installable on Linux"
    return None


def check_wheelhouse(wheel_dir: Path, python_minor: str) -> list[str]:
    """Return one problem string per incompatible wheel (empty list = compatible)."""
    minor = parse_python_minor(python_minor)
    if not wheel_dir.is_dir():
        return [f"wheelhouse not found: {wheel_dir}"]
    problems = []
    for wheel in sorted(wheel_dir.glob("*.whl")):
        problem = wheel_problem(wheel.name, minor)
        if problem:
            problems.append(problem)
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--python-minor", required=True, help="declared CPython minor, e.g. 3.12")
    parser.add_argument("--wheel-dir", required=True, type=Path, help="wheelhouse directory")
    args = parser.parse_args(argv)
    try:
        problems = check_wheelhouse(args.wheel_dir, args.python_minor)
    except ValueError as exc:
        sys.stderr.write(f"wheelhouse ABI check: {exc}\n")
        return 2
    if problems:
        sys.stderr.write(
            f"wheelhouse ABI check failed: {len(problems)} wheel(s) incompatible with Python {args.python_minor}:\n"
        )
        sys.stderr.writelines(f"  - {problem}\n" for problem in problems)
        return 1
    count = len(list(args.wheel_dir.glob("*.whl")))
    sys.stdout.write(f"wheelhouse ABI check passed: {count} wheel(s) compatible with Python {args.python_minor}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
