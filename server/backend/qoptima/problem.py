from __future__ import annotations

from dataclasses import dataclass
from typing import Any
import numpy as np

from .resilience import fragility_metrics, nearest_psd


@dataclass(frozen=True)
class QOptimaPortfolioProblem:
    symbols: tuple[str, ...]
    current_weights: np.ndarray
    expected_returns: np.ndarray
    covariance: np.ndarray
    target_gross: float
    max_name_weight: float = 0.20
    risk_aversion: float = 3.0
    turnover_penalty: float = 0.20
    risk_free_rate: float = 0.0
    transaction_cost_bps: float = 0.0
    horizon_sessions: int = 252
    fragility_enabled: bool = False
    effective_covariance: np.ndarray | None = None
    alpha_covariance: np.ndarray | None = None
    resilience_alpha: float = 1.0
    fragility_components: dict[str, np.ndarray] | None = None
    fragility_weights: dict[str, float] | None = None
    fragility_metadata: dict[str, Any] | None = None
    covariance_method: str = "sample"
    covariance_provenance: dict[str, Any] | None = None

    # Canonical full-QRS research objective.  These fields are optional so older
    # tests/callers keep the reviewed v0.4 behavior unless the portfolio builder
    # explicitly enables the new objective.
    full_qrs_enabled: bool = False
    lending_rate: float | None = None
    borrowing_rate: float | None = None
    full_qrs_beta: float = 0.10
    full_qrs_bootstrap_reps: int = 256
    full_qrs_quantile_confidence: float = 0.90
    forecast_error_samples: np.ndarray | None = None
    full_qrs_stress_covariances: dict[str, np.ndarray] | None = None
    full_qrs_stress_metadata: dict[str, Any] | None = None

    # V1A-QRS v1.6 research integration.  These fields are inert when
    # ``v1a_enabled`` is false so existing QOptima behavior is preserved.
    v1a_enabled: bool = False
    v1a_state_metadata: dict[str, Any] | None = None
    v1a_gamma: float = 0.0
    v1a_risk_budget_annual: float = 0.15
    v1a_risk_floor_ratio: float = 0.10
    v1a_forecast_error_covariance_floor: float = 1e-8
    v1a_forecast_error_covariance_window: int = 60
    v1a_forecast_error_min_history: int = 12
    v1a_quantile_block_stride: int = 1
    v1a_forecast_error_regime_labels: tuple[str | None, ...] | None = None
    v1a_forecast_error_information_cutoffs: tuple[str, ...] | None = None
    v1a_forecast_error_realized_at: tuple[str, ...] | None = None
    v1a_current_regime_label: str | None = None
    v1a_qaoa_low_tail_quantile: float = 0.05
    v1a_qaoa_phase_global_floor_ratio: float = 0.10
    v1a_qaoa_phase_absolute_floor: float = 1e-8
    v1a_qaoa_energy_cap: float = 8.0
    v1a_qaoa_infeasible_gap: float = 1.0
    v1a_scale_tolerance: float = 0.03

    # Utility contribution of holdings frozen outside a reduced QScan subproblem.
    # This keeps the reduced objective numerically comparable with the full objective.
    objective_constant_offset: float = 0.0

    def __post_init__(self) -> None:
        n = len(self.symbols)
        current = np.asarray(self.current_weights, dtype=float).reshape(-1)
        mu = np.asarray(self.expected_returns, dtype=float).reshape(-1)
        cov = np.asarray(self.covariance, dtype=float)
        if n < 2:
            raise ValueError("QOptima portfolio bursts require at least two securities")
        if len(current) != n or len(mu) != n or cov.shape != (n, n):
            raise ValueError("QOptima problem arrays must align with symbols")
        if not np.isfinite(current).all() or not np.isfinite(mu).all() or not np.isfinite(cov).all():
            raise ValueError("QOptima problem contains non-finite values")
        if np.any(current < -1e-10):
            raise ValueError("QOptima v0.4 is long-only")
        if not (0 < float(self.target_gross) <= 1.5):
            raise ValueError("target_gross must be within (0, 1.5]")
        if not (0 < float(self.max_name_weight) <= 1):
            raise ValueError("max_name_weight must be within (0, 1]")
        if self.risk_aversion < 0 or self.turnover_penalty < 0:
            raise ValueError("risk and turnover penalties must be non-negative")
        if self.transaction_cost_bps < 0:
            raise ValueError("transaction_cost_bps must be non-negative")
        if int(self.horizon_sessions) <= 0:
            raise ValueError("horizon_sessions must be positive")
        if not (0.0 <= float(self.resilience_alpha) <= 1.0):
            raise ValueError("resilience_alpha must be in [0,1]")
        if not (0.0 < float(self.full_qrs_beta) < 0.5):
            raise ValueError("full_qrs_beta must be in (0,0.5)")
        if int(self.full_qrs_bootstrap_reps) < 0:
            raise ValueError("full_qrs_bootstrap_reps must be non-negative")
        if not (0.5 <= float(self.full_qrs_quantile_confidence) < 1.0):
            raise ValueError("full_qrs_quantile_confidence must be in [0.5,1)")
        if self.v1a_enabled:
            if not (0.0 <= float(self.v1a_gamma) <= 1.0):
                raise ValueError("v1a_gamma must be in [0,1]")
            if float(self.v1a_risk_budget_annual) <= 0:
                raise ValueError("v1a_risk_budget_annual must be positive")
            if not (0.0 < float(self.v1a_risk_floor_ratio) <= 1.0):
                raise ValueError("v1a_risk_floor_ratio must be in (0,1]")
            if float(self.v1a_forecast_error_covariance_floor) <= 0:
                raise ValueError("v1a forecast-error covariance floor must be positive")
            if not (0.0 <= float(self.v1a_scale_tolerance) <= 0.50):
                raise ValueError("v1a_scale_tolerance must be in [0,0.50]")
        timing = (self.v1a_forecast_error_information_cutoffs, self.v1a_forecast_error_realized_at)
        if any(value is not None for value in timing):
            if self.forecast_error_samples is None:
                raise ValueError("forecast-error timing requires aligned samples")
            from .v1a.residual_tail import _validated_error_timing
            _validated_error_timing(len(self.forecast_error_samples), *timing)
        if self.forecast_error_samples is not None:
            samples = np.asarray(self.forecast_error_samples, dtype=float)
            if samples.ndim != 2 or samples.shape[1] != n or not np.isfinite(samples).all():
                raise ValueError("forecast_error_samples must be finite rows aligned with symbols")
            if self.v1a_forecast_error_regime_labels is not None and len(self.v1a_forecast_error_regime_labels) != len(samples):
                raise ValueError("v1a forecast-error regime labels must align with forecast_error_samples rows")
        for scenario_name, matrix in (self.full_qrs_stress_covariances or {}).items():
            arr = np.asarray(matrix, dtype=float)
            if arr.shape != (n, n) or not np.isfinite(arr).all():
                raise ValueError(f"full QRS stress covariance {scenario_name} must align with symbols and be finite")
        for name, matrix in {
            "effective_covariance": self.effective_covariance,
            "alpha_covariance": self.alpha_covariance,
        }.items():
            if matrix is not None:
                arr = np.asarray(matrix, dtype=float)
                if arr.shape != (n, n) or not np.isfinite(arr).all():
                    raise ValueError(f"{name} must align with symbols and be finite")
        for name, matrix in (self.fragility_components or {}).items():
            arr = np.asarray(matrix, dtype=float)
            if arr.shape != (n, n) or not np.isfinite(arr).all():
                raise ValueError(f"fragility component {name} must align with symbols and be finite")

    @property
    def n(self) -> int:
        return len(self.symbols)

    @property
    def optimization_covariance(self) -> np.ndarray:
        if self.fragility_enabled:
            if self.alpha_covariance is not None:
                return np.asarray(self.alpha_covariance, dtype=float)
            if self.effective_covariance is not None:
                a = float(np.clip(self.resilience_alpha, 0.0, 1.0))
                return nearest_psd(
                    (1.0 - a) * np.asarray(self.covariance, dtype=float)
                    + a * np.asarray(self.effective_covariance, dtype=float),
                    floor=1e-12,
                )
        return np.asarray(self.covariance, dtype=float)

    def objective(self, weights: np.ndarray) -> float:
        """Higher is better: expected return minus selected risk and turnover penalties."""
        w = np.asarray(weights, dtype=float).reshape(-1)
        current = np.asarray(self.current_weights, dtype=float).reshape(-1)
        mu = np.asarray(self.expected_returns, dtype=float).reshape(-1)
        cov = self.optimization_covariance
        return float(
            self.objective_constant_offset
            + mu @ w
            - self.risk_aversion * (w @ cov @ w)
            - self.turnover_penalty * np.square(w - current).sum()
        )

    def metrics(self, weights: np.ndarray) -> dict[str, Any]:
        w = np.asarray(weights, dtype=float).reshape(-1)
        mu = np.asarray(self.expected_returns, dtype=float).reshape(-1)
        cov = np.asarray(self.covariance, dtype=float)
        gross = float(w.sum())
        cash_weight = 1.0 - gross
        expected = float(mu @ w + cash_weight * float(self.risk_free_rate))
        variance = max(0.0, float(w @ cov @ w))
        volatility = float(np.sqrt(variance))
        sharpe = (expected - float(self.risk_free_rate)) / volatility if volatility > 1e-12 else None
        fragility = fragility_metrics(
            w,
            mu,
            float(self.risk_free_rate),
            cov,
            np.asarray(self.effective_covariance if self.effective_covariance is not None else cov, dtype=float),
            self.fragility_components,
            self.fragility_weights,
            alpha_covariance=self.optimization_covariance,
            alpha=float(self.resilience_alpha),
            current_weights=np.asarray(self.current_weights, dtype=float),
            transaction_cost_bps=float(self.transaction_cost_bps),
        )
        band_info = dict((self.fragility_metadata or {}).get("fragility_bands") or {})
        thresholds = band_info.get("thresholds") if isinstance(band_info, dict) else None
        if isinstance(thresholds, dict) and fragility.get("fragility_premium") is not None:
            fp = float(fragility["fragility_premium"])
            elevated = float(thresholds.get("elevated", float("inf")))
            high = float(thresholds.get("high", float("inf")))
            extreme = float(thresholds.get("extreme", float("inf")))
            if fp >= extreme:
                band = "EXTREME"
            elif fp >= high:
                band = "HIGH"
            elif fp >= elevated:
                band = "ELEVATED"
            else:
                band = "NORMAL"
            fragility["band"] = band
            fragility["band_source"] = "training_distribution_calibrated_not_predictive"
            fragility["band_thresholds"] = {"elevated": elevated, "high": high, "extreme": extreme}
        full_qrs = None
        if self.full_qrs_enabled:
            from .full_qrs import score_full_qrs
            full_qrs = score_full_qrs(self, w, margin_feasible=None)

        qrs_value = (full_qrs or {}).get("qrs") if full_qrs is not None else fragility.get("qrs")
        gross_qrs_value = (full_qrs or {}).get("gross_qrs_before_cost_and_forecast_tail") if full_qrs is not None else fragility.get("gross_qrs")
        cost_qrs_value = (full_qrs or {}).get("qrs") if full_qrs is not None else fragility.get("cost_adjusted_qrs")

        return {
            "expected_return": expected,
            "expected_return_including_cash": expected,
            "cash_weight": cash_weight,
            "modeled_variance": variance,
            "modeled_volatility": volatility,
            "return_to_volatility": expected / volatility if volatility > 1e-12 else None,
            "sharpe_ratio": sharpe,
            "qrs": qrs_value,
            "gross_qrs": gross_qrs_value,
            "cost_adjusted_qrs": cost_qrs_value,
            "legacy_qrs": fragility.get("qrs"),
            "legacy_cost_adjusted_qrs": fragility.get("cost_adjusted_qrs"),
            "full_qrs": full_qrs,
            "effective_volatility": fragility.get("effective_volatility"),
            "alpha_volatility": fragility.get("alpha_volatility"),
            "fragility_premium": fragility.get("fragility_premium"),
            "fragility_band": fragility.get("band"),
            "risk_free_rate": float(self.risk_free_rate),
            "horizon_sessions": int(self.horizon_sessions),
            "transaction_cost_bps": float(self.transaction_cost_bps),
            "gross": gross,
            "max_name_weight": float(w.max()) if len(w) else 0.0,
            "turnover_from_active": float(np.abs(w - np.asarray(self.current_weights, dtype=float)).sum()),
            "objective_utility": self.objective(w),
            "fragility": fragility,
            "fragility_enabled": bool(self.fragility_enabled),
            "resilience_alpha": float(self.resilience_alpha),
            "covariance_method": str(self.covariance_method),
            "covariance_provenance": dict(self.covariance_provenance or {}),
            "full_qrs_enabled": bool(self.full_qrs_enabled),
            "lending_rate": float(self.risk_free_rate if self.lending_rate is None else self.lending_rate),
            "borrowing_rate": float(self.risk_free_rate if self.borrowing_rate is None else self.borrowing_rate),
            "full_qrs_scenario_set_version": dict(self.full_qrs_stress_metadata or {}).get("scenario_set_version"),
        }
