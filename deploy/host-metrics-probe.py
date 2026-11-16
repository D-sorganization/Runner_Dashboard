#!/usr/bin/env python3
"""Background host metrics sampler (#4309).

Installed as a systemd oneshot triggered every ~30 s. Writes
``$RUNNER_DASHBOARD_STATE_DIR/host_metrics.json``; the dashboard and
runner-scheduler read that file only.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
_BACKEND = _REPO_ROOT / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from host_metrics_cache import sample_host_metrics, write_cache_atomic  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger("host-metrics-probe")


def main() -> int:
    payload = sample_host_metrics()
    path = write_cache_atomic(payload)
    log.info("wrote %s (windows=%s)", path, "yes" if payload.get("windows") else "no")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
