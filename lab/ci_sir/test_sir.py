"""TDD contract tests for the SIR epidemic model used to reason about red CI.

These tests are written against the *intended* public API of
``lab.ci_sir.sir`` before that module exists. They assert mathematical
invariants of the SIR compartment model rather than hardcoded traces, so they
remain valid for any correct implementation.
"""

import math

import pytest

from lab.ci_sir.sir import (
    SIRParams,
    basic_reproduction_number,
    r0_from_final_size,
    simulate,
)


def infected_series(params, i0=1.0, horizon_days=60.0, dt=0.05):
    return [s.infected for s in simulate(params, i0=i0, horizon_days=horizon_days, dt=dt)]


def test_population_conserved():
    params = SIRParams(beta=1.5, gamma=0.5, population=1000)
    for s in simulate(params, i0=5.0, horizon_days=60.0, dt=0.01):
        assert s.susceptible + s.infected + s.recovered == pytest.approx(
            float(params.population), rel=1e-6, abs=1e-6
        )


def test_susceptible_never_increases():
    params = SIRParams(beta=1.5, gamma=0.5, population=1000)
    sus = [s.susceptible for s in simulate(params, i0=5.0, horizon_days=60.0, dt=0.05)]
    for before, after in zip(sus, sus[1:]):
        assert after <= before + 1e-9


def test_recovered_never_decreases():
    params = SIRParams(beta=1.5, gamma=0.5, population=1000)
    rec = [s.recovered for s in simulate(params, i0=5.0, horizon_days=60.0, dt=0.05)]
    for before, after in zip(rec, rec[1:]):
        assert after >= before - 1e-9


def test_no_outbreak_when_r0_below_one():
    # beta < gamma  =>  R0 < 1  =>  infected only ever shrinks (no epidemic).
    params = SIRParams(beta=0.15, gamma=0.5, population=1000)
    infected = infected_series(params, i0=10.0)
    assert max(infected) == pytest.approx(infected[0], rel=1e-9)
    assert infected[-1] < infected[0]


def test_outbreak_has_interior_peak():
    # beta > gamma  =>  R0 > 1  =>  infected rises then falls (a real wave).
    params = SIRParams(beta=1.5, gamma=0.5, population=1000)
    infected = infected_series(params, i0=1.0)
    peak = max(range(len(infected)), key=infected.__getitem__)
    assert 0 < peak < len(infected) - 1
    assert infected[peak] > infected[0]
    assert infected[-1] < infected[peak]


def test_r0_from_final_size_roundtrip():
    # Final-size equation: z = 1 - exp(-R0 * z).  Inverting gives
    # R0 = -ln(1 - z) / z.  The two must be exact inverses.
    for z in (0.05, 0.3, 0.6, 0.9):
        r = r0_from_final_size(z)
        reconstructed = 1.0 - math.exp(-r * z)
        assert reconstructed == pytest.approx(z, rel=1e-9)


def test_basic_reproduction_number_is_ratio():
    params = SIRParams(beta=1.5, gamma=0.5, population=1000)
    assert basic_reproduction_number(params) == pytest.approx(3.0)
