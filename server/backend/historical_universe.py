"""Immutable point-in-time universe manifests for research only.

Historical experiments must not use today's constituents for an older decision
date.  This module turns dated source snapshots into compact manifests and
materializes an eligibility schedule without downloading data or creating
orders.  It deliberately stores source and timing evidence alongside symbols.
"""
from __future__ import annotations

from datetime import date
from typing import Any, Iterable, Sequence

from .research_experiments import fingerprint
from .universe_selection import UniverseSelectionConfig, select_universe


def build_universe_snapshot(
    records: Iterable[dict[str, Any]],
    *,
    decision_date: str | date,
    source_name: str,
    source_version: str,
    source_as_of: str | date,
    available_at: str | date,
    config: UniverseSelectionConfig = UniverseSelectionConfig(),
) -> dict[str, Any]:
    """Create a content-addressed universe snapshot eligible on one date."""
    decision = _date(decision_date, "decision_date")
    source_as_of_date = _date(source_as_of, "source_as_of")
    available_date = _date(available_at, "available_at")
    if not str(source_name).strip() or not str(source_version).strip():
        raise ValueError("source_name and source_version are required")
    if source_as_of_date > decision or available_date > decision:
        raise ValueError("A snapshot cannot be used before its source as-of and availability dates")
    clean_records = [dict(row) for row in records]
    selection = select_universe(clean_records, decision, config)
    source = {
        "name": str(source_name).strip(),
        "version": str(source_version).strip(),
        "as_of_date": source_as_of_date.isoformat(),
        "available_at": available_date.isoformat(),
    }
    input_fingerprint = fingerprint(clean_records)
    snapshot_id = fingerprint({"decision_date": decision.isoformat(), "source": source,
                               "selection_fingerprint": selection["fingerprint"], "input_fingerprint": input_fingerprint})
    return {
        "schema": "point-in-time-universe-v1",
        "snapshot_id": snapshot_id,
        "decision_date": decision.isoformat(),
        "source": source,
        "input_fingerprint": input_fingerprint,
        "selection": selection,
        "symbols": selection["symbols"],
        "fingerprint": fingerprint({"snapshot_id": snapshot_id, "symbols": selection["symbols"]}),
        "status": "research_only_not_connected_to_scanner_or_execution",
    }


def materialize_universe_schedule(
    snapshots: Sequence[dict[str, Any]], decision_dates: Iterable[str | date], *, max_staleness_days: int = 31,
) -> dict[str, Any]:
    """Pick only snapshots public by each date; never backfill later membership."""
    if max_staleness_days < 0:
        raise ValueError("max_staleness_days cannot be negative")
    normalized = [_validate_snapshot(snapshot) for snapshot in snapshots]
    if len({snapshot["snapshot_id"] for snapshot in normalized}) != len(normalized):
        raise ValueError("snapshot_id values must be unique")
    schedule = []
    for raw_day in sorted({_date(day, "decision_date") for day in decision_dates}):
        candidates = [item for item in normalized if item["decision_date"] <= raw_day and item["available_at"] <= raw_day]
        if not candidates:
            schedule.append({"decision_date": raw_day.isoformat(), "status": "missing_prior_snapshot", "symbols": []})
            continue
        chosen = max(candidates, key=lambda item: (item["decision_date"], item["available_at"], item["snapshot_id"]))
        age = (raw_day - chosen["decision_date"]).days
        if age > max_staleness_days:
            schedule.append({"decision_date": raw_day.isoformat(), "status": "stale_snapshot", "symbols": [],
                             "snapshot_id": chosen["snapshot_id"], "age_days": age})
            continue
        schedule.append({"decision_date": raw_day.isoformat(), "status": "eligible", "snapshot_id": chosen["snapshot_id"],
                         "source": chosen["source"], "snapshot_decision_date": chosen["decision_date"].isoformat(),
                         "age_days": age, "symbols": chosen["symbols"]})
    return {
        "schema": "point-in-time-universe-schedule-v1",
        "status": "research_only_not_connected_to_scanner_or_execution",
        "max_staleness_days": max_staleness_days,
        "schedule": schedule,
        "fingerprint": fingerprint(schedule),
        "invariants": [
            "A decision date may only use a snapshot whose decision and availability dates are not later than that date.",
            "Missing or stale coverage is reported as ineligible rather than filled from a later constituent list.",
            "Membership eligibility is separate from signals, weights, orders, and performance claims.",
        ],
    }


def _validate_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
    required = ("snapshot_id", "decision_date", "source", "symbols")
    if any(key not in snapshot for key in required):
        raise ValueError("snapshot is missing required point-in-time metadata")
    source = dict(snapshot["source"] or {})
    if not source.get("available_at"):
        raise ValueError("snapshot source availability is required")
    return {"snapshot_id": str(snapshot["snapshot_id"]), "decision_date": _date(snapshot["decision_date"], "snapshot decision_date"),
            "available_at": _date(source["available_at"], "snapshot available_at"), "source": source,
            "symbols": [str(symbol).upper() for symbol in snapshot["symbols"] if str(symbol).strip()]}


def _date(value: str | date, name: str) -> date:
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError as exc:
        raise ValueError(f"{name} must be an ISO date") from exc
