from __future__ import annotations

from dataclasses import dataclass
from typing import Any
import numpy as np

from ..problem import QOptimaPortfolioProblem


@dataclass(frozen=True)
class HardwareAllocationQUBO:
    """Hardware-scale binary-weight QUBO with optional explicit selection qubits.

    Variable ordering is asset-major: weight bits for asset i followed by one
    selection bit (when enabled).  The selection variable enables exact
    cardinality/replacement-style penalties without pretending unused qubits are
    doing useful work.
    """

    symbols: tuple[str, ...]
    bits_per_asset: int
    include_selection_qubit: bool
    weight_matrix: np.ndarray
    selection_indices: tuple[int | None, ...]
    linear: np.ndarray
    quadratic: dict[tuple[int, int], float]
    constant: float
    target_gross: float
    max_name_weight: float
    target_positions: int | None
    gross_penalty: float
    selection_link_penalty: float
    cardinality_penalty: float
    risk_aversion: float
    turnover_penalty: float

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

    def selected(self, bits) -> list[bool]:
        x = np.asarray(bits, dtype=float).reshape(-1)
        if not self.include_selection_qubit:
            return [bool(w > 1e-12) for w in self.weights(x)]
        return [bool(x[idx] >= 0.5) if idx is not None else False for idx in self.selection_indices]

    def as_dict(self) -> dict[str, Any]:
        return {
            "type": "hardware_binary_weight_selection_portfolio",
            "num_variables": self.num_variables,
            "assets": len(self.symbols),
            "symbols": list(self.symbols),
            "bits_per_asset": self.bits_per_asset,
            "include_selection_qubit": self.include_selection_qubit,
            "selection_qubits": sum(1 for x in self.selection_indices if x is not None),
            "quadratic_terms": len(self.quadratic),
            "target_gross": self.target_gross,
            "target_positions": self.target_positions,
            "max_name_weight": self.max_name_weight,
            "weight_resolution": self.max_name_weight / max(1, 2 ** self.bits_per_asset - 1),
            "penalties": {
                "gross": self.gross_penalty,
                "selection_link": self.selection_link_penalty,
                "cardinality": self.cardinality_penalty,
            },
        }


def _add_quadratic(store: dict[tuple[int, int], float], i: int, j: int, value: float) -> None:
    if i == j:
        raise ValueError("diagonal terms belong in linear coefficients")
    key = (i, j) if i < j else (j, i)
    store[key] = store.get(key, 0.0) + float(value)


def build_hardware_allocation_qubo(
    problem: QOptimaPortfolioProblem,
    *,
    bits_per_asset: int = 4,
    include_selection_qubit: bool = True,
    target_positions: int | None = None,
    gross_penalty: float = 80.0,
    selection_link_penalty: float = 20.0,
    cardinality_penalty: float = 12.0,
) -> HardwareAllocationQUBO:
    bits_per_asset = max(1, min(8, int(bits_per_asset)))
    n = int(problem.n)
    selection = bool(include_selection_qubit)
    stride = bits_per_asset + (1 if selection else 0)
    m = n * stride
    max_code = float(2 ** bits_per_asset - 1)
    step = float(problem.max_name_weight) / max_code

    S = np.zeros((n, m), dtype=float)
    selection_indices: list[int | None] = []
    for i in range(n):
        base = i * stride
        for b in range(bits_per_asset):
            S[i, base + b] = step * float(2 ** b)
        selection_indices.append(base + bits_per_asset if selection else None)

    mu = np.asarray(problem.expected_returns, dtype=float)
    cov = np.asarray(problem.optimization_covariance, dtype=float)
    current = np.asarray(problem.current_weights, dtype=float)
    lam = float(problem.risk_aversion)
    tau = float(problem.turnover_penalty)
    rho = float(gross_penalty)

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

    if selection:
        link = float(selection_link_penalty)
        for asset_i, y in enumerate(selection_indices):
            assert y is not None
            base = asset_i * stride
            # Penalty x_bit * (1-y): an allocation bit may not be active while
            # the security's include/exclude qubit is off.
            for b in range(bits_per_asset):
                xidx = base + b
                linear[xidx] += link
                _add_quadratic(quadratic, xidx, y, -link)

        if target_positions is not None:
            k = max(0, min(n, int(target_positions)))
            card = float(cardinality_penalty)
            ys = [int(i) for i in selection_indices if i is not None]
            # card * (sum y - k)^2
            constant += card * (k ** 2)
            for y in ys:
                linear[y] += card * (1.0 - 2.0 * k)
            for a in range(len(ys)):
                for b in range(a + 1, len(ys)):
                    _add_quadratic(quadratic, ys[a], ys[b], 2.0 * card)
            target_positions = k
    else:
        target_positions = None

    quadratic = {k: v for k, v in quadratic.items() if abs(v) > 1e-15}
    return HardwareAllocationQUBO(
        symbols=problem.symbols,
        bits_per_asset=bits_per_asset,
        include_selection_qubit=selection,
        weight_matrix=S,
        selection_indices=tuple(selection_indices),
        linear=linear,
        quadratic=quadratic,
        constant=constant,
        target_gross=float(problem.target_gross),
        max_name_weight=float(problem.max_name_weight),
        target_positions=target_positions,
        gross_penalty=rho,
        selection_link_penalty=float(selection_link_penalty),
        cardinality_penalty=float(cardinality_penalty),
        risk_aversion=lam,
        turnover_penalty=tau,
    )
