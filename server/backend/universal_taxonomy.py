"""Versioned multi-family taxonomy contracts for Universal V2 research.

Classifications are data, not model assumptions.  A dated provider snapshot
must supply each security's memberships before this module can affect a test.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from functools import lru_cache
import json
import math
from pathlib import Path
from typing import Any


CATALOG_PATH = Path(__file__).resolve().parents[1] / "data" / "universal_taxonomy" / "family_catalog.json"
SEED_SNAPSHOT_PATH = CATALOG_PATH.with_name("financedatabase_seed_25000.json")


@dataclass(frozen=True)
class FamilyExposure:
    """One source-backed security-to-family edge in a dated graph.

    ``downside_sensitivity`` answers the user's question directly: a value of
    0.70 means a 1% family decline contributes a 0.70% downward pressure to
    this stock before any residual/portfolio controls.  It is deliberately a
    hypothesis to estimate, not a permanent hard-coded beta.
    """

    security_id: str
    symbol: str
    family_id: str
    membership_weight: float
    downside_sensitivity: float
    confidence: float
    source: str
    as_of_date: str
    source_url: str = ""
    valid_from: str = ""
    valid_to: str = ""

    def __post_init__(self) -> None:
        if not self.security_id or not self.symbol or not self.family_id or not self.source or not self.as_of_date:
            raise ValueError("Each exposure needs identifiers, source and as-of date")
        for name, value in (("membership_weight", self.membership_weight), ("downside_sensitivity", self.downside_sensitivity), ("confidence", self.confidence)):
            if not math.isfinite(value):
                raise ValueError(f"{name} must be finite")
        if not (0 < self.membership_weight <= 1 and -3 <= self.downside_sensitivity <= 3 and 0 <= self.confidence <= 1):
            raise ValueError("Exposure weight, sensitivity or confidence is outside its valid range")


def load_family_catalog(path: Path = CATALOG_PATH) -> dict[str, dict[str, Any]]:
    """Load the curated tree and reject orphaned/cyclic family definitions."""
    payload = json.loads(path.read_text(encoding="utf-8"))
    rows = payload.get("families")
    if not isinstance(rows, list) or not rows:
        raise ValueError("Family catalog needs a non-empty families list")
    catalog = {str(row.get("id", "")): row for row in rows}
    if len(catalog) != len(rows) or "" in catalog:
        raise ValueError("Family catalog IDs must be unique and non-empty")
    for family_id, row in catalog.items():
        parent = row.get("parent_id")
        if parent is not None and parent not in catalog:
            raise ValueError(f"Family {family_id} has an unknown parent")
        _ancestor_ids(family_id, catalog)
    return catalog


def validate_exposures(exposures: list[FamilyExposure], catalog: dict[str, dict[str, Any]] | None = None) -> None:
    """Ensure all graph edges point to known families and keep duplicates out.

    A provider may supply both a broad sector and a specific industry. Both are
    retained as useful metadata; ``family_shock_pressure`` resolves that
    hierarchy to one contribution per root branch at calculation time.
    """
    catalog = catalog or load_family_catalog()
    seen: set[tuple[str, str, str]] = set()
    for exposure in exposures:
        if exposure.family_id not in catalog:
            raise ValueError(f"Unknown family {exposure.family_id}")
        key = (exposure.security_id, exposure.family_id, exposure.as_of_date)
        if key in seen:
            raise ValueError("Duplicate security/family/as-of exposure")
        seen.add(key)


def family_shock_pressure(exposures: list[FamilyExposure], family_returns: dict[str, float],
                          catalog: dict[str, dict[str, Any]] | None = None) -> dict[str, Any]:
    """Compute an auditable, non-double-counted cross-family downside pressure.

    Only the most specific selected leaf per top-level branch contributes. A
    ``family_returns`` map must use matched, completed-session benchmark
    returns.  The result is a diagnostic input for an optimizer, never an
    instruction to liquidate on its own.
    """
    catalog = catalog or load_family_catalog()
    validate_exposures(exposures, catalog)
    contributions = []
    for exposure in exposures:
        family_return = family_returns.get(exposure.family_id)
        if family_return is None:
            continue
        if not math.isfinite(family_return):
            raise ValueError("Family returns must be finite")
        if family_return >= 0:
            continue
        root = _ancestor_ids(exposure.family_id, catalog)[-1]
        contributions.append({"family_id": exposure.family_id, "root_id": root,
                              "pressure": -family_return * exposure.membership_weight * exposure.downside_sensitivity,
                              "confidence": exposure.confidence})
    # Pick one strongest, most specific edge per independent root to prevent a
    # sector and its industry child from being added twice.
    selected = {}
    for item in contributions:
        current = selected.get(item["root_id"])
        if current is None or abs(item["pressure"]) > abs(current["pressure"]):
            selected[item["root_id"]] = item
    total = sum(item["pressure"] * item["confidence"] for item in selected.values())
    return {"pressure": total, "contributions": sorted(selected.values(), key=lambda item: abs(item["pressure"]), reverse=True),
            "note": "Diagnostic pressure uses source-backed leaf memberships and one contribution per root branch; estimate sensitivities only with point-in-time data."}


def taxonomy_contract() -> dict[str, Any]:
    catalog = load_family_catalog()
    roots = [row for row in catalog.values() if row.get("parent_id") is None]
    seed = {"available": SEED_SNAPSHOT_PATH.is_file(), "path": "server/data/universal_taxonomy/financedatabase_seed_25000.json", "status": "automated_seed_only_not_point_in_time_backtest_data"}
    if SEED_SNAPSHOT_PATH.is_file():
        seed["bytes"] = SEED_SNAPSHOT_PATH.stat().st_size
    return {"id": "universal_multi_family_taxonomy_v1", "status": "catalog_and_seed_ready" if seed["available"] else "catalog_ready_snapshot_required",
            "catalog_path": "server/data/universal_taxonomy/family_catalog.json", "family_count": len(catalog),
            "seed_snapshot": seed,
            "root_families": [{"id": row["id"], "title": row["title"]} for row in roots],
            "exposure_fields": list(FamilyExposure.__dataclass_fields__),
            "snapshot_contract": {"universe_definition": "Top 25,000 eligible common equities by declared market-cap field, as of a provider timestamp", "required_fields": ["security_id", "symbol", "market_cap", "as_of_date", "exchange", "country", "asset_type"], "history_rule": "Every backtest date must use the membership snapshot valid on that date; never backfill today's constituents."},
            "invariants": ["A stock may have multiple family exposures with source, confidence and validity dates.", "Parent and child exposures are retained as metadata but are not summed in a root branch.", "Membership weight is distinct from downside sensitivity.", "No taxonomy snapshot is shipped as current or used by V2 until source coverage and point-in-time history are verified."],
            "build_command": "python tools/build_universal_taxonomy.py --universe-csv /path/universe.csv --memberships-csv /path/memberships.csv --output /path/taxonomy_snapshot.json"}


@lru_cache(maxsize=1)
def _seed_index() -> tuple[dict[str, list[dict[str, Any]]], dict[str, list[dict[str, Any]]]]:
    """Load the optional large seed only when a signed-in lookup requests it."""
    if not SEED_SNAPSHOT_PATH.is_file():
        raise FileNotFoundError("No local taxonomy seed snapshot is available")
    snapshot = json.loads(SEED_SNAPSHOT_PATH.read_text(encoding="utf-8"))
    by_symbol: dict[str, list[dict[str, Any]]] = {}
    by_security: dict[str, list[dict[str, Any]]] = {}
    for security in snapshot.get("universe", []):
        by_symbol.setdefault(str(security.get("symbol", "")).upper(), []).append(security)
    for edge in snapshot.get("exposures", []):
        by_security.setdefault(str(edge.get("security_id", "")), []).append(edge)
    return by_symbol, by_security


def seed_symbol_lookup(symbol: str) -> dict[str, Any]:
    """Return a bounded, source-attributed family view for a seed symbol."""
    normalized = symbol.strip().upper()
    if not normalized or len(normalized) > 32:
        raise ValueError("Supply a valid symbol")
    by_symbol, by_security = _seed_index()
    matches = by_symbol.get(normalized, [])[:12]
    if not matches:
        raise LookupError("Symbol is not present in the local taxonomy seed")
    return {"symbol": normalized, "matches": [{"security": item, "memberships": by_security.get(str(item.get("security_id", "")), [])} for item in matches],
            "status": "automated_seed_only_not_point_in_time_backtest_data"}


def _ancestor_ids(family_id: str, catalog: dict[str, dict[str, Any]]) -> list[str]:
    chain, current = [], family_id
    while current is not None:
        if current in chain:
            raise ValueError(f"Cycle in family catalog at {current}")
        chain.append(current)
        current = catalog[current].get("parent_id")
    return chain
