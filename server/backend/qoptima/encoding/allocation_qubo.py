from __future__ import annotations

from dataclasses import dataclass
from typing import Any
import numpy as np

from ..problem import QOptimaPortfolioProblem


@dataclass(frozen=True)
class AllocationQUBO:
    """Binary-weight QUBO for native portfolio search.

    Each asset receives ``bits_per_asset`` binary variables. The binary number
    maps directly to an absolute portfolio weight between 0 and max_name_weight.
    Unlike the v0.1 move QUBO, these bits describe the portfolio itself rather
    than accepting/rejecting a classical optimizer's proposed move.
    """

    symbols: tuple[str, ...]
    bits_per_asset: int
    weight_matrix: np.ndarray  # shape (assets, variables), w = S @ x
    linear: np.ndarray
    quadratic: dict[tuple[int, int], float]
    constant: float
    target_gross: float
    gross_penalty: float
    risk_aversion: float
    turnover_penalty: float
    max_name_weight: float

    @property
    def num_variables(self) -> int:
        return int(self.weight_matrix.shape[1])

    def energy(self, bits) -> float:
        x = np.asarray(bits, dtype=float).reshape(-1)
        if len(x) != self.num_variables:
            raise ValueError("QUBO bit vector length mismatch")
        value = float(self.constant + self.linear @ x)
        for (i, j), q in self.quadratic.items():
            value += float(q) * float(x[i]) * float(x[j])
        return value

    def weights(self, bits) -> np.ndarray:
        x = np.asarray(bits, dtype=float).reshape(-1)
        if len(x) != self.num_variables:
            raise ValueError("QUBO bit vector length mismatch")
        return np.asarray(self.weight_matrix @ x, dtype=float)

    def as_dict(self) -> dict[str, Any]:
        return {
            "type": "native_binary_weight_portfolio",
            "num_variables": self.num_variables,
            "assets": len(self.symbols),
            "symbols": list(self.symbols),
            "bits_per_asset": int(self.bits_per_asset),
            "quadratic_terms": len(self.quadratic),
            "target_gross": float(self.target_gross),
            "gross_penalty": float(self.gross_penalty),
            "risk_aversion": float(self.risk_aversion),
            "turnover_penalty": float(self.turnover_penalty),
            "max_name_weight": float(self.max_name_weight),
            "weight_resolution": float(self.max_name_weight / max(1, 2 ** self.bits_per_asset - 1)),
            "energy_semantics": "-portfolio_objective + gross_penalty; reduced subproblems include frozen-holding objective offset",
        }


def build_allocation_qubo(
    problem: QOptimaPortfolioProblem,
    *,
    bits_per_asset: int = 2,
    gross_penalty: float = 80.0,
) -> AllocationQUBO:
    bits_per_asset = max(1, min(5, int(bits_per_asset)))
    n = problem.n
    m = n * bits_per_asset
    max_code = float(2 ** bits_per_asset - 1)
    step = float(problem.max_name_weight) / max_code

    # w = Sx. For asset i, bits represent 1,2,4,... units of ``step``.
    S = np.zeros((n, m), dtype=float)
    for i in range(n):
        for b in range(bits_per_asset):
            S[i, i * bits_per_asset + b] = step * float(2 ** b)

    mu = np.asarray(problem.expected_returns, dtype=float)
    cov = np.asarray(problem.optimization_covariance, dtype=float)
    current = np.asarray(problem.current_weights, dtype=float)
    lam = float(problem.risk_aversion)
    tau = float(problem.turnover_penalty)
    rho = float(gross_penalty)

    # cost = -mu'w + lam*w'Cov*w + tau*||w-current||^2
    #        + rho*(1'w-target)^2
    M = lam * (S.T @ cov @ S) + tau * (S.T @ S)
    gross_vec = S.sum(axis=0)
    M += rho * np.outer(gross_vec, gross_vec)

    linear = -(S.T @ mu) - 2.0 * tau * (S.T @ current) - 2.0 * rho * float(problem.target_gross) * gross_vec
    linear = np.asarray(linear + np.diag(M), dtype=float)

    quadratic: dict[tuple[int, int], float] = {}
    for i in range(m):
        for j in range(i + 1, m):
            q = float(2.0 * M[i, j])
            if abs(q) > 1e-15:
                quadratic[(i, j)] = q

    constant = float(tau * (current @ current) + rho * float(problem.target_gross) ** 2 - float(problem.objective_constant_offset))
    return AllocationQUBO(
        symbols=problem.symbols,
        bits_per_asset=bits_per_asset,
        weight_matrix=S,
        linear=linear,
        quadratic=quadratic,
        constant=constant,
        target_gross=float(problem.target_gross),
        gross_penalty=rho,
        risk_aversion=lam,
        turnover_penalty=tau,
        max_name_weight=float(problem.max_name_weight),
    )
