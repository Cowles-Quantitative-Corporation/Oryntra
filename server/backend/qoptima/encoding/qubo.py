from __future__ import annotations

from dataclasses import dataclass
from typing import Any
import numpy as np

from ..problem import QOptimaPortfolioProblem


@dataclass(frozen=True)
class MoveQUBO:
    symbols: tuple[str, ...]
    active_indices: tuple[int, ...]
    base_weights: np.ndarray
    deltas: np.ndarray
    linear: np.ndarray
    quadratic: dict[tuple[int, int], float]
    constant: float
    target_gross: float
    gross_penalty: float

    @property
    def num_variables(self) -> int:
        return len(self.active_indices)

    def energy(self, bits: np.ndarray | list[int] | tuple[int, ...]) -> float:
        x = np.asarray(bits, dtype=float).reshape(-1)
        if len(x) != self.num_variables:
            raise ValueError("QUBO bit vector length mismatch")
        value = float(self.constant + self.linear @ x)
        for (i, j), q in self.quadratic.items():
            value += float(q) * float(x[i]) * float(x[j])
        return value

    def weights(self, bits: np.ndarray | list[int] | tuple[int, ...]) -> np.ndarray:
        x = np.asarray(bits, dtype=float).reshape(-1)
        w = np.asarray(self.base_weights, dtype=float).copy()
        for local_i, global_i in enumerate(self.active_indices):
            w[global_i] += self.deltas[local_i] * x[local_i]
        return w

    def as_dict(self) -> dict[str, Any]:
        return {
            "num_variables": self.num_variables,
            "symbols": [self.symbols[i] for i in self.active_indices],
            "active_indices": list(self.active_indices),
            "linear": [float(x) for x in self.linear],
            "quadratic_terms": len(self.quadratic),
            "target_gross": float(self.target_gross),
            "gross_penalty": float(self.gross_penalty),
        }


def build_move_qubo(
    problem: QOptimaPortfolioProblem,
    classical_weights: np.ndarray,
    *,
    minimum_move: float = 0.005,
    max_variables: int = 14,
    gross_penalty: float = 50.0,
) -> MoveQUBO:
    """Encode only material classical moves as binary accept/reject decisions.

    This deliberately reduces the number of quantum variables. A bit does not
    represent a stock from scratch; it represents whether QOptima accepts a
    material move away from the active portfolio.
    """
    current = np.asarray(problem.current_weights, dtype=float).reshape(-1)
    classical = np.asarray(classical_weights, dtype=float).reshape(-1)
    moves = classical - current
    ranked = [i for i in np.argsort(-np.abs(moves)) if abs(float(moves[i])) >= float(minimum_move)]
    if not ranked:
        ranked = list(np.argsort(-np.abs(moves))[: min(problem.n, max_variables)])
    active = tuple(int(i) for i in ranked[: max(1, int(max_variables))])
    d = np.array([moves[i] for i in active], dtype=float)
    c = current.copy()
    mu = np.asarray(problem.expected_returns, dtype=float)
    cov = np.asarray(problem.covariance, dtype=float)

    # cost(x) = -objective(c + D x) + gross_penalty*(sum(w)-target)^2
    # represented as constant + linear*x + sum_{i<j} q_ij*x_i*x_j.
    dcovc = np.array([d[k] * float(cov[global_i] @ c) for k, global_i in enumerate(active)])
    linear = np.array([-mu[global_i] * d[k] for k, global_i in enumerate(active)], dtype=float)
    linear += 2.0 * problem.risk_aversion * dcovc
    linear += problem.turnover_penalty * np.square(d)

    A = np.zeros((len(active), len(active)), dtype=float)
    for a, ga in enumerate(active):
        for b, gb in enumerate(active):
            A[a, b] = problem.risk_aversion * d[a] * cov[ga, gb] * d[b]

    gross_offset = float(c.sum() - problem.target_gross)
    linear += 2.0 * gross_penalty * gross_offset * d
    A += gross_penalty * np.outer(d, d)

    # Diagonal x_i^2 == x_i.
    linear += np.diag(A)
    quadratic: dict[tuple[int, int], float] = {}
    for i in range(len(active)):
        for j in range(i + 1, len(active)):
            q = float(2.0 * A[i, j])
            if abs(q) > 1e-15:
                quadratic[(i, j)] = q

    constant = float(
        -mu @ c
        + problem.risk_aversion * (c @ cov @ c)
        + gross_penalty * gross_offset * gross_offset
    )
    return MoveQUBO(
        symbols=problem.symbols,
        active_indices=active,
        base_weights=c,
        deltas=d,
        linear=linear,
        quadratic=quadratic,
        constant=constant,
        target_gross=problem.target_gross,
        gross_penalty=float(gross_penalty),
    )
