from __future__ import annotations

import json
import math
import os
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np

APP_DIR = Path(__file__).resolve().parents[1]
MODEL_DIR = APP_DIR / "data" / "models"
MODEL_PATH = MODEL_DIR / "vai_1_0_model.json"
META_PATH = MODEL_DIR / "vai_1_0_metadata.json"

NUMERIC_FEATURES = [
    "confidence", "rsi14", "adx14", "di_spread", "vol_ratio", "atr_pct",
    "momentum_5d", "momentum_20d", "momentum_60d", "above_ma20", "above_ma50", "above_ma200",
    "ma_stack", "target_hit_rate_hint", "stop_hit_rate_hint"
]
CATEGORICAL_FIELDS = ["ticker", "regime", "top_pattern", "setup_type"]


def _f(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except Exception:
        return default


def _sigmoid(x: np.ndarray | float) -> np.ndarray | float:
    return 1.0 / (1.0 + np.exp(-np.clip(x, -40, 40)))


def _safe_upper(value: Any, default: str = "UNKNOWN") -> str:
    text = str(value or default).strip().upper().replace(" ", "_")
    return text or default


def _grade(prob: float, expected_return: float, stop_risk: float) -> str:
    score = prob * 100 + expected_return * 4 - max(0.0, stop_risk - 55.0) * 0.35
    if score >= 82: return "A+"
    if score >= 76: return "A"
    if score >= 70: return "B+"
    if score >= 64: return "B"
    if score >= 58: return "C"
    if score >= 52: return "D"
    return "F"


def _row_numeric(row: dict[str, Any]) -> dict[str, float]:
    above20 = _f(row.get("above_ma20"))
    above50 = _f(row.get("above_ma50"))
    above200 = _f(row.get("above_ma200"))
    return {
        "confidence": _f(row.get("confidence"), 50.0) / 100.0,
        "rsi14": (_f(row.get("rsi14"), 50.0) - 50.0) / 50.0,
        "adx14": _f(row.get("adx14"), 0.0) / 60.0,
        "di_spread": (_f(row.get("di_plus")) - _f(row.get("di_minus"))) / 60.0,
        "vol_ratio": min(4.0, _f(row.get("vol_ratio"), 1.0)) / 4.0,
        "atr_pct": min(15.0, _f(row.get("atr_pct"), 3.0)) / 15.0,
        "momentum_5d": np.clip(_f(row.get("momentum_5d")) / 20.0, -2, 2),
        "momentum_20d": np.clip(_f(row.get("momentum_20d")) / 40.0, -2, 2),
        "momentum_60d": np.clip(_f(row.get("momentum_60d")) / 80.0, -2, 2),
        "above_ma20": 1.0 if above20 else 0.0,
        "above_ma50": 1.0 if above50 else 0.0,
        "above_ma200": 1.0 if above200 else 0.0,
        "ma_stack": (above20 + above50 + above200) / 3.0,
        "target_hit_rate_hint": 0.0,
        "stop_hit_rate_hint": 0.0,
    }


def _current_numeric(ind: dict[str, Any], setup: dict[str, Any]) -> dict[str, float]:
    row = dict(ind or {})
    row["confidence"] = setup.get("score") or setup.get("confidence") or 50
    return _row_numeric(row)


def _top_pattern(patterns: dict[str, Any] | None) -> str:
    try:
        adv = (patterns or {}).get("advanced_patterns") or {}
        top = adv.get("top_pattern") or {}
        return _safe_upper(top.get("pattern_name") or "UNKNOWN")
    except Exception:
        return "UNKNOWN"


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


def _setup_type(setup: dict[str, Any]) -> str:
    return _safe_upper(setup.get("setup_type") or setup.get("type") or "UNKNOWN")


def _direction(setup: dict[str, Any]) -> str:
    d = _safe_upper(setup.get("direction"), "NEUTRAL")
    if d in {"BULLISH", "BUY"}: return "LONG"
    if d in {"BEARISH", "SELL"}: return "SHORT"
    return d


def _build_categories(rows: list[dict[str, Any]], max_per_field: int = 80) -> dict[str, list[str]]:
    cats: dict[str, list[str]] = {}
    for field in CATEGORICAL_FIELDS:
        counts: dict[str, int] = {}
        for r in rows:
            value = _safe_upper(r.get(field))
            counts[value] = counts.get(value, 0) + 1
        cats[field] = [k for k, _ in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:max_per_field]]
    return cats


def _vector(row: dict[str, Any], cats: dict[str, list[str]]) -> list[float]:
    vals = _row_numeric(row)
    out = [float(vals[name]) for name in NUMERIC_FEATURES]
    for field in CATEGORICAL_FIELDS:
        value = _safe_upper(row.get(field))
        allowed = cats.get(field) or []
        out.extend([1.0 if value == c else 0.0 for c in allowed])
    return out


def _vector_current(ind: dict[str, Any], setup: dict[str, Any], patterns: dict[str, Any], cats: dict[str, list[str]]) -> list[float]:
    vals = _current_numeric(ind, setup)
    row = {name: vals[name] for name in NUMERIC_FEATURES}
    row["ticker"] = _safe_upper(ind.get("ticker") or setup.get("ticker") or "UNKNOWN")
    row["regime"] = _regime_from_ind(ind)
    row["top_pattern"] = _top_pattern(patterns)
    row["setup_type"] = _setup_type(setup)
    out = [float(vals[name]) for name in NUMERIC_FEATURES]
    for field in CATEGORICAL_FIELDS:
        value = _safe_upper(row.get(field))
        allowed = cats.get(field) or []
        out.extend([1.0 if value == c else 0.0 for c in allowed])
    return out


def _feature_names(cats: dict[str, list[str]]) -> list[str]:
    names = list(NUMERIC_FEATURES)
    for field in CATEGORICAL_FIELDS:
        names.extend([f"{field}={c}" for c in cats.get(field, [])])
    return names


def _good_label(row: dict[str, Any]) -> int:
    if not row.get("actionable"):
        return 0
    if _safe_upper(row.get("direction")) != "LONG":
        return 0
    ret = _f(row.get("return_pct"))
    mfe = _f(row.get("mfe_pct"))
    mae = abs(_f(row.get("mae_pct")))
    stop = bool(row.get("stop_hit"))
    target = bool(row.get("target_hit"))
    rr_ok = (mfe / max(mae, 0.01)) >= 0.95
    return 1 if (ret > 0.20 and (target or rr_ok) and not (stop and ret < 0.75)) else 0


def train_vai_from_lab_rows(rows: list[dict[str, Any]], horizon_days: int = 10, min_samples: int = 40) -> dict[str, Any]:
    rows = [dict(r) for r in rows or [] if not r.get("error")]
    rows = [r for r in rows if _safe_upper(r.get("direction")) in {"LONG", "SHORT", "NEUTRAL"}]
    if len(rows) < min_samples:
        return {
            "ok": False,
            "status": "not_enough_data",
            "samples": len(rows),
            "required": min_samples,
            "terminal_output": f"VAI 1.0 TRAINING FAILED\nSamples: {len(rows)}\nRequired: {min_samples}\nRun a larger Pattern Lab test first."
        }

    cats = _build_categories(rows)
    X = np.asarray([_vector(r, cats) for r in rows], dtype=float)
    y = np.asarray([_good_label(r) for r in rows], dtype=float)
    returns = np.asarray([_f(r.get("return_pct")) for r in rows], dtype=float)
    stop_hits = np.asarray([1.0 if r.get("stop_hit") else 0.0 for r in rows], dtype=float)

    if len(set(y.tolist())) < 2:
        return {
            "ok": False,
            "status": "single_class",
            "samples": len(rows),
            "positive_rate_pct": round(float(y.mean() * 100), 2),
            "terminal_output": "VAI 1.0 TRAINING FAILED\nTraining labels had only one class. Use more varied data."
        }

    n = len(rows)
    split = max(1, int(n * 0.75))
    X_train, X_val = X[:split], X[split:]
    y_train, y_val = y[:split], y[split:]
    ret_val = returns[split:]
    stop_val = stop_hits[split:]

    mu = X_train.mean(axis=0)
    sigma = X_train.std(axis=0)
    sigma[sigma < 1e-6] = 1.0
    Xt = (X_train - mu) / sigma
    Xv = (X_val - mu) / sigma

    w = np.zeros(Xt.shape[1], dtype=float)
    b = 0.0
    lr = 0.08
    l2 = 0.002
    epochs = 900
    for _ in range(epochs):
        z = Xt @ w + b
        p = _sigmoid(z)
        grad_w = (Xt.T @ (p - y_train)) / len(Xt) + l2 * w
        grad_b = float((p - y_train).mean())
        w -= lr * grad_w
        b -= lr * grad_b

    pv = np.asarray(_sigmoid(Xv @ w + b), dtype=float) if len(Xv) else np.array([])
    best = {"threshold": 0.58, "score": -999, "coverage": 0, "win_rate_pct": 0, "avg_return_pct": 0, "stop_hit_rate_pct": 0}
    for th in np.arange(0.45, 0.81, 0.025):
        take = pv >= th
        if take.sum() < max(5, len(pv) * 0.08):
            continue
        avg_ret = float(ret_val[take].mean()) if take.any() else 0.0
        win = float((ret_val[take] > 0).mean() * 100) if take.any() else 0.0
        stop = float(stop_val[take].mean() * 100) if take.any() else 0.0
        cov = float(take.mean() * 100) if len(take) else 0.0
        score = avg_ret * 12 + win * 0.35 - stop * 0.18 - max(0, cov - 45) * 0.12
        if score > best["score"]:
            best = {"threshold": round(float(th), 3), "score": round(float(score), 3), "coverage": round(cov, 2), "win_rate_pct": round(win, 2), "avg_return_pct": round(avg_ret, 3), "stop_hit_rate_pct": round(stop, 2)}

    feature_names = _feature_names(cats)
    weights = {name: float(w[i]) for i, name in enumerate(feature_names)}
    top_positive = sorted(weights.items(), key=lambda kv: kv[1], reverse=True)[:15]
    top_negative = sorted(weights.items(), key=lambda kv: kv[1])[:15]

    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    model = {
        "version": "VAI 1.0 Experimental",
        "created_at": datetime.utcnow().isoformat(),
        "horizon_days": int(horizon_days or 10),
        "feature_names": feature_names,
        "categorical_values": cats,
        "mu": mu.tolist(),
        "sigma": sigma.tolist(),
        "weights": w.tolist(),
        "bias": float(b),
        "threshold": float(best["threshold"]),
        "samples": int(n),
        "positive_rate_pct": round(float(y.mean() * 100), 2),
        "validation": best,
        "top_positive_features": top_positive,
        "top_negative_features": top_negative,
    }
    MODEL_PATH.write_text(json.dumps(model, indent=2), encoding="utf-8")
    META_PATH.write_text(json.dumps({k: model[k] for k in ["version", "created_at", "horizon_days", "samples", "positive_rate_pct", "validation", "top_positive_features", "top_negative_features"]}, indent=2), encoding="utf-8")

    terminal = _training_terminal(model)
    return {"ok": True, "status": "trained", "model": model, "terminal_output": terminal}


def _training_terminal(model: dict[str, Any]) -> str:
    val = model.get("validation") or {}
    lines = [
        "ORYNTRA VAI 1.0 EXPERIMENTAL — TRAINING REPORT",
        "=" * 58,
        f"Created: {model.get('created_at')}",
        f"Samples: {model.get('samples')}",
        f"Positive label rate: {model.get('positive_rate_pct')}%",
        f"Horizon: {model.get('horizon_days')} trading days",
        "",
        "VALIDATION SELECTION",
        f"Threshold: {val.get('threshold')}",
        f"Coverage: {val.get('coverage')}%",
        f"Win rate: {val.get('win_rate_pct')}%",
        f"Avg return: {val.get('avg_return_pct')}%",
        f"Stop hit: {val.get('stop_hit_rate_pct')}%",
        "",
        "TOP POSITIVE FEATURES",
    ]
    for name, weight in model.get("top_positive_features", [])[:12]:
        lines.append(f"  + {name}: {weight:.4f}")
    lines.append("")
    lines.append("TOP NEGATIVE FEATURES")
    for name, weight in model.get("top_negative_features", [])[:12]:
        lines.append(f"  - {name}: {weight:.4f}")
    lines.extend([
        "",
        "NOTES",
        "- Experimental model. Do not use as financial advice.",
        "- Train only on clean, warmed cache data.",
        "- Validate against V7 Official and Always Long before trusting.",
    ])
    return "\n".join(lines)


def load_vai_model() -> dict[str, Any] | None:
    try:
        if not MODEL_PATH.exists():
            return None
        return json.loads(MODEL_PATH.read_text(encoding="utf-8"))
    except Exception:
        return None


def get_vai_model_status() -> dict[str, Any]:
    model = load_vai_model()
    if not model:
        return {"trained": False, "model_path": str(MODEL_PATH), "message": "No VAI 1.0 model trained yet."}
    return {
        "trained": True,
        "model_path": str(MODEL_PATH),
        "version": model.get("version"),
        "created_at": model.get("created_at"),
        "samples": model.get("samples"),
        "positive_rate_pct": model.get("positive_rate_pct"),
        "horizon_days": model.get("horizon_days"),
        "threshold": model.get("threshold"),
        "validation": model.get("validation"),
        "top_positive_features": model.get("top_positive_features", [])[:10],
        "top_negative_features": model.get("top_negative_features", [])[:10],
    }


def predict_vai_setup(ind: dict[str, Any], setup: dict[str, Any], patterns: dict[str, Any]) -> dict[str, Any]:
    model = load_vai_model()
    if not model:
        return {"trained": False, "probability": None, "threshold": None, "decision": "FALLBACK_V7", "message": "No trained VAI model yet; using V7 fallback."}
    cats = model.get("categorical_values") or {}
    x = np.asarray(_vector_current(ind or {}, setup or {}, patterns or {}, cats), dtype=float)
    mu = np.asarray(model.get("mu") or [0.0] * len(x), dtype=float)
    sigma = np.asarray(model.get("sigma") or [1.0] * len(x), dtype=float)
    w = np.asarray(model.get("weights") or [0.0] * len(x), dtype=float)
    if len(mu) != len(x) or len(sigma) != len(x) or len(w) != len(x):
        return {"trained": False, "probability": None, "threshold": None, "decision": "MODEL_FEATURE_MISMATCH", "message": "VAI model feature size mismatch; retrain model."}
    z = ((x - mu) / np.where(sigma == 0, 1.0, sigma)) @ w + float(model.get("bias") or 0.0)
    prob = float(_sigmoid(z))
    threshold = float(model.get("threshold") or 0.58)
    direction = _direction(setup or {})
    decision = "TRADE" if (direction == "LONG" and prob >= threshold) else "NO_TRADE"
    val = model.get("validation") or {}
    expected_return = float(val.get("avg_return_pct") or 0.0) * (prob / max(threshold, 0.01))
    stop_risk = float(val.get("stop_hit_rate_pct") or 0.0)
    return {
        "trained": True,
        "probability": round(prob * 100, 2),
        "probability_raw": prob,
        "threshold": round(threshold * 100, 2),
        "decision": decision,
        "expected_return_pct": round(expected_return, 3),
        "validation_stop_hit_pct": stop_risk,
        "grade": _grade(prob, expected_return, stop_risk),
        "model_created_at": model.get("created_at"),
        "samples": model.get("samples"),
        "regime": _regime_from_ind(ind or {}),
        "top_pattern": _top_pattern(patterns),
    }

