from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

from .research_experiments import chronological_split

APP_DIR = Path(__file__).resolve().parents[1]
MODEL_ROOT = APP_DIR / "data" / "models" / "vai2"
RUNS_DIR = MODEL_ROOT / "runs"
PROMOTED_MODEL_PATH = MODEL_ROOT / "model.json"
PROMOTED_META_PATH = MODEL_ROOT / "metadata.json"

NUMERIC_FEATURES = [
    "rsi14", "adx14", "di_spread", "vol_ratio", "atr_pct",
    "momentum_5d", "momentum_20d", "momentum_60d",
    "above_ma20", "above_ma50", "above_ma200", "ma_stack",
    "risk_reward_hint", "trend_quality_hint", "volume_quality_hint",
]
# The ticker must never become a learned shortcut: it does not generalize to a
# new issuer and can encode a particular historical period.  Categories are
# fit on the training partition only.
CATEGORICAL_FIELDS = ["regime", "top_pattern", "setup_type"]


def _f(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        if isinstance(value, bool):
            return 1.0 if value else 0.0
        v = float(value)
        if math.isnan(v) or math.isinf(v):
            return default
        return v
    except Exception:
        return default


def _b(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value).strip().lower() in {"1", "true", "yes", "y"}


def _sigmoid(x: np.ndarray | float) -> np.ndarray | float:
    return 1.0 / (1.0 + np.exp(-np.clip(x, -40, 40)))


def _safe_upper(value: Any, default: str = "UNKNOWN") -> str:
    text = str(value or default).strip().upper().replace(" ", "_")
    return text or default


def _regime_from_ind(ind: dict[str, Any]) -> str:
    above50 = bool(ind.get("above_ma50"))
    above200 = bool(ind.get("above_ma200"))
    mom20 = _f(ind.get("momentum_20d"))
    vol = _f(ind.get("vol_ratio"), 1.0)
    adx = _f(ind.get("adx14"))
    low = vol < 0.90
    high = vol >= 1.35
    if above50 and above200 and mom20 > 0:
        return "BULL_TREND_HIGH_VOLUME" if high else ("BULL_TREND_LOW_VOLUME" if low else "BULL_TREND")
    if mom20 > 1:
        return "MOMENTUM_UP_HIGH_VOLUME" if high else ("MOMENTUM_UP_LOW_VOLUME" if low else "MOMENTUM_UP")
    if mom20 < -1:
        return "MOMENTUM_DOWN_HIGH_VOLUME" if high else ("MOMENTUM_DOWN_LOW_VOLUME" if low else "MOMENTUM_DOWN")
    if adx < 16:
        return "CHOP_SIDEWAYS_HIGH_VOLUME" if high else ("CHOP_SIDEWAYS_LOW_VOLUME" if low else "CHOP_SIDEWAYS")
    return "MIXED"


def _top_pattern(patterns: dict[str, Any] | None) -> str:
    try:
        adv = (patterns or {}).get("advanced_patterns") or {}
        top = adv.get("top_pattern") or {}
        return _safe_upper(top.get("pattern_name") or "UNKNOWN")
    except Exception:
        return "UNKNOWN"


def _setup_type(setup: dict[str, Any]) -> str:
    return _safe_upper(setup.get("setup_type") or setup.get("type") or "UNKNOWN")


def _direction(row_or_setup: dict[str, Any]) -> str:
    d = _safe_upper(row_or_setup.get("direction"), "NEUTRAL")
    if d in {"BULLISH", "BUY"}:
        return "LONG"
    if d in {"BEARISH", "SELL"}:
        return "SHORT"
    return d


def _row_numeric(row: dict[str, Any]) -> dict[str, float]:
    above20 = 1.0 if _b(row.get("above_ma20")) else 0.0
    above50 = 1.0 if _b(row.get("above_ma50")) else 0.0
    above200 = 1.0 if _b(row.get("above_ma200")) else 0.0
    vol = min(5.0, max(0.0, _f(row.get("vol_ratio"), 1.0)))
    atr = min(20.0, max(0.0, _f(row.get("atr_pct"), 3.0)))
    mom20 = _f(row.get("momentum_20d"))
    adx = _f(row.get("adx14"))
    di_spread = _f(row.get("di_plus")) - _f(row.get("di_minus"))
    trend_quality = 0.0
    if above20:
        trend_quality += 0.25
    if above50:
        trend_quality += 0.25
    if above200:
        trend_quality += 0.25
    if mom20 > 0:
        trend_quality += 0.15
    if adx >= 18 and di_spread > 0:
        trend_quality += 0.10
    volume_quality = 0.45
    if 0.75 <= vol <= 1.25:
        volume_quality = 0.70
    elif 1.25 < vol <= 1.75:
        volume_quality = 0.58
    elif vol > 1.75:
        volume_quality = 0.38
    risk_reward_hint = max(0.0, min(1.0, (12.0 - atr) / 12.0))
    return {
        "rsi14": (_f(row.get("rsi14"), 50.0) - 50.0) / 50.0,
        "adx14": adx / 60.0,
        "di_spread": di_spread / 60.0,
        "vol_ratio": vol / 5.0,
        "atr_pct": atr / 20.0,
        "momentum_5d": float(np.clip(_f(row.get("momentum_5d")) / 20.0, -2, 2)),
        "momentum_20d": float(np.clip(mom20 / 40.0, -2, 2)),
        "momentum_60d": float(np.clip(_f(row.get("momentum_60d")) / 80.0, -2, 2)),
        "above_ma20": above20,
        "above_ma50": above50,
        "above_ma200": above200,
        "ma_stack": (above20 + above50 + above200) / 3.0,
        "risk_reward_hint": risk_reward_hint,
        "trend_quality_hint": min(1.0, trend_quality),
        "volume_quality_hint": volume_quality,
    }


def _current_numeric(ind: dict[str, Any], setup: dict[str, Any]) -> dict[str, float]:
    row = dict(ind or {})
    return _row_numeric(row)


def _build_categories(rows: list[dict[str, Any]], max_per_field: int = 120) -> dict[str, list[str]]:
    cats: dict[str, list[str]] = {}
    for field in CATEGORICAL_FIELDS:
        counts: dict[str, int] = {}
        for r in rows:
            value = _safe_upper(r.get(field))
            counts[value] = counts.get(value, 0) + 1
        cats[field] = [k for k, _ in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:max_per_field]]
    return cats


def _feature_names(cats: dict[str, list[str]]) -> list[str]:
    names = list(NUMERIC_FEATURES)
    for field in CATEGORICAL_FIELDS:
        names.extend([f"{field}={c}" for c in cats.get(field, [])])
    return names


def _vector_from_numeric_and_categories(vals: dict[str, float], row: dict[str, Any], cats: dict[str, list[str]]) -> list[float]:
    out = [float(vals[name]) for name in NUMERIC_FEATURES]
    for field in CATEGORICAL_FIELDS:
        value = _safe_upper(row.get(field))
        allowed = cats.get(field) or []
        out.extend([1.0 if value == c else 0.0 for c in allowed])
    return out


def _vector(row: dict[str, Any], cats: dict[str, list[str]]) -> list[float]:
    return _vector_from_numeric_and_categories(_row_numeric(row), row, cats)


def _vector_current(ind: dict[str, Any], setup: dict[str, Any], patterns: dict[str, Any], cats: dict[str, list[str]]) -> list[float]:
    vals = _current_numeric(ind or {}, setup or {})
    row = {name: vals[name] for name in NUMERIC_FEATURES}
    row["regime"] = _regime_from_ind(ind or {})
    row["top_pattern"] = _top_pattern(patterns)
    row["setup_type"] = _setup_type(setup or {})
    return _vector_from_numeric_and_categories(vals, row, cats)


def _quality_metrics(row: dict[str, Any]) -> dict[str, float | int | bool]:
    ret = _f(row.get("return_pct"))
    mfe = max(0.0, _f(row.get("mfe_pct")))
    mae_abs = abs(_f(row.get("mae_pct")))
    rr = mfe / max(mae_abs, 0.01)
    target = _b(row.get("target_hit"))
    stop = _b(row.get("stop_hit"))
    actionable = bool(row.get("actionable")) and _direction(row) == "LONG"
    quality = (
        ret * 1.35
        + mfe * 0.22
        - mae_abs * 0.34
        + (0.85 if target else 0.0)
        - (1.35 if stop else 0.0)
        + (0.45 if ret > 0 else -0.25)
        + min(1.0, rr) * 0.35
    )
    good = int(actionable and (
        (ret >= 0.45 and rr >= 1.05 and not stop)
        or (ret >= 1.25 and rr >= 0.85 and target)
        or (ret >= 2.0 and not (stop and rr < 0.75))
    ))
    return {"return": ret, "mfe": mfe, "mae_abs": mae_abs, "rr": rr, "target": target, "stop": stop, "actionable": actionable, "quality": quality, "good": good}


def _fit_logistic(X: np.ndarray, y: np.ndarray, sample_weight: np.ndarray | None = None, *, epochs: int = 900, lr: float = 0.075, l2: float = 0.003) -> tuple[np.ndarray, float]:
    w = np.zeros(X.shape[1], dtype=float)
    b = 0.0
    sw = np.asarray(sample_weight if sample_weight is not None else np.ones(len(y)), dtype=float)
    sw = sw / max(sw.mean(), 1e-9)
    for _ in range(epochs):
        p = np.asarray(_sigmoid(X @ w + b), dtype=float)
        err = (p - y) * sw
        grad_w = (X.T @ err) / max(len(X), 1) + l2 * w
        grad_b = float(err.mean())
        w -= lr * grad_w
        b -= lr * grad_b
    return w, b


def _fit_ridge(X: np.ndarray, y: np.ndarray, l2: float = 1.0) -> tuple[np.ndarray, float]:
    if len(X) == 0:
        return np.zeros(X.shape[1], dtype=float), 0.0
    Xb = np.column_stack([X, np.ones(len(X))])
    reg = np.eye(Xb.shape[1]) * float(l2)
    reg[-1, -1] = 0.0
    try:
        coef = np.linalg.solve(Xb.T @ Xb + reg, Xb.T @ y)
    except Exception:
        coef = np.linalg.pinv(Xb.T @ Xb + reg) @ Xb.T @ y
    return np.asarray(coef[:-1], dtype=float), float(coef[-1])


def _summarize_taken(mask: np.ndarray, ret: np.ndarray, mfe: np.ndarray, mae_abs: np.ndarray, stop: np.ndarray, target: np.ndarray) -> dict[str, float]:
    if not bool(mask.any()):
        return {"signals": 0, "coverage_pct": 0.0, "win_rate_pct": 0.0, "avg_return_pct": 0.0, "avg_mfe_pct": 0.0, "avg_mae_pct": 0.0, "reward_risk_ratio": 0.0, "stop_hit_rate_pct": 0.0, "target_hit_rate_pct": 0.0}
    r = ret[mask]
    m = mfe[mask]
    a = mae_abs[mask]
    s = stop[mask]
    t = target[mask]
    rr = float(m.mean() / max(a.mean(), 0.01)) if len(m) else 0.0
    return {
        "signals": int(mask.sum()),
        "coverage_pct": round(float(mask.mean() * 100), 2),
        "win_rate_pct": round(float((r > 0).mean() * 100), 2),
        "avg_return_pct": round(float(r.mean()), 3),
        "avg_mfe_pct": round(float(m.mean()), 3),
        "avg_mae_pct": round(float(-a.mean()), 3),
        "reward_risk_ratio": round(rr, 3),
        "stop_hit_rate_pct": round(float(s.mean() * 100), 2),
        "target_hit_rate_pct": round(float(t.mean() * 100), 2),
    }


def _promotion_score(summary: dict[str, float]) -> float:
    coverage = float(summary.get("coverage_pct") or 0)
    signals = int(summary.get("signals") or 0)
    avg_ret = float(summary.get("avg_return_pct") or 0)
    win = float(summary.get("win_rate_pct") or 0)
    rr = float(summary.get("reward_risk_ratio") or 0)
    stop = float(summary.get("stop_hit_rate_pct") or 0)
    target = float(summary.get("target_hit_rate_pct") or 0)
    small_sample_penalty = max(0, 30 - signals) * 2.25
    low_coverage_penalty = max(0, 4 - coverage) * 1.1
    overtrade_penalty = max(0, coverage - 32) * 0.16
    return round(
        avg_ret * 24.0
        + win * 0.20
        + min(rr, 2.4) * 9.0
        + target * 0.08
        - stop * 0.48
        - small_sample_penalty
        - low_coverage_penalty
        - overtrade_penalty,
        3,
    )


def _load_promoted_model() -> dict[str, Any] | None:
    try:
        if not PROMOTED_MODEL_PATH.exists():
            return None
        return json.loads(PROMOTED_MODEL_PATH.read_text(encoding="utf-8"))
    except Exception:
        return None


def _promote_if_better(model: dict[str, Any], run_dir: Path, *, force_promote: bool = False) -> dict[str, Any]:
    MODEL_ROOT.mkdir(parents=True, exist_ok=True)
    current = _load_promoted_model()
    current_version = str((current or {}).get("version") or "")
    legacy_current = bool(current and "2.2" not in current_version)
    current_score = -999.0 if legacy_current else float(((current or {}).get("test") or {}).get("promotion_score") or -999)
    new_score = float((model.get("test") or {}).get("promotion_score") or -999)
    promoted = bool(force_promote or current is None or legacy_current or new_score >= current_score + 0.10)
    reason = "force_promote" if force_promote else ("first_vai2_2_model_replaces_legacy_vai2" if legacy_current else ("first_model" if current is None else ("untouched_test_score_improved" if promoted else "kept_existing_model")))
    model["promotion"] = {
        "promoted": promoted,
        "previous_score": None if legacy_current else (current_score if current is not None else None),
        "previous_version": current_version if current is not None else None,
        "new_score": new_score,
        "reason": reason,
    }
    (run_dir / "candidate_model.json").write_text(json.dumps(model, indent=2), encoding="utf-8")
    if promoted:
        PROMOTED_MODEL_PATH.write_text(json.dumps(model, indent=2), encoding="utf-8")
        meta = {k: model.get(k) for k in ["version", "created_at", "horizon_days", "samples", "positive_rate_pct", "validation", "test", "split", "promotion", "top_positive_features", "top_negative_features"]}
        PROMOTED_META_PATH.write_text(json.dumps(meta, indent=2), encoding="utf-8")
    return model["promotion"]


def train_vai2_from_lab_rows(rows: list[dict[str, Any]], horizon_days: int = 10, min_samples: int = 80, *, force_promote: bool = False, run_label: str | None = None) -> dict[str, Any]:
    rows = [dict(r) for r in rows or [] if not r.get("error")]
    rows = [r for r in rows if _direction(r) in {"LONG", "SHORT", "NEUTRAL"}]
    rows.sort(key=lambda row: (str(row.get("date") or ""), str(row.get("ticker") or "")))
    if len(rows) < int(min_samples or 80):
        return {"ok": False, "status": "not_enough_data", "samples": len(rows), "required": int(min_samples or 80), "terminal_output": f"VAI 2.2 TRAINING FAILED\nSamples: {len(rows)}\nRequired: {int(min_samples or 80)}"}

    metrics = [_quality_metrics(r) for r in rows]
    y_accept = np.asarray([m["good"] for m in metrics], dtype=float)
    if len(set(y_accept.tolist())) < 2:
        return {"ok": False, "status": "single_class", "samples": len(rows), "positive_rate_pct": round(float(y_accept.mean() * 100), 2), "terminal_output": "VAI 2.2 TRAINING FAILED\nLabels had only one class. Use more varied data."}

    # Dates, rather than individual rows, define the partitions.  A purge gap
    # equal to the outcome horizon prevents overlapping future windows from
    # leaking from train into validation or test.
    partition = chronological_split(rows, train_pct=0.65, validation_pct=0.15, purge_days=max(1, int(horizon_days or 10)))
    train_idx = np.asarray(partition["train"], dtype=int)
    validation_idx = np.asarray(partition["validation"], dtype=int)
    test_idx = np.asarray(partition["test"], dtype=int)
    if min(len(train_idx), len(validation_idx), len(test_idx)) < 8:
        return {
            "ok": False,
            "status": "insufficient_chronological_partitions",
            "samples": len(rows),
            "partition_sizes": {key: len(value) for key, value in partition.items()},
            "terminal_output": "VAI 2.2 TRAINING FAILED\nA date-based train/validation/test split with a purge gap needs more independent history.",
        }

    cats = _build_categories([rows[i] for i in train_idx])
    X = np.asarray([_vector(r, cats) for r in rows], dtype=float)
    ret = np.asarray([m["return"] for m in metrics], dtype=float)
    mfe = np.asarray([m["mfe"] for m in metrics], dtype=float)
    mae_abs = np.asarray([m["mae_abs"] for m in metrics], dtype=float)
    stop = np.asarray([1.0 if m["stop"] else 0.0 for m in metrics], dtype=float)
    target = np.asarray([1.0 if m["target"] else 0.0 for m in metrics], dtype=float)
    quality = np.asarray([m["quality"] for m in metrics], dtype=float)
    n = len(rows)
    X_train, X_val, X_test = X[train_idx], X[validation_idx], X[test_idx]
    ya_train = y_accept[train_idx]
    ret_train, ret_val, ret_test = ret[train_idx], ret[validation_idx], ret[test_idx]
    stop_train, stop_val, stop_test = stop[train_idx], stop[validation_idx], stop[test_idx]
    target_train = target[train_idx]
    quality_train = quality[train_idx]
    mfe_val, mae_val, target_val = mfe[validation_idx], mae_abs[validation_idx], target[validation_idx]
    mfe_test, mae_test, target_test = mfe[test_idx], mae_abs[test_idx], target[test_idx]

    mu = X_train.mean(axis=0)
    sigma = X_train.std(axis=0)
    sigma[sigma < 1e-6] = 1.0
    Xt = (X_train - mu) / sigma
    Xv = (X_val - mu) / sigma

    good_return = (ret_train > 0).astype(float)
    # Labels can shape training weights; a previous model's confidence cannot.
    sample_weight = 1.0 + target_train * 0.55 + good_return * np.clip(ret_train, 0, 8) * 0.10
    sample_weight += stop_train * 0.80 + (ret_train < 0).astype(float) * 0.70 + np.clip(-quality_train, 0, 5) * 0.22
    stop_weight = 1.0 + stop_train * 0.95

    accept_w, accept_b = _fit_logistic(Xt, ya_train, sample_weight, epochs=1200, lr=0.065, l2=0.0045)
    stop_w, stop_b = _fit_logistic(Xt, stop_train, stop_weight, epochs=900, lr=0.055, l2=0.0045)
    ret_w, ret_b = _fit_ridge(Xt, ret_train, l2=1.4)
    quality_w, quality_b = _fit_ridge(Xt, quality_train, l2=1.8)

    p_accept = np.asarray(_sigmoid(Xv @ accept_w + accept_b), dtype=float) if len(Xv) else np.array([])
    p_stop = np.asarray(_sigmoid(Xv @ stop_w + stop_b), dtype=float) if len(Xv) else np.array([])
    pred_ret = np.asarray(Xv @ ret_w + ret_b, dtype=float) if len(Xv) else np.array([])
    pred_quality = np.asarray(Xv @ quality_w + quality_b, dtype=float) if len(Xv) else np.array([])

    best = {"threshold": 0.60, "min_expected_return": 0.20, "max_stop_probability": 0.72, "min_decision_edge": 0.0, "promotion_score": -999.0}
    for th in np.arange(0.40, 0.86, 0.025):
        for min_er in [-0.05, 0.0, 0.15, 0.30, 0.50, 0.75, 1.00]:
            for max_sp in [0.50, 0.58, 0.64, 0.70, 0.76]:
                decision_edge = (
                    (p_accept - th) * 1.35
                    + (pred_ret - min_er) * 0.075
                    + (max_sp - p_stop) * 0.95
                    + pred_quality * 0.045
                )
                for min_edge in [-0.10, 0.0, 0.10, 0.20, 0.30]:
                    take = (p_accept >= th) & (pred_ret >= min_er) & (p_stop <= max_sp) & (pred_quality > -0.70) & (decision_edge >= min_edge)
                    if take.sum() < max(8, len(p_accept) * 0.035):
                        continue
                    summary = _summarize_taken(take, ret_val, mfe_val, mae_val, stop_val, target_val)
                    score = _promotion_score(summary)
                    if score > float(best.get("promotion_score") or -999):
                        best = dict(summary)
                        best.update({
                            "threshold": round(float(th), 3),
                            "min_expected_return": round(float(min_er), 3),
                            "max_stop_probability": round(float(max_sp), 3),
                            "min_decision_edge": round(float(min_edge), 3),
                            "promotion_score": score,
                        })

    if float(best.get("promotion_score") or -999) <= -998 or int(best.get("signals") or 0) <= 0:
        return {
            "ok": False,
            "status": "no_valid_threshold",
            "samples": int(n),
            "validation_samples": int(len(validation_idx)),
            "test_samples": int(len(test_idx)),
            "terminal_output": "VAI 2.2 TRAINING FAILED\nNo validation threshold produced enough clean signals. Use more samples, more tickers, or less restrictive data.",
        }

    feature_names = _feature_names(cats)
    combined = accept_w + 0.25 * ret_w - 0.45 * stop_w + 0.15 * quality_w
    weights = {name: float(combined[i]) for i, name in enumerate(feature_names)}
    top_positive = sorted(weights.items(), key=lambda kv: kv[1], reverse=True)[:18]
    top_negative = sorted(weights.items(), key=lambda kv: kv[1])[:18]

    run_id = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S") + ("_" + str(run_label).strip().replace(" ", "_")[:30] if run_label else "")
    run_dir = RUNS_DIR / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    model = {
        "version": "VAI 2.2 Chronological PIT Experimental",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "run_id": run_id,
        "horizon_days": int(horizon_days or 10),
        "feature_names": feature_names,
        "categorical_values": cats,
        "mu": mu.tolist(), "sigma": sigma.tolist(),
        "accept_weights": accept_w.tolist(), "accept_bias": float(accept_b),
        "stop_weights": stop_w.tolist(), "stop_bias": float(stop_b),
        "return_weights": ret_w.tolist(), "return_bias": float(ret_b),
        "quality_weights": quality_w.tolist(), "quality_bias": float(quality_b),
        "threshold": float(best.get("threshold") or 0.60),
        "min_expected_return": float(best.get("min_expected_return") or 0.20),
        "max_stop_probability": float(best.get("max_stop_probability") or 0.72),
        "min_decision_edge": float(best.get("min_decision_edge") or 0.0),
        "samples": int(n), "train_samples": int(len(train_idx)), "validation_samples": int(len(validation_idx)), "test_samples": int(len(test_idx)),
        "positive_rate_pct": round(float(y_accept.mean() * 100), 2),
        "validation": best,
        "test": {},
        "split": {"method": "chronological_dates_with_horizon_purge", "purge_days": max(1, int(horizon_days or 10)), "partition_sizes": {key: len(value) for key, value in partition.items()}},
        "top_positive_features": top_positive,
        "top_negative_features": top_negative,
        "training_policy": "VAI2.2 fits features and category vocabularies on the train dates only, selects thresholds on a chronologically later validation window, and promotes only from untouched test performance. A purge gap equal to the outcome horizon separates each partition. Scanner confidence, ticker identity, and AI explanation text are excluded from numeric prediction.",
    }
    p_accept_test = np.asarray(_sigmoid(X_test @ accept_w + accept_b), dtype=float)
    p_stop_test = np.asarray(_sigmoid(X_test @ stop_w + stop_b), dtype=float)
    pred_ret_test = np.asarray(X_test @ ret_w + ret_b, dtype=float)
    pred_quality_test = np.asarray(X_test @ quality_w + quality_b, dtype=float)
    decision_edge_test = (p_accept_test - model["threshold"]) * 1.35 + (pred_ret_test - model["min_expected_return"]) * 0.075 + (model["max_stop_probability"] - p_stop_test) * 0.95 + pred_quality_test * 0.045
    test_take = (p_accept_test >= model["threshold"]) & (pred_ret_test >= model["min_expected_return"]) & (p_stop_test <= model["max_stop_probability"]) & (pred_quality_test > -0.70) & (decision_edge_test >= model["min_decision_edge"])
    test_summary = _summarize_taken(test_take, ret_test, mfe_test, mae_test, stop_test, target_test)
    test_summary["promotion_score"] = _promotion_score(test_summary)
    model["test"] = test_summary
    promotion = _promote_if_better(model, run_dir, force_promote=force_promote)
    terminal = _training_terminal(model, promotion)
    (run_dir / "training_report.txt").write_text(terminal, encoding="utf-8")
    return {"ok": True, "status": "promoted" if promotion.get("promoted") else "trained_rejected", "model": model, "promotion": promotion, "terminal_output": terminal, "run_dir": str(run_dir)}


def _training_terminal(model: dict[str, Any], promotion: dict[str, Any] | None = None) -> str:
    val = model.get("validation") or {}
    test = model.get("test") or {}
    promo = promotion or model.get("promotion") or {}
    lines = [
        "ORYNTRA VAI 2.2 CHRONOLOGICAL PIT EXPERIMENTAL — TRAINING REPORT",
        "=" * 62,
        f"Created: {model.get('created_at')}",
        f"Run ID: {model.get('run_id')}",
        f"Samples: {model.get('samples')}  Train: {model.get('train_samples')}  Validation: {model.get('validation_samples')}  Test: {model.get('test_samples')}",
        f"Positive label rate: {model.get('positive_rate_pct')}%",
        f"Horizon: {model.get('horizon_days')} trading days",
        "",
        "VALIDATION (THRESHOLD SELECTION)",
        f"Threshold: {val.get('threshold')}",
        f"Min expected return: {val.get('min_expected_return')}%",
        f"Max stop probability: {val.get('max_stop_probability')}",
        f"Min decision edge: {val.get('min_decision_edge')}",
        f"Coverage: {val.get('coverage_pct')}%",
        f"Signals: {val.get('signals')}",
        f"Win rate: {val.get('win_rate_pct')}%",
        f"Avg return: {val.get('avg_return_pct')}%",
        f"Avg MFE / MAE: {val.get('avg_mfe_pct')}% / {val.get('avg_mae_pct')}%",
        f"MFE/MAE: {val.get('reward_risk_ratio')}",
        f"Target hit: {val.get('target_hit_rate_pct')}%",
        f"Stop hit: {val.get('stop_hit_rate_pct')}%",
        f"Validation score: {val.get('promotion_score')}",
        "",
        "UNTOUCHED TEST / PROMOTION",
        f"Promoted: {promo.get('promoted')}",
        f"Reason: {promo.get('reason')}",
        f"Previous test score: {promo.get('previous_score')}",
        f"New test score: {promo.get('new_score')}",
        f"Test signals: {test.get('signals')}  Coverage: {test.get('coverage_pct')}%  Win rate: {test.get('win_rate_pct')}%",
        f"Test avg return: {test.get('avg_return_pct')}%  Stop hit: {test.get('stop_hit_rate_pct')}%  Score: {test.get('promotion_score')}",
        "",
        "TOP POSITIVE FEATURES",
    ]
    for name, weight in model.get("top_positive_features", [])[:14]:
        lines.append(f"  + {name}: {float(weight):.4f}")
    lines.append("")
    lines.append("TOP NEGATIVE FEATURES")
    for name, weight in model.get("top_negative_features", [])[:14]:
        lines.append(f"  - {name}: {float(weight):.4f}")
    lines.extend([
        "", "NOTES",
        "- VAI2.2 uses an untouched chronological test partition for promotion, not win rate alone.",
        "- Scanner confidence, ticker identity, and AI explanation text cannot enter the numeric predictor.",
        "- Repeated training does not blindly overwrite the promoted model.",
        "- Rejected candidates are saved under data/models/vai2/runs/.",
        "- Educational only; not financial advice.",
    ])
    return "\n".join(lines)


def load_vai2_model() -> dict[str, Any] | None:
    return _load_promoted_model()


def get_vai2_model_status() -> dict[str, Any]:
    model = load_vai2_model()
    runs = []
    try:
        for p in sorted(RUNS_DIR.glob("*/candidate_model.json"), key=lambda x: x.stat().st_mtime, reverse=True)[:10]:
            m = json.loads(p.read_text(encoding="utf-8"))
            runs.append({
                "run_id": m.get("run_id"), "created_at": m.get("created_at"),
                "status": "promoted" if ((m.get("promotion") or {}).get("promoted")) else "candidate",
                "samples": m.get("samples"),
                "promotion_score": ((m.get("test") or {}).get("promotion_score")),
                "avg_return_pct": ((m.get("test") or {}).get("avg_return_pct")),
                "stop_hit_rate_pct": ((m.get("test") or {}).get("stop_hit_rate_pct")),
            })
    except Exception:
        runs = []
    if not model:
        return {"trained": False, "model_path": str(PROMOTED_MODEL_PATH), "message": "No promoted VAI 2.2 model trained yet.", "recent_runs": runs}
    return {
        "trained": True, "model_path": str(PROMOTED_MODEL_PATH),
        "version": model.get("version"), "created_at": model.get("created_at"), "run_id": model.get("run_id"),
        "samples": model.get("samples"), "positive_rate_pct": model.get("positive_rate_pct"), "horizon_days": model.get("horizon_days"),
        "threshold": model.get("threshold"), "min_expected_return": model.get("min_expected_return"), "max_stop_probability": model.get("max_stop_probability"), "min_decision_edge": model.get("min_decision_edge"),
        "validation": model.get("validation"), "test": model.get("test"), "split": model.get("split"), "promotion": model.get("promotion"),
        "top_positive_features": model.get("top_positive_features", [])[:12],
        "top_negative_features": model.get("top_negative_features", [])[:12],
        "recent_runs": runs,
    }


def predict_vai2_setup(ind: dict[str, Any], setup: dict[str, Any], patterns: dict[str, Any]) -> dict[str, Any]:
    model = load_vai2_model()
    if not model:
        return {"trained": False, "probability": None, "threshold": None, "decision": "FALLBACK_V7", "message": "No promoted VAI 2.2 model yet; using V7 fallback."}
    cats = model.get("categorical_values") or {}
    x = np.asarray(_vector_current(ind or {}, setup or {}, patterns or {}, cats), dtype=float)
    mu = np.asarray(model.get("mu") or [0.0] * len(x), dtype=float)
    sigma = np.asarray(model.get("sigma") or [1.0] * len(x), dtype=float)
    aw = np.asarray(model.get("accept_weights") or [0.0] * len(x), dtype=float)
    sw = np.asarray(model.get("stop_weights") or [0.0] * len(x), dtype=float)
    rw = np.asarray(model.get("return_weights") or [0.0] * len(x), dtype=float)
    qw = np.asarray(model.get("quality_weights") or [0.0] * len(x), dtype=float)
    if not (len(mu) == len(sigma) == len(aw) == len(sw) == len(rw) == len(qw) == len(x)):
        return {"trained": False, "probability": None, "threshold": None, "decision": "MODEL_FEATURE_MISMATCH", "message": "VAI2.2 model feature size mismatch; retrain."}
    xs = (x - mu) / np.where(sigma == 0, 1.0, sigma)
    prob = float(_sigmoid(xs @ aw + float(model.get("accept_bias") or 0.0)))
    stop_prob = float(_sigmoid(xs @ sw + float(model.get("stop_bias") or 0.0)))
    expected_return = float(xs @ rw + float(model.get("return_bias") or 0.0))
    quality_score = float(xs @ qw + float(model.get("quality_bias") or 0.0))
    threshold = float(model.get("threshold") or 0.60)
    min_er = float(model.get("min_expected_return") or 0.20)
    max_stop = float(model.get("max_stop_probability") or 0.72)
    min_edge = float(model.get("min_decision_edge") or ((model.get("validation") or {}).get("min_decision_edge") or 0.0))
    direction = _direction(setup or {})
    decision_edge = (
        (prob - threshold) * 1.35
        + (expected_return - min_er) * 0.075
        + (max_stop - stop_prob) * 0.95
        + quality_score * 0.045
    )
    decision = "TRADE" if (
        direction == "LONG"
        and prob >= threshold
        and expected_return >= min_er
        and stop_prob <= max_stop
        and quality_score > -0.70
        and decision_edge >= min_edge
    ) else "NO_TRADE"
    grade_score = prob * 100 + expected_return * 5.5 + quality_score * 4 - stop_prob * 28 + decision_edge * 18
    if grade_score >= 82:
        grade = "A+"
    elif grade_score >= 76:
        grade = "A"
    elif grade_score >= 70:
        grade = "B+"
    elif grade_score >= 64:
        grade = "B"
    elif grade_score >= 58:
        grade = "C"
    elif grade_score >= 50:
        grade = "D"
    else:
        grade = "F"
    if decision == "TRADE":
        size_pct = 0.35 + max(0.0, prob - threshold) * 2.2 + max(0.0, expected_return - min_er) * 0.18 + max(0.0, max_stop - stop_prob) * 0.80 + max(0.0, decision_edge) * 0.75
        if grade in {"A+", "A"}:
            size_pct += 0.25
        suggested_size = round(float(max(0.25, min(3.0, size_pct))), 2)
    else:
        suggested_size = 0.0

    return {
        "trained": True, "probability": round(prob * 100, 2), "probability_raw": prob,
        "threshold": round(threshold * 100, 2), "decision": decision,
        "expected_return_pct": round(expected_return, 3),
        "stop_probability_pct": round(stop_prob * 100, 2), "max_stop_probability_pct": round(max_stop * 100, 2),
        "quality_score": round(quality_score, 3), "decision_edge": round(decision_edge, 4), "min_decision_edge": round(min_edge, 4),
        "suggested_position_size_pct": suggested_size,
        "grade": grade,
        "model_created_at": model.get("created_at"), "run_id": model.get("run_id"), "samples": model.get("samples"),
        "regime": _regime_from_ind(ind or {}), "top_pattern": _top_pattern(patterns),
    }
