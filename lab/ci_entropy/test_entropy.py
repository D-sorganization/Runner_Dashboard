"""TDD contract tests for the information-theoretic lens on CI determinism.

These tests are written against the *intended* public API of
``lab.ci_entropy.entropy`` before that module exists. They assert the
mathematical invariants of Shannon entropy, relative entropy (KL divergence)
and a finite-state Markov chain's entropy rate, rather than hardcoded traces,
so they stay valid for any correct implementation.

Invariants under test
---------------------
- Shannon entropy: 0 for a single outcome, log2(n) for n uniform outcomes,
  non-negative, permutation-invariant.
- Relative entropy: Gibbs' inequality (>= 0), 0 iff distributions equal,
  undefined when q has no support where p does.
- Transition matrix: rows are stochastic (sum to 1); a state with no observed
  outgoing transition is completed with a self-loop.
- Stationary distribution: a fixed point of the row-stochastic matrix that
  sums to 1.
- Entropy rate: 0 for a deterministic chain, log2(n) for a uniform i.i.d.
  source, and always within [0, log2(n)].
"""

import math

import pytest

from lab.ci_entropy.entropy import (
    entropy_rate,
    normalize,
    relative_entropy,
    shannon_entropy,
    stationary_distribution,
    transition_matrix,
)

# --- normalize -----------------------------------------------------------------


def test_normalize_sums_to_one():
    assert sum(normalize([1.0, 2.0, 3.0])) == pytest.approx(1.0, abs=1e-12)


def test_normalize_keeps_proportions():
    assert normalize([1.0, 1.0, 2.0]) == pytest.approx([0.25, 0.25, 0.5])


def test_normalize_rejects_negative_count():
    with pytest.raises(ValueError):
        normalize([1.0, -1.0, 2.0])


def test_normalize_rejects_zero_or_empty():
    with pytest.raises(ValueError):
        normalize([])
    with pytest.raises(ValueError):
        normalize([0.0, 0.0])


# --- Shannon entropy -----------------------------------------------------------


def test_shannon_entropy_single_outcome_is_zero():
    assert shannon_entropy([0.0, 5.0, 0.0]) == pytest.approx(0.0, abs=1e-12)


def test_shannon_entropy_uniform_is_log2_n():
    # Four equally likely outcomes -> 2 bits exactly.
    assert shannon_entropy([1.0, 1.0, 1.0, 1.0]) == pytest.approx(2.0, abs=1e-12)


def test_shannon_entropy_is_nonnegative():
    for counts in ([0.1, 0.9], [1.0, 2.0, 7.0], [3.0, 3.0, 3.0, 3.0]):
        assert shannon_entropy(counts) >= 0.0


def test_shannon_entropy_is_permutation_invariant():
    assert shannon_entropy([1.0, 4.0, 2.0]) == pytest.approx(shannon_entropy([4.0, 2.0, 1.0]))


# --- Relative entropy (KL divergence) -------------------------------------------


def test_relative_entropy_zero_for_identical_distributions():
    p = [0.2, 0.5, 0.3]
    assert relative_entropy(p, p) == pytest.approx(0.0, abs=1e-12)


def test_relative_entropy_gibbs_nonnegative():
    cases = [
        ([0.2, 0.5, 0.3], [0.4, 0.4, 0.2]),
        ([0.5, 0.5], [0.1, 0.9]),
        ([0.1, 0.9], [0.9, 0.1]),
    ]
    for p, q in cases:
        assert relative_entropy(p, q) >= 0.0
        assert relative_entropy(q, p) >= 0.0


def test_relative_entropy_rejects_missing_support():
    # p assigns mass where q has none -> divergence is +inf, so reject.
    with pytest.raises(ValueError):
        relative_entropy([0.5, 0.5], [1.0, 0.0])


def test_relative_entropy_rejects_bad_distributions():
    with pytest.raises(ValueError):
        relative_entropy([0.5, 0.4], [0.5, 0.5])  # p does not sum to 1
    with pytest.raises(ValueError):
        relative_entropy([0.5, 0.5], [0.5, 0.4])  # q does not sum to 1


# --- Transition matrix ----------------------------------------------------------


def test_transition_matrix_rows_are_stochastic():
    seq = ["pass", "fail", "pass", "pass", "fail", "fail", "pass"]
    states, matrix = transition_matrix(seq)
    assert states == ["pass", "fail"]
    for row in matrix:
        assert sum(row) == pytest.approx(1.0, abs=1e-12)


def test_transition_matrix_self_loop_for_unseen_outgoing():
    # "b" has no outgoing transition -> completed as an absorbing self-loop.
    states, matrix = transition_matrix(["a", "b"])
    assert states == ["a", "b"]
    assert matrix[1][1] == pytest.approx(1.0, abs=1e-12)
    assert sum(matrix[1]) == pytest.approx(1.0, abs=1e-12)


# --- Stationary distribution ----------------------------------------------------


def test_stationary_distribution_sums_to_one():
    matrix = [[0.8, 0.2], [0.4, 0.6]]
    pi = stationary_distribution(matrix)
    assert sum(pi) == pytest.approx(1.0, abs=1e-9)


def test_stationary_distribution_is_fixed_point():
    # Closed form for this 2-state chain: pi = [2/3, 1/3].
    matrix = [[0.8, 0.2], [0.4, 0.6]]
    pi = stationary_distribution(matrix)
    assert pi == pytest.approx([2.0 / 3.0, 1.0 / 3.0], abs=1e-9)
    next_pi = [sum(pi[i] * matrix[i][j] for i in range(len(pi))) for j in range(len(pi))]
    assert next_pi == pytest.approx(pi, abs=1e-9)


# --- Entropy rate ----------------------------------------------------------------


def test_entropy_rate_deterministic_chain_is_zero():
    # Every state deterministically maps to the next -> zero surprise per run.
    identity = [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]]
    pi = stationary_distribution(identity)
    assert entropy_rate(identity, pi) == pytest.approx(0.0, abs=1e-12)


def test_entropy_rate_uniform_iid_is_log2_n():
    # Every row uniform, so the next run is independent of the last -> log2(3).
    n = 3
    uniform = [[1.0 / n] * n for _ in range(n)]
    pi = [1.0 / n] * n
    assert entropy_rate(uniform, pi) == pytest.approx(math.log2(n), abs=1e-9)


def test_entropy_rate_within_bounds():
    matrix = [[0.8, 0.2], [0.4, 0.6]]
    pi = stationary_distribution(matrix)
    h = entropy_rate(matrix, pi)
    assert 0.0 <= h <= math.log2(len(matrix))
