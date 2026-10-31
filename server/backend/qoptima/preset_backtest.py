from __future__ import annotations

"""One-click, development-only QPO preset backtesting.

This module is intentionally a QPO/QOptima harness, not a new quant model.
A seeded 75-name candidate pool is reduced by QScan at every rebalance; QPO
then solves the small discrete allocation problem.  Full-QRS presets use the
exact finite-domain reference for historical portfolio decisions and run the
local diagonal-QAOA simulator only on scheduled audit rebalances.
"""

from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import math
import random
from typing import Any, Callable

import numpy as np
import pandas as pd

from ..research_universe import clean_tickers
from .backtest_overlay import run_qoptima_backtest_overlay
from .v1a.config import V1AConfig
from .v1a.features import build_default_market_features
from .v1a.training import save_v1a_artifact, train_v1a
from .v1a.inference import load_v1a_artifact
from .v1a.cache_identity import (fold_training_identity, cache_metadata_matches,
    cache_artifact_lock, publish_cache_artifact)

PRESET_VERSION = "QOPTIMA_ROBUST_WALKFORWARD_V1_1"
DEFAULT_UNIVERSE_SIZE = 75


def json_safe(value: Any) -> Any:
    """Recursively convert research outputs into strict JSON primitives.

    QPO candidate/replay metadata legitimately contains NumPy arrays and
    scalars. FastAPI's default encoder does not serialize ndarray objects, and
    strict JSON responses also reject NaN/Infinity. This boundary sanitizer is
    deliberately lossless for finite numeric research results and converts
    non-finite diagnostic values to ``None`` rather than crashing after a long
    backtest has completed.
    """
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, np.generic):
        return json_safe(value.item())
    if isinstance(value, np.ndarray):
        return json_safe(value.tolist())
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    if isinstance(value, pd.Series):
        return json_safe(value.to_dict())
    if isinstance(value, pd.DataFrame):
        return json_safe(value.to_dict(orient="records"))
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(k): json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [json_safe(v) for v in value]
    if hasattr(value, "as_dict") and callable(value.as_dict):
        return json_safe(value.as_dict())
    if hasattr(value, "__dict__"):
        return json_safe(vars(value))
    return str(value)


def _emit_progress(callback: Callable[[dict[str, Any]], None] | None, *, stage: str, message: str, percent: float) -> None:
    if callback is None:
        return
    callback({"stage": str(stage), "message": str(message), "percent": float(max(0.0, min(100.0, percent)))})

# Add the liquid/core names that the supplemental research pool intentionally
# omits.  The pool is a *candidate source*, never a signal or historical index.
_CORE = """
AAPL MSFT NVDA AMZN META GOOGL GOOG AVGO TSLA JPM V MA COST LLY UNH XOM HD PG JNJ WMT
CRM ORCL NFLX ADBE AMD QCOM TXN AMAT LRCX KLAC NOW PANW CRWD MU INTC IBM CSCO ACN INTU
GS MS BAC C WFC AXP BLK SCHW COF BRK.B PGR TRV USB PNC TFC BK ICE CME ABBV MRK PFE TMO
DHR ABT ISRG SYK MDT GILD AMGN REGN VRTX BMY CVS CI ELV BSX LOW SBUX TGT TJX ROST CMG
YUM KO PEP CL KMB MDLZ GIS KR DE GE HON UPS FDX LMT NOC GD ETN EMR CSX NSC UNP COP SLB
EOG MPC PSX OXY KMI WMB HAL BKR DUK SO AEP EXC SRE XEL PEG ED AWK CAT RTX BA NEE MCD
""".split()
_EXCLUDED_ETFS = {"SPY", "QQQ", "IWM", "VXUS", "SMH", "DIA", "VTI", "VOO", "ARKK", "TQQQ", "SQQQ"}


def qpo_candidate_pool() -> list[str]:
    # The one-click preset deliberately draws from the long-history core rather
    # than the full contemporary research universe.  The broader pool contains
    # recent IPOs that are useful for live/custom research but can make a seeded
    # 75-name historical draw fail the six-year V1A warm-up.  Historical
    # eligibility is still enforced again at each rebalance from observed data.
    values, _ = clean_tickers(list(_CORE), max_count=500)
    return [s for s in values if s not in _EXCLUDED_ETFS]


def seeded_universe(seed: int, size: int = DEFAULT_UNIVERSE_SIZE) -> dict[str, Any]:
    pool = qpo_candidate_pool()
    n = max(5, min(int(size), min(100, len(pool))))
    rng = random.Random(int(seed))
    order = list(pool)
    rng.shuffle(order)
    chosen = order[:n]
    digest = sha256((str(int(seed)) + "|" + "|".join(chosen)).encode("utf-8")).hexdigest()
    return {
        "seed": (int(seed) % (2**32 - 1)) or 1,
        "universe_seed": int(seed),
        "size": len(chosen),
        "symbols": chosen,
        "universe_hash": digest,
        "pool_size": len(pool),
        "selection_policy": "deterministic seeded shuffle of curated long-history liquid-US QPO development pool; per-date eligibility still enforced from observed history",
        "warning": "The static candidate pool is not a point-in-time constituent database. Preset results are development research and retain survivorship-bias risk unless dated universe snapshots are supplied.",
    }


def _forward_market_target(returns: pd.DataFrame, horizon: int) -> pd.Series:
    market = returns.mean(axis=1, skipna=True).fillna(0.0)
    h = max(1, int(horizon))
    return (1.0 + market.clip(lower=-0.999999)).rolling(h, min_periods=h).apply(np.prod, raw=True).shift(-(h - 1)) - 1.0


def build_causal_v1a_artifact_schedule(
    returns: pd.DataFrame,
    *,
    evaluation_start: Any,
    evaluation_end: Any,
    output_directory: str | None,
    horizon_sessions: int,
    initial_train_sessions: int,
    refit_sessions: int,
    seed: int,
    max_epochs: int,
    patience: int,
    progress_callback: Callable[[dict[str, Any]], None] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Train development V1A artifacts only from information available by each cutoff."""
    # NumPy/PyTorch training seeds are normalized to the uint32-compatible
    # range.  The preset UI may use wider reproducibility seeds; those remain
    # recorded separately in the run manifest.
    seed = int(seed) % (2**32 - 1)
    if seed <= 0:
        seed = 1
    r = returns.replace([np.inf, -np.inf], np.nan).dropna(how="all").sort_index()
    if len(r) < int(initial_train_sessions):
        raise ValueError(
            f"V1A preset needs at least {int(initial_train_sessions)} sessions before/through the first development decision; only {len(r)} are available"
        )
    features = build_default_market_features(r)
    target = _forward_market_target(r, int(horizon_sessions))
    idx = r.index
    start_ts, end_ts = pd.Timestamp(evaluation_start), pd.Timestamp(evaluation_end)
    before = np.flatnonzero(idx < start_ts)
    if not len(before):
        raise ValueError("V1A preset has no pre-evaluation training history")
    first_cut = int(before[-1])
    if first_cut + 1 < int(initial_train_sessions):
        raise ValueError(
            f"V1A preset requires {int(initial_train_sessions)} pre-evaluation sessions; only {first_cut + 1} are available"
        )
    cutoffs = [first_cut]
    pos = first_cut + max(1, int(refit_sessions))
    end_pos = int(np.searchsorted(idx.values, np.datetime64(end_ts), side="right") - 1)
    while pos <= end_pos:
        cutoffs.append(pos)
        pos += max(1, int(refit_sessions))

    root = Path(output_directory) if output_directory else Path(__file__).resolve().parents[2] / "data" / "qoptima" / "v1a_preset_artifacts"
    # Future extensions/value revisions must not rename an unchanged past fold.
    # Complete admitted data/config/source identity is carried by each filename.
    run_key = sha256(f"V1A_CACHE_V2|{seed}|{start_ts}|{'|'.join(map(str,r.columns))}".encode()).hexdigest()[:16]
    out_dir = root / run_key
    out_dir.mkdir(parents=True, exist_ok=True)
    schedule: list[dict[str, Any]] = []
    diagnostics: list[dict[str, Any]] = []
    h = max(1, int(horizon_sessions))

    total_cutoffs = max(1, len(cutoffs))
    for j, cut in enumerate(cutoffs):
        _emit_progress(progress_callback, stage="v1a_training", message=f"Training causal V1A artifact {j+1}/{total_cutoffs}", percent=100.0 * j / total_cutoffs)
        cutoff_time = idx[cut]
        f = features.loc[:cutoff_time].copy()
        y = target.loc[:cutoff_time].copy()
        # The final h-1 labels would realize after the cutoff. Explicitly remove
        # them even though the full target vector was constructed once.
        if h > 1 and len(y):
            y.iloc[max(0, len(y) - (h - 1)) :] = np.nan
        cfg = V1AConfig(
            horizon_sessions=h,
            seed=(int(seed) + j) % (2**32 - 1) or 1,
            max_epochs=int(max_epochs),
            patience=int(patience),
        )
        # Identity uses only this fold's admitted features/labels. Preserve column
        # order and the source calendar; unavailable future labels stay masked.
        maturity_values = [idx[i+h-1] if i+h-1 < len(idx) else pd.NaT for i in range(len(idx))]
        maturities = pd.Series(maturity_values, index=idx).reindex(y.index)
        identity = fold_training_identity(f, y, config=cfg.as_dict(), information_cutoff=cutoff_time,
                                          target_maturities=maturities)
        path = out_dir / f"v1a_{j:03d}_{pd.Timestamp(cutoff_time).date()}_{identity['identity_sha256'][:16]}.pt"
        meta = None
        cache_status = "trained"
        with cache_artifact_lock(path):
            if path.is_file():
                try:
                    _cached_model, cached_meta, _cached_cal, _cached_digest = load_v1a_artifact(path)
                    if cache_metadata_matches(cached_meta, identity):
                        meta = dict(cached_meta)
                        cache_status = "reused"
                except Exception:
                    meta = None
            if meta is None:
                result = train_v1a(f, y, config=cfg, signed_control=False)
                publish_cache_artifact(result, path, identity, save=save_v1a_artifact, load=load_v1a_artifact)
                meta = dict(result["metadata"])
        schedule.append({
            "artifact_path": str(path),
            "artifact_information_cutoff": meta.get("artifact_information_cutoff"),
            "spec_hash": meta.get("spec_hash"),
        })
        diagnostics.append({
            "path": str(path),
            "training_cutoff": str(cutoff_time),
            "artifact_information_cutoff": meta.get("artifact_information_cutoff"),
            "training_rows": meta.get("training_rows"),
            "validation_rows": meta.get("validation_rows"),
            "best_validation_fz0": meta.get("best_validation_fz0"),
            "cache_status": cache_status,
        })
    _emit_progress(progress_callback, stage="v1a_training", message=f"Causal V1A artifacts ready ({len(schedule)})", percent=100.0)
    return schedule, {
        "mode": "CAUSAL_AUTO_TRAIN_DEVELOPMENT_ONLY",
        "artifact_count": len(schedule),
        "initial_train_sessions": int(initial_train_sessions),
        "refit_sessions": int(refit_sessions),
        "horizon_sessions": h,
        "artifacts": diagnostics,
    }


def preset_spec(*, model: str, seed: int, universe: list[str], provider: str = "auto") -> dict[str, Any]:
    model = str(model or "v1a_vs_canonical").strip().lower()
    if model not in {"v1a_vs_canonical", "canonical_full_qrs", "baseline_qpo"}:
        raise ValueError("model must be v1a_vs_canonical, canonical_full_qrs, or baseline_qpo")
    v1a = model == "v1a_vs_canonical"
    full = model != "baseline_qpo"
    return {
        "universe": list(universe),
        "provider": str(provider or "auto"),
        "evaluation_period": "3y",
        "holdout_sessions": 252,
        "lookback_sessions": 252,
        "rebalance_sessions": 21,
        "max_name_weight": 0.30,
        "risk_aversion": 3.0,
        "turnover_penalty": 0.20,
        "risk_free_rate": 0.0,
        "transaction_cost_bps": 5.0,
        "require_adjusted_prices": True,
        "covariance_methods": ["shrinkage"],
        "run_covariance_benchmarks": False,
        "selected_covariance_method": "shrinkage",
        "covariance_parameters": {"estimator": "ledoit_wolf"},
        "alpha_values": [1.0],
        "run_alpha_frontier": False,
        "true_qoptima_replay": True,
        "replay_bits_per_asset": 2,
        "replay_qscan_assets": 6,
        "replay_backend": "exact",
        "seed": int(seed),
        "full_qrs_primary_qaoa": bool(full),
        "full_qrs_beta": 0.10,
        "full_qrs_bootstrap_reps": 256,
        "full_qrs_quantile_confidence": 0.90,
        "full_qrs_exact_variable_cap": 12,
        "full_qrs_qaoa_reps": 2,
        "full_qrs_qaoa_evaluations": 48,
        "full_qrs_decision_mode": "exact_reference" if full else "qaoa_observed",
        "simulator_audit_every_rebalances": 6 if full else 0,
        "run_ablations": False,
        "run_component_ablations": False,
        "fragility_downside_weight": 0.0,
        "fragility_correlation_weight": 0.0,
        "fragility_regime_weight": 0.0,
        "fragility_correlation_blend": 0.65,
        "start_from_cash": True,
        "target_gross_override": 1.0,
        "v1a_enabled": bool(v1a),
        "v1a_auto_train": bool(v1a),
        "v1a_initial_train_sessions": 1512,
        "v1a_refit_sessions": 252,
        "v1a_training_max_epochs": 500,
        "v1a_training_patience": 50,
        "v1a_gate_b_block_sessions": 21,
        "v1a_gate_b_bootstrap_replications": 2000,
        "v1a_gate_b_alpha": 0.05,
        # Deliberately not a promotion pass/fail until an economic hurdle is frozen.
        "v1a_gate_b_effect_size": 0.0,
        "hardware_qscan_assets": 20,
        "hardware_bits_per_asset": 4,
        "hardware_selection_qubit": True,
        "hardware_qaoa_reps": 1,
        "hardware_optimizer_evaluations": 28,
        "hardware_frontier_points": 5,
        "hardware_estimator_shots": 1000,
        "hardware_sampler_shots": 10000,
        "hardware_mitigation_multiplier": 1.5,
        "planning_price_per_second": 1.60,
    }


def _trial_path() -> Path:
    return Path(__file__).resolve().parents[2] / "data" / "qoptima" / "preset_backtest_trials.jsonl"


def _append_trial(entry: dict[str, Any]) -> None:
    path = _trial_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, sort_keys=True, default=str) + "\n")


def run_qpo_preset_backtest(user_id: int, request: dict[str, Any], *, progress_callback: Callable[[dict[str, Any]], None] | None = None) -> dict[str, Any]:
    seed = int(request.get("seed") if request.get("seed") is not None else random.SystemRandom().randrange(1, 2**63 - 1))
    size = int(request.get("universe_size") or DEFAULT_UNIVERSE_SIZE)
    _emit_progress(progress_callback, stage="universe", message="Building reproducible 75-name QPO candidate universe", percent=1.0)
    u = seeded_universe(seed, size)
    model = str(request.get("model") or "v1a_vs_canonical")
    spec = preset_spec(model=model, seed=seed, universe=u["symbols"], provider=str(request.get("provider") or "auto"))
    started = datetime.now(timezone.utc).isoformat()
    try:
        _emit_progress(progress_callback, stage="starting", message="Starting causal QPO walk-forward", percent=2.0)
        result = run_qoptima_backtest_overlay(user_id, spec, progress_callback=progress_callback)
        status = "PASSED"
        error = None
    except Exception as exc:
        status = "FAILED"
        error = f"{type(exc).__name__}: {exc}"
        _append_trial({
            "preset_version": PRESET_VERSION, "started_at_utc": started, "status": status,
            "model": model, "seed": seed, "universe_hash": u["universe_hash"], "error": error,
        })
        raise
    manifest = dict(result.get("run_manifest") or {})
    manifest.update({
        "preset_version": PRESET_VERSION,
        "preset_model": model,
        "seeded_universe": u,
        "normal_button_can_spend_holdout": False,
        "historical_decision_engine": "exact finite-domain QPO/Full-QRS reference" if model != "baseline_qpo" else "baseline discrete QPO",
        "quantum_simulator_role": "limited audit at every 6th rebalance; never substitutes for exact reference decisions",
    })
    result["preset"] = manifest
    safe_result = json_safe(result)
    _append_trial(json_safe({
        "preset_version": PRESET_VERSION, "started_at_utc": started, "status": status,
        "model": model, "seed": seed, "universe_hash": u["universe_hash"],
        "universe_size": len(u["symbols"]), "rebalance_count": safe_result.get("rebalance_count"),
        "run_manifest": manifest,
        "qoptima": safe_result.get("true_qoptima_walkforward"),
        "v1a_gate_b": (safe_result.get("v1a") or {}).get("gate_b"),
    }))
    _emit_progress(progress_callback, stage="complete", message="QPO preset backtest complete", percent=100.0)
    return safe_result
