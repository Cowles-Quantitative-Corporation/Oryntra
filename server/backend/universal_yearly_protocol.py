"""Seeded, availability-gated nonconsecutive calendar-year research trials.

This is deliberately a *study protocol*, not a replacement alpha gate.  It
prevents a year from being chosen just because it was favorable: every drawn
year must have a complete SPY/QQQ benchmark, a complete cash series, a full
calendar of OHLCV bars for a minimum universe, and a fixed amount of history
before January.  Each eligible annual trial starts in cash.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Iterable

import numpy as np
import pandas as pd

from .universal_engine import UniversalConfig
from .universal_market_context import build_completed_market_observations
from .universal_research import run_universal
from .minerva_corporate import build_quality_and_observed_panels


PROTOCOL_ID = "universal_v2_seeded_nonconsecutive_years_v1"


@dataclass(frozen=True)
class SeededYearProtocol:
    seed: int = 20260906
    selected_years: int = 10
    minimum_year_spacing: int = 2
    minimum_symbols: int = 100
    maximum_symbols_per_trial: int = 200
    warmup_sessions: int = 756
    earliest_year: int = 2000

    def __post_init__(self) -> None:
        if not (1 <= self.selected_years <= 20 and 1 <= self.minimum_year_spacing <= 10):
            raise ValueError("Invalid selected-year count or spacing")
        if not (2 <= self.minimum_symbols <= 5000 and 2 <= self.maximum_symbols_per_trial <= 5000
                and 253 <= self.warmup_sessions <= 2520):
            raise ValueError("Invalid annual-study universe or warmup requirement")
        if not (1900 <= self.earliest_year <= 2100):
            raise ValueError("Invalid earliest study year")


def _complete_calendar(index: pd.DatetimeIndex, year: int, minimum_sessions: int) -> bool:
    return (len(index) >= minimum_sessions and index[0] <= pd.Timestamp(year, 1, 7)
            and index[-1] >= pd.Timestamp(year, 12, 24))


def _complete_symbols(histories: dict[str, pd.DataFrame], required_dates: pd.DatetimeIndex) -> list[str]:
    usable = []
    fields = ("Open", "High", "Low", "Close", "Volume")
    for symbol, history in sorted(histories.items()):
        if not set(fields).issubset(history.columns):
            continue
        frame = history.reindex(required_dates)[list(fields)]
        values = frame.to_numpy(dtype=float)
        if (len(frame) == len(required_dates) and np.isfinite(values).all()
                and (frame[["Open", "High", "Low", "Close"]] > 0).all().all() and (frame["Volume"] >= 0).all()):
            usable.append(symbol)
    return usable


def eligible_years(histories: dict[str, pd.DataFrame], benchmark: pd.Series, risk_free: pd.Series,
                   protocol: SeededYearProtocol = SeededYearProtocol()) -> list[dict]:
    """Return every objectively usable year, including unfavorable years.

    The benchmark calendar is the master calendar.  This is important: an
    absent QQQ or cash observation makes a year unavailable rather than being
    silently filled, while each security must survive the full warmup + year.
    """
    if not isinstance(benchmark.index, pd.DatetimeIndex) or not benchmark.index.is_monotonic_increasing:
        raise ValueError("Annual-study benchmark needs increasing dated sessions")
    if not isinstance(risk_free.index, pd.DatetimeIndex):
        raise ValueError("Annual-study cash series needs dated sessions")
    rows = []
    for year in range(max(protocol.earliest_year, int(benchmark.index.year.min())), int(benchmark.index.year.max()) + 1):
        annual = benchmark.loc[f"{year}-01-01":f"{year}-12-31"]
        complete_benchmark = _complete_calendar(annual.index, year, 240) and np.isfinite(annual.to_numpy()).all()
        complete_cash = complete_benchmark and risk_free.reindex(annual.index).notna().all() and np.isfinite(risk_free.reindex(annual.index).to_numpy()).all()
        before = benchmark.loc[benchmark.index < pd.Timestamp(year, 1, 1)].tail(protocol.warmup_sessions).index
        required = before.append(annual.index)
        symbols = _complete_symbols(histories, required) if len(before) == protocol.warmup_sessions and complete_cash else []
        rows.append({"year": year, "eligible": bool(len(symbols) >= protocol.minimum_symbols),
                     "eligible_symbols": len(symbols), "symbols": symbols,
                     "warmup_sessions": len(before), "complete_benchmark": bool(complete_benchmark),
                     "complete_cash": bool(complete_cash)})
    return rows


def select_seeded_years(rows: Iterable[dict], protocol: SeededYearProtocol = SeededYearProtocol()) -> list[dict]:
    """Select a fixed, reproducible, nonconsecutive subset without looking at returns."""
    candidates = [dict(row) for row in rows if row["eligible"]]
    order = np.random.default_rng(protocol.seed).permutation(len(candidates))
    selected: list[dict] = []
    for position in order:
        candidate = candidates[int(position)]
        if all(abs(candidate["year"] - prior["year"]) >= protocol.minimum_year_spacing for prior in selected):
            selected.append(candidate)
        if len(selected) == protocol.selected_years:
            break
    if len(selected) != protocol.selected_years:
        raise ValueError("Insufficient eligible years for the requested fixed spacing; do not silently relax the protocol")
    return sorted(selected, key=lambda row: row["year"])


def _trial_symbols(symbols: list[str], year: int, protocol: SeededYearProtocol) -> list[str]:
    """Bound runtime with a seed-derived subset, never a performance-derived one."""
    if len(symbols) <= protocol.maximum_symbols_per_trial:
        return list(symbols)
    chosen = np.random.default_rng(protocol.seed + year).choice(symbols, size=protocol.maximum_symbols_per_trial, replace=False)
    return sorted(str(symbol) for symbol in chosen)


def run_seeded_yearly_trials(histories: dict[str, pd.DataFrame], config: UniversalConfig,
                             benchmark: pd.Series, risk_free: pd.Series, *,
                             protocol: SeededYearProtocol = SeededYearProtocol(),
                             market_closes: pd.DataFrame | None = None,
                             corporate_facts: list[dict] | None = None,
                             declared_years: Iterable[int] | None = None) -> dict:
    """Run fixed-config cash-reset trials. Results are descriptive and research-only."""
    minimum_warmup = max(253, config.ridge_training_sessions if config.alpha_model == "walk_forward_ridge" else 253)
    if protocol.warmup_sessions < minimum_warmup:
        raise ValueError("Annual-study warmup must cover the selected model's completed-history requirement")
    availability = eligible_years(histories, benchmark, risk_free, protocol)
    if declared_years is None:
        selected = select_seeded_years(availability, protocol)
        selection_method = "seeded_nonconsecutive_draw"
    else:
        declared = [int(year) for year in declared_years]
        if len(declared) != len(set(declared)):
            raise ValueError("Declared annual-study years must be unique")
        rows_by_year = {row["year"]: row for row in availability}
        missing = [year for year in declared if year not in rows_by_year or not rows_by_year[year]["eligible"]]
        if missing:
            raise ValueError("Declared annual-study years are unavailable: " + ", ".join(map(str, missing)))
        selected = [rows_by_year[year] for year in sorted(declared)]
        selection_method = "declared_chunk_of_a_previously_recorded_protocol"
    trials = []
    for row in selected:
        year, symbols = row["year"], _trial_symbols(row["symbols"], row["year"], protocol)
        study_start, study_end = f"{year}-01-01", f"{year}-12-31"
        annual_dates = benchmark.loc[study_start:study_end].index
        warmup_dates = benchmark.loc[benchmark.index < pd.Timestamp(study_start)].tail(protocol.warmup_sessions).index
        study_dates = warmup_dates.append(annual_dates)
        # Trim to the availability-checked window.  A current constituent that
        # listed later cannot create an earlier study row through implicit NaNs.
        selected_histories = {symbol: histories[symbol].reindex(study_dates) for symbol in symbols}
        market_observations = None
        if config.market_context.enabled:
            if market_closes is None or not {"SPY", "QQQ"}.issubset(market_closes.columns):
                raise ValueError("Enabled market context requires complete dated SPY and QQQ closes")
            context_closes = pd.DataFrame({symbol: frame["Close"] for symbol, frame in selected_histories.items()})
            context_closes = pd.concat([context_closes, market_closes.reindex(study_dates)[["SPY", "QQQ"]]], axis=1)
            market_observations = build_completed_market_observations(
                context_closes, config.market_context, source_manifest_id=f"{PROTOCOL_ID}-{year}",
            )
        fundamental_scores, fundamental_observed = None, None
        if config.fundamental_quality_overlay_weight:
            if corporate_facts is None:
                raise ValueError("Fundamental quality overlay requires a declared corporate fact set")
            fundamental_scores, fundamental_observed = build_quality_and_observed_panels(corporate_facts, study_dates, symbols)
        report = run_universal(selected_histories, config, benchmark, risk_free, study_start, study_end,
                               market_observations=market_observations, learning_panels=None,
                               fundamental_scores=fundamental_scores, fundamental_observed=fundamental_observed)
        annual = [entry for entry in report["alpha"].get("annual", []) if entry.get("complete") and entry.get("status") == "available"]
        if len(annual) != 1:
            raise ValueError(f"Year {year} did not produce one complete annual alpha observation")
        trials.append({"year": year, "symbols": symbols, "symbol_selection_seed": protocol.seed + year,
                       "symbol_count": len(symbols), "eligible_symbol_count": row["eligible_symbols"], "alpha": annual[0],
                       "performance": report["results"][0], "execution": report["execution"],
                       "position_policy_events": report["position_policy_execution"]["event_count"],
                       "dataset_fingerprint": report["dataset_fingerprint"]})
    alphas = [trial["alpha"]["alpha_pct"] for trial in trials]
    return {"protocol": {"id": PROTOCOL_ID, **asdict(protocol)}, "selection_method": selection_method, "availability": availability,
            "selected_years": [row["year"] for row in selected], "trials": trials,
            "summary": {"mean_annual_alpha_pct": float(np.mean(alphas)), "median_annual_alpha_pct": float(np.median(alphas)),
                        "negative_years": int(sum(alpha < 0 for alpha in alphas)), "annual_alpha_pct": alphas},
            "qualification": "Seeded availability-gated cash-reset annual trials. Not a continuous portfolio, point-in-time constituent universe, or a basis for a performance claim."}
