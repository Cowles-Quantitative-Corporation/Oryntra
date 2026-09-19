"""Universal V2: one causal signal contract for scanner and portfolio research."""
from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json

import numpy as np
import pandas as pd

from .universal_position_policy import PositionPolicyConfig
from .universal_market_context import MarketContextConfig
from .universal_peer_shock import PeerShockConfig
from .universal_learning import walk_forward_ridge_scores
from .universal_institutional_decision import InstitutionalDecisionConfig, constrain_institutional_weights
from .universal_risk_supervisor import RiskSupervisorConfig, supervise_cross_sectional_weights
from .universal_risk_v202 import V202PredictiveConfig, build_systemic_risk_panel, predict_systemic_risk
from .universal_factor_model import FactorModelConfig, build_factor_snapshot, portfolio_factor_diagnostics
from .universal_optimizer import PortfolioOptimizerConfig, optimize_portfolio
from .universal_phase3 import Phase3Config, build_phase3_snapshot
from .alpha_v1 import AlphaV1Config, alpha_v1_scores


ENGINE_ID = "universal_v2"
ENGINE_VERSION = "2.2.0-research"


@dataclass(frozen=True)
class UniversalConfig:
    trend_weight: float = .40
    momentum_weight: float = .35
    breakout_weight: float = .15
    pullback_weight: float = .10
    residual_weight: float = .0
    fundamental_weight: float = .0
    fundamental_quality_overlay_weight: float = .0
    fundamental_acceleration_overlay_weight: float = .0
    minimum_fundamental_observed_fraction: float = .80
    alpha_model: str = "handcrafted"
    research_profile: str = "universal_v2"
    ridge_training_sessions: int = 756
    ridge_retrain_sessions: int = 21
    ridge_horizon_sessions: int = 5
    ridge_penalty: float = 10.0
    ridge_score_smoothing: float = 0.0
    ridge_include_residual_momentum: bool = False
    ridge_residual_momentum_21: bool = False
    ridge_residual_momentum_63: bool = False
    ridge_target_market_residual: bool = False
    ridge_ic_gate: bool = False
    ridge_ic_lookback_sessions: int = 63
    ridge_ic_minimum: float = 0.0
    ridge_economic_interactions: bool = False
    ridge_include_ohlcv_structure: bool = False
    entry_threshold: float = .15
    selection_mode: str = "absolute"
    minimum_relative_rank: float = .50
    maximum_asset_annual_volatility: float | None = None
    maximum_portfolio_market_beta: float | None = None
    vol_target: float = .12
    maximum_positions: int = 24
    name_cap: float = .25
    gross_cap: float = 1.0
    covariance_shrinkage: float = .5
    correlation_stress: float = .25
    risk_window: int = 63
    rebalance: str = "weekly"
    trade_buffer: float = .015
    cost_bps: float = 12.0
    impact_bps: float = 18.0
    participation: float = .02
    initial_equity: float = 1_000_000.0
    position_policy: PositionPolicyConfig = PositionPolicyConfig()
    market_context: MarketContextConfig = MarketContextConfig()
    peer_shock: PeerShockConfig = PeerShockConfig()
    risk_supervisor: RiskSupervisorConfig = RiskSupervisorConfig()
    institutional_decision: InstitutionalDecisionConfig = InstitutionalDecisionConfig()
    factor_model: FactorModelConfig = FactorModelConfig()
    portfolio_optimizer: PortfolioOptimizerConfig = PortfolioOptimizerConfig()
    phase3: Phase3Config = Phase3Config()
    alpha_v1: AlphaV1Config = AlphaV1Config()

    def __post_init__(self):
        for name, cls in (("position_policy", PositionPolicyConfig), ("market_context", MarketContextConfig), ("peer_shock", PeerShockConfig), ("risk_supervisor", RiskSupervisorConfig), ("institutional_decision", InstitutionalDecisionConfig), ("factor_model", FactorModelConfig), ("portfolio_optimizer", PortfolioOptimizerConfig), ("phase3", Phase3Config), ("alpha_v1", AlphaV1Config)):
            value = getattr(self, name)
            if isinstance(value, dict):
                object.__setattr__(self, name, cls(**value))
            elif not isinstance(value, cls):
                raise ValueError(f"{name} must be a validated configuration")
        for key, value in asdict(self).items():
            if key not in {"rebalance", "selection_mode", "alpha_model", "research_profile", "maximum_asset_annual_volatility", "maximum_portfolio_market_beta", "position_policy", "market_context", "peer_shock", "risk_supervisor", "institutional_decision", "factor_model", "portfolio_optimizer", "phase3", "alpha_v1"} and not np.isfinite(value):
                raise ValueError(f"{key} must be finite")
        weights = self.weights
        if min(weights) < 0 or not np.isclose(sum(weights), 1):
            raise ValueError("Signal weights must be nonnegative and sum to one")
        if not (0 <= self.entry_threshold < 1 and 0 < self.vol_target <= .5):
            raise ValueError("Invalid threshold or volatility target")
        if self.alpha_model not in {"handcrafted", "walk_forward_ridge", "alpha_v1"}:
            raise ValueError("alpha_model must be handcrafted, walk_forward_ridge, or alpha_v1")
        if self.research_profile not in {"universal_v2", "minerva_v1", "tba9_integrity", "phase2_factor_optimizer", "phase15_v203", "phase15_phase3", "phase15_phase2_phase3", "control_plane", "alpha_v1"}:
            raise ValueError("Unknown research profile")
        if not all(isinstance(value, bool) for value in (self.ridge_include_residual_momentum,
                                                          self.ridge_residual_momentum_21,
                                                          self.ridge_residual_momentum_63,
                                                          self.ridge_target_market_residual,
                                                          self.ridge_ic_gate,
                                                          self.ridge_economic_interactions,
                                                          self.ridge_include_ohlcv_structure)):
            raise ValueError("Ridge feature switches must be boolean")
        if self.residual_momentum_horizons and self.alpha_model != "walk_forward_ridge":
            raise ValueError("Residual momentum requires the walk_forward_ridge model")
        if not (0 <= self.fundamental_quality_overlay_weight <= 1
                and 0 <= self.fundamental_acceleration_overlay_weight <= 1
                and self.fundamental_quality_overlay_weight + self.fundamental_acceleration_overlay_weight <= 1):
            raise ValueError("Fundamental overlay weights must be nonnegative and sum to at most one")
        if not (0 < self.minimum_fundamental_observed_fraction <= 1):
            raise ValueError("minimum_fundamental_observed_fraction must be within (0, 1]")
        if self.alpha_model != "walk_forward_ridge" and (self.fundamental_quality_overlay_weight or self.fundamental_acceleration_overlay_weight):
            raise ValueError("Fundamental overlays require the walk_forward_ridge model")
        if not (252 <= self.ridge_training_sessions <= 2520 and 1 <= self.ridge_retrain_sessions <= 63
                and 2 <= self.ridge_horizon_sessions <= 21 and .001 <= self.ridge_penalty <= 1e5
                and 0 <= self.ridge_score_smoothing < 1):
            raise ValueError("Invalid walk-forward ridge configuration")
        if not (21 <= self.ridge_ic_lookback_sessions <= 252 and -1 <= self.ridge_ic_minimum <= 1):
            raise ValueError("Invalid information-coefficient gate configuration")
        if self.selection_mode not in {"absolute", "relative_rank"}:
            raise ValueError("selection_mode must be absolute or relative_rank")
        if not (0 < self.minimum_relative_rank < 1):
            raise ValueError("minimum_relative_rank must be in (0, 1)")
        if self.maximum_asset_annual_volatility is not None and not (.10 <= self.maximum_asset_annual_volatility <= 2):
            raise ValueError("maximum_asset_annual_volatility must be None or within [0.10, 2.00]")
        if self.maximum_portfolio_market_beta is not None and not (.10 <= self.maximum_portfolio_market_beta <= 1.50):
            raise ValueError("maximum_portfolio_market_beta must be None or within [0.10, 1.50]")
        if not (1 <= self.maximum_positions <= 24 and isinstance(self.maximum_positions, int)):
            raise ValueError("maximum_positions must be an integer from 1 through 24")
        if not (0 < self.name_cap <= 1 and 0 < self.gross_cap <= 1):
            raise ValueError("V2 is long-only with no leverage")
        if not (0 <= self.covariance_shrinkage <= 1 and 0 <= self.correlation_stress <= 1):
            raise ValueError("Risk shrinkage and correlation stress must be in [0, 1]")
        if not (21 <= self.risk_window <= 252 and isinstance(self.risk_window, int)):
            raise ValueError("risk_window must be an integer from 21 through 252")
        if self.rebalance not in {"daily", "weekly", "monthly"}:
            raise ValueError("Unknown rebalance schedule")
        if not (0 <= self.trade_buffer <= .2 and 0 < self.participation <= .25):
            raise ValueError("Invalid buffer or participation ceiling")
        if not (0 <= self.cost_bps <= 250 and 0 <= self.impact_bps <= 500 and self.initial_equity >= 100):
            raise ValueError("Invalid cost or capital assumption")
        if self.alpha_model == "alpha_v1" and not self.alpha_v1.enabled:
            object.__setattr__(self, "alpha_v1", AlphaV1Config(**({**asdict(self.alpha_v1), "enabled": True})))
        if self.alpha_model != "alpha_v1" and self.alpha_v1.enabled:
            raise ValueError("Alpha V1 configuration may only be enabled when alpha_model=alpha_v1")
        if self.portfolio_optimizer.enabled and not self.factor_model.enabled:
            raise ValueError("Phase 2 portfolio optimizer requires the factor model")

    @property
    def weights(self):
        return (self.trend_weight, self.momentum_weight, self.breakout_weight, self.pullback_weight,
                self.residual_weight, self.fundamental_weight)

    @property
    def fingerprint(self):
        return hashlib.sha256(json.dumps({"version": ENGINE_VERSION, **asdict(self)}, sort_keys=True).encode()).hexdigest()

    @property
    def residual_momentum_horizons(self) -> tuple[int, ...]:
        """Selected residual horizons; legacy switch maps to the full pair."""
        if self.ridge_include_residual_momentum:
            return (21, 63)
        return tuple(horizon for horizon, enabled in ((21, self.ridge_residual_momentum_21),
                                                       (63, self.ridge_residual_momentum_63)) if enabled)


def signal_panel(prices: pd.DataFrame, config: UniversalConfig = UniversalConfig(),
                 benchmark_returns: pd.Series | None = None,
                 fundamental_scores: pd.DataFrame | None = None,
                 opens: pd.DataFrame | None = None, volumes: pd.DataFrame | None = None,
                 learning_closes: pd.DataFrame | None = None, learning_opens: pd.DataFrame | None = None,
                 learning_volumes: pd.DataFrame | None = None,
                 learning_highs: pd.DataFrame | None = None, learning_lows: pd.DataFrame | None = None,
                 fundamental_acceleration_scores: pd.DataFrame | None = None,
                 alpha_sector_labels: pd.DataFrame | None = None,
                 alpha_value_scores: pd.DataFrame | None = None,
                 alpha_quality_scores: pd.DataFrame | None = None) -> dict[str, pd.DataFrame]:
    """Features at t use only data through t; signals are executed after t."""
    if not isinstance(prices.index, pd.DatetimeIndex) or not prices.index.is_monotonic_increasing or prices.index.has_duplicates:
        raise ValueError("Signals require unique, increasing dates")
    if prices.columns.has_duplicates or prices.empty:
        raise ValueError("Signals require unique symbols and history")
    prices = prices.astype(float)
    if not np.isfinite(prices.to_numpy()).all() or (prices <= 0).any().any():
        raise ValueError("Signals require positive finite prices; missing bars must be resolved explicitly")
    ret = prices.pct_change(fill_method=None)
    vol = ret.rolling(63, min_periods=63).std(ddof=0).clip(lower=.003)
    trend = sum(np.tanh(prices.pct_change(n, fill_method=None).div(vol * np.sqrt(n))) for n in (21, 63, 126, 252)) / 4
    momentum = np.tanh(prices.shift(21).div(prices.shift(252)).sub(1).div(vol * np.sqrt(231)))
    high, low = prices.rolling(63).max(), prices.rolling(63).min()
    breakout = prices.sub(low).div((high - low).replace(0, np.nan)).mul(2).sub(1).fillna(0)
    pullback = -np.tanh(prices.pct_change(5, fill_method=None).div(vol * np.sqrt(5))) * trend.clip(lower=0)
    residual = pd.DataFrame(0.0, index=prices.index, columns=prices.columns)
    residual_ready = pd.DataFrame(True, index=prices.index, columns=prices.columns)
    if config.residual_weight:
        if benchmark_returns is None:
            raise ValueError("Residual-strength V2 research requires an explicit completed benchmark return series")
        market = benchmark_returns.reindex(prices.index)
        if not np.isfinite(market.iloc[1:].to_numpy()).all() or (market.iloc[1:] <= -1).any():
            raise ValueError("Residual-strength benchmark must cover every price session after the first")
        market_variance = market.rolling(126, min_periods=126).var(ddof=0)
        beta = ret.rolling(126, min_periods=126).cov(market).div(market_variance, axis=0)
        daily_residual = ret.sub(beta.mul(market, axis=0), axis=0)
        residual_volatility = daily_residual.rolling(63, min_periods=63).std(ddof=0).clip(lower=.003)
        residual = np.tanh(daily_residual.rolling(63, min_periods=63).sum().div(residual_volatility * np.sqrt(63)))
        residual_ready = residual.notna()
    fundamental = pd.DataFrame(0.0, index=prices.index, columns=prices.columns)
    fundamental_acceleration = pd.DataFrame(0.0, index=prices.index, columns=prices.columns)
    needs_fundamental = config.fundamental_weight or config.fundamental_quality_overlay_weight
    if needs_fundamental:
        if fundamental_scores is None:
            raise ValueError("Fundamental V2 research requires an explicit availability-dated fundamental score panel")
        if not isinstance(fundamental_scores.index, pd.DatetimeIndex) or fundamental_scores.index.has_duplicates:
            raise ValueError("Fundamental score panel requires unique dated observations")
        if set(fundamental_scores.columns) != set(prices.columns):
            raise ValueError("Fundamental score panel symbols must exactly match the price panel")
        fundamental = fundamental_scores.reindex(index=prices.index, columns=prices.columns)
        if not np.isfinite(fundamental.to_numpy()).all() or (fundamental.abs() > 1).any().any():
            raise ValueError("Fundamental scores must be finite and bounded to [-1, 1]")
    if config.fundamental_acceleration_overlay_weight:
        if fundamental_acceleration_scores is None:
            raise ValueError("Fundamental acceleration overlay requires an availability-dated acceleration panel")
        if set(fundamental_acceleration_scores.columns) != set(prices.columns):
            raise ValueError("Fundamental acceleration panel symbols must exactly match prices")
        fundamental_acceleration = fundamental_acceleration_scores.reindex(index=prices.index, columns=prices.columns)
        if not np.isfinite(fundamental_acceleration.to_numpy()).all() or (fundamental_acceleration.abs() > 1).any().any():
            raise ValueError("Fundamental acceleration scores must be finite and bounded to [-1, 1]")
    components = dict(trend=trend, momentum=momentum, breakout=breakout, pullback=pullback,
                      residual=residual, fundamental=fundamental, fundamental_acceleration=fundamental_acceleration)
    if config.alpha_model == "alpha_v1":
        alpha_output = alpha_v1_scores(prices, benchmark_returns=benchmark_returns, sector_labels=alpha_sector_labels, value_scores=alpha_value_scores, quality_scores=alpha_quality_scores, config=config.alpha_v1)
        score = alpha_output["score"].reindex(index=prices.index, columns=prices.columns)
        ready = alpha_output["ready"].reindex(index=prices.index, columns=prices.columns).fillna(False)
        components.update(predicted_return=alpha_output["predicted_return"].reindex_like(prices), prediction_error=alpha_output["prediction_error"].reindex_like(prices), alpha_v1_score=score)
    elif config.alpha_model == "walk_forward_ridge":
        if opens is None or volumes is None:
            raise ValueError("Walk-forward ridge research requires aligned open and volume panels")
        supplied = (learning_closes, learning_opens, learning_volumes)
        if any(item is not None for item in supplied) and not all(item is not None for item in supplied):
            raise ValueError("Walk-forward learning requires close, open and volume panels together")
        source_closes, source_opens, source_volumes = (supplied if all(item is not None for item in supplied)
                                                        else (prices, opens, volumes))
        need_prediction_diagnostics = (
            config.institutional_decision.enabled
            or (config.risk_supervisor.enabled
                and (config.risk_supervisor.v201_enabled or config.risk_supervisor.v202_enabled or config.risk_supervisor.v203_enabled)
                and config.risk_supervisor.alpha_preservation_enabled)
        )
        learned_output = walk_forward_ridge_scores(source_closes, source_opens, source_volumes,
                                            training_sessions=config.ridge_training_sessions,
                                            retrain_sessions=config.ridge_retrain_sessions,
                                            horizon_sessions=config.ridge_horizon_sessions,
                                            penalty=config.ridge_penalty,
                                            benchmark_returns=benchmark_returns,
                                            residual_momentum_horizons=config.residual_momentum_horizons,
                                            target_market_residual=config.ridge_target_market_residual,
                                            include_economic_interactions=config.ridge_economic_interactions,
                                            highs=learning_highs,
                                            lows=learning_lows,
                                            include_ohlcv_structure=config.ridge_include_ohlcv_structure,
                                            score_smoothing=config.ridge_score_smoothing,
                                            ic_gate=config.ridge_ic_gate,
                                            ic_lookback_sessions=config.ridge_ic_lookback_sessions,
                                            ic_minimum=config.ridge_ic_minimum,
                                            return_diagnostics=need_prediction_diagnostics)
        if need_prediction_diagnostics:
            learned = learned_output["score"]
            components["predicted_return"] = learned_output["predicted_return"].reindex(index=prices.index, columns=prices.columns)
            components["prediction_error"] = learned_output["prediction_error"].reindex(index=prices.index, columns=prices.columns)
        else:
            learned = learned_output
        if not set(prices.columns).issubset(learned.columns):
            raise ValueError("Walk-forward learning panel must cover every evaluated symbol")
        learned = learned.reindex(index=prices.index, columns=prices.columns)
        ready = learned.ne(0)
        quality_weight = config.fundamental_quality_overlay_weight
        acceleration_weight = config.fundamental_acceleration_overlay_weight
        score = ((1 - quality_weight - acceleration_weight) * learned
                 + quality_weight * fundamental
                 + acceleration_weight * fundamental_acceleration).where(ready, 0)
        components["learned"] = learned
    else:
        score = sum(components[k] * w for k, w in zip(components, config.weights))
        ready = trend.notna() & momentum.notna() & residual_ready
    components.update(score=score.where(ready, 0).clip(-1, 1), volatility=vol, ready=ready)
    return components


def scan_snapshot(history: pd.DataFrame, config: UniversalConfig = UniversalConfig()) -> dict:
    if history is None or "Close" not in history or len(history) < 253:
        return {"engine": ENGINE_ID, "version": ENGINE_VERSION, "status": "insufficient_history", "required_bars": 253, "score": 0.0, "eligible": False, "config_fingerprint": config.fingerprint}
    if config.residual_weight:
        return {"engine": ENGINE_ID, "version": ENGINE_VERSION, "status": "benchmark_required_for_residual_strength", "score": 0.0,
                "eligible": False, "config_fingerprint": config.fingerprint,
                "score_meaning": "Residual-strength research needs a dated SPY/QQQ benchmark series and is not a standalone scanner mode."}
    if config.alpha_model == "walk_forward_ridge":
        return {"engine": ENGINE_ID, "version": ENGINE_VERSION, "status": "universe_required_for_walk_forward_ridge", "score": 0.0,
                "eligible": False, "config_fingerprint": config.fingerprint,
                "score_meaning": "Walk-forward ridge research needs a dated multi-stock price, open and volume panel and is not a standalone scanner mode."}
    if config.fundamental_weight or config.fundamental_quality_overlay_weight or config.fundamental_acceleration_overlay_weight:
        return {"engine": ENGINE_ID, "version": ENGINE_VERSION, "status": "fundamental_data_required", "score": 0.0,
                "eligible": False, "config_fingerprint": config.fingerprint,
                "score_meaning": "Fundamental research needs an availability-dated filing score panel and is not a standalone scanner mode."}
    panel = signal_panel(history[["Close"]], config)
    score = float(panel["score"].iloc[-1, 0])
    return {"engine": ENGINE_ID, "version": ENGINE_VERSION, "status": "available", "as_of": str(history.index[-1].date()), "score": score, "eligible": score >= config.entry_threshold, "components": {name: float(panel[name].iloc[-1, 0]) for name in ("trend", "momentum", "breakout", "pullback")}, "annual_volatility": float(panel["volatility"].iloc[-1, 0] * np.sqrt(252)), "config_fingerprint": config.fingerprint, "score_meaning": "Bounded signal strength; not a calibrated probability or predicted alpha."}


def scanner_setup(history: pd.DataFrame) -> dict:
    evidence = scan_snapshot(history)
    score = evidence["score"]
    eligible = evidence["eligible"]
    kind = "TREND_CONTINUATION" if eligible else "NO_TRADE"
    strength = float(50 + 50 * score)
    return {"setup_type": kind, "confidence": strength, "direction": "LONG" if eligible else "NEUTRAL", "rules_fired": [f"Universal V2 signal strength: {score:.3f}", "Same signal implementation as the V2 portfolio test"], "all_scores": {kind: strength}, "patterns": {"universal_evidence": evidence}, "universal_evidence": evidence}


def portfolio_targets(prices: pd.DataFrame, config: UniversalConfig = UniversalConfig(),
                      benchmark_returns: pd.Series | None = None,
                      fundamental_scores: pd.DataFrame | None = None,
                      opens: pd.DataFrame | None = None, volumes: pd.DataFrame | None = None,
                      learning_closes: pd.DataFrame | None = None, learning_opens: pd.DataFrame | None = None,
                      learning_volumes: pd.DataFrame | None = None,
                      learning_highs: pd.DataFrame | None = None, learning_lows: pd.DataFrame | None = None,
                      fundamental_acceleration_scores: pd.DataFrame | None = None,
                      factor_sector_labels: pd.DataFrame | None = None,
                      factor_size_scores: pd.DataFrame | None = None,
                      factor_value_scores: pd.DataFrame | None = None,
                      factor_quality_scores: pd.DataFrame | None = None,
                      alpha_sector_labels: pd.DataFrame | None = None,
                      alpha_value_scores: pd.DataFrame | None = None,
                      alpha_quality_scores: pd.DataFrame | None = None,
                      precomputed_panel: dict[str, pd.DataFrame] | None = None) -> pd.DataFrame:
    """Create causal targets with risk decisions on the configured rebalance schedule.

    Alpha, institutional proposal weights, V1 supervision and V2 research overlays
    are evaluated only on explicit portfolio decision events.  This preserves the
    original V1 timing and makes V2 persistence unambiguously count rebalance
    decisions rather than daily bars.  Resulting target changes still execute next open.
    """
    panel = precomputed_panel if precomputed_panel is not None else signal_panel(
        prices, config, benchmark_returns, fundamental_scores, opens, volumes,
        learning_closes, learning_opens, learning_volumes, learning_highs, learning_lows,
        fundamental_acceleration_scores=fundamental_acceleration_scores,
        alpha_sector_labels=alpha_sector_labels, alpha_value_scores=alpha_value_scores, alpha_quality_scores=alpha_quality_scores,
    )
    required_panel = {"score", "volatility"}
    if not required_panel.issubset(panel):
        raise ValueError("Precomputed signal panel is missing score or volatility")
    if not (panel["score"].index.equals(prices.index) and panel["score"].columns.equals(prices.columns)
            and panel["volatility"].index.equals(prices.index) and panel["volatility"].columns.equals(prices.columns)):
        raise ValueError("Precomputed signal panel must exactly align with prices")

    returns = prices.pct_change(fill_method=None)

    # V2.0.2/V2.0.3 precompute causal universe-level structural features once. Future
    # labels are availability-gated inside predict_systemic_risk, so historical
    # decisions can only train on outcomes already completed by that session.
    v202_predictive_panel = None
    v202_predictive_config = None
    if config.risk_supervisor.enabled and (config.risk_supervisor.v202_enabled or config.risk_supervisor.v203_enabled):
        clean_returns = returns.iloc[1:].to_numpy(dtype=float)
        if not np.isfinite(clean_returns).all():
            raise ValueError("V2 predictive risk requires complete finite price returns")
        v202_predictive_config = V202PredictiveConfig(
            feature_window=config.risk_supervisor.v201_topology_window,
            compare_window=config.risk_supervisor.v201_topology_compare_window,
            short_vol_window=config.risk_supervisor.v201_short_window,
            long_vol_window=max(config.risk_supervisor.v201_long_window // 2,
                                config.risk_supervisor.v201_short_window + 1),
            forecast_horizon=config.risk_supervisor.v202_forecast_horizon,
            training_window=config.risk_supervisor.v202_training_window,
            training_stride=config.risk_supervisor.v202_training_stride,
            minimum_training_observations=config.risk_supervisor.v202_minimum_training_observations,
            ridge_penalty=config.risk_supervisor.v202_ridge_penalty,
            persistence_sessions=config.risk_supervisor.v202_persistence_sessions,
            systemic_trigger_percentile=config.risk_supervisor.v202_systemic_trigger_percentile,
            minimum_validation_r2=config.risk_supervisor.v202_minimum_validation_r2,
            validation_fraction=config.risk_supervisor.v202_validation_fraction,
            maximum_additional_gross_reduction=config.risk_supervisor.v202_maximum_additional_gross_reduction,
            minimum_portfolio_change=config.risk_supervisor.v202_minimum_portfolio_change,
            optimizer_risk_aversion=config.risk_supervisor.v202_optimizer_risk_aversion,
            optimizer_turnover_penalty=config.risk_supervisor.v202_optimizer_turnover_penalty,
            optimizer_iterations=config.risk_supervisor.v202_optimizer_iterations,
            optimizer_learning_rate=config.risk_supervisor.v202_optimizer_learning_rate,
            optimizer_turnover_tolerance=config.risk_supervisor.v202_optimizer_turnover_tolerance,
        )
        v202_predictive_panel = build_systemic_risk_panel(clean_returns, v202_predictive_config)

    asset_market_beta = None
    if config.maximum_portfolio_market_beta is not None:
        if benchmark_returns is None:
            raise ValueError("Portfolio beta ceiling requires an explicit completed market benchmark")
        market = benchmark_returns.reindex(prices.index).astype(float).copy()
        if not np.isfinite(market.iloc[1:].to_numpy()).all() or (market.iloc[1:] <= -1).any():
            raise ValueError("Portfolio beta ceiling benchmark must cover every actionable price session")
        market.iloc[0] = 0.0
        market_variance = market.rolling(126, min_periods=126).var(ddof=0)
        asset_market_beta = returns.rolling(126, min_periods=126).cov(market).div(market_variance, axis=0)

    result = pd.DataFrame(0.0, index=prices.index, columns=prices.columns)
    raw_targets = pd.DataFrame(0.0, index=prices.index, columns=prices.columns)
    pre_optimizer_targets = pd.DataFrame(0.0, index=prices.index, columns=prices.columns)
    institutional_audit: list[dict[str, object]] = []
    factor_model_audit: list[dict[str, object]] = []
    portfolio_optimizer_audit: list[dict[str, object]] = []
    phase3_audit: list[dict[str, object]] = []
    risk_target_audit: list[dict[str, object]] = []
    previous_risk_state: str | None = None
    previous_risk_persistence_count = 0
    last_expected_alpha: np.ndarray | None = None
    last_prediction_error: np.ndarray | None = None
    last_priority: np.ndarray | None = None

    need_dollar_volume = (
        config.institutional_decision.enabled
        or config.portfolio_optimizer.enabled
        or config.phase3.enabled
        or (config.risk_supervisor.enabled
            and (config.risk_supervisor.v201_enabled or config.risk_supervisor.v202_enabled or config.risk_supervisor.v203_enabled)
            and config.risk_supervisor.liquidity_enabled)
    )
    prior_median_dollar_volume = None
    if need_dollar_volume and volumes is not None:
        if not (volumes.index.equals(prices.index) and volumes.columns.equals(prices.columns)):
            raise ValueError("Volume panel must exactly align with prices for institutional / V2 risk")
        prior_median_dollar_volume = prices.mul(volumes).rolling(20, min_periods=20).median().shift(1)

    periods = prices.index.to_period("W-FRI" if config.rebalance == "weekly" else "M")

    for i in range(252, len(prices)):
        scheduled_rebalance = (
            config.rebalance == "daily"
            or i == 252
            or periods[i] != periods[i - 1]
        )

        # Preserve the original portfolio/risk decision cadence.  V2.0.2's first
        # integration accidentally recomputed V1 every day; V2.0.3 makes the
        # decision unit explicit and carries the last approved target between
        # scheduled rebalances.
        if not scheduled_rebalance:
            raw_targets.iloc[i] = raw_targets.iloc[i - 1]
            pre_optimizer_targets.iloc[i] = pre_optimizer_targets.iloc[i - 1]
            result.iloc[i] = result.iloc[i - 1]
            continue

        if scheduled_rebalance:
            score = panel["score"].iloc[i]
            if config.selection_mode == "relative_rank":
                relative_rank = score.rank(pct=True, method="first")
                strength = relative_rank.where(
                    (relative_rank >= config.minimum_relative_rank) & (score >= 0), 0,
                )
            else:
                strength = score.where(score >= config.entry_threshold, 0)
            if config.maximum_asset_annual_volatility is not None:
                annual_volatility = panel["volatility"].iloc[i] * np.sqrt(252)
                strength = strength.where(annual_volatility <= config.maximum_asset_annual_volatility, 0)
            raw = strength.div(panel["volatility"].iloc[i]).to_numpy()
            eligible = np.flatnonzero(raw > 0)
            if len(eligible) > config.maximum_positions:
                selected = sorted(eligible, key=lambda j: (-float(raw[j]), str(prices.columns[j])))[:config.maximum_positions]
                raw = np.where(np.isin(np.arange(len(raw)), selected), raw, 0.0)
            total = raw.sum()
            if total <= 0:
                weights = np.zeros(len(prices.columns), dtype=float)
            else:
                weights = raw / total * config.gross_cap
                for _ in range(len(weights)):
                    excess = np.maximum(weights - config.name_cap, 0).sum()
                    weights = np.minimum(weights, config.name_cap)
                    free = (weights < config.name_cap - 1e-12) & (raw > 0)
                    if excess < 1e-12 or not free.any():
                        break
                    weights[free] += excess * raw[free] / raw[free].sum()
                weights = np.minimum(weights, config.name_cap)

            if config.institutional_decision.enabled and weights.sum() > 0:
                if prior_median_dollar_volume is None:
                    raise ValueError("Institutional decision layer requires volume data")
                weights, decision_audit = constrain_institutional_weights(
                    weights,
                    panel["predicted_return"].iloc[i].to_numpy(dtype=float),
                    panel["prediction_error"].iloc[i].to_numpy(dtype=float),
                    prior_median_dollar_volume.iloc[i].to_numpy(dtype=float),
                    capital=config.initial_equity,
                    base_cost_bps=config.cost_bps,
                    impact_bps=config.impact_bps,
                    participation=config.participation,
                    config=config.institutional_decision,
                )
                institutional_audit.append({"date": str(prices.index[i].date()), **decision_audit})

            if asset_market_beta is not None and weights.sum() > 0:
                betas = asset_market_beta.iloc[i].to_numpy(dtype=float)
                if not np.isfinite(betas).all():
                    raise ValueError("Portfolio beta ceiling needs 126 completed benchmark and asset return sessions")
                portfolio_beta = float(weights @ betas)
                if portfolio_beta > config.maximum_portfolio_market_beta:
                    weights *= config.maximum_portfolio_market_beta / portfolio_beta

            if config.peer_shock.enabled and i >= config.peer_shock.correlation_lookback_sessions and weights.sum() > 0:
                peer_returns = returns.iloc[i - config.peer_shock.correlation_lookback_sessions + 1:i + 1]
                correlation = peer_returns.corr()
                recent = prices.iloc[i].div(prices.iloc[i - config.peer_shock.shock_return_sessions]).sub(1)
                for j, symbol in enumerate(prices.columns):
                    peers = correlation.index[(correlation[symbol] >= config.peer_shock.minimum_peer_correlation) & (correlation.index != symbol)]
                    shocked = int((recent.reindex(peers) <= config.peer_shock.peer_shock_return).sum())
                    if shocked >= config.peer_shock.minimum_shocked_peers:
                        weights[j] *= config.peer_shock.exposure_multiplier

            if "predicted_return" in panel:
                last_expected_alpha = panel["predicted_return"].iloc[i].to_numpy(dtype=float)
            else:
                last_expected_alpha = None
            if "prediction_error" in panel:
                last_prediction_error = panel["prediction_error"].iloc[i].to_numpy(dtype=float)
            else:
                last_prediction_error = None
            last_priority = panel["score"].iloc[i].to_numpy(dtype=float)

            # Phase 2 sits between alpha construction and independent risk
            # supervision. The factor model explains the proposed portfolio; the
            # optimizer may reshape it, but cannot bypass V2.0.3 or the final vol cap.
            pre_optimizer_targets.iloc[i] = weights
            factor_snapshot = {"enabled": False, "available": False, "reason": "factor_model_disabled"}
            if config.factor_model.enabled and weights.sum() > 0:
                factor_snapshot = build_factor_snapshot(
                    returns, as_of=i, benchmark_returns=benchmark_returns, config=config.factor_model,
                    sector_labels=factor_sector_labels, size_scores=factor_size_scores,
                    value_scores=factor_value_scores, quality_scores=factor_quality_scores,
                )
                factor_entry: dict[str, object] = {
                    "date": str(prices.index[i].date()),
                    "available": bool(factor_snapshot.get("available")),
                    "reason": factor_snapshot.get("reason"),
                    "factor_names": list(factor_snapshot.get("factor_names", [])),
                    "factor_availability": factor_snapshot.get("factor_availability", {}),
                    "observations": factor_snapshot.get("observations"),
                }
                if factor_snapshot.get("available"):
                    factor_entry["proposal"] = portfolio_factor_diagnostics(weights, factor_snapshot)
                factor_model_audit.append(factor_entry)

            if config.portfolio_optimizer.enabled and weights.sum() > 0:
                optimizer_adv = None
                if prior_median_dollar_volume is not None:
                    row = prior_median_dollar_volume.iloc[i].to_numpy(dtype=float)
                    if np.isfinite(row).any():
                        optimizer_adv = row
                weights, optimizer_audit = optimize_portfolio(
                    weights, factor_snapshot=factor_snapshot, expected_alpha=last_expected_alpha,
                    prediction_error=last_prediction_error, fallback_priority=last_priority,
                    current_weights=result.iloc[i - 1].to_numpy(dtype=float) if i > 0 else None,
                    gross_cap=config.gross_cap, name_cap=config.name_cap,
                    maximum_market_beta=config.maximum_portfolio_market_beta, adv_dollars=optimizer_adv,
                    capital=config.initial_equity, participation=config.participation,
                    config=config.portfolio_optimizer,
                )
                optimizer_entry = {"date": str(prices.index[i].date()), **optimizer_audit}
                if factor_snapshot.get("available"):
                    optimizer_entry["factor_risk_after"] = portfolio_factor_diagnostics(weights, factor_snapshot)
                portfolio_optimizer_audit.append(optimizer_entry)

            raw_targets.iloc[i] = weights

        weights = raw_targets.iloc[i].to_numpy(dtype=float)
        if weights.sum() <= 0:
            result.iloc[i] = 0.0
            continue

        risk_lookback = config.risk_window
        if (config.risk_supervisor.enabled
                and (config.risk_supervisor.v201_enabled or config.risk_supervisor.v202_enabled or config.risk_supervisor.v203_enabled)):
            risk_lookback = max(risk_lookback, config.risk_supervisor.v201_long_window,
                                config.risk_supervisor.v201_tail_window,
                                config.risk_supervisor.v201_topology_window + config.risk_supervisor.v201_topology_compare_window)
        sample_frame = returns.iloc[max(1, i - risk_lookback + 1):i + 1]
        sample = sample_frame.to_numpy(dtype=float)
        if not np.isfinite(sample).all():
            raise ValueError("Risk model requires complete finite return history for the active universe")
        covariance_window = returns.iloc[max(1, i - config.risk_window + 1):i + 1].to_numpy(dtype=float)
        covariance = np.atleast_2d(np.cov(covariance_window, rowvar=False, ddof=0))
        diagonal = np.diag(np.diag(covariance))
        covariance = (1 - config.covariance_shrinkage) * covariance + config.covariance_shrinkage * diagonal
        marginal = np.sqrt(np.maximum(np.diag(covariance), 0))
        stressed = (1 - config.correlation_stress) * covariance + config.correlation_stress * np.outer(marginal, marginal)

        # Phase 2 research may give the supervisor a factor-reconstructed asset
        # covariance. The same correlation-stress transform is applied so V1/V2
        # semantics remain comparable; disabled Phase 2 profiles are unchanged.
        if (config.factor_model.enabled and config.factor_model.feed_supervisor_covariance
                and 'factor_snapshot' in locals() and factor_snapshot.get("available")):
            factor_covariance = np.asarray(factor_snapshot["asset_covariance"], dtype=float)
            factor_marginal = np.sqrt(np.maximum(np.diag(factor_covariance), 0.0))
            stressed = (1 - config.correlation_stress) * factor_covariance + config.correlation_stress * np.outer(factor_marginal, factor_marginal)

        adv = None
        if prior_median_dollar_volume is not None:
            adv_row = prior_median_dollar_volume.iloc[i].to_numpy(dtype=float)
            if np.isfinite(adv_row).any():
                adv = adv_row

        predictive_forecast = None
        if (config.risk_supervisor.v202_enabled or config.risk_supervisor.v203_enabled) and v202_predictive_panel is not None:
            predictive_forecast = predict_systemic_risk(
                v202_predictive_panel,
                as_of=i - 1,  # predictive panel begins at price session 1
                config=v202_predictive_config,
            )

        weights, risk_audit = supervise_cross_sectional_weights(
            weights, stressed, config.risk_supervisor,
            returns_history=sample,
            expected_alpha=last_expected_alpha,
            prediction_error=last_prediction_error,
            fallback_priority=last_priority,
            adv_dollars=adv,
            capital=config.initial_equity,
            symbols=[str(symbol) for symbol in prices.columns],
            previous_state=previous_risk_state,
            predictive_forecast=predictive_forecast,
            previous_persistence_count=previous_risk_persistence_count,
            current_weights=result.iloc[i - 1].to_numpy(dtype=float) if i > 0 else None,
        )
        if config.risk_supervisor.v201_enabled:
            previous_risk_state = str(risk_audit.get("state", previous_risk_state or "normal"))
        if config.risk_supervisor.v202_enabled or config.risk_supervisor.v203_enabled:
            previous_risk_persistence_count = int(risk_audit.get("persistence_rebalances", risk_audit.get("persistence_count", 0)))
        risk_target_audit.append({"date": str(prices.index[i].date()), **risk_audit})

        # Preserve Universal's independent ex-ante volatility ceiling. V2
        # may remove additional risk after V1; this ceiling remains the final absolute
        # portfolio-volatility budget used by every Universal profile.
        risk = float(np.sqrt(max(0, weights @ stressed @ weights) * 252))
        final_weights = weights * min(1.0, config.vol_target / max(risk, 1e-12))
        result.iloc[i] = final_weights

        # Phase 3 is deliberately non-binding. It observes the final approved
        # target after optimizer + supervisor + absolute vol ceiling, so enabling
        # Phase 3 cannot change P&L and can be studied independently of Phase 2.
        if config.phase3.enabled and final_weights.sum() > 0:
            phase3_start = max(1, i - config.phase3.historical_lookback_sessions + 1)
            phase3_frame = returns.iloc[phase3_start:i + 1]
            phase3_benchmark = None
            if benchmark_returns is not None:
                aligned_benchmark = benchmark_returns.reindex(phase3_frame.index).to_numpy(dtype=float)
                if np.isfinite(aligned_benchmark).all():
                    phase3_benchmark = aligned_benchmark
            phase3_factor = factor_snapshot if ('factor_snapshot' in locals() and factor_snapshot.get("available")) else None
            phase3_snapshot = build_phase3_snapshot(
                final_weights,
                phase3_frame.to_numpy(dtype=float),
                stressed,
                symbols=[str(symbol) for symbol in prices.columns],
                config=config.phase3,
                dates=[str(day.date()) for day in phase3_frame.index],
                benchmark_history=phase3_benchmark,
                expected_alpha=last_expected_alpha,
                adv_dollars=adv,
                capital=config.initial_equity,
                factor_snapshot=phase3_factor,
            )
            phase3_audit.append({"date": str(prices.index[i].date()), **phase3_snapshot})

    result.attrs["institutional_decision_audit"] = institutional_audit
    result.attrs["factor_model_audit"] = factor_model_audit
    result.attrs["portfolio_optimizer_audit"] = portfolio_optimizer_audit
    result.attrs["phase3_audit"] = phase3_audit
    result.attrs["risk_supervisor_target_audit"] = risk_target_audit
    result.attrs["pre_optimizer_targets"] = pre_optimizer_targets
    result.attrs["raw_proposal_targets"] = raw_targets
    return result
