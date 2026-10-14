"""Self-contained checks for the SIR lab experiment. Run with:

    python3 lab/sir-ci-outbreak/test_sir_model.py

Not wired into the repo's pytest suite on purpose (lab code stays out of the
main test run; see docs/fleet-mad-scientist.md lab safety rules).
"""

import logging
from datetime import datetime, timezone

from sir_model import bin_red_fraction, lag1_autocorrelation, simulate_sir


def test_bin_red_fraction_counts_red_runs():
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)  # noqa: UP017 (keep py3.10-compatible)
    runs = [
        (base, "success"),
        (base, "failure"),
        (base.replace(hour=3), "cancelled"),
    ]
    fractions = bin_red_fraction(runs, bin_hours=2)
    assert fractions == [0.5, 1.0]


def test_simulate_sir_stays_in_unit_interval():
    series = simulate_sir(i0=0.1, beta=0.5, gamma=0.1, steps=20)
    assert all(0.0 <= x <= 1.0 for x in series)


def test_lag1_autocorrelation_of_constant_series_is_zero():
    assert lag1_autocorrelation([0.5] * 10) == 0.0


def test_lag1_autocorrelation_of_alternating_series_is_negative():
    assert lag1_autocorrelation([0.0, 1.0] * 10) < 0


if __name__ == "__main__":
    test_bin_red_fraction_counts_red_runs()
    test_simulate_sir_stays_in_unit_interval()
    test_lag1_autocorrelation_of_constant_series_is_zero()
    test_lag1_autocorrelation_of_alternating_series_is_negative()
    logging.info("All lab checks passed.")
