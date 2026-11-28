"""Deterministic SIR epidemic model (standard library only).

A cheap lens on CI "outbreaks": a wave of red (failing) runs on main is
treated as an epidemic spreading through a fixed population of runs.

Compartments, per day:
  S  susceptible  — runs that have not failed yet (green, at risk)
  I  infected     — runs currently red (failing)
  R  recovered    — runs that have gone green again (fixed / closed)

Mass-action SIR:
  dS/dt = -beta * S * I / N
  dI/dt =  beta * S * I / N - gamma * I
  dR/dt =  gamma * I

The basic reproduction number is R0 = beta / gamma: an outbreak happens
iff R0 > 1.  The final-size equation  z = 1 - exp(-R0 * z)  links the
attack rate z to R0 and inverts in closed form.
"""

from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class SIRParams:
    beta: float  # effective contact rate, 1/day
    gamma: float  # recovery rate, 1/day
    population: int  # N — total runs/slots in the observation window


@dataclass(frozen=True)
class SIRState:
    susceptible: float
    infected: float
    recovered: float


def basic_reproduction_number(params: SIRParams) -> float:
    """R0 = beta / gamma. Above 1 the wave grows; below 1 it fizzles."""
    if params.gamma <= 0:
        raise ValueError("gamma must be positive")
    return params.beta / params.gamma


def r0_from_final_size(attack_rate: float) -> float:
    """Invert the SIR final-size equation z = 1 - exp(-R0 * z) for R0."""
    if not 0.0 < attack_rate < 1.0:
        raise ValueError("attack_rate must be strictly between 0 and 1")
    return -math.log(1.0 - attack_rate) / attack_rate


def simulate(
    params: SIRParams,
    i0: float = 1.0,
    horizon_days: float = 60.0,
    dt: float = 0.05,
) -> list[SIRState]:
    """Euler integration; returns one SIRState per step (t = 0, dt, 2dt, ...)."""
    if params.population <= 0:
        raise ValueError("population must be positive")
    if params.beta < 0 or params.gamma < 0:
        raise ValueError("beta and gamma must be non-negative")
    if not 0.0 < i0 < params.population:
        raise ValueError("i0 must be strictly between 0 and population")
    if dt <= 0 or horizon_days <= 0:
        raise ValueError("dt and horizon_days must be positive")

    n = float(params.population)
    s = n - float(i0)
    i = float(i0)
    r = 0.0

    states = [SIRState(s, i, r)]
    for _ in range(int(horizon_days / dt)):
        new_infections = params.beta * s * i / n * dt
        recoveries = params.gamma * i * dt
        s = max(0.0, s - new_infections)
        i = max(0.0, i + new_infections - recoveries)
        r += recoveries
        states.append(SIRState(s, i, r))
    return states
