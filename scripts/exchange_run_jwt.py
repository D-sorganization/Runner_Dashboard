"""CLI wrapper for per-run JWT exchange (#3879 step 3)."""

from __future__ import annotations

import logging
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(_ROOT), str(_ROOT / "clients" / "fleet")]

from fleet_run_jwt import fetch_run_jwt  # noqa: E402

log = logging.getLogger("fleet.run_jwt")


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args[:1] != ["token"]:
        log.error("usage: exchange_run_jwt.py token")
        return 2
    try:
        minted, _expires_at, _jti = fetch_run_jwt()
    except Exception as exc:  # noqa: BLE001
        log.error("run-jwt exchange failed: %s", exc)
        return 1
    sys.stdout.write(minted)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
