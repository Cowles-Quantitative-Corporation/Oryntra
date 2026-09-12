"""Shared research-scope and provenance records for Oryntra model outputs.

These records describe what a result used and what it did *not* test.  They are
metadata only: they do not change a scanner score, a trained-model decision, or
a portfolio allocation.
"""
from __future__ import annotations

from typing import Any

import pandas as pd

from .research_experiments import fingerprint


_MODEL_DETAILS = {
    "universal_v2": ("Universal V2", "shared_causal_signal_engine", "research"),
    "official": ("V7 Official Momentum", "deterministic_scanner", "experimental"),
    "v8": ("V8 Evidence", "deterministic_scanner", "research"),
    "vai": ("VAI 1.0", "trained_scanner_overlay", "experimental"),
    "vai2": ("VAI 2.2", "chronological_pit_trained_scanner_overlay", "experimental"),
    "quant": ("Quant Lab", "portfolio_research_simulator", "research"),
}


def scanner_research_governance(mode: str, history: pd.DataFrame) -> dict[str, Any]:
    """Return a comparable research contract for any scanner engine result."""
    clean_mode = str(mode or "official").lower()
    name, family, maturity = _MODEL_DETAILS.get(
        clean_mode, (clean_mode.upper(), "deterministic_scanner", "experimental")
    )
    dates = history.index if isinstance(history.index, pd.DatetimeIndex) else pd.DatetimeIndex([])
    digest_rows = [
        {
            "date": str(index.date()) if hasattr(index, "date") else str(index),
            "close": _finite_or_none(row.get("Close")),
            "volume": _finite_or_none(row.get("Volume")),
        }
        for index, row in history[[column for column in ("Close", "Volume") if column in history.columns]].iterrows()
    ]
    validation = (
        "Training uses chronological partitions and an outcome-horizon purge; promotion is based on an untouched test partition."
        if clean_mode == "vai2"
        else "Evaluate on later dates and symbols that were not used to tune the rules or model."
    )
    return {
        "schema_version": "research-governance-v1",
        "model": {"id": clean_mode, "name": name, "family": family, "maturity": maturity},
        "data_lineage": {
            "bar_frequency": "daily",
            "observations": int(len(history)),
            "first_session": str(dates.min().date()) if len(dates) else None,
            "last_session": str(dates.max().date()) if len(dates) else None,
            "fingerprint": fingerprint(digest_rows),
        },
        "validation_requirement": validation,
        "portfolio_scope": "not_applicable_single_symbol_scanner",
        "execution_scope": "No fills, borrowing, market impact, liquidity capacity, or correlation-shock simulation is implied by this scanner result.",
        "research_boundary": "Educational research output only; it is not investment advice or an instruction to trade.",
    }


def quant_research_governance(config: dict[str, Any], universe: dict[str, Any]) -> dict[str, Any]:
    """Return the corresponding contract for a multi-asset Quant Lab report."""
    name, family, maturity = _MODEL_DETAILS["quant"]
    return {
        "schema_version": "research-governance-v1",
        "model": {"id": str(config.get("model") or "quant"), "name": name, "family": family, "maturity": maturity},
        "data_lineage": {
            "bar_frequency": "daily",
            "symbols": list(universe.get("symbols") or []),
            "first_session": universe.get("start"),
            "last_session": universe.get("end"),
            "observations": universe.get("sessions"),
            "fingerprint": fingerprint({"config": config, "universe": universe}),
        },
        "validation_requirement": "Treat walk-forward and chronological holdout reports as diagnostics, not proof of future performance.",
        "portfolio_scope": "Multi-asset historical simulation with configured volatility, concentration, correlation-stress, and liquidity-capacity diagnostics.",
        "execution_scope": "Daily-bar cost and capacity proxies are scenario assumptions, not an order-book, fire-sale, or executable-fill model.",
        "research_boundary": "Educational research output only; it is not investment advice, broker integration, or live execution.",
    }


def _finite_or_none(value: Any) -> float | None:
    try:
        number = float(value)
        return number if pd.notna(number) else None
    except (TypeError, ValueError):
        return None
