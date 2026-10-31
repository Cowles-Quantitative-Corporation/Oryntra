"""Universal Risk V2.0.5 — Beta + V1A state-aware downstream risk layer.

V2.0.5 deliberately keeps portfolio *search* out of the risk supervisor.  It
starts from the V2.0.3-approved composition, evaluates the existing V2.0.4 Beta
macro state and the QOptima V1A tail/stress state independently, then applies
only the more restrictive state cut.  The overlay can never increase exposure.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Mapping, Optional
import math
import numpy as np

from ..qoptima.v1a.stress import soft_rmt_v1a_scenario
from .state_time import validate_state_time

RISK_MODEL_V205_ID = "universal_stock_risk_supervisor_v2_0_5"


@dataclass(frozen=True)
class V205V1AControlConfig:
    v1a_enabled: bool = True
    require_positive_definite: bool = True
    reject_signed_control: bool = True
    maximum_state_age_sessions: int = 5
    minimum_gamma: float = 0.05
    stress_premium_trigger: float = 0.10
    maximum_additional_gross_reduction: float = 0.12
    minimum_additional_gross_change: float = 0.01
    maximum_incremental_turnover: float = 0.12
    persistence_rebalances: int = 1
    reliability_rho: float = 0.50
    additive_floor_epsilon: float = 1e-8
    downside_quantile: float = 0.20
    high_vol_quantile: float = 0.80
    correlation_collapse_blend: float = 0.65
    maximum_combined_state_reduction: float = 0.15
    research_candidate_mode: bool = False

    def __post_init__(self) -> None:
        if not all(isinstance(x, bool) for x in (
            self.v1a_enabled, self.require_positive_definite, self.reject_signed_control,
            self.research_candidate_mode,
        )):
            raise ValueError("V2.0.5 switches must be boolean")
        if not (1 <= self.maximum_state_age_sessions <= 63):
            raise ValueError("invalid V1A state age")
        if not (0 <= self.minimum_gamma <= 1):
            raise ValueError("invalid V1A minimum gamma")
        if not (0 < self.stress_premium_trigger <= 2):
            raise ValueError("invalid V1A stress premium trigger")
        if not (0 <= self.maximum_additional_gross_reduction <= .35):
            raise ValueError("invalid V1A gross reduction")
        if not (0 <= self.minimum_additional_gross_change <= .20):
            raise ValueError("invalid V1A no-trade band")
        if not (0 <= self.maximum_incremental_turnover <= .50):
            raise ValueError("invalid V1A incremental turnover")
        if not (1 <= self.persistence_rebalances <= 8):
            raise ValueError("invalid V1A persistence")
        if not (0 <= self.reliability_rho <= 1):
            raise ValueError("invalid V1A reliability rho")
        if not (0 < self.additive_floor_epsilon <= 1e-2):
            raise ValueError("invalid covariance floor")
        if not (0.05 <= self.downside_quantile <= .45 and .55 <= self.high_vol_quantile <= .95):
            raise ValueError("invalid V1A stress quantiles")
        if not (0 <= self.correlation_collapse_blend <= 1):
            raise ValueError("invalid correlation-collapse blend")
        if not (0 <= self.maximum_combined_state_reduction <= .40):
            raise ValueError("invalid combined state reduction")


def _ord(x: Any) -> int | None:
    if x is None:
        return None
    if isinstance(x, datetime):
        x = x.date()
    if isinstance(x, date):
        return x.toordinal()
    try:
        return date.fromisoformat(str(x)[:10]).toordinal()
    except Exception:
        return None


def _snapshot_map(state: Any) -> dict[str, Any]:
    if state is None:
        return {}
    if hasattr(state, "as_dict"):
        return dict(state.as_dict())
    if isinstance(state, Mapping):
        return dict(state)
    raise ValueError("v1a_state must be a mapping/V1AStateSnapshot")


def _cov(x: np.ndarray, n: int) -> np.ndarray:
    frame = np.asarray(x, dtype=float)
    if frame.ndim != 2 or frame.shape[1] != n:
        raise ValueError("V2.0.5 stress history shape mismatch")
    if len(frame) < 2:
        return np.eye(n) * 1e-8
    out = np.atleast_2d(np.cov(frame, rowvar=False, ddof=0))
    if out.shape != (n, n):
        out = np.diag(np.repeat(float(np.var(frame)), n))
    return (out + out.T) / 2.0


def v1a_stress_diagnostics_v205(
    weights: np.ndarray,
    base_covariance: np.ndarray,
    returns_history: np.ndarray,
    *,
    gamma: float,
    control: V205V1AControlConfig,
) -> dict[str, Any]:
    """Evaluate QOptima-style state-conditioned stress without running QPO/QAOA."""
    w = np.maximum(np.asarray(weights, dtype=float).reshape(-1), 0.0)
    base = np.asarray(base_covariance, dtype=float)
    hist = np.asarray(returns_history, dtype=float)
    n = len(w)
    if base.shape != (n, n) or hist.ndim != 2 or hist.shape[1] != n or len(hist) < 20:
        return {"available": False, "reason": "insufficient_stress_history"}
    if not np.isfinite(base).all() or not np.isfinite(hist).all():
        return {"available": False, "reason": "nonfinite_stress_inputs"}
    eps = float(control.additive_floor_epsilon)
    base = (base + base.T) / 2.0 + eps * np.eye(n)
    market = hist.mean(axis=1)
    q_down = float(np.quantile(market, control.downside_quantile))
    downside = hist[market <= q_down]
    row_vol = np.sqrt(np.mean(np.square(hist), axis=1))
    q_high = float(np.quantile(row_vol, control.high_vol_quantile))
    high_vol = hist[row_vol >= q_high]
    scenarios: dict[str, np.ndarray] = {
        "downside": _cov(downside if len(downside) >= 5 else hist, n),
        "high_volatility": _cov(high_vol if len(high_vol) >= 5 else hist, n),
    }
    # Correlation-collapse scenario: preserve variances while pulling off-diagonal
    # correlations toward +1.  This is the same risk direction QOptima treats as
    # a fragility scenario, but Risk only evaluates it; it never searches weights.
    std = np.sqrt(np.maximum(np.diag(base), eps))
    corr = base / np.maximum(np.outer(std, std), eps)
    blend = float(control.correlation_collapse_blend)
    corr_stress = (1.0 - blend) * corr + blend * np.ones_like(corr)
    np.fill_diagonal(corr_stress, 1.0)
    scenarios["correlation_collapse"] = (corr_stress * np.outer(std, std) + eps * np.eye(n))

    base_var = float(w @ base @ w)
    base_vol = math.sqrt(max(base_var, 0.0))
    rows: dict[str, Any] = {}
    worst_name = None
    worst_vol = base_vol
    for name, raw in scenarios.items():
        try:
            matrix, audit = soft_rmt_v1a_scenario(
                base,
                raw,
                reliability_rho=float(control.reliability_rho),
                gamma=float(np.clip(gamma, 0.0, 1.0)),
                effective_sample_count=max(1, len(downside) if name == "downside" else len(high_vol) if name == "high_volatility" else len(hist)),
                additive_floor_epsilon=eps,
            )
            vol = math.sqrt(max(float(w @ matrix @ w), 0.0))
            premium = vol / max(base_vol, 1e-12) - 1.0
            rows[name] = {
                "portfolio_volatility": vol,
                "stress_premium": premium,
                "loewner_difference_min_eigenvalue": audit.get("loewner_difference_min_eigenvalue"),
                "tau_mp": audit.get("tau_mp"),
            }
            if vol > worst_vol:
                worst_vol, worst_name = vol, name
        except Exception as exc:
            rows[name] = {"error": f"{type(exc).__name__}: {exc}"}
    premium = worst_vol / max(base_vol, 1e-12) - 1.0
    return {
        "available": True,
        "base_portfolio_volatility": base_vol,
        "worst_v1a_portfolio_volatility": worst_vol,
        "worst_scenario": worst_name,
        "stress_premium": float(max(0.0, premium)),
        "scenarios": rows,
        "policy": "QOPTIMA_V1A_SOFT_RMT_RISK_DIAGNOSTIC_V1_6",
    }


def v1a_regime_modifier_v205(
    v1a_state: Any,
    *,
    weights: np.ndarray,
    covariance: np.ndarray,
    returns_history: np.ndarray,
    control: V205V1AControlConfig,
    previous_persistence_rebalances: int = 0,
    current_session: Any = None,
) -> dict[str, Any]:
    if not control.v1a_enabled:
        return {"available": False, "triggered": False, "reason": "v1a_disabled", "gross_modifier": 1.0, "persistence_rebalances": 0}
    s = _snapshot_map(v1a_state)
    if not s:
        return {"available": False, "triggered": False, "reason": "v1a_state_missing", "gross_modifier": 1.0, "persistence_rebalances": 0}
    gamma = float(s.get("gamma", np.nan))
    es = float(s.get("es_calibrated", np.nan))
    blockers: list[str] = []
    if not math.isfinite(gamma) or not (0 <= gamma <= 1):
        blockers.append("v1a_gamma_invalid")
    if not math.isfinite(es) or es >= 0:
        blockers.append("v1a_es_invalid")
    if control.require_positive_definite and not bool(s.get("positive_definite", False)):
        blockers.append("v1a_state_not_positive_definite")
    if control.reject_signed_control and bool(s.get("signed_control", False)):
        blockers.append("v1a_signed_control_forbidden")
    age, time_blockers = validate_state_time(
        s.get("information_cutoff"), current_session, prefix="v1a",
        maximum_age_sessions=control.maximum_state_age_sessions,
    )
    blockers.extend(time_blockers)
    if blockers:
        return {"available": True, "triggered": False, "reason": "v1a_gate_blocked", "blockers": blockers, "gross_modifier": 1.0, "persistence_rebalances": 0, "gamma": gamma if math.isfinite(gamma) else None, "es_calibrated": es if math.isfinite(es) else None}

    stress = v1a_stress_diagnostics_v205(weights, covariance, returns_history, gamma=gamma, control=control)
    stress_premium = float(stress.get("stress_premium") or 0.0) if stress.get("available") else 0.0
    gamma_severity = float(np.clip((gamma - control.minimum_gamma) / max(1.0 - control.minimum_gamma, 1e-12), 0.0, 1.0))
    stress_severity = float(np.clip(stress_premium / max(control.stress_premium_trigger, 1e-12), 0.0, 1.0))
    severity = max(gamma_severity, stress_severity)
    cut = severity * control.maximum_additional_gross_reduction
    raw = cut >= control.minimum_additional_gross_change - 1e-15
    persistence = previous_persistence_rebalances + 1 if raw else 0
    base = {
        "available": True,
        "gamma": gamma,
        "var": s.get("var"),
        "es": s.get("es"),
        "es_calibrated": es,
        "artifact_sha256": s.get("artifact_sha256"),
        "artifact_information_cutoff": s.get("artifact_information_cutoff"),
        "state_age_calendar_days": age,
        "stress": stress,
        "stress_premium": stress_premium,
        "gamma_severity": gamma_severity,
        "stress_severity": stress_severity,
        "severity": severity,
        "proposed_cut": cut,
        "persistence_rebalances": int(persistence),
        "research_candidate_mode": bool(control.research_candidate_mode),
    }
    if not raw:
        return {**base, "triggered": False, "reason": "v1a_below_no_trade_band", "gross_modifier": 1.0}
    if persistence < control.persistence_rebalances:
        return {**base, "triggered": False, "reason": "v1a_persistence_not_met", "gross_modifier": 1.0}
    return {**base, "triggered": True, "reason": "v1a_tail_or_stress_state", "gross_modifier": 1.0 - cut, "additional_gross_reduction": cut}


def combine_state_modifiers_v205(beta_modifier: Mapping[str, Any], v1a_modifier: Mapping[str, Any], *, control: V205V1AControlConfig) -> dict[str, Any]:
    beta_cut = (1.0 - float(beta_modifier.get("gross_modifier", 1.0))) if beta_modifier.get("triggered", False) else 0.0
    v1a_cut = (1.0 - float(v1a_modifier.get("gross_modifier", 1.0))) if v1a_modifier.get("triggered", False) else 0.0
    cut = min(float(control.maximum_combined_state_reduction), max(0.0, beta_cut, v1a_cut))
    source = "none"
    if cut > 0:
        source = "v1a" if v1a_cut > beta_cut + 1e-12 else "beta" if beta_cut > v1a_cut + 1e-12 else "beta_and_v1a"
    return {
        "triggered": bool(cut > 0),
        "gross_modifier": 1.0 - cut,
        "additional_gross_reduction": cut,
        "binding_source": source,
        "beta_cut": beta_cut,
        "v1a_cut": v1a_cut,
        "combination_rule": "max_not_sum",
    }


def apply_state_overlay_v205(
    v203_weights: np.ndarray,
    combined_modifier: Mapping[str, Any],
    *,
    current_weights: Optional[np.ndarray],
    control: V205V1AControlConfig,
) -> tuple[np.ndarray, dict[str, Any]]:
    base = np.maximum(np.asarray(v203_weights, dtype=float).reshape(-1), 0.0)
    if base.sum() <= 1e-12 or not combined_modifier.get("triggered", False):
        return base.copy(), {"selected": "v203_fallback", "gross_before": float(base.sum()), "gross_after": float(base.sum()), "incremental_turnover": 0.0, "modifier": dict(combined_modifier)}
    mod = float(np.clip(combined_modifier.get("gross_modifier", 1.0), 1.0 - control.maximum_combined_state_reduction, 1.0))
    cand = np.minimum(base, np.maximum(base * mod, 0.0))
    ref = base if current_weights is None else np.maximum(np.asarray(current_weights, dtype=float).reshape(-1), 0.0)
    incr = max(0.0, float(np.abs(cand - ref).sum() - np.abs(base - ref).sum()))
    if incr > control.maximum_incremental_turnover + 1e-12:
        return base.copy(), {"selected": "v203_fallback", "reason": "v205_incremental_turnover_gate", "gross_before": float(base.sum()), "gross_after": float(base.sum()), "incremental_turnover": incr, "modifier": dict(combined_modifier)}
    return cand, {"selected": "state_scaled_v203", "reason": "v205_state_overlay_accepted", "gross_before": float(base.sum()), "gross_after": float(cand.sum()), "incremental_turnover": incr, "modifier": dict(combined_modifier)}


def v205_contract(control: V205V1AControlConfig = V205V1AControlConfig()) -> dict[str, Any]:
    return {
        "id": RISK_MODEL_V205_ID,
        "version": "2.0.5-research",
        "baseline": "V2.0.3 structural/predictive risk supervisor",
        "state_inputs": ["Beta macro regime", "QOptima V1A v1.6 tail/stress state"],
        "combination_rule": "max(beta_cut, v1a_cut); never additive",
        "v1a_can_increase_risk": False,
        "qpo_or_qaoa_inside_risk": False,
        "controls": control.__dict__.copy(),
        "research_only": True,
    }
