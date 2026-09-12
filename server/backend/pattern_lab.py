from __future__ import annotations

import asyncio
import hashlib
import math
import os
import random
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Iterable

import numpy as np
import pandas as pd

from .database import get_ohlcv_cache_size_bytes
from .indicators import calculate_all_indicators
from .market_repository import get_market_repository, normalize_period
from .pattern_analyzer import analyze_patterns_multi
from .research_experiments import (
    FEATURE_VERSION,
    SCHEMA_VERSION,
    compare_engine_inputs,
    fingerprint,
    grouped_metrics,
    record_experiment,
    return_metrics,
    rows_fingerprint,
)
from .research_universe import clean_tickers, select_unseen_tickers
from .setup_detector import detect_setup

V7_PRESET_TICKERS = {
    "AAPL", "MSFT", "NVDA", "TSLA", "AMZN", "META", "GOOGL", "AMD", "AVGO",
    "JPM", "V", "XOM", "CVX", "UNH", "LLY", "JNJ", "WMT", "COST", "HD",
    "MCD", "NKE", "CAT", "BA", "RTX", "NEE", "PLTR", "CRWD", "SPY", "QQQ", "SMH",
}
ENGINE_ORDER = ["official", "v8", "vai2", "universal_v2"]


def _get(request: Any, name: str, default: Any = None) -> Any:
    if isinstance(request, dict):
        return request.get(name, default)
    return getattr(request, name, default)


def _finite(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
        return number if math.isfinite(number) else default
    except Exception:
        return default


def _bool(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _date_text(value: Any) -> str:
    return pd.Timestamp(value).date().isoformat()


def cached_tickers() -> list[str]:
    return get_market_repository().cached_tickers(minimum_bars=1)


def resolve_tickers(request: Any) -> dict[str, Any]:
    mode = str(_get(request, "universe_mode", "manual") or "manual").strip().lower()
    seed = int(_get(request, "random_seed", 73021) or 73021)
    maximum = max(1, int(os.getenv("ORYNTRA_RESEARCH_MAX_UNIVERSE", "15000")))
    requested_size = max(1, min(int(_get(request, "universe_size", 150) or 150), maximum))
    supplied, rejected = clean_tickers(_get(request, "tickers", []) or [], max_count=maximum)
    needs_cached_universe = mode in {
        "all", "all_cached", "full_cache", "market_cache",
        "unseen", "unseen150", "random_unseen", "random_cached", "mixed",
    }
    try:
        cached = cached_tickers() if needs_cached_universe else []
    except Exception:
        cached = []
    cached_set = set(cached)

    if mode in {"all", "all_cached", "full_cache", "market_cache"}:
        tickers = cached[:requested_size] if requested_size < len(cached) else cached
        origin = "canonical_cache_universe"
    elif mode in {"unseen", "unseen150", "random_unseen"}:
        tickers = supplied[:requested_size] or select_unseen_tickers(
            cached,
            count=requested_size,
            seed=seed,
        )
        origin = "supplied_universe_lock" if supplied else "generated_unseen_reference_pool"
    elif mode == "random_cached":
        rng = random.Random(seed)
        tickers = list(cached)
        rng.shuffle(tickers)
        tickers = tickers[:requested_size]
        origin = "random_from_canonical_cache"
    elif mode == "mixed":
        rng = random.Random(seed)
        available = [ticker for ticker in cached if ticker not in set(supplied)]
        rng.shuffle(available)
        tickers = (supplied + available)[:requested_size]
        origin = "manual_plus_random_cached"
    else:
        tickers = supplied[:requested_size]
        origin = "manual"

    if not tickers:
        tickers = sorted(V7_PRESET_TICKERS)[: min(requested_size, 30)]
        origin = "preset_fallback"

    return {
        "tickers": tickers,
        "rejected_tickers": rejected,
        "universe_mode": mode,
        "selection_origin": origin,
        "random_seed": seed,
        "cached_before_run": len(cached),
        "unused_at_selection": [ticker for ticker in tickers if ticker not in cached_set],
        "requested_size": requested_size,
        "resolved_size": len(tickers),
    }


def _clean_modes(values: Iterable[str] | None) -> list[str]:
    aliases = {
        "v7": "official",
        "vai2.1": "vai2",
        "vai2.2": "vai2",
    }
    selected: list[str] = []
    for raw in values or []:
        value = str(raw or "").strip().lower()
        value = aliases.get(value, value)
        if value in ENGINE_ORDER and value not in selected:
            selected.append(value)
    ordered = [engine for engine in ENGINE_ORDER if engine in selected]
    return ordered or ["official", "v8"]


async def _load_history(ticker: str, period: str, data_source: str):
    repository = get_market_repository()
    result = await asyncio.to_thread(
        repository.get_history,
        ticker,
        period=period,
        minimum_bars=20,
        allow_api=data_source != "cache_only",
        force_refresh=data_source == "api_first",
        allow_stale_on_error=data_source != "api_first",
    )
    return result.history, result.metadata.source, result.metadata.fallback_used

def _candidate_indexes(
    history: pd.DataFrame,
    *,
    ticker: str,
    min_history: int,
    horizon_days: int | None = None,
    horizon: int | None = None,
    step: int,
    max_tests: int,
    sampling_mode: str,
    seed: int,
    random_window_bars: int,
    start_date: str,
    end_date: str,
) -> tuple[list[int], dict[str, Any]]:
    horizon_days = int(horizon_days if horizon_days is not None else (horizon if horizon is not None else 10))


    last_signal_index = len(history) - horizon_days - 1
    candidates = list(range(max(2, min_history - 1), last_signal_index + 1, max(1, step)))
    if start_date:
        candidates = [index for index in candidates if _date_text(history.index[index]) >= start_date]
    if end_date:
        candidates = [index for index in candidates if _date_text(history.index[index]) <= end_date]

    ticker_seed = seed + int(hashlib.sha256(ticker.encode("utf-8")).hexdigest()[:8], 16)
    rng = random.Random(ticker_seed)
    sampling_mode = str(sampling_mode or "even").lower()
    window: dict[str, Any] = {"sampling_mode": sampling_mode, "start": None, "end": None}

    if sampling_mode == "random_windows" and candidates:
        width = max(40, min(int(random_window_bars), len(history)))
        earliest = candidates[0]
        latest = max(earliest, candidates[-1] - width + 1)
        window_start = rng.randint(earliest, latest)
        window_end = min(candidates[-1], window_start + width - 1)
        selected = [index for index in candidates if window_start <= index <= window_end]
        rng.shuffle(selected)
        candidates = sorted(selected[:max_tests])
    elif sampling_mode == "random_dates":
        rng.shuffle(candidates)
        candidates = sorted(candidates[:max_tests])
    elif sampling_mode == "recent":
        candidates = candidates[-max_tests:]
    elif len(candidates) > max_tests:
        positions = np.linspace(0, len(candidates) - 1, max_tests, dtype=int)
        candidates = [candidates[int(position)] for position in positions]

    allow_overlap = bool(False)


    if not allow_overlap and candidates:
        non_overlapping: list[int] = []
        next_allowed = -1
        for candidate in sorted(candidates):
            if candidate >= next_allowed:
                non_overlapping.append(candidate)
                next_allowed = candidate + horizon_days + 1
        candidates = non_overlapping
        window["overlap_policy"] = "one_position_per_ticker"
    if candidates:
        window["start"] = _date_text(history.index[candidates[0]])
        window["end"] = _date_text(history.index[candidates[-1]])
    return candidates, window


def _regime(indicators: dict[str, Any]) -> str:
    above50 = _bool(indicators.get("above_ma50"))
    above200 = _bool(indicators.get("above_ma200"))
    momentum = _finite(indicators.get("momentum_20d"))
    adx = _finite(indicators.get("adx14"))
    volume = _finite(indicators.get("vol_ratio"), 1.0)
    if above50 and above200 and momentum > 0:
        base = "BULL_TREND"
    elif not above50 and not above200 and momentum < 0:
        base = "BEAR_TREND"
    elif adx < 16:
        base = "CHOP"
    elif momentum > 1:
        base = "MOMENTUM_UP"
    elif momentum < -1:
        base = "MOMENTUM_DOWN"
    else:
        base = "MIXED"
    suffix = "_HIGH_VOLUME" if volume >= 1.5 else ("_LOW_VOLUME" if volume < 0.7 else "")
    return base + suffix


def _top_pattern(setup: dict[str, Any]) -> tuple[str, float]:
    patterns = setup.get("patterns") or {}
    advanced = patterns.get("advanced_patterns") or {}
    top = advanced.get("top_pattern") or patterns.get("recent_pattern") or {}
    if not isinstance(top, dict):
        return "NONE", 0.0
    name = top.get("pattern_name") or top.get("pattern") or "NONE"
    confidence = _finite(top.get("confidence"))
    return str(name or "NONE").upper(), confidence


def _simulate_target_stop(
    *,
    direction: str,
    entry_price: float,
    future: pd.DataFrame,
    target_pct: float,
    stop_pct: float,
    ambiguity_policy: str,
) -> dict[str, Any]:
    direction = direction.upper()
    if direction not in {"LONG", "SHORT"} or future.empty:
        return {
            "target_hit": False,
            "stop_hit": False,
            "target_stop_outcome": "not_actionable",
            "target_stop_exit_pct": 0.0,
            "ambiguous_bar": False,
        }
    target_price = entry_price * (1 + target_pct / 100.0) if direction == "LONG" else entry_price * (1 - target_pct / 100.0)
    stop_price = entry_price * (1 - stop_pct / 100.0) if direction == "LONG" else entry_price * (1 + stop_pct / 100.0)
    for _, bar in future.iterrows():
        high = _finite(bar.get("High"))
        low = _finite(bar.get("Low"))
        if direction == "LONG":
            target = high >= target_price
            stop = low <= stop_price
        else:
            target = low <= target_price
            stop = high >= stop_price
        if target and stop:
            conservative_stop = ambiguity_policy != "target_first"
            return {
                "target_hit": not conservative_stop,
                "stop_hit": conservative_stop,
                "target_stop_outcome": "ambiguous_stop_first" if conservative_stop else "ambiguous_target_first",
                "target_stop_exit_pct": -stop_pct if conservative_stop else target_pct,
                "ambiguous_bar": True,
            }
        if stop:
            return {
                "target_hit": False,
                "stop_hit": True,
                "target_stop_outcome": "stop",
                "target_stop_exit_pct": -stop_pct,
                "ambiguous_bar": False,
            }
        if target:
            return {
                "target_hit": True,
                "stop_hit": False,
                "target_stop_outcome": "target",
                "target_stop_exit_pct": target_pct,
                "ambiguous_bar": False,
            }
    return {
        "target_hit": False,
        "stop_hit": False,
        "target_stop_outcome": "neither",
        "target_stop_exit_pct": 0.0,
        "ambiguous_bar": False,
    }


def _base_observation(
    ticker: str,
    history: pd.DataFrame,
    index: int,
    *,
    horizon_days: int,
    source: str,
    window: dict[str, Any],
    lookback_bars: int = 280,
) -> tuple[dict[str, Any], dict[str, Any], pd.DataFrame] | None:


    start = max(0, index + 1 - max(280, int(lookback_bars)))
    signal_history = history.iloc[start : index + 1]
    future = history.iloc[index + 1 : index + 1 + horizon_days]
    if future.empty or len(future) < horizon_days:
        return None
    entry_price = _finite(future["Open"].iloc[0])
    future_close = _finite(future["Close"].iloc[-1])
    future_high = _finite(future["High"].max())
    future_low = _finite(future["Low"].min())
    if min(entry_price, future_close, future_high, future_low) <= 0:
        return None
    indicators = calculate_all_indicators(signal_history)
    indicators["ticker"] = ticker
    raw_long_return = (future_close - entry_price) / entry_price * 100.0
    raw_long_mfe = (future_high - entry_price) / entry_price * 100.0
    raw_long_mae = (future_low - entry_price) / entry_price * 100.0
    observation = {
        "ticker": ticker,
        "date": _date_text(history.index[index]),
        "signal_date": _date_text(history.index[index]),
        "entry_date": _date_text(future.index[0]),
        "exit_date": _date_text(future.index[-1]),
        "entry_price": entry_price,
        "future_close": future_close,
        "future_high": future_high,
        "future_low": future_low,
        "raw_long_return_pct": raw_long_return,
        "raw_long_mfe_pct": raw_long_mfe,
        "raw_long_mae_pct": raw_long_mae,
        "horizon_days": horizon_days,
        "source": source,
        "time_window": dict(window),
        "feature_version": FEATURE_VERSION,
        "schema_version": SCHEMA_VERSION,
    }
    return observation, indicators, signal_history


def _evaluate_engine(
    mode: str,
    observation: dict[str, Any],
    indicators: dict[str, Any],
    signal_history: pd.DataFrame,
    future: pd.DataFrame,
    *,
    cost_pct: float,
    minimum_confidence: float,
    target_pct: float,
    stop_pct: float,
    ambiguity_policy: str,
    patterns_override: dict[str, Any] | None = None,
) -> dict[str, Any]:
    try:
        setup = detect_setup(
            indicators, signal_history, pattern_mode=mode,
            patterns_override=patterns_override,
        )
        direction = str(setup.get("direction") or "NEUTRAL").upper()
        setup_type = str(setup.get("setup_type") or "NO_TRADE").upper()
        confidence = _finite(setup.get("confidence"))
        actionable = (
            direction in {"LONG", "SHORT"}
            and setup_type != "NO_TRADE"
            and confidence >= minimum_confidence
        )
        raw_long_return = _finite(observation["raw_long_return_pct"])
        raw_long_mfe = _finite(observation["raw_long_mfe_pct"])
        raw_long_mae = _finite(observation["raw_long_mae_pct"])
        if direction == "LONG":
            gross_return = raw_long_return
            mfe = raw_long_mfe
            mae = raw_long_mae
        elif direction == "SHORT":
            gross_return = -raw_long_return
            mfe = -raw_long_mae
            mae = -raw_long_mfe
        else:
            gross_return = mfe = mae = 0.0
        pattern_name, pattern_confidence = _top_pattern(setup)
        atr_pct = max(0.0, _finite(indicators.get("atr_pct")))
        effective_stop_pct = max(0.35, min(12.0, atr_pct * 1.25)) if atr_pct > 0 else stop_pct
        effective_target_pct = max(0.50, min(24.0, effective_stop_pct * 1.75))
        flags = _simulate_target_stop(
            direction=direction,
            entry_price=_finite(observation["entry_price"]),
            future=future,
            target_pct=effective_target_pct,
            stop_pct=effective_stop_pct,
            ambiguity_policy=ambiguity_policy,
        ) if actionable else _simulate_target_stop(
            direction="NEUTRAL",
            entry_price=_finite(observation["entry_price"]),
            future=future,
            target_pct=effective_target_pct,
            stop_pct=effective_stop_pct,
            ambiguity_policy=ambiguity_policy,
        )
        if actionable and flags.get("target_stop_outcome") not in {"neither", "not_actionable"}:
            gross_return = _finite(flags.get("target_stop_exit_pct"))
        net_return = gross_return - cost_pct if actionable else 0.0
        feature_fields = {
            key: indicators.get(key)
            for key in (
                "rsi14", "rsi7", "adx14", "di_plus", "di_minus", "vol_ratio",
                "atr_pct", "bb_width", "momentum_5d", "momentum_20d",
                "momentum_60d", "above_ma20", "above_ma50", "above_ma200",
                "trend_strength", "pct_from_52w_high", "day_change",
            )
        }
        return {
            **observation,
            **feature_fields,
            "mode": mode,
            "setup_type": setup_type,
            "direction": direction,
            "candidate_direction": direction,
            "confidence": confidence,
            "actionable": actionable,
            "return_pct": net_return,
            "gross_return_pct": gross_return,
            "mfe_pct": mfe,
            "mae_pct": mae,
            "winner": bool(actionable and net_return > 0),
            "exit_model": "atr_stop_target_then_horizon_close",
            "effective_stop_pct": round(effective_stop_pct, 4),
            "effective_target_pct": round(effective_target_pct, 4),
            "regime": _regime(indicators),
            "top_pattern": pattern_name,
            "top_pattern_confidence": pattern_confidence,
            "rules_fired": list(setup.get("rules_fired") or []),
            **flags,
        }
    except Exception as exc:
        return {
            **observation,
            "mode": mode,
            "actionable": False,
            "direction": "NEUTRAL",
            "setup_type": "ERROR",
            "error": str(exc),
        }


def _summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    metrics = return_metrics(rows)
    actionable = [row for row in rows if not row.get("error") and row.get("actionable")]
    avg_confidence = np.mean([_finite(row.get("confidence")) for row in actionable]) if actionable else 0.0
    avg_mfe = _finite(metrics.get("avg_mfe_pct"))
    avg_mae = abs(_finite(metrics.get("avg_mae_pct")))
    metrics.update(
        {
            "actionable": metrics.pop("signals"),
            "target_hit_rate_pct": round(
                sum(bool(row.get("target_hit")) for row in actionable) / len(actionable) * 100.0,
                2,
            ) if actionable else 0.0,
            "stop_hit_rate_pct": round(
                sum(bool(row.get("stop_hit")) for row in actionable) / len(actionable) * 100.0,
                2,
            ) if actionable else 0.0,
            "avg_confidence": round(float(avg_confidence), 2),
            "reward_risk_ratio": round(avg_mfe / avg_mae, 4) if avg_mae > 1e-12 else 0.0,
        }
    )
    return metrics


def _baseline_row(observation: dict[str, Any], direction: str, cost_pct: float) -> dict[str, Any]:
    direction = direction.upper()
    raw_return = _finite(observation["raw_long_return_pct"])
    raw_mfe = _finite(observation["raw_long_mfe_pct"])
    raw_mae = _finite(observation["raw_long_mae_pct"])
    if direction == "SHORT":
        gross, mfe, mae = -raw_return, -raw_mae, -raw_mfe
    else:
        gross, mfe, mae = raw_return, raw_mfe, raw_mae
    return {
        **observation,
        "direction": direction,
        "actionable": True,
        "return_pct": gross - cost_pct,
        "mfe_pct": mfe,
        "mae_pct": mae,
        "winner": gross - cost_pct > 0,
    }


def _confidence_buckets(mode_rows: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    boundaries = [(0, 49, "0-49"), (50, 64, "50-64"), (65, 79, "65-79"), (80, 89, "80-89"), (90, 1000, "90+")]
    for mode, rows in mode_rows.items():
        for low, high, label in boundaries:
            bucket = [row for row in rows if low <= _finite(row.get("confidence")) <= high]
            if bucket:
                output.append({"mode": mode, "bucket": label, **_summary(bucket)})
    return output


def _threshold_report(mode_rows: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for mode, rows in mode_rows.items():
        for threshold in (20, 40, 50, 60, 70, 80, 90):
            filtered = []
            for row in rows:
                clone = dict(row)
                if _finite(clone.get("confidence")) < threshold:
                    clone["actionable"] = False
                    clone["return_pct"] = 0.0
                    clone["mfe_pct"] = 0.0
                    clone["mae_pct"] = 0.0
                filtered.append(clone)
            output.append({"mode": mode, "threshold": threshold, **_summary(filtered)})
    return output


def _bias_audit(mode_rows: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for mode, rows in mode_rows.items():
        actionable = [row for row in rows if row.get("actionable") and not row.get("error")]
        long_share = (
            sum(str(row.get("direction")) == "LONG" for row in actionable) / len(actionable) * 100.0
            if actionable else 0.0
        )
        preset = [row for row in rows if str(row.get("ticker")) in V7_PRESET_TICKERS]
        outside = [row for row in rows if str(row.get("ticker")) not in V7_PRESET_TICKERS]
        preset_metrics = _summary(preset)
        outside_metrics = _summary(outside)
        by_year: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in rows:
            by_year[str(row.get("date") or "")[:4]].append(row)
        year_returns = [_summary(bucket)["avg_return_pct"] for bucket in by_year.values() if bucket]
        time_range = max(year_returns) - min(year_returns) if year_returns else 0.0
        return_gap = preset_metrics["avg_return_pct"] - outside_metrics["avg_return_pct"]
        coverage_gap = preset_metrics["coverage_pct"] - outside_metrics["coverage_pct"]
        output.append(
            {
                "mode": mode,
                "long_signal_share_pct": round(long_share, 2),
                "preset_return_gap_pct": round(return_gap, 4),
                "preset_coverage_gap_pp": round(coverage_gap, 4),
                "time_return_range_pct": round(time_range, 4),
                "passes_direction_balance": 15 <= long_share <= 85 if actionable else False,
                "passes_preset_gap": abs(return_gap) <= 2.0,
                "passes_time_stability": time_range <= 5.0,
            }
        )
    return output


def _apply_confidence_threshold(rows: list[dict[str, Any]], threshold: float) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for row in rows:
        clone = dict(row)
        if _finite(clone.get("confidence")) < threshold:
            clone.update({"actionable": False, "return_pct": 0.0, "mfe_pct": 0.0, "mae_pct": 0.0, "winner": False})
        output.append(clone)
    return output


def _cluster_bootstrap_ci(rows: list[dict[str, Any]], *, samples: int, seed: int) -> dict[str, Any]:
    actionable = [row for row in rows if row.get("actionable") and not row.get("error")]
    clusters: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in actionable:
        clusters[str(row.get("ticker") or "UNKNOWN")].append(row)
    names = sorted(clusters)
    if len(actionable) < 8 or not names or samples <= 0:
        return {"method": "ticker_cluster_bootstrap", "samples": 0, "expectancy_95_ci_pct": None, "win_rate_95_ci_pct": None}
    rng = random.Random(seed)
    expectancies: list[float] = []
    win_rates: list[float] = []
    for _ in range(samples):
        draw: list[dict[str, Any]] = []
        for _cluster in names:
            selected = rng.choice(names)
            draw.extend(clusters[selected])
        metrics = _summary(draw)
        expectancies.append(_finite(metrics.get("expectancy_pct")))
        win_rates.append(_finite(metrics.get("win_rate_pct")))
    return {
        "method": "ticker_cluster_bootstrap",
        "samples": samples,
        "expectancy_95_ci_pct": [round(float(np.percentile(expectancies, 2.5)), 4), round(float(np.percentile(expectancies, 97.5)), 4)],
        "win_rate_95_ci_pct": [round(float(np.percentile(win_rates, 2.5)), 2), round(float(np.percentile(win_rates, 97.5)), 2)],
    }


def _walk_forward_report(rows: list[dict[str, Any]], *, folds: int, purge_days: int) -> dict[str, Any]:
    valid = [row for row in rows if not row.get("error")]
    dates = sorted({str(row.get("date") or "") for row in valid if row.get("date")})
    if len(dates) < max(20, folds * 4):
        return {"method": "expanding_purged_walk_forward", "folds": [], "status": "insufficient_dates"}
    folds = max(2, min(folds, 10))
    boundaries = np.linspace(0, len(dates), folds + 2, dtype=int)
    reports: list[dict[str, Any]] = []
    pooled_test_rows: list[dict[str, Any]] = []
    thresholds = (40, 50, 60, 70, 80, 90)
    for fold in range(1, folds + 1):
        test_start_i = int(boundaries[fold])
        test_end_i = int(boundaries[fold + 1])
        if test_end_i <= test_start_i:
            continue
        train_end_i = max(0, test_start_i - max(1, purge_days))
        train_dates = set(dates[:train_end_i])
        test_dates = set(dates[test_start_i:test_end_i])
        train = [row for row in valid if str(row.get("date")) in train_dates]
        test = [row for row in valid if str(row.get("date")) in test_dates]
        candidates = []
        for threshold in thresholds:
            metrics = _summary(_apply_confidence_threshold(train, threshold))
            if int(metrics.get("actionable") or 0) >= 8:
                candidates.append((threshold, metrics))
        if not candidates:
            selected_threshold, train_metrics = 70, _summary(_apply_confidence_threshold(train, 70))
        else:
            selected_threshold, train_metrics = max(candidates, key=lambda item: (item[1].get("expectancy_pct", 0), item[1].get("win_rate_pct", 0)))
        thresholded_test = _apply_confidence_threshold(test, selected_threshold)
        pooled_test_rows.extend(thresholded_test)
        test_metrics = _summary(thresholded_test)
        reports.append({
            "fold": fold,
            "train_start": min(train_dates) if train_dates else None,
            "train_end": max(train_dates) if train_dates else None,
            "test_start": min(test_dates) if test_dates else None,
            "test_end": max(test_dates) if test_dates else None,
            "purge_sessions": max(1, purge_days),
            "selected_confidence_threshold": selected_threshold,
            "train": train_metrics,
            "test": test_metrics,
        })
    test_expectancies = [_finite(report["test"].get("expectancy_pct")) for report in reports]
    positive = sum(value > 0 for value in test_expectancies)
    return {
        "method": "expanding_purged_walk_forward",
        "folds": reports,
        "positive_test_folds": positive,
        "total_test_folds": len(reports),
        "median_test_expectancy_pct": round(float(np.median(test_expectancies)), 4) if test_expectancies else 0.0,
        "pooled_out_of_sample": _summary(pooled_test_rows),
        "_pooled_rows": pooled_test_rows,
    }


def _year_report(mode_rows: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for mode, rows in mode_rows.items():
        grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in rows:
            year = str(row.get("date") or "")[:4]
            if year:
                grouped[year].append(row)
        for year in sorted(grouped):
            output.append({"mode": mode, "year": year, **_summary(grouped[year])})
    return output


async def run_pattern_lab(
    request: Any,
    *,
    job: dict[str, Any] | None = None,
    stop_event: Any | None = None,
    checkpoint_callback: Any | None = None,
    resume_state: dict[str, Any] | None = None,
) -> dict[str, Any]:
    universe = resolve_tickers(request)
    tickers = universe["tickers"]
    modes = _clean_modes(_get(request, "engine_modes", None))
    period = normalize_period(_get(request, "period", "all"))
    horizon = max(1, min(int(_get(request, "horizon_days", 10) or 10), 120))
    step = max(1, min(int(_get(request, "step", 5) or 5), 252))
    min_history = max(40, min(int(_get(request, "min_history", 220) or 220), 1000))
    max_tests = max(1, min(int(_get(request, "max_tests_per_ticker", 40) or 40), 1000))
    data_source = str(_get(request, "data_source", "cache_only") or "cache_only").lower()
    if data_source not in {"cache_only", "cache_first", "api_first"}:
        data_source = "cache_only"
    sampling_mode = str(_get(request, "sampling_mode", "even") or "even").lower()
    seed = int(_get(request, "random_seed", 73021) or 73021)
    random_window_bars = max(40, int(_get(request, "random_window_bars", 180) or 180))
    start_date = str(_get(request, "start_date", "") or "")[:10]
    end_date = str(_get(request, "end_date", "") or "")[:10]
    transaction_cost_bps = max(0.0, _finite(_get(request, "transaction_cost_bps", 6.0), 6.0))
    slippage_bps = max(0.0, _finite(_get(request, "slippage_bps", 4.0), 4.0))
    cost_pct = (transaction_cost_bps + slippage_bps) / 100.0
    minimum_confidence = max(0.0, _finite(_get(request, "minimum_confidence", 20.0), 20.0))
    target_pct = max(0.1, _finite(_get(request, "target_pct", 4.0), 4.0))
    stop_pct = max(0.1, _finite(_get(request, "stop_pct", 2.5), 2.5))
    ambiguity_policy = str(_get(request, "ambiguity_policy", "stop_first") or "stop_first").lower()
    include_rows = _bool(_get(request, "include_rows", len(tickers) <= 250))
    max_returned_rows = max(0, int(_get(request, "max_returned_rows", 250000) or 250000))
    walk_forward_folds = max(2, min(int(_get(request, "walk_forward_folds", 5) or 5), 10))
    bootstrap_samples = max(
        0,
        min(
            int(_get(request, "bootstrap_samples", 150) or 150),
            int(os.getenv("ORYNTRA_PATTERN_LAB_MAX_BOOTSTRAP", "300")),
        ),
    )
    lookback_bars = max(280, min(int(_get(request, "lookback_bars", 280) or 280), 1000))
    pattern_lookback_bars = max(90, min(int(_get(request, "pattern_lookback_bars", 180) or 180), lookback_bars))


    max_returned_rows = min(
        max_returned_rows,
        max(1000, int(os.getenv("ORYNTRA_PATTERN_LAB_MAX_RETURNED_ROWS", "20000"))),
    )

    config = {
        "tickers": tickers,
        "period": period,
        "horizon_days": horizon,
        "step": step,
        "min_history": min_history,
        "max_tests_per_ticker": max_tests,
        "data_source": data_source,
        "engine_modes": modes,
        "sampling_mode": sampling_mode,
        "random_seed": seed,
        "random_window_bars": random_window_bars,
        "start_date": start_date or None,
        "end_date": end_date or None,
        "transaction_cost_bps": transaction_cost_bps,
        "slippage_bps": slippage_bps,
        "minimum_confidence": minimum_confidence,
        "target_pct": target_pct,
        "stop_pct": stop_pct,
        "ambiguity_policy": ambiguity_policy,
        "overlap_policy": "one_position_per_ticker",
        "walk_forward_folds": walk_forward_folds,
        "bootstrap_samples": bootstrap_samples,
        "lookback_bars": lookback_bars,
        "pattern_lookback_bars": pattern_lookback_bars,
        "schema_version": SCHEMA_VERSION,
        "feature_version": FEATURE_VERSION,
        "code_version": "pattern-lab-next-1.1-efficient-worker",
    }
    experiment_id = record_experiment(
        experiment_type="pattern_lab",
        status="running",
        config=config,
        symbols=tickers,
    )

    resume_state = resume_state or {}
    mode_rows: dict[str, list[dict[str, Any]]] = {
        mode: list((resume_state.get("mode_rows") or {}).get(mode) or []) for mode in modes
    }
    baselines: dict[str, list[dict[str, Any]]] = {
        "always_long": list((resume_state.get("baselines") or {}).get("always_long") or []),
        "random_direction": list((resume_state.get("baselines") or {}).get("random_direction") or []),
    }
    ticker_errors: list[dict[str, str]] = list(resume_state.get("ticker_errors") or [])
    sampled_windows: list[dict[str, Any]] = list(resume_state.get("sampled_windows") or [])
    source_counts: dict[str, int] = defaultdict(int, resume_state.get("source_counts") or {})
    api_fetches = int(resume_state.get("api_fetches") or 0)
    cache_hits = int(resume_state.get("cache_hits") or 0)
    total_checks_estimated = len(tickers) * max_tests * len(modes)
    completed_checks = int(resume_state.get("completed_checks") or 0)
    all_observations: list[dict[str, Any]] = list(resume_state.get("all_observations") or [])
    completed_ticker_names = set(resume_state.get("completed_tickers") or [])
    repository = get_market_repository()
    stopped = False

    if job is not None:
        job.update(
            {
                "status": "running",
                "phase": "loading_market_cache",
                "progress_pct": 0.0,
                "tickers": tickers,
                "total_tickers": len(tickers),
                "completed_tickers": len(completed_ticker_names),
                "completed_checks": completed_checks,
                "total_checks_estimated": total_checks_estimated,
                "message": "Loading canonical cached candles." if not completed_ticker_names else f"Resuming after {len(completed_ticker_names)} completed tickers.",
                "experiment_id": experiment_id,
            }
        )

    for ticker_index, ticker in enumerate(tickers, start=1):
        if ticker in completed_ticker_names:
            continue
        if stop_event is not None and stop_event.is_set():
            stopped = True
            break
        if job is not None:
            job.update(
                {
                    "current_ticker": ticker,
                    "phase": "loading_ticker",
                    "message": f"Loading {ticker} from canonical market repository.",
                }
            )
        try:
            history, source, did_api = await _load_history(ticker, period, data_source)
            history = history if history is not None else pd.DataFrame()
            source_counts[source] += 1
            api_fetches += int(did_api)
            cache_hits += int(not did_api and history is not None and not history.empty)
            if len(history) < min_history + horizon + 1:
                raise ValueError(
                    f"Only {len(history)} daily candles are available; at least "
                    f"{min_history + horizon + 1} are required."
                )
            if not history.index.is_monotonic_increasing:
                history = history.sort_index()
            candidates, window = _candidate_indexes(
                history,
                ticker=ticker,
                min_history=min_history,
                horizon_days=horizon,
                step=step,
                max_tests=max_tests,
                sampling_mode=sampling_mode,
                seed=seed,
                random_window_bars=random_window_bars,
                start_date=start_date,
                end_date=end_date,
            )
            sampled_windows.append({"ticker": ticker, **window, "tests": len(candidates)})
            rng = random.Random(seed + int(hashlib.sha256(ticker.encode()).hexdigest()[:8], 16))

            for local_test_index, index in enumerate(candidates, start=1):
                built = _base_observation(
                    ticker,
                    history,
                    index,
                    horizon_days=horizon,
                    source=source,
                    window=window,
                    lookback_bars=lookback_bars,
                )
                if built is None:
                    continue
                observation, indicators, signal_history = built
                future = history.iloc[index + 1 : index + 1 + horizon]
                pattern_history = signal_history.tail(pattern_lookback_bars)
                legacy_modes = [mode for mode in modes if mode != "universal_v2"]
                patterns_by_mode = analyze_patterns_multi(pattern_history, indicators, legacy_modes) if legacy_modes else {}
                all_observations.append(observation)
                baselines["always_long"].append(_baseline_row(observation, "LONG", cost_pct))
                baselines["random_direction"].append(
                    _baseline_row(observation, rng.choice(["LONG", "SHORT"]), cost_pct)
                )
                for mode in modes:
                    row = _evaluate_engine(
                        mode,
                        observation,
                        indicators,
                        signal_history if mode == "universal_v2" else pattern_history,
                        future,
                        cost_pct=cost_pct,
                        minimum_confidence=minimum_confidence,
                        target_pct=target_pct,
                        stop_pct=stop_pct,
                        ambiguity_policy=ambiguity_policy,
                        patterns_override=patterns_by_mode.get(mode),
                    )
                    mode_rows[mode].append(row)
                    completed_checks += 1
                    if job is not None:
                        job.update(
                            {
                                "phase": "testing",
                                "current_date": observation["date"],
                                "current_test_index": local_test_index,
                                "current_ticker_tests": len(candidates),
                                "completed_checks": completed_checks,
                                "progress_pct": round(
                                    min(99.0, completed_checks / max(1, total_checks_estimated) * 100.0),
                                    2,
                                ),
                                "message": f"{ticker}: observation {local_test_index}/{len(candidates)}, engine {mode}.",
                            }
                        )
                await asyncio.sleep(0)
            completed_ticker_names.add(ticker)
            if job is not None:
                job["completed_tickers"] = len(completed_ticker_names)
            if checkpoint_callback is not None:
                checkpoint_callback({
                    "mode_rows": mode_rows,
                    "baselines": baselines,
                    "ticker_errors": ticker_errors,
                    "sampled_windows": sampled_windows,
                    "source_counts": dict(source_counts),
                    "api_fetches": api_fetches,
                    "cache_hits": cache_hits,
                    "completed_checks": completed_checks,
                    "all_observations": all_observations,
                    "completed_tickers": sorted(completed_ticker_names),
                    "stopped": stopped,
                    "config": config,
                })
            if stopped:
                break
        except Exception as exc:
            ticker_errors.append({"ticker": ticker, "error": str(exc)})
            completed_ticker_names.add(ticker)
            if job is not None:
                job["ticker_errors"] = ticker_errors[-30:]
                job["completed_tickers"] = len(completed_ticker_names)
            if checkpoint_callback is not None:
                checkpoint_callback({
                    "mode_rows": mode_rows,
                    "baselines": baselines,
                    "ticker_errors": ticker_errors,
                    "sampled_windows": sampled_windows,
                    "source_counts": dict(source_counts),
                    "api_fetches": api_fetches,
                    "cache_hits": cache_hits,
                    "completed_checks": completed_checks,
                    "all_observations": all_observations,
                    "completed_tickers": sorted(completed_ticker_names),
                    "stopped": stopped,
                    "config": config,
                })

    comparability = compare_engine_inputs(mode_rows)
    summaries = [{"mode": mode, **_summary(rows)} for mode, rows in mode_rows.items()]
    baseline_summaries = [{"mode": mode, **_summary(rows)} for mode, rows in baselines.items()]
    best = None
    if comparability["comparable"]:
        best = max(
            summaries,
            key=lambda item: (
                item["expectancy_pct"],
                item["max_drawdown_pct"],
                item["win_rate_pct"],
                item["coverage_pct"],
            ),
            default=None,
        )

    dataset_fp = rows_fingerprint(all_observations)
    valid_rows = sum(len(rows) for rows in mode_rows.values())
    status = "stopped" if stopped else ("done" if valid_rows else "failed")
    message = (
        "Pattern Lab stopped with reproducible partial results."
        if status == "stopped"
        else (
            "Pattern Lab Next complete."
            if status == "done"
            else "Pattern Lab could not build any valid observations; warm the local cache or use cache_first."
        )
    )
    all_dates = sorted({row["date"] for row in all_observations})
    metrics_for_storage = {
        "summary": summaries,
        "comparability": comparability,
        "ticker_errors": ticker_errors,
    }
    record_experiment(
        experiment_type="pattern_lab",
        status=status,
        config=config,
        dataset_fingerprint=dataset_fp,
        dataset_start=all_dates[0] if all_dates else None,
        dataset_end=all_dates[-1] if all_dates else None,
        symbols=tickers,
        sample_count=len(all_observations),
        metrics=metrics_for_storage,
        notes=message,
        experiment_id=experiment_id,
    )

    robust_validation = {}
    for index, (mode, rows) in enumerate(mode_rows.items(), start=1):
        walk_forward = _walk_forward_report(rows, folds=walk_forward_folds, purge_days=horizon)
        pooled_oos_rows = walk_forward.pop("_pooled_rows", [])
        bootstrap = _cluster_bootstrap_ci(
            pooled_oos_rows, samples=bootstrap_samples, seed=seed + index * 1009
        ) if walk_forward.get("total_test_folds", 0) else {
            "method": "ticker_cluster_bootstrap_on_pooled_oos",
            "samples": 0,
            "expectancy_95_ci_pct": None,
            "win_rate_95_ci_pct": None,
            "status": "insufficient_out_of_sample_folds",
        }
        bootstrap["population"] = "pooled_out_of_sample"
        robust_validation[mode] = {"walk_forward": walk_forward, "bootstrap": bootstrap}
    raw_row_count = sum(len(rows) for rows in mode_rows.values())
    expose_rows = include_rows and raw_row_count <= max_returned_rows
    result_payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "lab_version": "next-1.1",
        "experiment_id": experiment_id,
        "status": status,
        "message": message,
        "params": config,
        "universe": universe,
        "sampled_windows": sampled_windows,
        "dataset": {
            "fingerprint": dataset_fp,
            "observations": len(all_observations),
            "first_date": all_dates[0] if all_dates else None,
            "last_date": all_dates[-1] if all_dates else None,
            "schema_version": SCHEMA_VERSION,
            "feature_version": FEATURE_VERSION,
            "next_session_execution": True,
            "causal_features": True,
            "non_overlapping_positions": True,
            "purged_walk_forward": True,
            "cluster_bootstrap": True,
            "rows_returned": expose_rows,
            "raw_engine_rows": raw_row_count,
            "resumed_from_checkpoint": bool(resume_state),
            "completed_tickers": len(completed_ticker_names),
        },
        "engine_comparability": comparability,
        "summary": summaries,
        "robust_validation": robust_validation,
        "year_level": _year_report(mode_rows),
        "best_mode": best,
        "baselines": baseline_summaries,
        "bias_audit": _bias_audit(mode_rows),
        "direction_split": grouped_metrics(mode_rows, "direction", limit=80),
        "ticker_level": grouped_metrics(mode_rows, "ticker", limit=500),
        "pattern_level": grouped_metrics(mode_rows, "top_pattern", limit=300),
        "setup_level": grouped_metrics(mode_rows, "setup_type", limit=200),
        "regime_level": grouped_metrics(mode_rows, "regime", limit=200),
        "confidence_buckets": _confidence_buckets(mode_rows),
        "threshold_report": _threshold_report(mode_rows),
        "short_diagnostics": {
            "by_mode": [
                {"mode": mode, **_summary([row for row in rows if row.get("direction") == "SHORT"])}
                for mode, rows in mode_rows.items()
            ]
        },
        "production_promotion": {
            "automatic": False,
            "policy": "Pattern Lab results never alter live-engine weights or production decisions automatically.",
        },
        "counter_update": {
            "added_stock_analyses": 0,
            "added_engine_checks": 0,
            "policy": "Research does not inflate public analysis counters.",
        },
        "rows": mode_rows if expose_rows else {},
        "baseline_rows": baselines if expose_rows else {},
        "ticker_errors": ticker_errors,
        "cache": {
            "data_source": data_source,
            "cache_hits": cache_hits,
            "cache_misses": max(0, len(tickers) - cache_hits - api_fetches),
            "api_fetches": api_fetches,
            "source_counts": dict(source_counts),
            "db_size_bytes": get_ohlcv_cache_size_bytes(),
            "db_size_mb": round(get_ohlcv_cache_size_bytes() / 1024 / 1024, 3),
        },
        "note": (
            "Educational causal daily-bar research. Signals use data through the signal close, "
            "entries use the next session open, costs and slippage are applied, and same-day "
            "target/stop ambiguity is resolved conservatively unless configured otherwise. "
            "Daily data still cannot model exact intraday sequencing, liquidity, taxes, or news."
        ),
    }

    if job is not None:
        job.update(
            {
                "status": status,
                "phase": "complete" if status == "done" else status,
                "progress_pct": 100.0 if status == "done" else job.get("progress_pct", 0.0),
                "summary": summaries,
                "best_mode": best,
                "ticker_errors": ticker_errors,
                "finished_at": datetime.now(timezone.utc).isoformat(),
                "message": message,
            }
        )
    return result_payload
