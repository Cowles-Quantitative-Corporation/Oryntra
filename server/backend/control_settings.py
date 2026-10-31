"""Persistent local settings for the Oryntra Control desktop runtime.

The settings file stores non-secret operator preferences only. Market-data API
keys remain encrypted in SQLite through provider_credentials.py.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

_DEFAULTS: dict[str, Any] = {
    "provider": "auto",
    "automation_enabled": True,
    "automation_hour_et": 18,
    "automation_interval_seconds": 3600,
    "default_alpha_model": "tba8",
    "default_construction": "phase15",
    "default_risk_model": "v203",
    "default_phase3_enabled": True,
    "default_model_benchmark": "SPY/QQQ/IWM/VXUS",
    "default_comparison_benchmarks": ["SPY", "QQQ", "IWM", "VXUS", "SPY/QQQ/IWM/VXUS"],
}


def settings_path() -> Path:
    raw = os.getenv("ORYNTRA_CONTROL_SETTINGS_PATH", "").strip()
    if raw:
        return Path(raw).expanduser()
    return Path.home() / "Library" / "Application Support" / "Oryntra Control" / "settings.json"


def load_settings() -> dict[str, Any]:
    out = dict(_DEFAULTS)
    path = settings_path()
    if path.is_file():
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(payload, dict):
                out.update(payload)
                # 5.1.2 migration: move only untouched legacy Control defaults to
                # the broader operator-requested benchmark. Explicit custom
                # benchmark settings are preserved exactly as entered.
                if payload.get("default_model_benchmark") == "SPY/QQQ":
                    out["default_model_benchmark"] = "SPY/QQQ/IWM/VXUS"
                if payload.get("default_comparison_benchmarks") == ["SPY", "QQQ", "SPY/QQQ"]:
                    out["default_comparison_benchmarks"] = ["SPY", "QQQ", "IWM", "VXUS", "SPY/QQQ/IWM/VXUS"]
        except Exception:
            pass
    return _validated(out)


def _validated(payload: dict[str, Any]) -> dict[str, Any]:
    provider = str(payload.get("provider") or "auto").strip().lower()
    if provider not in {"auto", "cache_only", "polygon", "twelvedata", "yfinance"}:
        provider = "auto"
    hour = max(0, min(23, int(payload.get("automation_hour_et", 18))))
    interval = max(300, min(86400, int(payload.get("automation_interval_seconds", 3600))))
    alpha = str(payload.get("default_alpha_model") or "tba8").strip().lower()
    construction = str(payload.get("default_construction") or "phase15").strip().lower()
    risk = str(payload.get("default_risk_model") or "v203").strip().lower()
    comparisons = [str(v).strip() for v in (payload.get("default_comparison_benchmarks") or []) if str(v).strip()][:12]
    if not comparisons:
        comparisons = ["SPY", "QQQ", "IWM", "VXUS", "SPY/QQQ/IWM/VXUS"]
    return {
        "provider": provider,
        "automation_enabled": bool(payload.get("automation_enabled", True)),
        "automation_hour_et": hour,
        "automation_interval_seconds": interval,
        "default_alpha_model": alpha,
        "default_construction": construction,
        "default_risk_model": risk,
        "default_phase3_enabled": bool(payload.get("default_phase3_enabled", True)),
        "default_model_benchmark": str(payload.get("default_model_benchmark") or "SPY/QQQ/IWM/VXUS").strip(),
        "default_comparison_benchmarks": comparisons,
    }


def save_settings(changes: dict[str, Any]) -> dict[str, Any]:
    current = load_settings()
    current.update({key: value for key, value in changes.items() if value is not None})
    clean = _validated(current)
    path = settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(clean, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    try:
        os.chmod(temp, 0o600)
    except OSError:
        pass
    temp.replace(path)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    apply_runtime_settings(clean)
    return clean


def apply_runtime_settings(settings: dict[str, Any] | None = None) -> dict[str, Any]:
    clean = _validated(settings or load_settings())
    os.environ["ORYNTRA_PHASE4_AUTO_CYCLE"] = "true" if clean["automation_enabled"] else "false"
    os.environ["ORYNTRA_PHASE4_AUTO_HOUR_ET"] = str(clean["automation_hour_et"])
    os.environ["ORYNTRA_PHASE4_AUTO_INTERVAL_SECONDS"] = str(clean["automation_interval_seconds"])
    os.environ["ORYNTRA_CONTROL_PROVIDER"] = clean["provider"]
    return clean
