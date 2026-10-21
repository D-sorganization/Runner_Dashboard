"""Timing semantics and pinned upstream source provenance contracts."""

from __future__ import annotations

import hashlib
from pathlib import Path

from ci_fleet_baseline.metrics import job_execute_seconds, job_queue_seconds
from job_metric_semantics import job_durations


def test_job_timing_formulas_parse_and_clamp_invalid_or_negative_values() -> None:
    assert job_durations("2026-10-21T10:00:00Z", "2026-10-21T10:00:10Z", "2026-10-21T10:01:10Z") == (
        10.0,
        60.0,
    )
    assert job_durations("bad", "2026-10-21T10:00:10Z", "2026-10-21T10:00:00Z") == (None, 0.0)


def test_timing_adapter_matches_exact_pinned_vendor_bytes() -> None:
    package = Path(__file__).parents[1] / "backend" / "ci_fleet_baseline"
    # Public upstream source digests; these pins are not credentials.
    expected = {
        "metrics.py": "aef74ecb2dc0da84a6eab5c35e94d52d52b0d730446b4cbdf9835aa180bd348b",  # pragma: allowlist secret
        "window.py": "f2edab6d41050408966fa2693674896e2b364e877bd27190961fa6bbab8afa00",  # pragma: allowlist secret
        "constants.py": "c2554385fb811aad67e6f7bead3e6ac9b65f0e37d4231804650d813cffb30b2a",  # pragma: allowlist secret
    }
    for name, fingerprint in expected.items():
        content = (package / name).read_bytes()
        if name == "metrics.py":
            content = content.replace(
                b"from .constants import", b"from shared_scripts.ci_fleet_baseline.constants import", 1
            ).replace(b"from .window import", b"from shared_scripts.ci_fleet_baseline.window import", 1)
        assert hashlib.sha256(content).hexdigest() == fingerprint
    timestamps = ("2026-10-21T10:00:00Z", "2026-10-21T10:00:10Z", "2026-10-21T10:01:10Z")
    assert job_durations(*timestamps) == (
        job_queue_seconds(timestamps[0], timestamps[1]),
        job_execute_seconds(timestamps[1], timestamps[2]),
    )
