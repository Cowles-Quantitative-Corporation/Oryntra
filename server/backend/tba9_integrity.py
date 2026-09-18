"""Integrity-gated runner for the private TBA 9 research candidate.

TBA 9 retains TBA 8's frozen signal and risk configuration.  Its only model
upgrade is a stricter research contract: dated constituent evidence gates
membership, chronological development/holdout dates are locked before scoring,
and benchmark, ablation, and sensitivity results are recorded as diagnostics
rather than used to select a winner on the holdout.
"""
from __future__ import annotations

from dataclasses import replace
from typing import Any, Mapping, Sequence

import numpy as np
import pandas as pd

from .historical_universe import materialize_universe_schedule
from .minerva import TBA9_ID, TBA9_STATUS, tba8_institutional_risk_candidate, tba9_integrity_candidate
from .research_experiments import fingerprint, record_experiment, record_experiment_partitions, record_universe_snapshot
from .universal_institutional_decision import InstitutionalDecisionConfig
from .universal_research import run_universal


def run_tba9_integrity_research(
    histories: Mapping[str, pd.DataFrame],
    *,
    universe_snapshots: Sequence[dict[str, Any]],
    benchmark_returns: pd.Series,
    risk_free: pd.Series,
    development_end: str,
    holdout_start: str,
    named_benchmarks: Mapping[str, pd.Series] | None = None,
    max_snapshot_staleness_days: int = 31,
    include_diagnostics: bool = True,
) -> dict[str, Any]:
    """Run one frozen TBA 9 evaluation against a declared, locked holdout.

    ``universe_snapshots`` must contain availability-dated historical source
    evidence.  Missing or stale dates are ineligible, never filled with a
    later constituent list.  Diagnostics are descriptive only: this function
    never promotes or swaps the candidate based on a holdout result.
    """
    if not histories:
        raise ValueError("TBA 9 requires at least one symbol history")
    normalized = {str(symbol).upper(): frame for symbol, frame in histories.items()}
    _validate_histories(normalized)
    dates = next(iter(normalized.values())).index
    development = dates[dates <= pd.Timestamp(development_end)]
    holdout = dates[dates >= pd.Timestamp(holdout_start)]
    if not len(development) or not len(holdout) or development[-1] >= holdout[0]:
        raise ValueError("TBA 9 requires non-overlapping declared development_end and holdout_start dates")
    if len(development) < 253:
        raise ValueError("TBA 9 development period must contain at least 253 sessions before holdout")
    schedule = materialize_universe_schedule(
        universe_snapshots, dates, max_staleness_days=max_snapshot_staleness_days,
    )
    for snapshot in universe_snapshots:
        record_universe_snapshot(snapshot)
    eligibility = _eligibility_panel(schedule, dates, list(normalized))
    holdout_coverage = eligibility.reindex(holdout).to_numpy().mean()
    if holdout_coverage <= 0:
        raise ValueError("TBA 9 holdout has no eligible point-in-time universe observations")
    config = tba9_integrity_candidate()
    report = run_universal(
        normalized, config, benchmark_returns, risk_free,
        evaluation_start=str(holdout[0].date()),
        universe_eligibility=eligibility,
    )
    experiment_id = record_experiment(
        experiment_type="tba9_integrity_candidate",
        status="done",
        config={
            "code_version": TBA9_ID,
            "candidate_config_fingerprint": config.fingerprint,
            "universe_schedule_fingerprint": schedule["fingerprint"],
            "holdout_policy": "chronological_locked_before_holdout_scoring",
            "diagnostics_policy": "diagnostic_only_no_holdout_selection",
        },
        dataset_fingerprint=report["dataset_fingerprint"],
        dataset_start=str(dates[0].date()),
        dataset_end=str(dates[-1].date()),
        symbols=list(normalized),
        sample_count=int(len(dates) * len(normalized)),
        metrics={"alpha": report["alpha"], "result": report["results"][0]},
        notes="Private research candidate; no product, execution, or investment-advice use.",
    )
    record_experiment_partitions(experiment_id, {
        "development": [str(day.date()) for day in development],
        "holdout": [str(day.date()) for day in holdout],
    })
    diagnostics = _diagnostic_suite(
        normalized, benchmark_returns, risk_free, eligibility, str(holdout[0].date()),
        named_benchmarks or {}, config, include_diagnostics,
    )
    report["research_integrity"] = {
        "candidate_id": TBA9_ID,
        "status": TBA9_STATUS,
        "experiment_id": experiment_id,
        "holdout_locked_before_scoring": True,
        "development": _partition(development),
        "holdout": _partition(holdout),
        "point_in_time_universe": {
            "schedule_fingerprint": schedule["fingerprint"],
            "max_snapshot_staleness_days": max_snapshot_staleness_days,
            "holdout_eligible_observation_pct": round(float(holdout_coverage * 100), 2),
            "missing_or_stale_dates": [item["decision_date"] for item in schedule["schedule"] if item["status"] != "eligible"],
            "invariant": "An ineligible symbol is forced to zero target; later membership is never backfilled.",
        },
        "integrity_code_fingerprint": fingerprint({
            "tba9_integrity.py": open(__file__, "rb").read().hex(),
            "historical_universe.py": open(__file__.replace("tba9_integrity.py", "historical_universe.py"), "rb").read().hex(),
        }),
        **diagnostics,
        "promotion_rule": "Do not select weights, features, or variants from this holdout. Freeze a successor before a new holdout evaluation.",
    }
    return report


def _diagnostic_suite(histories, benchmark_returns, risk_free, eligibility, holdout_start, named_benchmarks, config, enabled):
    result = {
        "benchmark_suite": _benchmark_suite(named_benchmarks, holdout_start),
        "ablation": {"status": "not_run"},
        "parameter_sensitivity": {"status": "not_run"},
    }
    if not enabled:
        return result
    # Each comparison uses identical source data, timing, membership and
    # locked holdout.  They are audit evidence, never a tuning instruction.
    variants = {
        "tba8_without_integrity_contract": tba8_institutional_risk_candidate(),
        "without_score_persistence": replace(config, ridge_score_smoothing=0.0),
        "without_confidence_capacity": replace(config, institutional_decision=InstitutionalDecisionConfig()),
    }
    sensitivity = {
        "persistence_0_15": replace(config, ridge_score_smoothing=.15),
        "persistence_0_25_frozen": config,
        "persistence_0_35": replace(config, ridge_score_smoothing=.35),
    }
    result["ablation"] = {"status": "descriptive_not_selection", "runs": _run_variants(
        histories, variants, benchmark_returns, risk_free, eligibility, holdout_start,
    )}
    result["parameter_sensitivity"] = {"status": "descriptive_not_selection", "parameter": "ridge_score_smoothing", "runs": _run_variants(
        histories, sensitivity, benchmark_returns, risk_free, eligibility, holdout_start,
    )}
    return result


def _run_variants(histories, variants, benchmark_returns, risk_free, eligibility, holdout_start):
    output = {}
    for label, config in variants.items():
        candidate = run_universal(
            histories, config, benchmark_returns, risk_free,
            evaluation_start=holdout_start, universe_eligibility=eligibility,
        )
        output[label] = {
            "config_fingerprint": candidate["config_fingerprint"],
            "summary": candidate["results"][0],
            "alpha": candidate["alpha"],
        }
    return output


def _benchmark_suite(named_benchmarks: Mapping[str, pd.Series], holdout_start: str) -> dict[str, Any]:
    if not named_benchmarks:
        return {"status": "requires_named_completed_return_series", "benchmarks": {}}
    output = {}
    start = pd.Timestamp(holdout_start)
    for name, returns in sorted(named_benchmarks.items()):
        sliced = pd.Series(returns).loc[lambda value: value.index >= start].dropna()
        if not len(sliced):
            raise ValueError(f"Named benchmark {name} has no holdout observations")
        output[str(name)] = {
            "sessions": int(len(sliced)),
            "cumulative_return_pct": round(float(((1 + sliced).prod() - 1) * 100), 4),
            "annualized_return_pct": round(float(((1 + sliced).prod() ** (252 / len(sliced)) - 1) * 100), 4),
        }
    return {"status": "completed_return_series", "benchmarks": output,
            "invariant": "Benchmarks are reported separately and do not alter TBA 9 weights."}


def _eligibility_panel(schedule: Mapping[str, Any], dates: pd.DatetimeIndex, symbols: list[str]) -> pd.DataFrame:
    by_date = {pd.Timestamp(item["decision_date"]): set(item["symbols"]) for item in schedule["schedule"]}
    return pd.DataFrame(
        [[symbol in by_date.get(day, set()) for symbol in symbols] for day in dates], index=dates, columns=symbols, dtype=bool,
    )


def _validate_histories(histories: Mapping[str, pd.DataFrame]) -> None:
    first_index = None
    for symbol, frame in histories.items():
        if not {"Open", "High", "Low", "Close", "Volume"}.issubset(frame.columns):
            raise ValueError(f"TBA 9 history for {symbol} must contain OHLCV")
        if not isinstance(frame.index, pd.DatetimeIndex) or frame.index.has_duplicates or not frame.index.is_monotonic_increasing:
            raise ValueError(f"TBA 9 history for {symbol} requires unique, increasing session dates")
        if first_index is None:
            first_index = frame.index
        elif not first_index.equals(frame.index):
            raise ValueError("TBA 9 requires an explicit common aligned historical panel")


def _partition(days: pd.DatetimeIndex) -> dict[str, Any]:
    values = [str(day.date()) for day in days]
    return {"start": values[0], "end": values[-1], "sessions": len(values), "fingerprint": fingerprint(values)}
