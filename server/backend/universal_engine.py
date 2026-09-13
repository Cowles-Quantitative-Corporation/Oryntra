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
from .universal_risk_supervisor import RiskSupervisorConfig, supervise_cross_sectional_weights


ENGINE_ID = "universal_v2"
ENGINE_VERSION = "2.0.0-research"


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

    def __post_init__(self):
        for name, cls in (("position_policy", PositionPolicyConfig), ("market_context", MarketContextConfig), ("peer_shock", PeerShockConfig), ("risk_supervisor", RiskSupervisorConfig)):
            value = getattr(self, name)
            if isinstance(value, dict):
                object.__setattr__(self, name, cls(**value))
            elif not isinstance(value, cls):
                raise ValueError(f"{name} must be a validated configuration")
        for key, value in asdict(self).items():
            if key not in {"rebalance", "selection_mode", "alpha_model", "research_profile", "maximum_asset_annual_volatility", "maximum_portfolio_market_beta", "position_policy", "market_context", "peer_shock", "risk_supervisor"} and not np.isfinite(value):
                raise ValueError(f"{key} must be finite")
        weights = self.weights
        if min(weights) < 0 or not np.isclose(sum(weights), 1):
            raise ValueError("Signal weights must be nonnegative and sum to one")
        if not (0 <= self.entry_threshold < 1 and 0 < self.vol_target <= .5):
            raise ValueError("Invalid threshold or volatility target")
        if self.alpha_model not in {"handcrafted", "walk_forward_ridge"}:
            raise ValueError("alpha_model must be handcrafted or walk_forward_ridge")
        if self.research_profile not in {"universal_v2", "minerva_v1"}:
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
                 fundamental_acceleration_scores: pd.DataFrame | None = None) -> dict[str, pd.DataFrame]:
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
    if config.alpha_model == "walk_forward_ridge":
        if opens is None or volumes is None:
            raise ValueError("Walk-forward ridge research requires aligned open and volume panels")
        supplied = (learning_closes, learning_opens, learning_volumes)
        if any(item is not None for item in supplied) and not all(item is not None for item in supplied):
            raise ValueError("Walk-forward learning requires close, open and volume panels together")
        source_closes, source_opens, source_volumes = (supplied if all(item is not None for item in supplied)
                                                        else (prices, opens, volumes))
        learned = walk_forward_ridge_scores(source_closes, source_opens, source_volumes,
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
                                            ic_minimum=config.ridge_ic_minimum)
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
                      precomputed_panel: dict[str, pd.DataFrame] | None = None) -> pd.DataFrame:
    """Create causal targets, optionally reusing a just-computed causal panel.

    ``run_universal`` needs the panel for lifecycle scoring as well as targets.
    Reusing it prevents a second identical walk-forward model fit without
    changing data availability, signals, or target math.
    """
    panel = precomputed_panel if precomputed_panel is not None else signal_panel(
        prices, config, benchmark_returns, fundamental_scores, opens, volumes,
        learning_closes, learning_opens, learning_volumes, learning_highs, learning_lows,
        fundamental_acceleration_scores=fundamental_acceleration_scores,
    )
    required_panel = {"score", "volatility"}
    if not required_panel.issubset(panel):
        raise ValueError("Precomputed signal panel is missing score or volatility")
    if not (panel["score"].index.equals(prices.index) and panel["score"].columns.equals(prices.columns)
            and panel["volatility"].index.equals(prices.index) and panel["volatility"].columns.equals(prices.columns)):
        raise ValueError("Precomputed signal panel must exactly align with prices")
    returns = prices.pct_change(fill_method=None)
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
    periods = prices.index.to_period("W-FRI" if config.rebalance == "weekly" else "M")
    for i in range(252, len(prices)):
        if config.rebalance != "daily" and i > 252 and periods[i] == periods[i - 1]:
            result.iloc[i] = result.iloc[i - 1]
            continue
        score = panel["score"].iloc[i]
        if config.selection_mode == "relative_rank":
            # The rank is cross-sectional at this completed close.  Retaining
            # the zero score floor prevents a relative ranking from buying a
            # weak stock simply because every stock is weak.
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
            continue
        weights = raw / total * config.gross_cap
        # Cap iteratively, redistributing only to eligible names with remaining space.
        for _ in range(len(weights)):
            excess = np.maximum(weights - config.name_cap, 0).sum()
            weights = np.minimum(weights, config.name_cap)
            free = (weights < config.name_cap - 1e-12) & (raw > 0)
            if excess < 1e-12 or not free.any():
                break
            weights[free] += excess * raw[free] / raw[free].sum()
        weights = np.minimum(weights, config.name_cap)
        if asset_market_beta is not None:
            betas = asset_market_beta.iloc[i].to_numpy(dtype=float)
            if not np.isfinite(betas).all():
                raise ValueError("Portfolio beta ceiling needs 126 completed benchmark and asset return sessions")
            portfolio_beta = float(weights @ betas)
            if portfolio_beta > config.maximum_portfolio_market_beta:
                weights *= config.maximum_portfolio_market_beta / portfolio_beta
        if config.peer_shock.enabled and i >= config.peer_shock.correlation_lookback_sessions:
            peer_returns = returns.iloc[i - config.peer_shock.correlation_lookback_sessions + 1:i + 1]
            correlation = peer_returns.corr()
            recent = prices.iloc[i].div(prices.iloc[i - config.peer_shock.shock_return_sessions]).sub(1)
            for j, symbol in enumerate(prices.columns):
                peers = correlation.index[(correlation[symbol] >= config.peer_shock.minimum_peer_correlation) & (correlation.index != symbol)]
                shocked = int((recent.reindex(peers) <= config.peer_shock.peer_shock_return).sum())
                if shocked >= config.peer_shock.minimum_shocked_peers:
                    weights[j] *= config.peer_shock.exposure_multiplier
        sample = returns.iloc[max(1, i - config.risk_window + 1):i + 1].to_numpy()
        covariance = np.atleast_2d(np.cov(sample, rowvar=False, ddof=0))
        diagonal = np.diag(np.diag(covariance))
        covariance = (1 - config.covariance_shrinkage) * covariance + config.covariance_shrinkage * diagonal
        marginal = np.sqrt(np.maximum(np.diag(covariance), 0))
        stressed = (1 - config.correlation_stress) * covariance + config.correlation_stress * np.outer(marginal, marginal)
        weights, _ = supervise_cross_sectional_weights(weights, stressed, config.risk_supervisor)
        risk = float(np.sqrt(max(0, weights @ stressed @ weights) * 252))
        result.iloc[i] = weights * min(1.0, config.vol_target / max(risk, 1e-12))
    return result
