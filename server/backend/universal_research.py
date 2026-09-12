"""Universal V2 report adapter for Quant Lab and reproducible offline research."""
from __future__ import annotations

from dataclasses import asdict
import hashlib
from pathlib import Path

import numpy as np
import pandas as pd

from .alpha_consistency import consistency_scorecard, BETA_SESSIONS
from .portfolio_execution import simulate_book
from .universal_engine import ENGINE_ID, ENGINE_VERSION, UniversalConfig, portfolio_targets, scan_snapshot, signal_panel
from .universal_position_policy import policy_contract
from .universal_market_context import market_context_contract, apply_market_context
from .universal_peer_shock import peer_shock_contract
from .universal_taxonomy import taxonomy_contract
from .universal_research_blueprint import research_blueprint


def run_universal(histories: dict[str, pd.DataFrame], config: UniversalConfig = UniversalConfig(),
                  benchmark_returns: pd.Series | None = None, risk_free: pd.Series | None = None,
                  evaluation_start: str | None = None, evaluation_end: str | None = None,
                  market_observations: pd.DataFrame | None = None,
                  fundamental_scores: pd.DataFrame | None = None,
                  fundamental_acceleration_scores: pd.DataFrame | None = None,
                  fundamental_observed: pd.DataFrame | None = None,
                  learning_panels: tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame] | None = None) -> dict:
    from .quant_research import _summary, _performance_diagnostics, _correlation_stress_report
    if not histories:
        raise ValueError("Supply at least one history")
    prices = pd.DataFrame({ticker: frame["Close"] for ticker, frame in sorted(histories.items())})
    opens = pd.DataFrame({ticker: frame["Open"] for ticker, frame in sorted(histories.items())})
    highs = pd.DataFrame({ticker: frame["High"] for ticker, frame in sorted(histories.items())})
    lows = pd.DataFrame({ticker: frame["Low"] for ticker, frame in sorted(histories.items())})
    volumes = pd.DataFrame({ticker: frame["Volume"] for ticker, frame in sorted(histories.items())})
    if len(prices) < 254:
        raise ValueError("Universal V2 requires 253 warmup bars and at least one evaluation bar")
    if evaluation_end:
        prices = prices.loc[:evaluation_end]
        opens = opens.loc[:evaluation_end]
        volumes = volumes.loc[:evaluation_end]
    if len(prices) < 254:
        raise ValueError("Evaluation end leaves fewer than 253 warmup bars and one evaluation bar")
    needs_fundamental = config.fundamental_weight or config.fundamental_quality_overlay_weight
    if needs_fundamental:
        if fundamental_scores is None or not set(prices.columns).issubset(fundamental_scores.columns):
            raise ValueError("Fundamental score panel must cover every researched symbol")
        fundamental_scores = fundamental_scores.reindex(index=prices.index, columns=prices.columns)
    if config.fundamental_acceleration_overlay_weight:
        if fundamental_acceleration_scores is None or not set(prices.columns).issubset(fundamental_acceleration_scores.columns):
            raise ValueError("Fundamental acceleration panel must cover every researched symbol")
        fundamental_acceleration_scores = fundamental_acceleration_scores.reindex(index=prices.index, columns=prices.columns)
    observed_fraction = None
    uses_corporate_overlay = bool(config.fundamental_quality_overlay_weight or config.fundamental_acceleration_overlay_weight)
    if uses_corporate_overlay:
        if fundamental_observed is None:
            raise ValueError("Corporate overlay requires an exact availability-dated fundamental_observed mask")
        if not (fundamental_observed.index.equals(prices.index) and fundamental_observed.columns.equals(prices.columns)):
            raise ValueError("fundamental_observed must exactly align with researched prices")
        values = fundamental_observed.to_numpy()
        if values.dtype != bool:
            raise ValueError("fundamental_observed must be boolean, not inferred from zero scores")
    learning = (None, None, None) if learning_panels is None else learning_panels
    panel = signal_panel(prices, config, benchmark_returns, fundamental_scores, opens, volumes, *learning, highs, lows,
                         fundamental_acceleration_scores=fundamental_acceleration_scores)
    target = portfolio_targets(prices, config, benchmark_returns, fundamental_scores, opens, volumes, *learning, highs, lows,
                               fundamental_acceleration_scores=fundamental_acceleration_scores, precomputed_panel=panel)
    target, entry_gate, context_audit = apply_market_context(target, market_observations, config.market_context)
    start = pd.Timestamp(evaluation_start) if evaluation_start else prices.index[253]
    if start <= prices.index[252]:
        raise ValueError("Evaluation must begin after the 253-bar signal warmup")
    evaluation_index = prices.index[prices.index >= start]
    if not len(evaluation_index):
        raise ValueError("No evaluation sessions in requested range")
    if uses_corporate_overlay:
        observed_fraction = float(fundamental_observed.reindex(evaluation_index).to_numpy().mean())
        if observed_fraction < config.minimum_fundamental_observed_fraction:
            raise ValueError(f"Corporate overlay observed coverage {observed_fraction:.1%} is below the declared minimum {config.minimum_fundamental_observed_fraction:.1%}")
    # Keep the prior close for first-session execution, start with cash at evaluation boundary.
    first = prices.index.get_loc(evaluation_index[0])
    # Shadow calibration uses this same frozen strategy before the scored
    # flat-start account; its returns do not contribute to evaluated NAV/P&L.
    prior_net = None
    if benchmark_returns is not None and risk_free is not None and first >= 253 + BETA_SESSIONS:
        calibration_dates = prices.index[:first]
        calibration_cash = risk_free.reindex(calibration_dates).copy()
        calibration_cash.iloc[:253] = 0.0
        shadow = simulate_book(histories, target.loc[calibration_dates], config,
                               calibration_cash, entry_gate.loc[calibration_dates],
                               panel["score"].loc[calibration_dates], panel["volatility"].loc[calibration_dates])
        prior_net = shadow["net"].iloc[253:].tail(BETA_SESSIONS)
    target.iloc[:first - 1] = 0
    cash_series = None if risk_free is None else risk_free.reindex(prices.index)
    if cash_series is not None:
        cash_series.loc[cash_series.index < evaluation_index[0]] = 0
    simulation = simulate_book(histories, target, config, cash_series, entry_gate, panel["score"], panel["volatility"])
    net = simulation["net"].reindex(evaluation_index)
    turnover = simulation["turnover"].reindex(evaluation_index)
    held = simulation["held"].reindex(evaluation_index)
    report_summary = _summary(net, turnover, held)
    source_hash = hashlib.sha256()
    for symbol in prices.columns:
        source_hash.update(symbol.encode())
        columns = ["Open", "Close", "Volume"] + (["High", "Low"] if (config.position_policy.enabled or config.ridge_include_ohlcv_structure) else [])
        source_hash.update(pd.util.hash_pandas_object(histories[symbol].reindex(prices.index)[columns], index=True).values.tobytes())
    for name, series in (("benchmark", benchmark_returns), ("risk_free", risk_free)):
        source_hash.update(name.encode())
        if series is not None:
            source_hash.update(pd.util.hash_pandas_object(series.reindex(prices.index), index=True).values.tobytes())
    if config.market_context.enabled:
        source_hash.update(pd.util.hash_pandas_object(market_observations.reindex(prices.index[252:]).sort_index(axis=1), index=True).values.tobytes())
    if needs_fundamental:
        if fundamental_scores is None:
            raise ValueError("Fundamental V2 research requires an availability-dated fundamental score panel")
        source_hash.update(pd.util.hash_pandas_object(fundamental_scores.reindex(prices.index).sort_index(axis=1), index=True).values.tobytes())
    if config.fundamental_acceleration_overlay_weight:
        if fundamental_acceleration_scores is None:
            raise ValueError("Fundamental acceleration overlay requires an availability-dated panel")
        source_hash.update(pd.util.hash_pandas_object(fundamental_acceleration_scores.reindex(prices.index).sort_index(axis=1), index=True).values.tobytes())
    if uses_corporate_overlay:
        source_hash.update(pd.util.hash_pandas_object(fundamental_observed, index=True).values.tobytes())
    if config.alpha_model == "walk_forward_ridge" and learning_panels is not None:
        for panel_source in learning_panels:
            source_hash.update(pd.util.hash_pandas_object(panel_source, index=True).values.tobytes())
    fills = simulation["fills"]
    exposure = held.iloc[-1]
    scorecard = consistency_scorecard(net, benchmark_returns, risk_free, prior_strategy=prior_net)
    code_hash = hashlib.sha256()
    for module in ("universal_engine.py", "universal_learning.py", "universal_fundamentals.py", "portfolio_execution.py", "alpha_evaluation.py", "alpha_consistency.py", "universal_position_policy.py", "universal_yearly_protocol.py", "universal_market_context.py", "universal_taxonomy.py", "universe_selection.py", "universal_research_blueprint.py", "universal_research.py", "minerva.py", "minerva_corporate.py", "quant_research.py"):
        code_hash.update(module.encode())
        code_hash.update(Path(__file__).with_name(module).read_bytes())
    return {"engine": ENGINE_ID, "engine_version": ENGINE_VERSION, "code_fingerprint": code_hash.hexdigest(), "configuration": asdict(config), "engine_configuration": asdict(config),
            "config_fingerprint": config.fingerprint, "dataset_fingerprint": source_hash.hexdigest(),
            "universe": {"symbols": list(prices.columns), "start": str(evaluation_index[0].date()), "end": str(evaluation_index[-1].date()), "sessions": len(net)},
            "results": [{"id": "strategy_ensemble", "label": "Universal V2 research engine", **report_summary}],
            "alpha": scorecard, "validation": {"status": "fixed_rule_evaluation", "note": "Calendar results are descriptive; unseen data and a frozen selection protocol are required for confirmation."},
            "execution": {"model": "cash_and_shares_next_open", "fill_count": len(fills),
                          "cost_pct_sum": float(simulation["costs"].reindex(evaluation_index).sum() * 100),
                          "unfilled_notional": simulation["unfilled_notional"],
                          "maximum_participation_pct": max((r["participation"] * 100 for r in fills), default=0),
                          "note": "Unfilled quantity expires at that rebalance; volume capacity uses prior 20-session median dollar volume. No order-book model."},
            "position_policy": policy_contract(config.position_policy),
            "position_policy_execution": {"status": "active_research_ledger" if config.position_policy.enabled else "disabled_by_default",
                                          "event_count": len(simulation["position_policy_events"]),
                                          "events": simulation["position_policy_events"],
                                          "open_states": simulation["position_policy_open_states"]},
            "market_context": {**market_context_contract(config.market_context),
                               "status": "applied_to_targets" if config.market_context.enabled else "disabled_by_default",
                               "integration_status": "Targets scale once from each completed-close context; risk-off blocks buys at the next open.",
                               "decisions": [r for r in context_audit if r["date"] >= str(prices.index[first - 1].date())]},
            "peer_shock": peer_shock_contract(config.peer_shock),
            "taxonomy": taxonomy_contract(),
            "research_blueprint": research_blueprint(),
            "trade_outcomes": {"definition": "Cash-flow P&L of flat-to-flat position episodes, including partial fills and costs; open episodes excluded", "closed": simulation["closed_episodes"], "open_count": len(simulation["open_episodes"]), "win_rate_pct": simulation["win_rate_pct"]},
            "latest_signals": {symbol: scan_snapshot(histories[symbol].reindex(prices.index), config) for symbol in prices.columns},
            "portfolio_risk": {"latest_positions": [{"symbol": str(name), "weight_pct": float(weight * 100)} for name, weight in exposure.items() if weight > 0]},
            "visual_diagnostics": {"performance": _performance_diagnostics(net), "correlation_stress": _correlation_stress_report(held, prices.pct_change(fill_method=None))},
            "corporate_data": {"status": "availability_dated_fundamental_panel" if (needs_fundamental or config.fundamental_acceleration_overlay_weight) else "not_used",
                               "signal_coverage_pct": round(observed_fraction * 100, 2) if observed_fraction is not None else 0}, "macro_data": {"status": "not_used", "signal_coverage_pct": 0},
            "methodology": {"execution_timing": "t close signal -> t+1 open transaction -> mark at close", "cash_rate": "explicit daily risk-free series" if risk_free is not None else "zero; alpha unavailable without risk-free input", "warnings": ["Long-only research candidate; no validated alpha claim.", "Adjusted OHLC must use consistent corporate-action adjustments; complete data required.", "Current-survivor baskets are exploratory and may contain selection bias."]},
            "daily_returns": [{"date": str(day.date()), "net_return": float(value)} for day, value in net.items()]}
