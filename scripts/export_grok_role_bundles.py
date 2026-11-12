"""Export or verify Grok Bot role bundles (#3099)."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_BACKEND = _ROOT / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from staff.grok_role_bundle import check_bundle_tree_drift, export_role_bundles  # noqa: E402
from staff.roles import roles_dir  # noqa: E402

_DEFAULT_OUT = _ROOT / "config" / "grok_role_bundles"
_DEFAULT_ROLES = _ROOT / "tests" / "fixtures" / "grok_role_sync" / "roles"


def _resolve_roles_dir(arg: str | None) -> Path:
    if arg:
        return Path(arg).expanduser()
    env = os.environ.get("STAFF_ROLES_DIR")
    if env:
        return Path(env).expanduser()
    discovered = roles_dir()
    if discovered is not None:
        return discovered
    return _DEFAULT_ROLES


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--roles-dir", default=None, help="staff/roles directory (default: STAFF_ROLES_DIR or fixture)")
    parser.add_argument("--out", type=Path, default=_DEFAULT_OUT, help="bundle output directory")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--write", action="store_true", help="write bundles and manifest")
    mode.add_argument("--check", action="store_true", help="exit 1 when bundles are stale")
    args = parser.parse_args(argv)
    roles_path = _resolve_roles_dir(args.roles_dir)
    if args.write:
        export_role_bundles(roles_path, args.out)
        return 0
    drift = check_bundle_tree_drift(roles_path, args.out)
    if drift:
        for role in drift:
            print(f"stale: {role}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
