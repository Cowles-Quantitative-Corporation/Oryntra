"""Persistent non-secret preferences for the private Oryntra Control runtime."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

_DEFAULTS: dict[str, Any] = {
    "provider": "auto", "automation_enabled": True, "automation_hour_et": 18,
    "automation_interval_seconds": 3600, "default_alpha_model": "tba8",
    "default_construction": "phase15", "default_risk_model": "v203",
    "default_phase3_enabled": True, "default_model_benchmark": "SPY/QQQ",
    "default_comparison_benchmarks": ["SPY", "QQQ", "SPY/QQQ"],
}


def settings_path() -> Path:
    configured = os.getenv("ORYNTRA_CONTROL_SETTINGS_PATH", "").strip()
    return Path(configured).expanduser() if configured else Path.home() / "Library" / "Application Support" / "Oryntra Control" / "settings.json"


def _validated(payload: dict[str, Any]) -> dict[str, Any]:
    provider = str(payload.get("provider") or "auto").lower()
    if provider not in {"auto", "cache_only", "polygon", "twelvedata", "yfinance"}:
        provider = "auto"
    comparisons = [str(value).strip() for value in payload.get("default_comparison_benchmarks") or [] if str(value).strip()][:12]
    return {
        "provider": provider,
        "automation_enabled": bool(payload.get("automation_enabled", True)),
        "automation_hour_et": max(0, min(23, int(payload.get("automation_hour_et", 18)))),
        "automation_interval_seconds": max(300, min(86400, int(payload.get("automation_interval_seconds", 3600)))),
        "default_alpha_model": str(payload.get("default_alpha_model") or "tba8").lower(),
        "default_construction": str(payload.get("default_construction") or "phase15").lower(),
        "default_risk_model": str(payload.get("default_risk_model") or "v203").lower(),
        "default_phase3_enabled": bool(payload.get("default_phase3_enabled", True)),
        "default_model_benchmark": str(payload.get("default_model_benchmark") or "SPY/QQQ").strip(),
        "default_comparison_benchmarks": comparisons or ["SPY", "QQQ", "SPY/QQQ"],
    }


def load_settings() -> dict[str, Any]:
    value = dict(_DEFAULTS)
    try:
        stored = json.loads(settings_path().read_text(encoding="utf-8"))
        if isinstance(stored, dict):
            value.update(stored)
    except (OSError, ValueError, TypeError):
        pass
    return _validated(value)


def apply_runtime_settings(settings: dict[str, Any] | None = None) -> dict[str, Any]:
    clean = _validated(settings or load_settings())
    os.environ["ORYNTRA_PHASE4_AUTO_CYCLE"] = "true" if clean["automation_enabled"] else "false"
    os.environ["ORYNTRA_PHASE4_AUTO_HOUR_ET"] = str(clean["automation_hour_et"])
    os.environ["ORYNTRA_PHASE4_AUTO_INTERVAL_SECONDS"] = str(clean["automation_interval_seconds"])
    os.environ["ORYNTRA_CONTROL_PROVIDER"] = clean["provider"]
    return clean


def save_settings(changes: dict[str, Any]) -> dict[str, Any]:
    value = load_settings()
    value.update({key: item for key, item in changes.items() if item is not None})
    clean = _validated(value)
    path = settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(clean, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    try:
        os.chmod(temporary, 0o600)
    except OSError:
        pass
    temporary.replace(path)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return apply_runtime_settings(clean)
