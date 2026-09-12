"""Causal, research-only market-context controls for Universal V2.

When enabled in a frozen experiment, completed-close observations scale V2
targets for the following open. It is never a live scanner or broker control.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import math
from typing import Any, Literal

import numpy as np
import pandas as pd


RiskRegime = Literal["risk_on", "neutral", "caution", "risk_off"]


@dataclass(frozen=True)
class MarketContextConfig:
    """Bounded knobs for a completed-session, market-aware exposure overlay."""

    enabled: bool = False
    market_lookback_sessions: int = 63
    caution_market_return: float = -.04
    risk_off_market_return: float = -.10
    caution_breadth: float = .45
    risk_off_breadth: float = .30
    caution_correlation: float = .60
    risk_off_correlation: float = .80
    caution_market_annual_volatility: float | None = None
    risk_off_market_annual_volatility: float | None = None
    caution_exposure_multiplier: float = .70
    risk_off_exposure_multiplier: float = .35
    minimum_confirming_signals: int = 2

    def __post_init__(self) -> None:
        for name, value in asdict(self).items():
            if name not in {"enabled", "caution_market_annual_volatility", "risk_off_market_annual_volatility"} and not math.isfinite(value):
                raise ValueError(f"{name} must be finite")
        if not (21 <= self.market_lookback_sessions <= 252 and isinstance(self.market_lookback_sessions, int)):
            raise ValueError("market_lookback_sessions must be an integer from 21 through 252")
        if not (-.80 <= self.risk_off_market_return < self.caution_market_return < .20):
            raise ValueError("Market return thresholds must be ordered and plausible")
        if not (0 < self.risk_off_breadth < self.caution_breadth < 1):
            raise ValueError("Breadth thresholds must be ordered within (0, 1)")
        if not (0 < self.caution_correlation < self.risk_off_correlation <= 1):
            raise ValueError("Correlation thresholds must be ordered within (0, 1]")
        if (self.caution_market_annual_volatility is None) != (self.risk_off_market_annual_volatility is None):
            raise ValueError("Market-volatility thresholds must be supplied together or both disabled")
        if self.caution_market_annual_volatility is not None and not (
            .05 <= self.caution_market_annual_volatility < self.risk_off_market_annual_volatility <= 2
        ):
            raise ValueError("Market-volatility thresholds must be ordered annualized values within [0.05, 2.00]")
        if not (0 <= self.risk_off_exposure_multiplier <= self.caution_exposure_multiplier <= 1):
            raise ValueError("Exposure multipliers must be ordered within [0, 1]")
        if not (2 <= self.minimum_confirming_signals <= 3 and type(self.minimum_confirming_signals) is int):
            raise ValueError("minimum_confirming_signals must be 2 or 3; a single signal cannot de-risk the book")


@dataclass(frozen=True)
class CompletedMarketContext:
    """Inputs known only after a completed close; all values are as-of one session."""

    as_of_date: str
    market_return: float
    breadth: float
    median_pairwise_correlation: float
    market_annual_volatility: float | None = None
    equal_weight_return: float | None = None
    source_manifest_id: str = ""

    def __post_init__(self) -> None:
        values = (self.market_return, self.breadth, self.median_pairwise_correlation)
        if not all(math.isfinite(value) for value in values):
            raise ValueError("Market context needs finite market return, breadth and correlation")
        if not (0 <= self.breadth <= 1 and -1 <= self.median_pairwise_correlation <= 1):
            raise ValueError("Breadth or correlation is outside its valid range")
        if self.market_annual_volatility is not None and not (math.isfinite(self.market_annual_volatility) and self.market_annual_volatility > 0):
            raise ValueError("Market annual volatility must be positive and finite when supplied")
        if self.equal_weight_return is not None and not math.isfinite(self.equal_weight_return):
            raise ValueError("equal_weight_return must be finite when supplied")


@dataclass(frozen=True)
class MarketContextDirective:
    regime: RiskRegime
    exposure_multiplier: float
    entry_allowed: bool
    reasons: tuple[str, ...]
    timing: str = "next_open"
    note: str = "Computed after close; any allocation change executes no earlier than the next open."


def evaluate_market_context(context: CompletedMarketContext,
                            config: MarketContextConfig = MarketContextConfig()) -> MarketContextDirective:
    """Return an interpretable portfolio directive from completed market data.

    At least two confirming stress signals are required by default.  This is a
    guard against one noisy market series liquidating otherwise independent
    positions.  It is a state classifier, not a forecast.
    """
    if not config.enabled:
        return MarketContextDirective("neutral", 1.0, True, ("market_context_disabled",))
    caution = _stress_count(context, config, severe=False)
    severe = _stress_count(context, config, severe=True)
    if severe >= config.minimum_confirming_signals:
        return MarketContextDirective("risk_off", config.risk_off_exposure_multiplier, False,
                                      _reasons(context, config, severe=True))
    if caution >= config.minimum_confirming_signals:
        return MarketContextDirective("caution", config.caution_exposure_multiplier, True,
                                      _reasons(context, config, severe=False))
    if context.market_return > 0 and context.breadth >= .55 and context.median_pairwise_correlation < config.caution_correlation:
        return MarketContextDirective("risk_on", 1.0, True, ("positive_market_breadth_without_correlation_stress",))
    return MarketContextDirective("neutral", 1.0, True, ("insufficient_confirming_market_stress",))


def market_context_contract(config: MarketContextConfig = MarketContextConfig()) -> dict[str, Any]:
    """Machine-readable integration and tuning contract for UI and Astra."""
    return {
        "id": "universal_market_context_v1",
        "status": "disabled_by_default" if not config.enabled else "active_research_target_overlay",
        "configuration": asdict(config),
        "input_fields": list(CompletedMarketContext.__dataclass_fields__),
        "output_fields": list(MarketContextDirective.__dataclass_fields__),
        "invariants": [
            "Market, breadth and correlation inputs must be dated completed-session observations.",
            "A risk-off state requires the declared number of confirming stress signals; one red index day is insufficient.",
            "The overlay scales targets and blocks new entries; it does not fabricate an intraday exit price.",
            "Any directive created at close executes no earlier than the next open using the ledger's fill/cost rules.",
            "A stock may be evaluated against market, sector and several cross-cutting families; overlapping exposure is not silently summed.",
            "The overlay is research-only and cannot send broker orders.",
        ],
        "integration_status": "Enabled context observations scale V2 research targets and gate new entries at the next open. Family pressure remains a separate, data-dependent research input.",
    }


def _stress_count(context: CompletedMarketContext, config: MarketContextConfig, *, severe: bool) -> int:
    market = config.risk_off_market_return if severe else config.caution_market_return
    breadth = config.risk_off_breadth if severe else config.caution_breadth
    correlation = config.risk_off_correlation if severe else config.caution_correlation
    volatility = config.risk_off_market_annual_volatility if severe else config.caution_market_annual_volatility
    if volatility is not None and context.market_annual_volatility is None:
        raise ValueError("Configured market-volatility context requires an annualized volatility observation")
    return (int(context.market_return <= market) + int(context.breadth <= breadth) +
            int(context.median_pairwise_correlation >= correlation) +
            int(volatility is not None and context.market_annual_volatility >= volatility))


def _reasons(context: CompletedMarketContext, config: MarketContextConfig, *, severe: bool) -> tuple[str, ...]:
    market = config.risk_off_market_return if severe else config.caution_market_return
    breadth = config.risk_off_breadth if severe else config.caution_breadth
    correlation = config.risk_off_correlation if severe else config.caution_correlation
    reasons = []
    if context.market_return <= market:
        reasons.append("market_return_stress")
    if context.breadth <= breadth:
        reasons.append("breadth_deterioration")
    if context.median_pairwise_correlation >= correlation:
        reasons.append("correlation_convergence")
    volatility = config.risk_off_market_annual_volatility if severe else config.caution_market_annual_volatility
    if volatility is not None and context.market_annual_volatility >= volatility:
        reasons.append("market_volatility_stress")
    return tuple(reasons)


def apply_market_context(targets: pd.DataFrame, observations: pd.DataFrame | None,
                         config: MarketContextConfig) -> tuple[pd.DataFrame, pd.Series, list[dict]]:
    """Apply each close's multiplier once to base targets, retaining causality.

    Observation timestamps must contain offsets. US-equity decisions happen at
    16:00 America/New_York; data published later cannot be used at that close.
    Early-close sessions require already available observations; the supplied
    decision_at must be the actual session close (verified by data provenance).
    Full context is required for all investable target dates, even cash dates.
    """
    allowed = pd.Series(True, index=targets.index, dtype=bool)
    if not config.enabled:
        return targets.copy(), allowed, []
    if observations is None or observations.empty:
        raise ValueError("Enabled market context requires availability-dated observations")
    required = {"market_return", "breadth", "median_pairwise_correlation", "observed_at",
                "available_at", "decision_at", "source_manifest_id", "lookback_sessions"}
    if not required.issubset(observations.columns):
        raise ValueError("Market context missing fields: " + ", ".join(sorted(required - set(observations.columns))))
    if observations.index.has_duplicates or not observations.index.is_monotonic_increasing:
        raise ValueError("Market context dates must be unique and increasing")
    result, audit = targets.copy(), []
    for day in targets.index[252:]:
        if day not in observations.index:
            raise ValueError(f"Missing market context for {day.date()}")
        row = observations.loc[day]
        stamps = [pd.Timestamp(row[key]) for key in ("observed_at", "available_at", "decision_at")]
        if any(pd.isna(s) or s.tzinfo is None for s in stamps):
            raise ValueError("Context timestamps must be timezone-aware and finite")
        observed, available, decision = stamps
        close = (day + pd.Timedelta(hours=16)).tz_localize("America/New_York")
        if not (observed <= available <= decision <= close) or decision.tz_convert("America/New_York").date() != day.date():
            raise ValueError("Market context was not available by this session's decision close")
        if observed.tz_convert("America/New_York").date() != day.date():
            raise ValueError("Stale context observations require an explicit new research definition")
        if not str(row.source_manifest_id).strip() or pd.isna(row.source_manifest_id):
            raise ValueError("Market context needs a source manifest identifier")
        if row.lookback_sessions != config.market_lookback_sessions:
            raise ValueError("Context lookback differs from the declared configuration")
        context = CompletedMarketContext(str(day.date()), float(row.market_return), float(row.breadth),
                                         float(row.median_pairwise_correlation),
                                         market_annual_volatility=float(row.market_annual_volatility) if "market_annual_volatility" in row else None,
                                         source_manifest_id=str(row.source_manifest_id))
        directive = evaluate_market_context(context, config)
        result.loc[day] *= directive.exposure_multiplier
        allowed.loc[day] = directive.entry_allowed
        audit.append({"date": str(day.date()), "decision_at": decision.isoformat(),
                      "available_at": available.isoformat(), "source_manifest_id": str(row.source_manifest_id),
                      **asdict(directive)})
    if not np.isfinite(result.to_numpy()).all():
        raise ValueError("Invalid context-adjusted targets")
    return result, allowed, audit


def build_completed_market_observations(closes: pd.DataFrame, config: MarketContextConfig,
                                        *, source_manifest_id: str) -> pd.DataFrame:
    """Derive a completed-close study context from frozen daily OHLCV inputs.

    SPY and QQQ are averaged for the market leg. Breadth and correlation use
    the supplied non-benchmark study universe. Each observation is dated at
    the same session close and is therefore consumed at the following open by
    the V2 ledger. This is a research-data adapter, not a market forecast.
    """
    if not source_manifest_id.strip():
        raise ValueError("Market context needs a source manifest identifier")
    if not isinstance(closes.index, pd.DatetimeIndex) or closes.index.has_duplicates or not closes.index.is_monotonic_increasing:
        raise ValueError("Market-context closes need unique increasing dates")
    required = {"SPY", "QQQ"}
    if not required.issubset(closes.columns):
        raise ValueError("Completed market context requires both SPY and QQQ closes")
    universe = closes.drop(columns=["SPY", "QQQ"])
    if universe.shape[1] < 2 or not np.isfinite(closes.to_numpy()).all() or (closes <= 0).any().any():
        raise ValueError("Market context needs at least two complete, positive universe price histories")
    lookback = config.market_lookback_sessions
    returns = closes.pct_change(fill_method=None)
    market_daily_return = closes[["SPY", "QQQ"]].pct_change(fill_method=None).sum(axis=1, min_count=2) / 2
    benchmark_return = (closes[["SPY", "QQQ"]].pct_change(lookback, fill_method=None).sum(axis=1, min_count=2) / 2)
    market_annual_volatility = market_daily_return.rolling(lookback, min_periods=lookback).std(ddof=0) * np.sqrt(252)
    breadth = universe.pct_change(lookback, fill_method=None).gt(0).mean(axis=1)
    correlations = returns[universe.columns].rolling(lookback, min_periods=lookback).corr()
    rows = []
    for day in closes.index[lookback:]:
        corr = correlations.loc[day]
        pairwise = corr.to_numpy()[np.triu_indices(len(corr), k=1)]
        value = float(np.median(pairwise[np.isfinite(pairwise)])) if np.isfinite(pairwise).any() else np.nan
        if not (np.isfinite(benchmark_return.loc[day]) and np.isfinite(breadth.loc[day]) and np.isfinite(value)):
            raise ValueError(f"Market context is incomplete for {day.date()}")
        stamp = (pd.Timestamp(day).tz_localize("America/New_York") + pd.Timedelta(hours=16)).to_pydatetime()
        rows.append({"date": day, "observed_at": stamp, "available_at": stamp, "decision_at": stamp,
                     "source_manifest_id": source_manifest_id, "lookback_sessions": lookback,
                     "market_return": float(benchmark_return.loc[day]), "breadth": float(breadth.loc[day]),
                     "median_pairwise_correlation": value, "market_annual_volatility": float(market_annual_volatility.loc[day])})
    return pd.DataFrame(rows).set_index("date")
