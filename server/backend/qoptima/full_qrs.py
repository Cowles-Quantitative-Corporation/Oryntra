from __future__ import annotations

"""Canonical Full-QRS evaluator and quantum-search encodings.

Primary reviewed path: the QScan-selected finite portfolio domain is scored by
the complete piecewise QRS_FULL equation.  With V1A disabled, feasible basis
states retain the reviewed literal cost E(x) = -QRS_FULL(w(x)).  With V1A-QRS
v1.6 enabled, the raw economic energy remains -QRS_V1A(w(x)), but the phase
table applies the frozen robust *linear* low-energy scaling and high-energy cap
that preserve the feasible ground-state ordering while bounding QAOA phases.
States failing the configured hard viability gate receive a strictly dominated
finite phase energy.  No active-set or threshold surrogate replaces Full-QRS.

That exact finite-domain encoding is exponential (2**n score evaluations) and
is deliberately capped for research.  The older active-set threshold-QUBO
helpers remain below for backward compatibility and matched approximation
studies; they are no longer the primary Full-QRS objective.
"""

from dataclasses import dataclass
from typing import Any, Callable, Iterable

import numpy as np
import pandas as pd

from .encoding.allocation_qubo import AllocationQUBO
from .resilience import downside_covariance, correlation_collapse_covariance, regime_stress_covariance


FULL_QRS_SCHEMA = "cqc.qoptima.full_qrs.v1"
DEFAULT_CONDITION_NUMBER_CAP = 1.0e8


def _sym(a: np.ndarray) -> np.ndarray:
    x = np.asarray(a, dtype=float)
    return (x + x.T) / 2.0


def condition_covariance(
    matrix: np.ndarray,
    *,
    max_condition_number: float = DEFAULT_CONDITION_NUMBER_CAP,
    numerical_floor: float = 1e-12,
) -> tuple[np.ndarray, dict[str, Any]]:
    """Condition a covariance matrix using a deterministic eigenvalue floor."""
    a = _sym(np.asarray(matrix, dtype=float))
    vals, vecs = np.linalg.eigh(a)
    max_eig = float(max(float(vals.max()) if vals.size else 0.0, numerical_floor))
    kappa = max(1.0, float(max_condition_number))
    floor = max(float(numerical_floor), max_eig / kappa)
    clipped = np.maximum(vals, floor)
    out = _sym((vecs * clipped) @ vecs.T)
    denom = float(np.linalg.norm(a, ord="fro"))
    delta = float(np.linalg.norm(out - a, ord="fro"))
    raw_min = float(vals.min()) if vals.size else 0.0
    return out, {
        "conditioning_policy": "max_condition_number_floor_v1",
        "max_condition_number": kappa,
        "lambda_max": max_eig,
        "lambda_min_raw": raw_min,
        "lambda_floor": floor,
        "lambda_min_conditioned": float(clipped.min()) if clipped.size else floor,
        "condition_number_conditioned": float(clipped.max() / clipped.min()) if clipped.size else 1.0,
        "relative_frobenius_change": delta / denom if denom > 0 else 0.0,
        "eigenvalues_floored": int(np.sum(vals < floor)),
    }


def _stress_counts(returns: pd.DataFrame) -> dict[str, int]:
    clean = returns.replace([np.inf, -np.inf], np.nan).dropna(how="any")
    if clean.empty:
        return {"base": 0, "downside": 0, "correlation_collapse": len(clean), "regime": 0}
    market = clean.mean(axis=1)
    downside = clean.loc[market < 0]
    if len(downside) < max(12, int(0.20 * len(clean))):
        cutoff = float(market.quantile(0.35))
        downside = clean.loc[market <= cutoff]
    rolling = market.rolling(20, min_periods=10).std()
    threshold = float(rolling.dropna().quantile(0.75)) if rolling.notna().any() else float("nan")
    regime = clean.loc[rolling >= threshold] if np.isfinite(threshold) else clean.iloc[0:0]
    return {
        "base": int(len(clean)),
        "downside": int(len(downside)),
        "correlation_collapse": int(len(clean)),
        "regime": int(len(regime)),
    }


def _sample_reliability(n_eff: int, dimension: int) -> float:
    """Dimension-adjusted estimator reliability, not fitted to portfolio returns.

    This is deliberately simple and versioned.  A sparse stress sample shrinks
    toward the base covariance; a large sample approaches the stress estimate.
    It is an engineering/statistical policy, not a backtest-tuned alpha.
    """
    n = max(0, int(n_eff))
    p = max(1, int(dimension))
    if n <= 2:
        return 0.0
    return float(np.clip((n - 2.0) / (n + 2.0 * p), 0.0, 1.0))


def build_full_qrs_scenarios(
    returns: pd.DataFrame,
    base_covariance: np.ndarray,
    *,
    correlation_blend: float = 0.65,
    max_condition_number: float = DEFAULT_CONDITION_NUMBER_CAP,
    v1a_gamma: float | None = None,
    v1a_additive_floor: float = 1e-8,
) -> tuple[dict[str, np.ndarray], dict[str, Any]]:
    """Build Canonical or V1A-v1.6 state-conditioned covariance scenarios.

    With ``v1a_gamma is None`` this preserves the reviewed v0.4 scenario builder.
    V1A mode uses a fixed additive covariance floor and soft RMT directional
    shrinkage.  The Canonical reliability-shrunk stress matrix is the Loewner
    floor; V1A can only recover positive raw-stress headroom and never exceed
    the raw generalized stress eigenvalue.
    """
    counts = _stress_counts(returns)
    if v1a_gamma is None:
        base, base_cond = condition_covariance(base_covariance, max_condition_number=max_condition_number)
        raw = {
            "BASE": base,
            "DOWNSIDE": downside_covariance(returns, base),
            "CORRELATION_COLLAPSE": correlation_collapse_covariance(base, blend=correlation_blend),
            "REGIME_HIGH_VOL": regime_stress_covariance(returns, base),
        }
        n_assets = int(base.shape[0])
        out: dict[str, np.ndarray] = {}
        meta: dict[str, Any] = {
            "schema": FULL_QRS_SCHEMA,
            "scenario_set_version": "QRS_STRESS_SET_V1",
            "scenario_membership": list(raw.keys()),
            "reliability_policy": "dimension_adjusted_sample_reliability_v1",
            "shrinkage_target": "conditioned_base_covariance",
            "correlation_blend": float(correlation_blend),
            "scenarios": {},
            "base_conditioning": base_cond,
            "v1a_enabled": False,
        }
        for name, stress_raw in raw.items():
            if name == "BASE":
                rho = 1.0
                sample_count = counts["base"]
                shrunk = base
            elif name == "CORRELATION_COLLAPSE":
                rho = 1.0
                sample_count = counts["correlation_collapse"]
                shrunk = np.asarray(stress_raw, dtype=float)
            else:
                key = "downside" if name == "DOWNSIDE" else "regime"
                sample_count = counts[key]
                rho = _sample_reliability(sample_count, n_assets)
                shrunk = (1.0 - rho) * base + rho * np.asarray(stress_raw, dtype=float)
            conditioned, cond = condition_covariance(shrunk, max_condition_number=max_condition_number)
            out[name] = conditioned
            meta["scenarios"][name] = {
                "effective_sample_count": int(sample_count),
                "reliability_rho": float(rho),
                "construction": (
                    "conditioned_base_covariance"
                    if name == "BASE"
                    else "deterministic_correlation_stress_from_base"
                    if name == "CORRELATION_COLLAPSE"
                    else "conditional_covariance_shrunk_toward_base"
                ),
                "conditioning": cond,
            }
        return out, meta

    from .v1a.stress import additive_floor, soft_rmt_v1a_scenario

    eps = max(float(v1a_additive_floor), 1e-16)
    gamma = float(np.clip(v1a_gamma, 0.0, 1.0))
    base = additive_floor(np.asarray(base_covariance, dtype=float), eps)
    raw = {
        "BASE": base,
        "DOWNSIDE": downside_covariance(returns, base),
        "CORRELATION_COLLAPSE": correlation_collapse_covariance(base, blend=correlation_blend),
        "REGIME_HIGH_VOL": regime_stress_covariance(returns, base),
    }
    n_assets = int(base.shape[0])
    out: dict[str, np.ndarray] = {"BASE": base}
    meta = {
        "schema": FULL_QRS_SCHEMA,
        "scenario_set_version": "V1A_QRS_STRESS_SET_V1_6",
        "scenario_membership": list(raw.keys()),
        "reliability_policy": "dimension_adjusted_sample_reliability_v1",
        "shrinkage_target": "additively_floored_base_covariance",
        "correlation_blend": float(correlation_blend),
        "scenarios": {
            "BASE": {
                "effective_sample_count": int(counts["base"]),
                "reliability_rho": 1.0,
                "construction": "fixed_additive_covariance_floor",
            }
        },
        "v1a_enabled": True,
        "v1a_spec_version": "cqc.qoptima.v1a_qrs.v1.6",
        "v1a_gamma": gamma,
        "additive_covariance_floor": eps,
    }
    for name, stress_raw in raw.items():
        if name == "BASE":
            continue
        if name == "CORRELATION_COLLAPSE":
            rho = 1.0
            sample_count = counts["correlation_collapse"]
        else:
            key = "downside" if name == "DOWNSIDE" else "regime"
            sample_count = counts[key]
            rho = _sample_reliability(sample_count, n_assets)
        matrix, details = soft_rmt_v1a_scenario(
            base, np.asarray(stress_raw, dtype=float),
            reliability_rho=float(rho), gamma=gamma,
            effective_sample_count=int(sample_count),
            additive_floor_epsilon=eps,
        )
        out[name] = matrix
        meta["scenarios"][name] = {
            "effective_sample_count": int(sample_count),
            "reliability_rho": float(rho),
            "construction": "V1A_SOFT_RMT_STRESS_V1_6",
            "v1a": details,
        }
    return out, meta


def conservative_portfolio_error_quantile(
    error_samples: np.ndarray | None,
    weights: np.ndarray,
    *,
    beta: float = 0.10,
    bootstrap_reps: int = 256,
    confidence: float = 0.90,
    seed: int = 41,
) -> dict[str, Any]:
    """Conservative lower-tail portfolio forecast-error estimate.

    The empirical beta-quantile is supplemented with a deterministic bootstrap
    lower confidence bound.  This addresses sampling uncertainty, not unknown
    future distribution shifts; that limitation is explicit in provenance.
    """
    if error_samples is None:
        return {
            "available": False,
            "value": 0.0,
            "reason": "no_point_in_time_realized_forecast_error_samples",
            "active_sample_index": None,
            "sample_count": 0,
        }
    samples = np.asarray(error_samples, dtype=float)
    w = np.asarray(weights, dtype=float).reshape(-1)
    if samples.ndim != 2 or samples.shape[1] != len(w) or len(samples) < 2 or not np.isfinite(samples).all():
        return {
            "available": False,
            "value": 0.0,
            "reason": "insufficient_or_misaligned_forecast_error_samples",
            "active_sample_index": None,
            "sample_count": int(samples.shape[0]) if samples.ndim == 2 else 0,
        }
    b = float(np.clip(beta, 1e-4, 0.4999))
    values = samples @ w
    try:
        empirical = float(np.quantile(values, b, method="lower"))
    except TypeError:  # numpy<1.22 compatibility
        empirical = float(np.quantile(values, b, interpolation="lower"))
    conservative = empirical
    reps = max(0, int(bootstrap_reps))
    if reps > 0 and len(values) >= 8:
        from .scoring_memo import bootstrap_indices
        n = len(values)
        indices = bootstrap_indices(n, reps, int(seed))
        if indices is not None:
            try:
                q = np.quantile(values[indices], b, axis=1, method="lower")
            except TypeError:
                q = np.quantile(values[indices], b, axis=1, interpolation="lower")
        else:
            rng = np.random.default_rng(int(seed))
            q = np.empty(reps, dtype=float)
            for i in range(reps):
                draw = values[rng.integers(0, n, size=n)]
                try:
                    q[i] = float(np.quantile(draw, b, method="lower"))
                except TypeError:
                    q[i] = float(np.quantile(draw, b, interpolation="lower"))
        tail_prob = float(np.clip(1.0 - confidence, 0.001, 0.5))
        try:
            lower_ci = float(np.quantile(q, tail_prob, method="lower"))
        except TypeError:
            lower_ci = float(np.quantile(q, tail_prob, interpolation="lower"))
        conservative = min(empirical, lower_ci)
    idx = int(np.argmin(np.abs(values - conservative)))
    return {
        "available": True,
        "value": float(conservative),
        "empirical_quantile": float(empirical),
        "beta": b,
        "confidence": float(confidence),
        "bootstrap_reps": reps,
        "sample_count": int(len(values)),
        "active_sample_index": idx,
        "active_sample_projection": float(values[idx]),
        "limitation": "sampling uncertainty only; does not guarantee robustness to unmodeled distribution shifts",
    }


def _rates(problem: Any) -> tuple[float, float]:
    lend = float(problem.risk_free_rate if getattr(problem, "lending_rate", None) is None else problem.lending_rate)
    borrow = float(lend if getattr(problem, "borrowing_rate", None) is None else problem.borrowing_rate)
    return lend, borrow


def score_full_qrs(
    problem: Any,
    weights: np.ndarray,
    *,
    margin_feasible: bool | None = None,
    bootstrap_seed: int = 41,
) -> dict[str, Any]:
    """Evaluate the canonical full QRS exactly for a candidate portfolio.

    ``margin_feasible=False`` applies the literal canonical -infinity gate.
    ``None`` means an exact broker margin engine was not evaluated; the score is
    still reported for research ranking and provenance clearly marks the gate as
    unavailable.  Control's separate QRM gate remains mandatory downstream.
    """
    w = np.asarray(weights, dtype=float).reshape(-1)
    mu = np.asarray(problem.expected_returns, dtype=float).reshape(-1)
    if len(w) != len(mu):
        raise ValueError("full QRS weights do not align with expected returns")
    lend, borrow = _rates(problem)
    gross = float(w.sum())
    cash = 1.0 - gross
    cash_lending = max(cash, 0.0) * lend
    borrowing_cost = max(-cash, 0.0) * borrow
    expected_with_financing = float(mu @ w + cash_lending - borrowing_cost)
    excess_over_cash = expected_with_financing - lend

    current = np.asarray(problem.current_weights, dtype=float)
    turnover_l1 = float(np.abs(w - current).sum())
    transaction_cost = turnover_l1 * float(problem.transaction_cost_bps) / 10000.0

    if bool(getattr(problem, "v1a_enabled", False)):
        from .v1a.residual_tail import standardized_conservative_portfolio_tail
        tail = standardized_conservative_portfolio_tail(
            getattr(problem, "forecast_error_samples", None),
            w,
            beta=float(getattr(problem, "full_qrs_beta", 0.10)),
            confidence=float(getattr(problem, "full_qrs_quantile_confidence", 0.90)),
            covariance_window=int(getattr(problem, "v1a_forecast_error_covariance_window", 60)),
            min_history=int(getattr(problem, "v1a_forecast_error_min_history", 12)),
            covariance_floor=float(getattr(problem, "v1a_forecast_error_covariance_floor", 1e-8)),
            block_stride=int(getattr(problem, "v1a_quantile_block_stride", 1)),
            information_cutoffs=getattr(problem, "v1a_forecast_error_information_cutoffs", None),
            realized_at=getattr(problem, "v1a_forecast_error_realized_at", None),
            regime_labels=getattr(problem, "v1a_forecast_error_regime_labels", None),
            current_regime_label=getattr(problem, "v1a_current_regime_label", None),
        )
    else:
        tail = conservative_portfolio_error_quantile(
            getattr(problem, "forecast_error_samples", None),
            w,
            beta=float(getattr(problem, "full_qrs_beta", 0.10)),
            bootstrap_reps=int(getattr(problem, "full_qrs_bootstrap_reps", 256)),
            confidence=float(getattr(problem, "full_qrs_quantile_confidence", 0.90)),
            seed=int(bootstrap_seed),
        )
    tail_value = float(tail.get("value") or 0.0)
    numerator = float(excess_over_cash - transaction_cost + tail_value)

    scenarios = dict(getattr(problem, "full_qrs_stress_covariances", None) or {})
    base = np.asarray(scenarios.get("BASE", problem.covariance), dtype=float)
    base_var = max(0.0, float(w @ base @ w))
    if not scenarios:
        scenarios = {"BASE": np.asarray(problem.optimization_covariance, dtype=float)}
    scenario_variances: dict[str, float] = {}
    for name, matrix in scenarios.items():
        a = np.asarray(matrix, dtype=float)
        scenario_variances[str(name)] = max(0.0, float(w @ a @ w))
    worst_name = max(scenario_variances, key=scenario_variances.get)
    worst_var = float(scenario_variances[worst_name])
    alpha = float(np.clip(getattr(problem, "resilience_alpha", 1.0), 0.0, 1.0))
    robust_var = max(0.0, (1.0 - alpha) * base_var + alpha * worst_var)
    robust_vol = float(np.sqrt(robust_var))
    v1a_enabled = bool(getattr(problem, "v1a_enabled", False))
    if v1a_enabled:
        # QOptima covariance and expected-return contracts are annualized, so the
        # Control risk budget is expressed in annualized-volatility units as well.
        risk_budget = max(float(getattr(problem, "v1a_risk_budget_annual", 0.15)), 1e-12)
        risk_floor = max(float(getattr(problem, "v1a_risk_floor_ratio", 0.10)) * risk_budget, 1e-12)
        risk_epsilon = float(np.sqrt(robust_vol * robust_vol + risk_floor * risk_floor))
        if numerator > 0.0:
            raw_qrs = float(numerator / risk_epsilon)
            score_branch = "positive_opportunity_ratio"
        else:
            raw_qrs = float(numerator * max(risk_epsilon, risk_budget) / (risk_budget * risk_budget))
            score_branch = "nonpositive_opportunity_defensive"
    else:
        risk_budget = None
        risk_floor = None
        risk_epsilon = robust_vol
        raw_qrs = numerator / robust_vol if robust_vol > 1e-15 else None
        score_branch = "legacy_full_qrs_ratio"

    margin_status = "NOT_EVALUATED" if margin_feasible is None else ("PASS" if margin_feasible else "FAIL")
    qrs = raw_qrs
    finite = qrs is not None and np.isfinite(qrs)
    if margin_feasible is False:
        qrs = float("-inf")
        finite = False

    # A diagnostic gross score before transaction cost / forecast-tail haircut.
    gross_numerator = float(excess_over_cash)
    if v1a_enabled:
        gross_qrs = gross_numerator / max(float(risk_epsilon), 1e-12)
    else:
        gross_qrs = gross_numerator / robust_vol if robust_vol > 1e-15 else None
    return {
        "schema": FULL_QRS_SCHEMA,
        "qrs": qrs,
        "finite": bool(finite),
        "raw_qrs_before_margin_gate": raw_qrs,
        "gross_qrs_before_cost_and_forecast_tail": gross_qrs,
        "numerator": numerator,
        "expected_return_with_cash_and_financing": expected_with_financing,
        "excess_over_lending_baseline": excess_over_cash,
        "cash_weight": cash,
        "lending_rate": lend,
        "borrowing_rate": borrow,
        "cash_lending_return": cash_lending,
        "borrowing_cost": borrowing_cost,
        "turnover_l1": turnover_l1,
        "transaction_cost_return": transaction_cost,
        "forecast_error_tail": tail,
        "base_variance": base_var,
        "worst_stress_variance": worst_var,
        "worst_stress_scenario": worst_name,
        "scenario_variances": scenario_variances,
        "resilience_alpha": alpha,
        "robust_variance": robust_var,
        "robust_volatility": robust_vol,
        "v1a_enabled": v1a_enabled,
        "v1a_gamma": float(getattr(problem, "v1a_gamma", 0.0)) if v1a_enabled else None,
        "v1a_score_branch": score_branch,
        "v1a_risk_budget_annual": risk_budget,
        "v1a_risk_floor": risk_floor,
        "v1a_risk_epsilon": risk_epsilon if v1a_enabled else None,
        "positive_numerator": bool(numerator > 0.0),
        "margin_gate": margin_status,
        "margin_gate_literal_minus_infinity_applied": bool(margin_feasible is False),
        "scenario_set_version": dict(getattr(problem, "full_qrs_stress_metadata", None) or {}).get("scenario_set_version"),
        "limitation": "robustness is conditional on modeled forecast errors and the explicit scenario set; unmodeled future states remain outside the guarantee",
    }


def active_full_qrs_state(problem: Any, weights: np.ndarray, *, seed: int = 41) -> dict[str, Any]:
    w = np.asarray(weights, dtype=float).reshape(-1)
    score = score_full_qrs(problem, w, margin_feasible=None, bootstrap_seed=seed)
    scenarios = dict(getattr(problem, "full_qrs_stress_covariances", None) or {})
    active_scenario = str(score["worst_stress_scenario"])
    if active_scenario not in scenarios:
        scenarios[active_scenario] = np.asarray(problem.optimization_covariance, dtype=float)
    tail = score.get("forecast_error_tail") or {}
    tail_idx = tail.get("active_sample_index")
    samples = getattr(problem, "forecast_error_samples", None)
    if samples is not None and tail_idx is not None:
        error_vector = np.asarray(samples, dtype=float)[int(tail_idx)].copy()
    else:
        error_vector = np.zeros(problem.n, dtype=float)
    current = np.asarray(problem.current_weights, dtype=float)
    signs = np.sign(w - current)
    # If a weight is exactly unchanged, choose a deterministic directional
    # subgradient from relative expected return so turnover cannot become a free
    # negative reward in the first active-set QUBO.
    lend, _ = _rates(problem)
    undecided = np.abs(signs) < 0.5
    signs[undecided] = np.where(np.asarray(problem.expected_returns)[undecided] >= lend, 1.0, -1.0)
    return {
        "active_scenario": active_scenario,
        "active_error_sample_index": None if tail_idx is None else int(tail_idx),
        "active_error_vector": error_vector,
        "turnover_signs": signs.astype(float),
        "score": score,
    }


@dataclass(frozen=True)
class FullQRSThresholdQUBO:
    threshold: float
    qubo: AllocationQUBO
    selected_indices: tuple[int, ...]
    fixed_weights: np.ndarray
    full_weight_matrix: np.ndarray
    numerator_constant: float
    numerator_linear_bits: np.ndarray
    risk_constant: float
    risk_linear_bits: np.ndarray
    risk_quadratic_bits: np.ndarray
    active_scenario: str
    active_error_sample_index: int | None
    turnover_signs: np.ndarray
    positive_numerator_bias: float = 1e-3

    def full_weights(self, bits: Iterable[int | float]) -> np.ndarray:
        x = np.asarray(list(bits), dtype=float).reshape(-1)
        return np.asarray(self.fixed_weights + self.full_weight_matrix @ x, dtype=float)


def _weight_encoding(problem: Any, selected_indices: list[int], bits_per_asset: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    selected = np.asarray(selected_indices, dtype=int)
    if len(selected) == 0:
        raise ValueError("full QRS QUBO requires at least one selected asset")
    bits = max(1, min(5, int(bits_per_asset)))
    max_code = float(2**bits - 1)
    step = float(problem.max_name_weight) / max_code
    m = len(selected) * bits
    local = np.zeros((len(selected), m), dtype=float)
    for i in range(len(selected)):
        for b in range(bits):
            local[i, i * bits + b] = step * float(2**b)
    W = np.zeros((problem.n, m), dtype=float)
    for local_i, global_i in enumerate(selected):
        W[global_i, :] = local[local_i, :]
    fixed = np.asarray(problem.current_weights, dtype=float).copy()
    fixed[selected] = 0.0
    return local, W, fixed




@dataclass
class ExactFullQRSDiagonalObjective:
    """Literal Full Canonical QRS as a finite diagonal cost Hamiltonian.

    The encoded portfolio domain is finite.  For every basis state this object
    evaluates ``score_full_qrs`` including cash/borrowing, L1 trading cost,
    conservative portfolio forecast-error tail, worst reliability-shrunk stress
    covariance, and the configured hard viability callback.  Feasible states are
    mapped to the minimization energy ``-QRS_FULL``.  States whose canonical QRS
    is -infinity receive a strictly
    dominated finite phase energy so the quantum phase operator itself remains
    well-defined.

    No active stress/tail/turnover approximation is used.  The cost Hamiltonian
    is therefore exact on the finite encoded domain.  The price is exponential
    table/oracle construction: 2**num_variables Full-QRS evaluations.
    """

    problem: Any
    selected_indices: tuple[int, ...]
    bits_per_asset: int
    fixed_weights: np.ndarray
    full_weight_matrix: np.ndarray
    viability_callback: Callable[[np.ndarray], bool | tuple[bool, dict[str, Any]]] | None = None
    bootstrap_seed: int = 41
    require_viability_gate: bool = True
    infeasible_phase_energy: float = 2.0
    exact_diagonal_cost: bool = True
    objective_kind: str = "full_canonical_qrs_exact_diagonal"
    _energy_cache: np.ndarray | None = None
    _raw_energy_cache: np.ndarray | None = None
    _qrs_cache: np.ndarray | None = None
    _feasible_cache: np.ndarray | None = None
    _metadata_cache: dict[str, Any] | None = None

    @property
    def num_variables(self) -> int:
        return int(self.full_weight_matrix.shape[1])

    def full_weights(self, bits: Iterable[int | float]) -> np.ndarray:
        x = np.asarray(list(bits), dtype=float).reshape(-1)
        if len(x) != self.num_variables:
            raise ValueError(f"expected {self.num_variables} Full-QRS bits, got {len(x)}")
        return np.asarray(self.fixed_weights + self.full_weight_matrix @ x, dtype=float)

    @staticmethod
    def _index(bits: Iterable[int | float]) -> int:
        value = 0
        for i, bit in enumerate(bits):
            if int(round(float(bit))) != 0:
                value |= 1 << i
        return int(value)

    def _viability(self, weights: np.ndarray) -> tuple[bool | None, dict[str, Any]]:
        if self.viability_callback is None:
            if self.require_viability_gate:
                raise ValueError(
                    "Literal QRS_FULL optimization requires a deterministic viability/margin gate callback; "
                    "otherwise the piecewise -infinity branch is undefined."
                )
            return None, {"source": "NOT_EVALUATED"}
        raw = self.viability_callback(np.asarray(weights, dtype=float))
        if isinstance(raw, tuple):
            return bool(raw[0]), dict(raw[1] or {})
        return bool(raw), {}

    def _build_table(self) -> None:
        if self._energy_cache is not None:
            return
        n = self.num_variables
        states = 2 ** n
        qrs_values = np.full(states, float("-inf"), dtype=np.float64)
        feasible = np.zeros(states, dtype=bool)
        viability_pass = 0
        viability_fail = 0
        invalid_score = 0
        for index in range(states):
            bits = np.fromiter(((index >> i) & 1 for i in range(n)), dtype=np.float64, count=n)
            weights = np.asarray(self.fixed_weights + self.full_weight_matrix @ bits, dtype=float)
            if bool(getattr(self.problem, "v1a_enabled", False)):
                # V1A-QRS ranks composition at a Control-approved scale.  The
                # lattice is therefore constrained to a narrow gross-exposure
                # bucket rather than being allowed to choose leverage implicitly.
                step = float(self.problem.max_name_weight) / max(1.0, float(2 ** int(self.bits_per_asset) - 1))
                tolerance = max(float(getattr(self.problem, "v1a_scale_tolerance", 0.03)), 0.5 * step)
                if abs(float(weights.sum()) - float(self.problem.target_gross)) > tolerance:
                    viability_fail += 1
                    continue
            gate, _meta = self._viability(weights)
            if gate is False:
                viability_fail += 1
                continue
            if gate is True:
                viability_pass += 1
            score = score_full_qrs(
                self.problem,
                weights,
                margin_feasible=gate,
                bootstrap_seed=int(self.bootstrap_seed),
            )
            q = score.get("qrs")
            if q is None or not np.isfinite(q):
                invalid_score += 1
                continue
            qrs_values[index] = float(q)
            feasible[index] = True

        energy = np.full(states, float(self.infeasible_phase_energy), dtype=np.float64)
        raw_energy = np.full(states, float("inf"), dtype=np.float64)
        phase_meta: dict[str, Any] = {"policy": "legacy_literal_negative_qrs"}
        if feasible.any():
            finite_q = qrs_values[feasible]
            q_max = float(finite_q.max())
            q_min = float(finite_q.min())
            span = float(q_max - q_min)
            feasible_raw = -finite_q
            raw_energy[feasible] = feasible_raw
            if bool(getattr(self.problem, "v1a_enabled", False)):
                # V1A v1.6: preserve resolution around the best states without
                # allowing an outlier or near-tie to define the entire phase scale.
                best = float(feasible_raw.min())
                low_q = float(np.quantile(feasible_raw, float(getattr(self.problem, "v1a_qaoa_low_tail_quantile", 0.05))))
                q25, q75 = np.quantile(feasible_raw, [0.25, 0.75])
                local = max(0.0, low_q - best)
                global_scale = max(0.0, float(q75 - q25))
                eta = max(0.0, float(getattr(self.problem, "v1a_qaoa_phase_global_floor_ratio", 0.10)))
                abs_floor = max(1e-15, float(getattr(self.problem, "v1a_qaoa_phase_absolute_floor", 1e-8)))
                phase_scale = max(local, eta * global_scale, abs_floor)
                cap = max(1.0, float(getattr(self.problem, "v1a_qaoa_energy_cap", 8.0)))
                gap = max(1e-6, float(getattr(self.problem, "v1a_qaoa_infeasible_gap", 1.0)))
                feasible_energy = np.minimum((feasible_raw - best) / phase_scale, cap)
                energy[feasible] = feasible_energy
                infeasible_energy = cap + gap
                energy[~feasible] = infeasible_energy
                scale = phase_scale
                phase_meta = {
                    "policy": "V1A_QAOA_LOW_ENERGY_LINEAR_SCALE_V1_6",
                    "raw_energy_definition": "-QRS_V1A",
                    "low_tail_quantile": float(getattr(self.problem, "v1a_qaoa_low_tail_quantile", 0.05)),
                    "local_scale": local,
                    "global_iqr_scale": global_scale,
                    "global_floor_ratio": eta,
                    "phase_scale": phase_scale,
                    "high_energy_cap": cap,
                    "infeasible_gap": gap,
                    "best_raw_energy": best,
                }
            else:
                # Reviewed v0.4 path is unchanged when V1A is disabled.
                feasible_energy = feasible_raw
                energy[feasible] = feasible_energy
                worst_feasible_energy = float(feasible_energy.max())
                guard = max(1.0, abs(span), abs(worst_feasible_energy) * 0.05)
                infeasible_energy = worst_feasible_energy + guard
                energy[~feasible] = infeasible_energy
                scale = 1.0
            best_index = int(np.flatnonzero(feasible)[int(np.argmax(finite_q))])
        else:
            q_max = q_min = None
            scale = None
            infeasible_energy = float(self.infeasible_phase_energy)
            best_index = None

        self._energy_cache = energy
        self._raw_energy_cache = raw_energy
        self._qrs_cache = qrs_values
        self._feasible_cache = feasible
        self._metadata_cache = {
            "schema": FULL_QRS_SCHEMA,
            "objective": "literal_full_canonical_qrs",
            "hamiltonian_encoding": "exact_finite_domain_diagonal_truth_table_v1",
            "selected_indices": [int(i) for i in self.selected_indices],
            "bits_per_asset": int(self.bits_per_asset),
            "variables": int(n),
            "basis_states": int(states),
            "feasible_states": int(feasible.sum()),
            "infeasible_or_invalid_states": int(states - feasible.sum()),
            "viability_pass_states": int(viability_pass),
            "viability_fail_states": int(viability_fail),
            "invalid_score_states": int(invalid_score),
            "qrs_max": q_max,
            "qrs_min": q_min,
            "qrs_affine_scale": scale,
            "feasible_energy_definition": (
                "robust_linear_phase_normalization_of_-QRS_V1A"
                if bool(getattr(self.problem, "v1a_enabled", False))
                else "E(x) = -QRS_FULL(w(x))"
            ),
            "phase_normalization": phase_meta,
            "infeasible_phase_energy": float(infeasible_energy),
            "global_best_basis_index_for_validation": best_index,
            "active_set_surrogate": False,
            "threshold_surrogate": False,
            "full_formula_terms_in_cost": [
                "forecast_return",
                "cash_lending_and_borrowing",
                "cash_benchmark",
                "l1_turnover_transaction_cost",
                "conservative_portfolio_forecast_error_tail",
                "ordinary_covariance_risk",
                "worst_reliability_shrunk_stress_risk",
                "hard_configured_viability_gate",
            ],
            "scalability_limitation": (
                "An arbitrary exact diagonal Full-QRS Hamiltonian requires 2^n finite-domain cost values in this implementation. "
                "It is intended for small QScan-selected research problems and is not a scalable quantum-advantage claim."
            ),
        }

    def energy_table(self) -> np.ndarray:
        self._build_table()
        return np.asarray(self._energy_cache, dtype=np.float64)

    def raw_energy_table(self) -> np.ndarray:
        self._build_table()
        return np.asarray(self._raw_energy_cache, dtype=np.float64)

    def qrs_table(self) -> np.ndarray:
        self._build_table()
        return np.asarray(self._qrs_cache, dtype=np.float64)

    def feasible_table(self) -> np.ndarray:
        self._build_table()
        return np.asarray(self._feasible_cache, dtype=bool)

    def metadata(self) -> dict[str, Any]:
        self._build_table()
        return dict(self._metadata_cache or {})

    def energy(self, bits: Iterable[int | float]) -> float:
        table = self.energy_table()
        return float(table[self._index(bits)])

    def qrs(self, bits: Iterable[int | float]) -> float:
        table = self.qrs_table()
        return float(table[self._index(bits)])


def build_exact_full_qrs_diagonal_objective(
    problem: Any,
    selected_indices: list[int],
    *,
    bits_per_asset: int = 2,
    viability_callback: Callable[[np.ndarray], bool | tuple[bool, dict[str, Any]]] | None,
    seed: int = 41,
    require_viability_gate: bool = True,
) -> ExactFullQRSDiagonalObjective:
    """Encode the complete finite-domain Full-QRS scorer for diagonal QAOA.

    V1A keeps the literal economic score in ``raw_energy_table`` and applies
    only the frozen ground-state-preserving phase normalization to
    ``energy_table``.
    """
    selected = list(dict.fromkeys(int(i) for i in selected_indices))
    if any(i < 0 or i >= problem.n for i in selected):
        raise ValueError("selected Full-QRS indices out of range")
    _local, W, fixed = _weight_encoding(problem, selected, bits_per_asset)
    return ExactFullQRSDiagonalObjective(
        problem=problem,
        selected_indices=tuple(selected),
        bits_per_asset=max(1, min(5, int(bits_per_asset))),
        fixed_weights=np.asarray(fixed, dtype=float),
        full_weight_matrix=np.asarray(W, dtype=float),
        viability_callback=viability_callback,
        bootstrap_seed=int(seed),
        require_viability_gate=bool(require_viability_gate),
    )


def full_qrs_exact_qaoa_search(
    problem: Any,
    solver: Any,
    *,
    selected_indices: list[int],
    bits_per_asset: int = 2,
    shots: int = 4096,
    seed: int = 41,
    viability_callback: Callable[[np.ndarray], bool | tuple[bool, dict[str, Any]]] | None,
    require_viability_gate: bool = True,
) -> dict[str, Any]:
    """Run QAOA on the complete finite-domain Full-QRS diagonal objective.

    Legacy mode phases literal ``-QRS_FULL``. V1A mode phases the frozen
    monotone linear normalization of the complete ``-QRS_V1A`` table.
    This function deliberately does *not* use threshold squaring, an active
    stress scenario, an active forecast-tail sample, an active turnover orthant,
    or a QUBO approximation.  Every encoded bitstring is scored by the complete
    piecewise Full Canonical QRS equation before the QAOA phase operator is built.
    """
    objective = build_exact_full_qrs_diagonal_objective(
        problem,
        selected_indices,
        bits_per_asset=bits_per_asset,
        viability_callback=viability_callback,
        seed=seed,
        require_viability_gate=require_viability_gate,
    )
    table = objective.energy_table()
    meta = objective.metadata()
    if int(meta.get("feasible_states") or 0) <= 0:
        return {
            "schema": FULL_QRS_SCHEMA,
            "method": "exact_diagonal_full_canonical_qrs_qaoa",
            "available": False,
            "status": "NO_FEASIBLE_ENCODED_STATE",
            "objective_metadata": meta,
            "best_observed": None,
            "note": "Every encoded portfolio hit the literal -infinity/invalid branch of QRS_FULL.",
        }

    result = solver.solve(objective, shots=int(shots), seed=int(seed))
    bits = [int(v) for v in result.bits]
    weights = objective.full_weights(bits)
    gate = None
    gate_meta: dict[str, Any] = {}
    if viability_callback is not None:
        raw_gate = viability_callback(weights)
        if isinstance(raw_gate, tuple):
            gate, gate_meta = bool(raw_gate[0]), dict(raw_gate[1] or {})
        else:
            gate = bool(raw_gate)
    exact_score = score_full_qrs(problem, weights, margin_feasible=gate, bootstrap_seed=int(seed))
    index = objective._index(bits)
    qrs_from_table = float(objective.qrs_table()[index])

    # Since the exact diagonal table is necessarily materialized to construct
    # this arbitrary phase oracle, we can report the classical global optimum as
    # a validation reference.  It is *not* substituted for the QAOA observation.
    qtable = objective.qrs_table()
    feasible = objective.feasible_table()
    best_global_index = int(np.nanargmax(np.where(feasible, qtable, -np.inf)))
    best_global_bits = [int((best_global_index >> i) & 1) for i in range(objective.num_variables)]
    best_global_weights = objective.full_weights(best_global_bits)
    return {
        "schema": FULL_QRS_SCHEMA,
        "method": "exact_diagonal_full_canonical_qrs_qaoa",
        "available": True,
        "status": "QAOA_OBSERVATION",
        "variables": int(objective.num_variables),
        "bits_per_asset": int(objective.bits_per_asset),
        "selected_indices": [int(i) for i in objective.selected_indices],
        "cost_operator": (
            "complete_QRS_V1A_raw_energy_with_low_energy_linear_phase_normalization"
            if bool(getattr(problem, "v1a_enabled", False))
            else "literal_QRS_FULL_exact_diagonal_on_encoded_domain"
        ),
        "objective_metadata": meta,
        "best_observed": {
            "bits": bits,
            "energy": float(result.energy),
            "weights": weights.tolist(),
            "full_qrs": exact_score,
            "qrs_table_value": qrs_from_table,
            "backend": result.backend,
            "execution_seconds": float(result.execution_seconds),
            "backend_metadata": dict(result.metadata or {}),
            "viability_metadata": gate_meta,
        },
        "validation_reference": {
            "global_best_basis_index": int(best_global_index),
            "global_best_bits": best_global_bits,
            "global_best_weights": best_global_weights.tolist(),
            "global_best_qrs": float(qtable[best_global_index]),
            "qaoa_observed_global_best": bool(index == best_global_index),
            "purpose": "finite-domain validation only; arbitrary-diagonal table construction is exponential",
        },
        "note": (
            "V1A mode uses the complete finite-domain QRS_V1A score as raw economic energy and a frozen monotone linear phase normalization; "
            "the feasible ground-state ordering is unchanged. The QAOA optimizer remains approximate and the arbitrary-diagonal table construction is exponential."
            if bool(getattr(problem, "v1a_enabled", False))
            else "QAOA phase separation uses the complete finite-domain QRS_FULL value. The QAOA optimizer remains approximate even though the encoded cost Hamiltonian is exact."
        ),
    }


def build_full_qrs_threshold_qubo(
    problem: Any,
    selected_indices: list[int],
    *,
    threshold: float,
    active_scenario: str,
    active_error_vector: np.ndarray | None,
    turnover_signs: np.ndarray,
    bits_per_asset: int = 2,
    gross_penalty: float = 80.0,
    positive_numerator_bias: float = 1e-3,
) -> FullQRSThresholdQUBO:
    """Build one exact active-set QUBO for the full canonical QRS threshold."""
    selected = list(dict.fromkeys(int(i) for i in selected_indices))
    if any(i < 0 or i >= problem.n for i in selected):
        raise ValueError("selected full-QRS indices out of range")
    local_S, W, fixed = _weight_encoding(problem, selected, bits_per_asset)
    m = W.shape[1]
    mu = np.asarray(problem.expected_returns, dtype=float)
    lend, borrow = _rates(problem)
    # Gross regime is fixed by the target-gross constraint.  The exact score is
    # always re-evaluated after decoding, so deviations caused by finite gross
    # penalty cannot silently change the reported canonical numerator.
    if float(problem.target_gross) <= 1.0 + 1e-12:
        finance_coeff = mu - lend
        finance_const = 0.0
        cash_regime = "lending_or_fully_invested"
    else:
        finance_coeff = mu - borrow
        finance_const = borrow - lend
        cash_regime = "borrowing"

    err = np.zeros(problem.n, dtype=float) if active_error_vector is None else np.asarray(active_error_vector, dtype=float).reshape(-1)
    if len(err) != problem.n:
        raise ValueError("active forecast-error vector must align with full universe")
    signs = np.asarray(turnover_signs, dtype=float).reshape(-1)
    if len(signs) != problem.n:
        raise ValueError("turnover sign vector must align with full universe")
    tc = float(problem.transaction_cost_bps) / 10000.0
    # Exact L1 transaction cost inside the current orthant:
    # C = tc * sign'(w-current).  The active-set loop updates signs whenever a
    # decoded candidate crosses an orthant boundary.
    numerator_coeff = finance_coeff + err - tc * signs
    numerator_const = float(finance_const + tc * (signs @ np.asarray(problem.current_weights, dtype=float)))
    n0 = float(numerator_const + numerator_coeff @ fixed)
    nvec = np.asarray(W.T @ numerator_coeff, dtype=float)

    scenarios = dict(getattr(problem, "full_qrs_stress_covariances", None) or {})
    base = np.asarray(scenarios.get("BASE", problem.covariance), dtype=float)
    stress = np.asarray(scenarios.get(active_scenario, problem.optimization_covariance), dtype=float)
    alpha = float(np.clip(getattr(problem, "resilience_alpha", 1.0), 0.0, 1.0))
    risk = _sym((1.0 - alpha) * base + alpha * stress)
    A = np.asarray(W.T @ risk @ W, dtype=float)
    rlin = np.asarray(2.0 * (W.T @ risk @ fixed), dtype=float)
    r0 = float(fixed @ risk @ fixed)

    t = max(0.0, float(threshold))
    # t^2*(x'Ax + rlin'x + r0) - (n0+nvec'x)^2
    M = (t * t) * A - np.outer(nvec, nvec)
    linear_raw = (t * t) * rlin - 2.0 * n0 * nvec - float(positive_numerator_bias) * nvec
    constant = (t * t) * r0 - n0 * n0 - float(positive_numerator_bias) * n0

    gross_vec = np.asarray(W.sum(axis=0), dtype=float)
    gross_const = float(fixed.sum())
    rho = float(gross_penalty)
    M = M + rho * np.outer(gross_vec, gross_vec)
    linear_raw = linear_raw + 2.0 * rho * (gross_const - float(problem.target_gross)) * gross_vec
    constant += rho * (gross_const - float(problem.target_gross)) ** 2

    # Convert x'Mx + l'x + c into QUBO convention with diagonal folded into linear.
    linear = np.asarray(linear_raw + np.diag(M), dtype=float)
    quadratic: dict[tuple[int, int], float] = {}
    for i in range(m):
        for j in range(i + 1, m):
            q = float(2.0 * M[i, j])
            if abs(q) > 1e-15:
                quadratic[(i, j)] = q
    qubo = AllocationQUBO(
        symbols=tuple(problem.symbols[i] for i in selected),
        bits_per_asset=max(1, min(5, int(bits_per_asset))),
        weight_matrix=local_S,
        linear=linear,
        quadratic=quadratic,
        constant=float(constant),
        target_gross=float(problem.target_gross - fixed.sum()),
        gross_penalty=rho,
        risk_aversion=t * t,
        turnover_penalty=0.0,
        max_name_weight=float(problem.max_name_weight),
    )
    # Add active-set semantics to the serializable QUBO object through the
    # wrapper; AllocationQUBO itself remains backward-compatible.
    return FullQRSThresholdQUBO(
        threshold=t,
        qubo=qubo,
        selected_indices=tuple(selected),
        fixed_weights=fixed,
        full_weight_matrix=W,
        numerator_constant=n0,
        numerator_linear_bits=nvec,
        risk_constant=r0,
        risk_linear_bits=rlin,
        risk_quadratic_bits=A,
        active_scenario=str(active_scenario),
        active_error_sample_index=None,
        turnover_signs=signs,
        positive_numerator_bias=float(positive_numerator_bias),
    )


def active_qubo_identity(built: FullQRSThresholdQUBO, bits: Iterable[int | float], problem: Any) -> dict[str, float]:
    """Audit the algebra represented by one active-set QUBO."""
    x = np.asarray(list(bits), dtype=float).reshape(-1)
    n = float(built.numerator_constant + built.numerator_linear_bits @ x)
    v = float(
        built.risk_constant
        + built.risk_linear_bits @ x
        + x @ built.risk_quadratic_bits @ x
    )
    w = built.full_weights(x)
    gross_penalty = float(built.qubo.gross_penalty) * (float(w.sum()) - float(problem.target_gross)) ** 2
    expected = float((built.threshold * built.threshold) * v - n * n - float(built.positive_numerator_bias) * n + gross_penalty)
    return {
        "active_numerator": n,
        "active_variance": v,
        "gross_penalty": gross_penalty,
        "expected_qubo_energy": expected,
        "reported_qubo_energy": float(built.qubo.energy(x)),
        "absolute_error": abs(float(built.qubo.energy(x)) - expected),
    }


def _repair_selected_gross(problem: Any, weights: np.ndarray, selected_indices: list[int]) -> np.ndarray:
    """Deterministic cap/gross repair used only when the raw discrete sample misses gross."""
    w = np.asarray(weights, dtype=float).copy()
    selected = np.asarray(selected_indices, dtype=int)
    fixed = np.asarray([i for i in range(problem.n) if i not in set(selected_indices)], dtype=int)
    target = max(0.0, float(problem.target_gross) - float(w[fixed].sum()))
    x = np.clip(w[selected], 0.0, float(problem.max_name_weight))
    for _ in range(100):
        total = float(x.sum())
        if abs(total - target) <= 1e-10:
            break
        if total > target and total > 1e-15:
            x *= target / total
        else:
            room = np.maximum(float(problem.max_name_weight) - x, 0.0)
            if float(room.sum()) <= 1e-15:
                break
            add = min(target - total, float(room.sum()))
            x += add * room / float(room.sum())
        x = np.clip(x, 0.0, float(problem.max_name_weight))
    w[selected] = x
    return w


def full_qrs_threshold_search(
    problem: Any,
    solver: Any,
    *,
    selected_indices: list[int],
    bits_per_asset: int = 2,
    low: float = 0.0,
    high: float = 3.0,
    threshold_iterations: int = 6,
    active_set_iterations: int = 4,
    gross_penalty: float = 80.0,
    shots: int = 2048,
    seed: int = 41,
    initial_weights: np.ndarray | None = None,
    viability_callback: Callable[[np.ndarray], bool | tuple[bool, dict[str, Any]]] | None = None,
) -> dict[str, Any]:
    """Approximate full-canonical-QRS optimization by active-set threshold QUBOs."""
    selected = list(dict.fromkeys(int(i) for i in selected_indices))
    start = np.asarray(initial_weights if initial_weights is not None else problem.current_weights, dtype=float)
    if len(start) != problem.n:
        start = np.asarray(problem.current_weights, dtype=float)
    state = active_full_qrs_state(problem, start, seed=seed)
    lo = max(0.0, float(low))
    hi = max(lo, float(high))
    archive: list[dict[str, Any]] = []
    best: dict[str, Any] | None = None
    total_solves = 0

    for outer in range(max(1, int(threshold_iterations))):
        threshold = 0.5 * (lo + hi)
        local_state = dict(state)
        previous_bits: tuple[int, ...] | None = None
        final_row: dict[str, Any] | None = None
        for inner in range(max(1, int(active_set_iterations))):
            built = build_full_qrs_threshold_qubo(
                problem,
                selected,
                threshold=threshold,
                active_scenario=str(local_state["active_scenario"]),
                active_error_vector=np.asarray(local_state["active_error_vector"], dtype=float),
                turnover_signs=np.asarray(local_state["turnover_signs"], dtype=float),
                bits_per_asset=bits_per_asset,
                gross_penalty=gross_penalty,
            )
            result = solver.solve(built.qubo, shots=shots, seed=seed + 100 * outer + inner)
            total_solves += 1
            raw_w = built.full_weights(result.bits)
            gross_error = float(raw_w.sum() - float(problem.target_gross))
            if abs(gross_error) <= 1e-6:
                w = raw_w
                repaired = False
            else:
                w = _repair_selected_gross(problem, raw_w, selected)
                repaired = True

            viability = None
            viability_meta: dict[str, Any] = {}
            if viability_callback is not None:
                raw_v = viability_callback(w)
                if isinstance(raw_v, tuple):
                    viability = bool(raw_v[0])
                    viability_meta = dict(raw_v[1] or {})
                else:
                    viability = bool(raw_v)
            exact = score_full_qrs(problem, w, margin_feasible=viability, bootstrap_seed=seed)
            new_state = active_full_qrs_state(problem, w, seed=seed)
            bits_tuple = tuple(int(x) for x in np.asarray(result.bits, dtype=int).tolist())
            state_changed = bool(
                str(new_state["active_scenario"]) != str(local_state["active_scenario"])
                or new_state.get("active_error_sample_index") != local_state.get("active_error_sample_index")
                or not np.array_equal(
                    np.sign(np.asarray(new_state["turnover_signs"], dtype=float)),
                    np.sign(np.asarray(local_state["turnover_signs"], dtype=float)),
                )
            )
            row = {
                "threshold": float(threshold),
                "outer_iteration": int(outer + 1),
                "active_iteration": int(inner + 1),
                "bits": list(bits_tuple),
                "energy": float(result.energy),
                "backend": result.backend,
                "execution_seconds": float(result.execution_seconds),
                "backend_metadata": dict(result.metadata or {}),
                "raw_weights": raw_w.tolist(),
                "weights": w.tolist(),
                "repair_applied": bool(repaired),
                "raw_gross_error": gross_error,
                "full_qrs": exact,
                "active_state_before": {
                    "scenario": str(local_state["active_scenario"]),
                    "error_sample_index": local_state.get("active_error_sample_index"),
                    "turnover_signs": np.asarray(local_state["turnover_signs"], dtype=float).tolist(),
                },
                "active_state_after": {
                    "scenario": str(new_state["active_scenario"]),
                    "error_sample_index": new_state.get("active_error_sample_index"),
                    "turnover_signs": np.asarray(new_state["turnover_signs"], dtype=float).tolist(),
                },
                "active_set_changed": state_changed,
                "active_qubo_identity": active_qubo_identity(built, result.bits, problem),
                "viability": viability,
                "viability_metadata": viability_meta,
                "qubo": {
                    **built.qubo.as_dict(),
                    "type": "full_canonical_qrs_active_set_threshold",
                    "full_qrs_schema": FULL_QRS_SCHEMA,
                    "active_scenario": str(local_state["active_scenario"]),
                    "active_error_sample_index": local_state.get("active_error_sample_index"),
                    "threshold": float(threshold),
                    "selected_indices": selected,
                    "energy_semantics": "t^2*V_active(w)-N_active(w)^2 + gross penalty; full piecewise QRS is re-evaluated after every solve",
                },
            }
            archive.append(row)
            final_row = row
            stable_bits = previous_bits == bits_tuple
            previous_bits = bits_tuple
            local_state = new_state
            if (not state_changed) and stable_bits:
                break

        if final_row is None:
            continue
        exact = final_row["full_qrs"]
        q = exact.get("qrs")
        meets = bool(
            q is not None
            and np.isfinite(q)
            and exact.get("positive_numerator")
            and float(q) >= float(threshold)
            and (final_row.get("viability") is not False)
        )
        final_row["meets_threshold"] = meets
        if q is not None and np.isfinite(q) and exact.get("positive_numerator") and (final_row.get("viability") is not False):
            if best is None or float(q) > float(best["full_qrs"]["qrs"]):
                best = final_row
                state = active_full_qrs_state(problem, np.asarray(final_row["weights"], dtype=float), seed=seed)
        if meets:
            lo = threshold
        else:
            # Heuristic only because QAOA / active-set solves are approximate.
            hi = threshold

    return {
        "schema": FULL_QRS_SCHEMA,
        "method": "active_set_threshold_qaoa",
        "best_observed": best,
        "archive": archive,
        "selected_indices": selected,
        "bits_per_asset": int(bits_per_asset),
        "threshold_bounds_final": [float(lo), float(hi)],
        "threshold_iterations": int(max(1, threshold_iterations)),
        "active_set_iterations": int(max(1, active_set_iterations)),
        "qubo_solves": int(total_solves),
        "note": "QAOA optimizes exact quadratic active pieces of the full canonical QRS; every decoded candidate is rescored with the full max-scenario, empirical-tail, exact-L1-cost evaluator. Non-observation is not proof of infeasibility.",
        "margin_note": "A real non-convex broker margin engine is not fabricated. viability_callback is a hard post-solve gate; Control currently supplies QRM research viability unless an exact margin engine is connected.",
    }
