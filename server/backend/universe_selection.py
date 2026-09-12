"""Point-in-time coarse-to-fine universe selection for research.

This is deliberately a data-contract layer.  It resembles the useful part of
LEAN's two-stage universe selection, but it does not subscribe to securities,
place orders, or silently turn a current constituent list into history.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date
from hashlib import sha256
import json
import math
from typing import Any, Iterable


@dataclass(frozen=True)
class UniverseSelectionConfig:
    """Explicit, bounded eligibility rules for a single decision date."""

    minimum_price: float = 5.0
    minimum_dollar_volume: float = 5_000_000.0
    maximum_coarse_count: int = 1_000
    maximum_fine_count: int = 250
    allowed_asset_types: tuple[str, ...] = ("common_stock",)
    allowed_countries: tuple[str, ...] = ("US",)
    require_fundamentals: bool = False
    minimum_market_cap: float | None = None
    maximum_market_cap: float | None = None

    def __post_init__(self) -> None:
        if not (math.isfinite(self.minimum_price) and self.minimum_price > 0):
            raise ValueError("minimum_price must be positive and finite")
        if not (math.isfinite(self.minimum_dollar_volume) and self.minimum_dollar_volume > 0):
            raise ValueError("minimum_dollar_volume must be positive and finite")
        if not (1 <= self.maximum_fine_count <= self.maximum_coarse_count <= 25_000):
            raise ValueError("selection counts must be ordered integers within the supported bound")
        if not self.allowed_asset_types or not self.allowed_countries:
            raise ValueError("asset-type and country lists cannot be empty")
        for name, value in (("minimum_market_cap", self.minimum_market_cap), ("maximum_market_cap", self.maximum_market_cap)):
            if value is not None and (not math.isfinite(value) or value <= 0):
                raise ValueError(f"{name} must be positive and finite when supplied")
        if self.minimum_market_cap and self.maximum_market_cap and self.minimum_market_cap > self.maximum_market_cap:
            raise ValueError("minimum_market_cap cannot exceed maximum_market_cap")


def select_universe(records: Iterable[dict[str, Any]], decision_date: str | date,
                    config: UniverseSelectionConfig = UniverseSelectionConfig()) -> dict[str, Any]:
    """Select an auditable universe using only records public by *decision_date*.

    Required coarse fields are ``symbol``, ``as_of_date``, ``available_at``,
    ``asset_type``, ``country``, ``price`` and ``dollar_volume``.  Fine rules
    only inspect a market-cap observation when a configured cap makes it
    necessary.  Every accepted row is source-eligible as of the decision date;
    missing or late records are rejected rather than backfilled.
    """
    decision = _as_date(decision_date, "decision_date")
    accepted: list[dict[str, Any]] = []
    rejected: dict[str, int] = {}
    symbols: set[str] = set()
    for raw in records:
        row = dict(raw)
        reason = _coarse_rejection(row, decision, config)
        if reason:
            rejected[reason] = rejected.get(reason, 0) + 1
            continue
        symbol = str(row["symbol"]).upper()
        if symbol in symbols:
            rejected["duplicate_symbol"] = rejected.get("duplicate_symbol", 0) + 1
            continue
        symbols.add(symbol)
        row["symbol"] = symbol
        accepted.append(row)
    coarse = sorted(accepted, key=lambda row: (-float(row["dollar_volume"]), row["symbol"]))[:config.maximum_coarse_count]
    fine: list[dict[str, Any]] = []
    for row in coarse:
        reason = _fine_rejection(row, decision, config)
        if reason:
            rejected[reason] = rejected.get(reason, 0) + 1
            continue
        fine.append(row)
    selected = sorted(fine, key=lambda row: (-float(row["dollar_volume"]), row["symbol"]))[:config.maximum_fine_count]
    provenance_rows = [{key: row.get(key) for key in (
        "symbol", "as_of_date", "available_at", "asset_type", "country", "exchange",
        "price", "dollar_volume", "market_cap", "fundamental_available_at", "source",
    )} for row in selected]
    fingerprint = sha256(json.dumps({"decision_date": decision.isoformat(), "config": asdict(config),
                                    "rows": provenance_rows}, sort_keys=True, default=str).encode()).hexdigest()
    return {
        "id": "point_in_time_coarse_fine_universe_v1",
        "status": "research_only_not_connected_to_scanner_or_execution",
        "decision_date": decision.isoformat(),
        "configuration": asdict(config),
        "counts": {"input": len(accepted) + sum(rejected.values()), "coarse": len(coarse), "selected": len(selected)},
        "rejected": dict(sorted(rejected.items())),
        "symbols": [row["symbol"] for row in selected],
        "selected_rows": provenance_rows,
        "fingerprint": fingerprint,
        "invariants": [
            "A row is eligible only when its as-of and availability dates are on or before the decision date.",
            "The seed taxonomy is not a valid historical-universe input without dated provider snapshots.",
            "This selector defines research eligibility only; it does not create a signal, target, or order.",
        ],
    }


def _coarse_rejection(row: dict[str, Any], decision: date, config: UniverseSelectionConfig) -> str | None:
    required = ("symbol", "as_of_date", "available_at", "asset_type", "country", "price", "dollar_volume")
    if any(row.get(name) in (None, "") for name in required):
        return "missing_coarse_field"
    try:
        as_of, available = _as_date(row["as_of_date"], "as_of_date"), _as_date(row["available_at"], "available_at")
        price, volume = float(row["price"]), float(row["dollar_volume"])
    except (TypeError, ValueError):
        return "invalid_coarse_value"
    if as_of > decision or available > decision:
        return "not_available_as_of_decision"
    if not str(row["symbol"]).strip():
        return "missing_symbol"
    if str(row["asset_type"]).lower() not in {item.lower() for item in config.allowed_asset_types}:
        return "asset_type_ineligible"
    if str(row["country"]).upper() not in {item.upper() for item in config.allowed_countries}:
        return "country_ineligible"
    if not math.isfinite(price) or price < config.minimum_price:
        return "price_ineligible"
    if not math.isfinite(volume) or volume < config.minimum_dollar_volume:
        return "liquidity_ineligible"
    return None


def _fine_rejection(row: dict[str, Any], decision: date, config: UniverseSelectionConfig) -> str | None:
    needs_cap = config.minimum_market_cap is not None or config.maximum_market_cap is not None
    if config.require_fundamentals:
        if not row.get("fundamental_available_at"):
            return "fundamentals_missing"
        try:
            if _as_date(row["fundamental_available_at"], "fundamental_available_at") > decision:
                return "fundamentals_not_available_as_of_decision"
        except (TypeError, ValueError):
            return "fundamentals_invalid"
    if needs_cap:
        try:
            market_cap = float(row["market_cap"])
        except (KeyError, TypeError, ValueError):
            return "market_cap_missing"
        if not math.isfinite(market_cap) or (config.minimum_market_cap and market_cap < config.minimum_market_cap) or (config.maximum_market_cap and market_cap > config.maximum_market_cap):
            return "market_cap_ineligible"
    return None


def _as_date(value: str | date, name: str) -> date:
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError as exc:
        raise ValueError(f"{name} must be an ISO date") from exc
