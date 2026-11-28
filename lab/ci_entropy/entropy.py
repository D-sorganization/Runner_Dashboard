"""Information-theoretic primitives, applied to CI run conclusions.

This module is deliberately small and dependency-free (standard library only,
matching the ``lab/`` convention): the goal is to answer one question with the
fewest moving parts possible — *is CI deterministic enough to trust auto-merge?*

Terms used throughout
---------------------
- **Shannon entropy** ``H(X) = -sum p_i log2 p_i``: the average surprise, in
  bits, of a single run conclusion. 0 means one outcome always happens;
  ``log2(n)`` means ``n`` outcomes are equally likely.
- **Relative entropy (KL divergence)** ``D(p || q)``: how many extra bits it
  costs to encode a source drawn from ``p`` using an optimal code for ``q``.
  By Gibbs' inequality it is always ``>= 0``, and ``0`` iff ``p == q``.
- **Transition matrix**: a row-stochastic Markov matrix ``P`` where
  ``P[i][j]`` is the empirical probability of concluding with state ``j``
  immediately after state ``i``.
- **Stationary distribution**: the long-run fraction of runs spent in each
  state, i.e. the left fixed point ``pi = pi @ P``.
- **Entropy rate** ``H(X_{t+1} | X_t)``: how much surprise remains about the
  *next* run even when you know the current one. This — not the marginal
  entropy — is the honest measure of CI predictability.
"""

from __future__ import annotations

from collections.abc import Sequence
from math import log2


def _validate_distribution(values: Sequence[float], name: str) -> None:
    if not values:
        raise ValueError(f"{name} must be non-empty")
    if any(v < 0 for v in values):
        raise ValueError(f"{name} must be non-negative")
    total = sum(values)
    if abs(total - 1.0) > 1e-9:
        raise ValueError(f"{name} must sum to 1, got {total!r}")


def normalize(counts: Sequence[float]) -> list[float]:
    """Turn raw counts into a probability vector that sums to 1.

    Raises ``ValueError`` if any count is negative or the total is not
    strictly positive.
    """
    counts = list(counts)
    if any(c < 0 for c in counts):
        raise ValueError("counts must be non-negative")
    total = sum(counts)
    if total <= 0:
        raise ValueError("total count must be strictly positive")
    return [c / total for c in counts]


def shannon_entropy(counts: Sequence[float]) -> float:
    """Shannon entropy of a count vector, in bits (``0 * log2 0 == 0``)."""
    probs = normalize(counts)
    return -sum(p * log2(p) for p in probs if p > 0)


def relative_entropy(p: Sequence[float], q: Sequence[float]) -> float:
    """KL divergence ``D(p || q)`` in bits; raises ``ValueError`` if undefined.

    ``p`` and ``q`` must be proper same-length distributions, and ``q`` must
    have support wherever ``p`` does (otherwise the divergence is ``+inf``).
    """
    p = list(p)
    q = list(q)
    if len(p) != len(q):
        raise ValueError("p and q must have the same length")
    _validate_distribution(p, "p")
    _validate_distribution(q, "q")
    if any(pi > 0 and qi == 0 for pi, qi in zip(p, q, strict=True)):
        raise ValueError("q has no support where p does; D(p || q) is infinite")
    return sum(pi * log2(pi / qi) for pi, qi in zip(p, q, strict=True) if pi > 0)


def transition_matrix(sequence: Sequence[str]) -> tuple[list[str], list[list[float]]]:
    """Empirical row-stochastic transition matrix over ``sequence``.

    States are ordered by first appearance. A state with no observed outgoing
    transition (only ever the final element, or a one-element sequence) is
    completed with a self-loop, i.e. treated as absorbing.
    """
    if not sequence:
        raise ValueError("sequence must be non-empty")

    states: list[str] = []
    index: dict[str, int] = {}
    for symbol in sequence:
        if symbol not in index:
            index[symbol] = len(states)
            states.append(symbol)

    n = len(states)
    counts = [[0.0] * n for _ in range(n)]
    for left, right in zip(sequence, sequence[1:], strict=False):
        counts[index[left]][index[right]] += 1.0

    matrix = []
    for i in range(n):
        row = counts[i]
        total = sum(row)
        if total <= 0:
            # No observed outgoing transition: assume it stays (absorbing).
            row = [0.0] * n
            row[i] = 1.0
        else:
            row = [v / total for v in row]
        matrix.append(row)

    return states, matrix


def stationary_distribution(
    matrix: Sequence[Sequence[float]],
    *,
    tol: float = 1e-12,
    max_iter: int = 10_000,
) -> list[float]:
    """Stationary distribution of a row-stochastic matrix via power iteration."""
    rows = [list(row) for row in matrix]
    n = len(rows)
    if n == 0:
        raise ValueError("matrix must be non-empty")
    if any(len(row) != n for row in rows):
        raise ValueError("matrix must be square")
    for row in rows:
        if any(v < 0 for v in row):
            raise ValueError("matrix entries must be non-negative")
        if abs(sum(row) - 1.0) > 1e-9:
            raise ValueError("matrix rows must sum to 1")

    pi = [1.0 / n] * n
    for _ in range(max_iter):
        nxt = [sum(pi[i] * rows[i][j] for i in range(n)) for j in range(n)]
        if max(abs(a - b) for a, b in zip(nxt, pi, strict=True)) < tol:
            pi = nxt
            break
        pi = nxt
    return pi


def entropy_rate(matrix: Sequence[Sequence[float]], distribution: Sequence[float]) -> float:
    """Conditional entropy rate ``H(X_{t+1} | X_t)`` in bits per run."""
    rows = [list(row) for row in matrix]
    pi = list(distribution)
    n = len(rows)
    if n == 0 or any(len(row) != n for row in rows):
        raise ValueError("matrix must be square and non-empty")
    _validate_distribution(pi, "distribution")
    if len(pi) != n:
        raise ValueError("distribution length must match matrix dimension")

    total = 0.0
    for i in range(n):
        row_entropy = -sum(p * log2(p) for p in rows[i] if p > 0)
        total += pi[i] * row_entropy
    return total
