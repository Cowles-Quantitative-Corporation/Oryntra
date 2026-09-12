from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

from .research_governance import quant_research_governance

from .quant_system import (
    factor_and_relative_value_attribution,
    liquidity_execution_costs,
    liquidity_capacity_scenarios,
    probabilistic_regimes,
    regime_conditioned_weights,
    strategy_health,
)


STRATEGIES = {
    "time_series_trend": {"label": "V1.0 time-series trend", "description": "Long positive trailing returns and short negative trailing returns across each asset.", "how_it_works": "Measures each symbol's trailing return at the close and holds the resulting signal for the following session.", "best_environment": "Persistent, diversified market trends with enough symbols to reduce single-asset noise.", "key_risk": "Whipsaw losses when prices repeatedly reverse in range-bound markets."},
    "cross_sectional_momentum": {"label": "V1.0 cross-sectional momentum", "description": "Long the strongest trailing-return group and short the weakest group within one universe.", "how_it_works": "Ranks the selected universe by trailing return, then forms a basket from relative leaders and laggards.", "best_environment": "Broad, liquid universes where relative strength persists across many symbols.", "key_risk": "Momentum crashes and high turnover when leadership changes abruptly."},
    "mean_reversion": {"label": "V1.0 mean-reversion comparator", "description": "A deliberately simple contrarian comparator after unusually large short-horizon moves.", "how_it_works": "Takes the opposite side of unusually large short-window moves after a volatility check.", "best_environment": "Liquid, mean-reverting ranges with contained transaction costs.", "key_risk": "Can fight a genuine breakout and usually has higher turnover."},
    "defensive_low_volatility": {"label": "V1.0 defensive low-volatility sleeve", "description": "Favors lower realized-volatility symbols and, in long/short research, offsets them with the highest-volatility group.", "how_it_works": "Ranks symbols by trailing realized volatility and constructs a bounded lower-versus-higher volatility basket using only prior returns.", "best_environment": "Broad universes where volatility dispersion persists and execution costs remain controlled.", "key_risk": "Can become concentrated or lag sharply when high-beta leadership dominates."},
    "corporate_quality": {"label": "Corporate quality and change sleeve", "description": "Ranks companies from point-in-time public corporate facts such as growth, margins, cash generation, guidance, ownership and capital structure changes.", "how_it_works": "Uses only facts whose recorded public availability time precedes each session, then forms a relative quality basket.", "best_environment": "Broad equity universes with complete and consistently timestamped corporate disclosures.", "key_risk": "Sparse, revised, incomparable, or late-tagged disclosures can create false confidence; zero coverage is not a valid signal."},
}

MODEL_PROFILES = {
    "universal_v2": {"label": "Universal V2 research engine", "description": "Shared scanner signals, covariance risk control and a cash-funded next-open simulation.", "allocations": {}, "long_short_default": False},
    "v8_regime_diversified": {"label": "V1.0 diversified price baseline", "description": "A transparent mix of trend, relative strength, reversal, and a defensive low-volatility comparator.", "allocations": {"time_series_trend": 35, "cross_sectional_momentum": 30, "mean_reversion": 15, "defensive_low_volatility": 20}},
    "v8_balanced": {"label": "V1.0 balanced price baseline", "description": "A neutral research profile diversifying across trend, relative momentum, and a contrarian sleeve.", "allocations": {"time_series_trend": 45, "cross_sectional_momentum": 40, "mean_reversion": 15}},
    "v8_trend_first": {"label": "V1.0 trend-first price baseline", "description": "A profile emphasizing persistent-trend evidence while retaining diversification checks.", "allocations": {"time_series_trend": 65, "cross_sectional_momentum": 25, "mean_reversion": 10}},
    "v8_relative_strength": {"label": "V1.0 relative-strength price baseline", "description": "A profile emphasizing cross-sectional momentum in a broad liquid universe.", "allocations": {"time_series_trend": 25, "cross_sectional_momentum": 65, "mean_reversion": 10}},
    "equal_weight_baseline": {"label": "V1.0 equal-weight baseline", "description": "A price-only baseline that gives selected strategies equal influence for comparison.", "allocations": {"time_series_trend": 34, "cross_sectional_momentum": 33, "mean_reversion": 33}},
    "v1_corporate_quant_system": {"label": "Oryntra V1.0 corporate quant system", "description": "A research-only portfolio stack combining price evidence with point-in-time public corporate evidence, portfolio risk controls, and liquidity-aware simulated execution.", "allocations": {"time_series_trend": 25, "cross_sectional_momentum": 20, "mean_reversion": 10, "defensive_low_volatility": 10, "corporate_quality": 35}},
    "v1_long_only_trend_momentum_research": {"label": "Oryntra V1.1 long-only trend/momentum research", "description": "A validation-gated, long-only price-research candidate using trend and relative strength while retaining the shared timing, risk, liquidity, correlation, and governance controls.", "allocations": {"time_series_trend": 60, "cross_sectional_momentum": 40}, "long_short_default": False},
}


@dataclass(frozen=True)
class QuantConfig:
    strategies: tuple[str, ...] = ("time_series_trend", "cross_sectional_momentum", "mean_reversion", "defensive_low_volatility", "corporate_quality")
    trend_lookback: int = 126
    momentum_lookback: int = 126
    reversal_lookback: int = 5
    cost_bps: float = 12.0
    borrow_bps_annual: float = 50.0
    long_short: bool | None = None
    model: str = "v1_corporate_quant_system"
    strategy_weights: dict[str, float] | None = None
    target_annual_volatility: float = 12.0
    max_gross_exposure: float = 1.0
    max_single_name_weight: float = 0.35
    rebalance_frequency: str = "weekly"
    walk_forward_folds: int = 3
    regime_conditioned_weights: bool = True
    liquidity_aware_costs: bool = True
    portfolio_value_assumption: float = 1_000_000.0
    impact_coefficient_bps: float = 18.0
    max_adv_participation_pct: float = 2.0

    def strategy_allocations_pct(self) -> dict[str, float]:
        selected = [item for item in self.strategies if item in STRATEGIES]
        profile = MODEL_PROFILES.get(self.model, MODEL_PROFILES["v8_regime_diversified"])
        source = self.strategy_weights or profile["allocations"]
        raw = {item: max(0.0, float(source.get(item, 0.0))) for item in selected}
        total = sum(raw.values())
        if total <= 0 and selected:
            raw, total = {item: 1.0 for item in selected}, float(len(selected))
        return {item: round(value / total * 100.0, 3) for item, value in raw.items()} if total else {}

    def effective_long_short(self) -> bool:
        if self.long_short is not None:
            return bool(self.long_short)
        profile = MODEL_PROFILES.get(self.model, MODEL_PROFILES["v8_regime_diversified"])
        return bool(profile.get("long_short_default", True))

    def as_dict(self) -> dict[str, Any]:
        return {"strategies": list(self.strategies), "trend_lookback": self.trend_lookback, "momentum_lookback": self.momentum_lookback, "reversal_lookback": self.reversal_lookback, "cost_bps": self.cost_bps, "borrow_bps_annual": self.borrow_bps_annual, "long_short": self.effective_long_short(), "model": self.model, "strategy_weights": self.strategy_allocations_pct(), "target_annual_volatility": self.target_annual_volatility, "max_gross_exposure": self.max_gross_exposure, "max_single_name_weight": self.max_single_name_weight, "rebalance_frequency": self.rebalance_frequency, "walk_forward_folds": self.walk_forward_folds, "regime_conditioned_weights": self.regime_conditioned_weights, "liquidity_aware_costs": self.liquidity_aware_costs, "portfolio_value_assumption": self.portfolio_value_assumption, "impact_coefficient_bps": self.impact_coefficient_bps, "max_adv_participation_pct": self.max_adv_participation_pct}


def _normalise_gross(signal: pd.DataFrame) -> pd.DataFrame:
    return signal.div(signal.abs().sum(axis=1).replace(0, np.nan), axis=0).fillna(0.0)


def _trend(prices: pd.DataFrame, config: QuantConfig) -> pd.DataFrame:
    signal = np.sign(prices.pct_change(config.trend_lookback, fill_method=None)).replace([np.inf, -np.inf], np.nan).fillna(0.0)
    return _normalise_gross(signal if config.effective_long_short() else signal.clip(lower=0.0))


def _momentum(prices: pd.DataFrame, config: QuantConfig) -> pd.DataFrame:
    trailing = prices.pct_change(config.momentum_lookback, fill_method=None)
    ranks, available = trailing.rank(axis=1, pct=True, method="first"), trailing.notna().sum(axis=1)
    signal = (ranks >= .70).astype(float) + (-(ranks <= .30).astype(float) if config.effective_long_short() else 0.0)
    signal.loc[available < 4, :] = 0.0
    return _normalise_gross(signal)


def _reversion(prices: pd.DataFrame, config: QuantConfig) -> pd.DataFrame:
    move = prices.pct_change(config.reversal_lookback, fill_method=None)
    z_score = move.div(move.rolling(63, min_periods=20).std().replace(0, np.nan))
    signal = pd.DataFrame(0.0, index=prices.index, columns=prices.columns).mask(z_score >= 1.5, -1.0).mask(z_score <= -1.5, 1.0)
    return _normalise_gross(signal if config.effective_long_short() else signal.clip(lower=0.0))


def _low_volatility(prices: pd.DataFrame, config: QuantConfig) -> pd.DataFrame:
    volatility = prices.pct_change(fill_method=None).rolling(63, min_periods=20).std()
    ranks, available = volatility.rank(axis=1, pct=True, method="first"), volatility.notna().sum(axis=1)
    signal = (ranks <= .35).astype(float) + (-(ranks >= .65).astype(float) if config.effective_long_short() else 0.0)
    signal.loc[available < 4, :] = 0.0
    return _normalise_gross(signal)


def _corporate_quality(corporate_scores: pd.DataFrame | None, prices: pd.DataFrame, config: QuantConfig) -> pd.DataFrame:
    if corporate_scores is None or corporate_scores.empty:
        return pd.DataFrame(0.0, index=prices.index, columns=prices.columns)
    scores = corporate_scores.reindex(index=prices.index, columns=prices.columns).fillna(0.0)
    ranks = scores.rank(axis=1, pct=True, method="first")
    available = scores.ne(0).sum(axis=1)
    signal = (ranks >= .70).astype(float) + (-(ranks <= .30).astype(float) if config.effective_long_short() else 0.0)
    signal.loc[available < 4, :] = 0.0
    return _normalise_gross(signal)


def _controls(target: pd.DataFrame, returns: pd.DataFrame, config: QuantConfig) -> pd.DataFrame:
    controlled = target.clip(-config.max_single_name_weight, config.max_single_name_weight).fillna(0.0)
    scale = (config.max_gross_exposure / controlled.abs().sum(axis=1).replace(0, np.nan)).clip(upper=1.0).fillna(0.0)
    controlled = controlled.mul(scale, axis=0)
    if config.rebalance_frequency in {"weekly", "monthly"}:
        if config.rebalance_frequency == "weekly":
            rebalance = pd.Series(False, index=controlled.index); rebalance.iloc[::5] = True
        else:
            months = controlled.index.to_series().dt.to_period("M")
            rebalance = months.ne(months.shift(1))
        mask = pd.DataFrame(np.repeat(rebalance.to_numpy()[:, None], len(controlled.columns), axis=1), index=controlled.index, columns=controlled.columns)
        controlled = controlled.where(mask, np.nan).ffill().fillna(0.0)
    held = controlled.shift(1).fillna(0.0)
    realized = (held * returns.fillna(0.0)).sum(axis=1).rolling(21, min_periods=10).std(ddof=0) * np.sqrt(252)
    scale = (config.target_annual_volatility / 100.0 / realized).clip(upper=1.0).shift(1).fillna(1.0).clip(0.0, 1.0)
    return controlled.mul(scale, axis=0)


def _summary(net: pd.Series, turnover: pd.Series, weights: pd.DataFrame) -> dict[str, Any]:
    clean = net.replace([np.inf, -np.inf], np.nan).dropna()
    if clean.empty: return {"status": "insufficient_data"}
    equity = (1 + clean).cumprod(); drawdown = equity.div(equity.cummax().clip(lower=1.0)).sub(1)
    curve = equity.iloc[np.unique(np.linspace(0, len(equity) - 1, min(260, len(equity)), dtype=int))]
    ann = (float(equity.iloc[-1]) ** (252 / len(clean)) - 1) if len(clean) > 1 and float(equity.iloc[-1]) > 0 else None
    vol = float(clean.std(ddof=0) * np.sqrt(252)) if len(clean) > 1 else 0.0
    sharpe = float(clean.mean() / clean.std(ddof=0) * np.sqrt(252)) if clean.std(ddof=0) > 0 else None
    var = float(clean.quantile(.05)) if len(clean) >= 20 else None
    shortfall = float(clean[clean <= var].mean()) if var is not None and not clean[clean <= var].empty else None
    runs = drawdown.lt(0).astype(int).groupby(drawdown.eq(0).cumsum()).sum()
    calmar = ann / abs(float(drawdown.min())) if ann is not None and float(drawdown.min()) < 0 else None
    return {"status": "research_only", "observations": int(len(clean)), "total_return_pct": round((float(equity.iloc[-1]) - 1) * 100, 2), "annualized_return_pct": round(ann * 100, 2) if ann is not None else None, "annualized_volatility_pct": round(vol * 100, 2), "sharpe_zero_cash_rate": round(sharpe, 2) if sharpe is not None and np.isfinite(sharpe) else None, "max_drawdown_pct": round(float(drawdown.min()) * 100, 2), "annualized_turnover": round(float(turnover.reindex(clean.index).mean()) * 252, 2), "average_gross_exposure": round(float(weights.abs().sum(axis=1).reindex(clean.index).mean()), 3), "worst_day_pct": round(float(clean.min()) * 100, 2), "historical_var_95_pct": round(var * 100, 2) if var is not None else None, "historical_expected_shortfall_95_pct": round(shortfall * 100, 2) if shortfall is not None else None, "calmar_ratio": round(calmar, 2) if calmar is not None and np.isfinite(calmar) else None, "longest_drawdown_sessions": int(runs.max()) if not runs.empty else 0, "equity_curve": [{"date": str(index.date()), "value": round(float(value), 4)} for index, value in curve.items()]}


def _simulate(target: pd.DataFrame, returns: pd.DataFrame, config: QuantConfig, prices: pd.DataFrame | None = None, volumes: pd.DataFrame | None = None) -> tuple[pd.Series, pd.Series, pd.DataFrame]:
    target = target.where(returns.notna(), 0.0).fillna(0.0); held = target.shift(1).fillna(0.0)
    turnover = target.sub(target.shift(1).fillna(0.0)).abs().sum(axis=1)
    gross = (held * returns.fillna(0.0)).sum(axis=1)
    if config.liquidity_aware_costs and prices is not None and volumes is not None:
        execution_costs, _ = liquidity_execution_costs(target, prices, volumes, base_cost_bps=config.cost_bps, portfolio_value=config.portfolio_value_assumption, impact_coefficient_bps=config.impact_coefficient_bps, max_adv_participation_pct=config.max_adv_participation_pct)
    else:
        execution_costs = turnover * (config.cost_bps / 10000)
    costs = execution_costs + held.clip(upper=0.0).abs().sum(axis=1) * (config.borrow_bps_annual / 10000 / 252)
    return gross.sub(costs), turnover, held


def _regime_breakdown(net: pd.Series, benchmark: pd.Series) -> list[dict[str, Any]]:
    cumulative = (1 + benchmark.fillna(0)).cumprod(); trend = cumulative.pct_change(63)
    vol = benchmark.rolling(21, min_periods=15).std(ddof=0); baseline = vol.rolling(126, min_periods=30).median()
    labels = pd.Series("insufficient history", index=benchmark.index, dtype="object")
    ready = trend.notna() & vol.notna() & baseline.notna()
    labels.loc[ready] = [f"{'uptrend' if t >= 0 else 'downtrend'} · {'high volatility' if v > b else 'contained volatility'}" for t, v, b in zip(trend[ready], vol[ready], baseline[ready])]
    rows = []
    for label, values in net.groupby(labels.reindex(net.index)):
        clean = values.replace([np.inf, -np.inf], np.nan).dropna()
        if not clean.empty: rows.append({"regime": str(label), "sessions": int(len(clean)), "total_return_pct": round(((1 + clean).prod() - 1) * 100, 2), "annualized_volatility_pct": round(float(clean.std(ddof=0) * np.sqrt(252)) * 100, 2) if len(clean) > 1 else 0.0})
    return sorted(rows, key=lambda item: item["regime"])


def _validation(net: pd.Series, turnover: pd.Series, held: pd.DataFrame, folds: int) -> dict[str, Any]:
    clean = net.replace([np.inf, -np.inf], np.nan).dropna()
    if len(clean) < 126: return {"status": "insufficient_history", "message": "At least 126 completed sessions are needed for the chronological holdout report."}
    holdout_sessions = max(63, len(clean) // 5); development, holdout = clean.iloc[:-holdout_sessions], clean.iloc[-holdout_sessions:]
    split_count = max(1, min(folds, len(development) // 42))
    windows = [development.iloc[indexes] for indexes in np.array_split(np.arange(len(development)), split_count)]
    return {"status": "chronological_holdout", "note": "Rules are fixed rather than fitted here; this split is a chronological robustness check, not model training.", "development": _summary(development, turnover, held), "holdout": _summary(holdout, turnover, held), "walk_forward": [{"fold": number, "start": str(window.index.min().date()), "end": str(window.index.max().date()), "sessions": int(len(window)), "total_return_pct": round(((1 + window).prod() - 1) * 100, 2)} for number, window in enumerate(windows, 1) if len(window) >= 21]}


def _risk_report(held: pd.DataFrame, returns: pd.DataFrame) -> dict[str, Any]:
    latest = held.fillna(0.).iloc[-1]; gross = float(latest.abs().sum()); normalized = latest.abs() / gross if gross else latest.abs(); hhi = float((normalized ** 2).sum()) if gross else 0.0
    correlation = returns.tail(126).corr().to_numpy(); off_diagonal = correlation[np.triu_indices_from(correlation, 1)] if correlation.size else np.array([])
    return {"latest_gross_exposure": round(gross, 3), "latest_net_exposure": round(float(latest.sum()), 3), "largest_name_weight_pct": round(float(normalized.max()) * 100, 2) if gross else 0.0, "effective_number_of_positions": round(1 / hhi, 2) if hhi else 0.0, "average_abs_correlation_126_sessions": round(float(np.nanmean(np.abs(off_diagonal))), 3) if off_diagonal.size else None, "latest_positions": [{"symbol": str(symbol), "weight_pct": round(float(weight) * 100, 2)} for symbol, weight in latest.sort_values(key=lambda value: value.abs(), ascending=False).head(10).items() if abs(float(weight)) > .0001]}


def _quality(prices: pd.DataFrame) -> dict[str, Any]:
    return {"symbols": [{"symbol": str(symbol), "bars": int(prices[symbol].notna().sum()), "first_bar": str(prices[symbol].dropna().index.min().date()) if prices[symbol].notna().any() else None, "last_bar": str(prices[symbol].dropna().index.max().date()) if prices[symbol].notna().any() else None, "missing_session_pct": round(float(prices[symbol].isna().mean()) * 100, 2)} for symbol in prices.columns], "complete_overlap_sessions": int(prices.dropna(how="any").shape[0])}


def _assumption_ledger(config: QuantConfig, corporate_coverage_pct: float, macro_coverage_pct: float) -> dict[str, Any]:
    """Return the fixed model inputs and material omissions behind one research run."""
    return {
        "timing": [
            {"label": "Signal timing", "value": "Session close t; holdings begin in session t+1"},
            {"label": "Rebalance schedule", "value": config.rebalance_frequency},
            {"label": "Volatility scaling", "value": f"May reduce exposure toward {config.target_annual_volatility:g}% annualized; never adds leverage"},
        ],
        "portfolio": [
            {"label": "Gross exposure cap", "value": f"{config.max_gross_exposure:g}×"},
            {"label": "Single-name cap", "value": f"{config.max_single_name_weight * 100:g}%"},
            {"label": "Short exposure", "value": "Enabled" if config.effective_long_short() else "Disabled"},
            {"label": "Annual short-borrow assumption", "value": f"{config.borrow_bps_annual:g} bps"},
        ],
        "execution": [
            {"label": "Base trading-cost assumption", "value": f"{config.cost_bps:g} bps per unit of turnover"},
            {"label": "Liquidity model", "value": "Base cost plus square-root ADV-participation proxy" if config.liquidity_aware_costs else "Base trading-cost assumption only"},
            {"label": "Portfolio-value assumption", "value": f"${config.portfolio_value_assumption:,.0f}" if config.liquidity_aware_costs else "Not used"},
            {"label": "Impact / participation inputs", "value": f"{config.impact_coefficient_bps:g} bps / {config.max_adv_participation_pct:g}% ADV" if config.liquidity_aware_costs else "Not used"},
        ],
        "evidence": [
            {"label": "Price history", "value": "Daily OHLCV-shaped input; coverage is reported per symbol"},
            {"label": "Corporate PIT coverage", "value": f"{corporate_coverage_pct:.1f}%"},
            {"label": "Macro PIT coverage", "value": f"{macro_coverage_pct:.1f}%"},
            {"label": "Correlation stress", "value": "Trailing covariance sensitivity with unchanged latest weights and marginal volatility"},
        ],
        "omissions": [
            "No order-book history, bid/ask spread series, venue depth, fill simulation, or best-execution measurement.",
            "No point-in-time delisted-security history is included in this package.",
            "No taxes, financing beyond the stated short-borrow assumption, or unfilled-order opportunity cost is modeled.",
            "Historical diagnostics do not establish future profitability or an allocation instruction.",
        ],
        "note": "This ledger records the assumptions for this report. It is a research-audit aid, not an execution specification.",
    }


def _correlation_heatmap(returns: pd.DataFrame) -> dict[str, Any]:
    """Return a bounded, display-ready trailing correlation matrix."""
    sample = returns.tail(126).dropna(axis=1, how="all")
    correlation = sample.corr(min_periods=20)
    values = []
    for _, row in correlation.iterrows():
        values.append([round(float(value), 3) if pd.notna(value) else None for value in row])
    return {
        "window_sessions": int(len(sample)),
        "symbols": [str(symbol) for symbol in correlation.columns],
        "values": values,
        "note": "Pairwise daily-return correlations. Correlation is descriptive, not a diversification guarantee.",
    }


def _correlation_stress_report(held: pd.DataFrame, returns: pd.DataFrame) -> dict[str, Any]:
    """Estimate hypothetical diversification failure with unchanged marginal volatility."""
    window_sessions, horizon_sessions = 126, 21
    latest = held.reindex(columns=returns.columns).fillna(0.0).iloc[-1]
    active = latest[latest.abs() > .0001].index.tolist()
    sample = returns.reindex(columns=active).tail(window_sessions).dropna(how="any")
    if len(active) < 2 or len(sample) < 20:
        return {
            "status": "insufficient_data",
            "window_sessions": int(len(sample)),
            "horizon_sessions": horizon_sessions,
            "note": "At least two held positions and 20 overlapping daily-return observations are required for the correlation stress report.",
            "scenarios": [],
        }
    weights = latest.reindex(active).to_numpy(dtype=float)
    covariance = sample.cov().to_numpy(dtype=float)
    marginal_volatility = np.sqrt(np.maximum(np.diag(covariance), 0.0))
    if not np.isfinite(covariance).all() or not np.isfinite(marginal_volatility).all() or np.any(marginal_volatility <= 0):
        return {
            "status": "insufficient_data",
            "window_sessions": int(len(sample)),
            "horizon_sessions": horizon_sessions,
            "note": "The held positions do not have a complete, non-zero trailing covariance estimate.",
            "scenarios": [],
        }
    correlation = covariance / np.outer(marginal_volatility, marginal_volatility)
    correlation = np.clip((correlation + correlation.T) / 2.0, -1.0, 1.0)
    np.fill_diagonal(correlation, 1.0)
    off_diagonal = np.triu_indices_from(correlation, 1)
    baseline_variance = max(0.0, float(weights @ covariance @ weights))
    baseline_correlation = float(np.mean(correlation[off_diagonal])) if off_diagonal[0].size else None
    scenarios = []
    for scenario_id, label, convergence in (
        ("moderate_convergence", "Moderate correlation convergence", 0.50),
        ("severe_convergence", "Severe correlation convergence", 0.85),
    ):
        stressed_correlation = (1.0 - convergence) * correlation + convergence * np.ones_like(correlation)
        np.fill_diagonal(stressed_correlation, 1.0)
        stressed_covariance = stressed_correlation * np.outer(marginal_volatility, marginal_volatility)
        stressed_variance = max(0.0, float(weights @ stressed_covariance @ weights))
        baseline_horizon_volatility = np.sqrt(baseline_variance * horizon_sessions) * 100.0
        stressed_horizon_volatility = np.sqrt(stressed_variance * horizon_sessions) * 100.0
        baseline_annualized = np.sqrt(baseline_variance * 252.0) * 100.0
        stressed_annualized = np.sqrt(stressed_variance * 252.0) * 100.0
        scenarios.append({
            "id": scenario_id,
            "label": label,
            "convergence_to_positive_one": convergence,
            "baseline_average_pair_correlation": round(baseline_correlation, 3) if baseline_correlation is not None else None,
            "stressed_average_pair_correlation": round(float(np.mean(stressed_correlation[off_diagonal])), 3) if off_diagonal[0].size else None,
            "baseline_21_session_volatility_pct": round(baseline_horizon_volatility, 2),
            "stressed_21_session_volatility_pct": round(stressed_horizon_volatility, 2),
            "baseline_annualized_volatility_pct": round(baseline_annualized, 2),
            "stressed_annualized_volatility_pct": round(stressed_annualized, 2),
            "risk_multiplier": round(stressed_annualized / baseline_annualized, 3) if baseline_annualized > 0 else None,
        })
    return {
        "status": "available",
        "window_sessions": int(len(sample)),
        "horizon_sessions": horizon_sessions,
        "positions": [str(symbol) for symbol in active],
        "method": "Hypothetical correlation convergence with unchanged latest weights and trailing individual volatility.",
        "note": "Each scenario moves every pairwise correlation partway toward +1. It estimates a diversification-breakdown risk change only; it is not a price shock, loss forecast, or allocation instruction.",
        "scenarios": scenarios,
    }


def _monthly_return_heatmap(net: pd.Series) -> dict[str, Any]:
    """Aggregate net simulated returns by calendar month without changing the model."""
    clean = net.replace([np.inf, -np.inf], np.nan).dropna()
    if clean.empty:
        return {"years": [], "months": list(range(1, 13)), "values": []}
    monthly = (1 + clean).resample("ME").prod() - 1
    years = list(dict.fromkeys(int(index.year) for index in monthly.index))[-5:]
    matrix = []
    for year in years:
        row = []
        for month in range(1, 13):
            matched = monthly[(monthly.index.year == year) & (monthly.index.month == month)]
            row.append(round(float(matched.iloc[-1]) * 100, 2) if not matched.empty else None)
        matrix.append(row)
    return {"years": years, "months": list(range(1, 13)), "values": matrix, "note": "Net simulated monthly return after the configured trading-cost model."}


def _performance_diagnostics(net: pd.Series) -> dict[str, Any]:
    """Supply downsampled equity, drawdown, and rolling-risk series for the research UI."""
    clean = net.replace([np.inf, -np.inf], np.nan).dropna()
    if clean.empty:
        return {"equity_curve": [], "drawdown_curve": [], "rolling_volatility_63_pct": []}
    equity = (1 + clean).cumprod()
    drawdown = equity.div(equity.cummax().clip(lower=1.0)).sub(1)
    rolling_volatility = clean.rolling(63, min_periods=21).std(ddof=0).mul(np.sqrt(252) * 100)
    def points(series: pd.Series) -> list[dict[str, Any]]:
        available = series.dropna()
        indexes = np.unique(np.linspace(0, len(available) - 1, min(260, len(available)), dtype=int))
        return [{"date": str(index.date()), "value": round(float(value), 4)} for index, value in available.iloc[indexes].items()]
    return {
        "equity_curve": points(equity),
        "drawdown_curve": points(drawdown.mul(100)),
        "rolling_volatility_63_pct": points(rolling_volatility),
        "note": "Equity is indexed to 1.0. Drawdown and volatility use net simulated returns after configured costs.",
    }


def evaluate_strategies(histories: dict[str, pd.DataFrame], config: QuantConfig, corporate_scores: pd.DataFrame | None = None, macro_features: pd.DataFrame | None = None) -> dict[str, Any]:
    if config.model == "universal_v2":
        from .universal_engine import UniversalConfig
        from .universal_research import run_universal
        if config.effective_long_short() or config.max_gross_exposure > 1:
            raise ValueError("Universal V2 currently supports cash-funded long-only portfolios")
        return run_universal(histories, UniversalConfig(vol_target=config.target_annual_volatility / 100,
            name_cap=config.max_single_name_weight, gross_cap=config.max_gross_exposure,
            rebalance=config.rebalance_frequency, cost_bps=config.cost_bps,
            impact_bps=config.impact_coefficient_bps if config.liquidity_aware_costs else 0,
            participation=config.max_adv_participation_pct / 100, initial_equity=config.portfolio_value_assumption))
    closes = {ticker: frame["Close"].astype(float).rename(ticker) for ticker, frame in histories.items() if frame is not None and "Close" in frame and len(frame) >= 2}
    if len(closes) < 2: raise ValueError("Quant Lab needs at least two symbols with usable daily closes.")
    prices = pd.concat(closes.values(), axis=1).sort_index().loc[lambda frame: ~frame.index.duplicated(keep="last")]
    volume_series = {
        ticker: frame["Volume"].astype(float).rename(ticker)
        for ticker, frame in histories.items()
        if frame is not None and "Volume" in frame and ticker in prices.columns
    }
    volumes = pd.concat(volume_series.values(), axis=1).reindex(index=prices.index, columns=prices.columns) if volume_series else pd.DataFrame(0.0, index=prices.index, columns=prices.columns)
    returns = prices.pct_change(fill_method=None).replace([np.inf, -np.inf], np.nan)
    benchmark = returns.mean(axis=1, skipna=True)
    builders = {"time_series_trend": _trend, "cross_sectional_momentum": _momentum, "mean_reversion": _reversion, "defensive_low_volatility": _low_volatility, "corporate_quality": lambda p, c: _corporate_quality(corporate_scores, p, c)}
    simulations, results = {}, []
    allocations = config.strategy_allocations_pct()
    for strategy in config.strategies:
        if strategy not in builders: continue
        target = _controls(builders[strategy](prices, config), returns, config); net, turnover, held = _simulate(target, returns, config, prices, volumes)
        result = {"id": strategy, "configured_allocation_pct": allocations.get(strategy, 0.), **STRATEGIES[strategy], **_summary(net, turnover, held)}
        if strategy == "cross_sectional_momentum" and len(prices.columns) < 4: result["validation_warning"] = "This strategy needs at least four symbols; no comparison was formed."
        results.append(result); simulations[strategy] = {"target": target, "net": net, "held": held}
    active = [key for key in config.strategies if key in simulations and allocations.get(key, 0) > 0 and simulations[key]["target"].abs().sum().sum() > 0]
    if not active: raise ValueError("At least one selected strategy needs a positive allocation.")
    regime_probabilities = probabilistic_regimes(benchmark, macro_features)
    if len(active) > 1:
        if config.regime_conditioned_weights:
            weight_matrix = regime_conditioned_weights({key: allocations[key] for key in active}, regime_probabilities)
            target = _controls(sum(simulations[key]["target"].mul(weight_matrix[key], axis=0) for key in active), returns, config)
        else:
            weight_matrix = pd.DataFrame({key: allocations[key] / 100 for key in active}, index=prices.index)
            target = _controls(sum(simulations[key]["target"] * (allocations[key] / 100) for key in active), returns, config)
        net, turnover, held = _simulate(target, returns, config, prices, volumes); profile = MODEL_PROFILES.get(config.model, MODEL_PROFILES["v8_regime_diversified"])
        latest_weights = {key: round(float(weight_matrix[key].iloc[-1]) * 100, 2) for key in active}
        results.insert(0, {"id": "strategy_ensemble", "label": profile["label"], "description": profile["description"], "how_it_works": "Combines selected sleeves, applies transparent regime-conditioned weights, then applies the same next-session timing and liquidity-aware cost model to combined holdings.", "best_environment": "Assess only after multiple regimes and a separate chronological holdout.", "key_risk": "A blended historical result can still be overfit and does not predict future returns.", "configured_allocation_pct": 100., "component_allocations_pct": {key: allocations[key] for key in active}, "latest_regime_conditioned_allocations_pct": latest_weights, **_summary(net, turnover, held)})
    else:
        first = simulations[active[0]]; target, net, held = first["target"], first["net"], first["held"]
    component_returns = {key: simulations[key]["net"] for key in active}
    _, execution_report = liquidity_execution_costs(target, prices, volumes, base_cost_bps=config.cost_bps, portfolio_value=config.portfolio_value_assumption, impact_coefficient_bps=config.impact_coefficient_bps, max_adv_participation_pct=config.max_adv_participation_pct)
    execution_report["capacity_sensitivity"] = liquidity_capacity_scenarios(target, prices, volumes, base_cost_bps=config.cost_bps, portfolio_value=config.portfolio_value_assumption, impact_coefficient_bps=config.impact_coefficient_bps, max_adv_participation_pct=config.max_adv_participation_pct)
    corporate_coverage = float(corporate_scores.reindex(index=prices.index, columns=prices.columns).ne(0).mean().mean() * 100) if corporate_scores is not None and not corporate_scores.empty else 0.0
    macro_coverage = float(macro_features.reindex(index=prices.index).notna().mean().mean() * 100) if macro_features is not None and not macro_features.empty else 0.0
    equal_weight = pd.DataFrame(1.0 / len(prices.columns), index=prices.index, columns=prices.columns)
    benchmark_report = {
        "label": "Equal-weight buy-and-hold reference",
        "definition": "Daily equal-weight return across the selected symbols; no rebalancing cost, borrow, or liquidity adjustment.",
        **_summary(benchmark, pd.Series(0.0, index=benchmark.index), equal_weight),
    }
    universe = {"symbols": list(prices.columns), "start": str(prices.index.min().date()), "end": str(prices.index.max().date()), "sessions": int(len(prices))}
    report = {"methodology": {"execution_timing": "signal at session close, held for the next session", "portfolio_construction": "weights are capped, rebalanced on the selected schedule, and may only scale down to the requested volatility target", "cash_rate": "0% for displayed Sharpe", "warnings": ["Research simulation only. It does not place orders or identify a best trade.", "Signals use the close of day t and returns begin on day t+1; costs are deducted from every weight change.", "Corporate facts and macro observations are only eligible after their recorded public availability timestamp; zero coverage is treated as no structured signal.", "Correlation-convergence scenarios estimate diversification-breakdown risk only; they do not forecast losses or change simulated allocations.", "This package does not include point-in-time delisted-security history, so results are not production-grade evidence."], "model_profile": MODEL_PROFILES.get(config.model, MODEL_PROFILES["v8_regime_diversified"])}, "universe": universe, "results": results, "benchmark": benchmark_report, "validation": _validation(net, turnover, held, config.walk_forward_folds), "regime_breakdown": _regime_breakdown(net, benchmark), "regime_probabilities": [{"date": str(day.date()), **{key: round(float(value), 4) for key, value in row.items()}} for day, row in regime_probabilities.iloc[::max(1, len(regime_probabilities) // 120)].iterrows()][-120:], "portfolio_risk": _risk_report(held, returns), "execution": execution_report, "factor_attribution": factor_and_relative_value_attribution(held, returns, benchmark, component_returns), "strategy_health": strategy_health(component_returns), "corporate_data": {"signal_coverage_pct": round(corporate_coverage, 2), "status": "available" if corporate_coverage > 0 else "not_yet_loaded"}, "macro_data": {"signal_coverage_pct": round(macro_coverage, 2), "status": "available" if macro_coverage > 0 else "not_yet_loaded", "features": ["policy_rate", "yield_2y", "yield_10y", "credit_spread_bps", "inflation_yoy"]}, "assumption_ledger": _assumption_ledger(config, corporate_coverage, macro_coverage), "data_quality": _quality(prices), "visual_diagnostics": {"correlation": _correlation_heatmap(returns), "correlation_stress": _correlation_stress_report(held, returns), "monthly_returns": _monthly_return_heatmap(net), "performance": _performance_diagnostics(net)}}
    report["research_governance"] = quant_research_governance(config.as_dict(), universe)
    return report
