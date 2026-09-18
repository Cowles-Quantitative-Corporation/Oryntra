from __future__ import annotations

import hashlib
import json
import math
import statistics
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Iterable, Sequence

from .database import get_connection

SCHEMA_VERSION = "research-v3"
FEATURE_VERSION = "causal-daily-v3"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def stable_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def fingerprint(value: Any) -> str:
    return hashlib.sha256(stable_json(value).encode("utf-8")).hexdigest()


def dataset_key(row: dict[str, Any]) -> tuple[str, str]:
    return (str(row.get("ticker") or "").upper(), str(row.get("date") or ""))


def rows_fingerprint(rows: Sequence[dict[str, Any]]) -> str:
    stable_rows = []
    fields = (
        "ticker",
        "date",
        "signal_date",
        "entry_date",
        "entry_price",
        "future_close",
        "future_high",
        "future_low",
        "raw_long_return_pct",
        "raw_long_mfe_pct",
        "raw_long_mae_pct",
        "horizon_days",
    )
    for row in sorted(rows, key=dataset_key):
        stable_rows.append({field: row.get(field) for field in fields})
    return fingerprint(stable_rows)


def engine_input_fingerprint(rows: Sequence[dict[str, Any]]) -> str:
    return fingerprint(sorted(dataset_key(row) for row in rows if not row.get("error")))


def compare_engine_inputs(mode_rows: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    fingerprints: dict[str, str] = {}
    key_sets: dict[str, set[tuple[str, str]]] = {}
    for mode, rows in mode_rows.items():
        keys = {dataset_key(row) for row in rows if not row.get("error")}
        key_sets[mode] = keys
        fingerprints[mode] = fingerprint(sorted(keys))
    modes = list(mode_rows)
    if not modes:
        return {
            "comparable": False,
            "reason": "No engine rows were produced.",
            "fingerprints": {},
            "differences": {},
        }
    reference = modes[0]
    ref_keys = key_sets[reference]
    differences: dict[str, Any] = {}
    for mode in modes[1:]:
        missing = sorted(ref_keys - key_sets[mode])
        extra = sorted(key_sets[mode] - ref_keys)
        if missing or extra:
            differences[mode] = {
                "missing_vs_reference": missing[:50],
                "extra_vs_reference": extra[:50],
                "missing_count": len(missing),
                "extra_count": len(extra),
            }
    comparable = not differences
    return {
        "comparable": comparable,
        "reference_engine": reference,
        "reason": None if comparable else "Engines were not evaluated on identical ticker/date observations.",
        "fingerprints": fingerprints,
        "differences": differences,
    }


def chronological_split(
    rows: Sequence[dict[str, Any]],
    *,
    train_pct: float = 0.65,
    validation_pct: float = 0.15,
    purge_days: int = 0,
) -> dict[str, list[int]]:


    dates = sorted({str(row.get("date") or "") for row in rows if row.get("date")})
    if len(dates) < 3:
        return {"train": list(range(len(rows))), "validation": [], "test": []}
    train_pct = min(0.85, max(0.4, float(train_pct)))
    validation_pct = min(0.3, max(0.05, float(validation_pct)))
    if train_pct + validation_pct >= 0.95:
        validation_pct = 0.15
        train_pct = 0.65
    train_end = max(1, int(len(dates) * train_pct))
    validation_end = max(train_end + 1, int(len(dates) * (train_pct + validation_pct)))
    validation_end = min(validation_end, len(dates) - 1)
    purge = max(0, int(purge_days))
    train_dates = set(dates[: max(0, train_end - purge)])
    validation_dates = set(dates[min(len(dates), train_end + purge) : max(train_end + purge, validation_end - purge)])
    test_dates = set(dates[min(len(dates), validation_end + purge) :])
    split = {"train": [], "validation": [], "test": []}
    for index, row in enumerate(rows):
        day = str(row.get("date") or "")
        if day in train_dates:
            split["train"].append(index)
        elif day in validation_dates:
            split["validation"].append(index)
        elif day in test_dates:
            split["test"].append(index)
    return split


def walk_forward_folds(
    rows: Sequence[dict[str, Any]],
    *,
    minimum_train_dates: int = 80,
    test_dates_per_fold: int = 20,
    purge_dates: int = 0,
) -> list[dict[str, list[int]]]:
    dates = sorted({str(row.get("date") or "") for row in rows if row.get("date")})
    minimum_train_dates = max(20, int(minimum_train_dates))
    test_dates_per_fold = max(5, int(test_dates_per_fold))
    purge_dates = max(0, int(purge_dates))
    folds: list[dict[str, list[int]]] = []
    cursor = minimum_train_dates
    while cursor + purge_dates < len(dates):
        train_dates = set(dates[:cursor])
        test_start = min(len(dates), cursor + purge_dates)
        test_end = min(len(dates), test_start + test_dates_per_fold)
        test_dates = set(dates[test_start:test_end])
        if not test_dates:
            break
        train_indexes = [i for i, row in enumerate(rows) if str(row.get("date") or "") in train_dates]
        test_indexes = [i for i, row in enumerate(rows) if str(row.get("date") or "") in test_dates]
        if train_indexes and test_indexes:
            folds.append({"train": train_indexes, "test": test_indexes})
        cursor = test_end
    return folds


def _finite(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
        return number if math.isfinite(number) else default
    except Exception:
        return default


def return_metrics(rows: Iterable[dict[str, Any]]) -> dict[str, Any]:
    clean = [row for row in rows if not row.get("error") and bool(row.get("actionable"))]
    returns = [_finite(row.get("return_pct")) for row in clean]
    wins = [value for value in returns if value > 0]
    losses = [value for value in returns if value <= 0]
    gross_profit = sum(wins)
    gross_loss = abs(sum(losses))
    win_rate = len(wins) / len(returns) * 100.0 if returns else 0.0
    avg_return = statistics.fmean(returns) if returns else 0.0
    std_return = statistics.pstdev(returns) if len(returns) > 1 else 0.0
    sharpe_like = avg_return / std_return * math.sqrt(252 / 10) if std_return > 1e-12 else 0.0
    equity = 1.0
    peak = 1.0
    max_drawdown = 0.0
    for value in returns:
        equity *= 1.0 + value / 100.0
        peak = max(peak, equity)
        drawdown = (equity / peak - 1.0) * 100.0
        max_drawdown = min(max_drawdown, drawdown)
    total_rows = [row for row in rows if not row.get("error")]
    signal_count = len(clean)
    target_hits = sum(bool(row.get("target_hit")) for row in clean)
    stop_hits = sum(bool(row.get("stop_hit")) for row in clean)
    avg_mfe = statistics.fmean([_finite(row.get("mfe_pct")) for row in clean]) if clean else 0.0
    avg_mae = statistics.fmean([_finite(row.get("mae_pct")) for row in clean]) if clean else 0.0
    return {
        "tests": len(total_rows),
        "signals": signal_count,
        "actionable": signal_count,
        "coverage_pct": round(signal_count / len(total_rows) * 100.0, 2) if total_rows else 0.0,
        "wins": len(wins),
        "losses": len(losses),
        "win_rate_pct": round(win_rate, 2),
        "avg_return_pct": round(avg_return, 4),
        "expectancy_pct": round(avg_return, 4),
        "median_return_pct": round(statistics.median(returns), 4) if returns else 0.0,
        "profit_factor": round(gross_profit / gross_loss, 4) if gross_loss > 1e-12 else (999.0 if gross_profit > 0 else 0.0),
        "cumulative_return_pct": round((equity - 1.0) * 100.0, 4),
        "max_drawdown_pct": round(max_drawdown, 4),
        "sharpe_like": round(sharpe_like, 4),
        "avg_mfe_pct": round(avg_mfe, 4),
        "avg_mae_pct": round(avg_mae, 4),
        "reward_risk_ratio": round(avg_mfe / abs(avg_mae), 4) if abs(avg_mae) > 1e-12 else 0.0,
        "avg_confidence": round(statistics.fmean([_finite(row.get("confidence")) for row in clean]), 2) if clean else 0.0,
        "target_hits": target_hits,
        "stop_hits": stop_hits,
        "target_hit_rate_pct": round(target_hits / signal_count * 100.0, 2) if signal_count else 0.0,
        "stop_hit_rate_pct": round(stop_hits / signal_count * 100.0, 2) if signal_count else 0.0,
        "errors": sum(bool(row.get("error")) for row in rows),
    }


def grouped_metrics(
    mode_rows: dict[str, list[dict[str, Any]]],
    field: str,
    *,
    limit: int = 250,
) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for mode, rows in mode_rows.items():
        buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in rows:
            key = str(row.get(field) or "UNKNOWN")
            buckets[key].append(row)
        for key, bucket in buckets.items():
            output.append({"mode": mode, field: key, **return_metrics(bucket)})
    output.sort(key=lambda item: (item["signals"], item["avg_return_pct"]), reverse=True)
    return output[: max(1, int(limit))]


def ensure_experiment_schema() -> None:
    conn = get_connection()
    try:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS research_experiments (
                experiment_id       TEXT PRIMARY KEY,
                experiment_type     TEXT NOT NULL,
                status              TEXT NOT NULL,
                created_at          TEXT NOT NULL,
                completed_at        TEXT,
                schema_version      TEXT NOT NULL,
                feature_version     TEXT NOT NULL,
                code_version        TEXT,
                config_json         TEXT NOT NULL,
                dataset_fingerprint TEXT,
                dataset_start       TEXT,
                dataset_end         TEXT,
                symbol_count        INTEGER,
                sample_count        INTEGER,
                artifact_path       TEXT,
                metrics_json        TEXT,
                notes               TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_research_experiment_created
                ON research_experiments(created_at DESC);
            CREATE TABLE IF NOT EXISTS research_universe_snapshots (
                snapshot_id TEXT PRIMARY KEY,
                decision_date TEXT NOT NULL,
                available_at TEXT NOT NULL,
                source_json TEXT NOT NULL,
                selection_fingerprint TEXT NOT NULL,
                input_fingerprint TEXT NOT NULL,
                symbols_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS research_experiment_partitions (
                experiment_id TEXT NOT NULL,
                partition_name TEXT NOT NULL,
                start_date TEXT NOT NULL,
                end_date TEXT NOT NULL,
                session_count INTEGER NOT NULL,
                dataset_fingerprint TEXT NOT NULL,
                locked_at TEXT NOT NULL,
                PRIMARY KEY (experiment_id, partition_name)
            );
            """
        )
        conn.commit()
    finally:
        conn.close()


def record_experiment(
    *,
    experiment_type: str,
    status: str,
    config: dict[str, Any],
    dataset_fingerprint: str | None = None,
    dataset_start: str | None = None,
    dataset_end: str | None = None,
    symbols: Sequence[str] | None = None,
    sample_count: int | None = None,
    artifact_path: str | None = None,
    metrics: dict[str, Any] | None = None,
    notes: str | None = None,
    experiment_id: str | None = None,
) -> str:
    ensure_experiment_schema()
    identifier = experiment_id or uuid.uuid4().hex
    now = utc_now_iso()
    conn = get_connection()
    try:
        conn.execute(
            """
            INSERT INTO research_experiments
                (experiment_id, experiment_type, status, created_at, completed_at,
                 schema_version, feature_version, code_version, config_json,
                 dataset_fingerprint, dataset_start, dataset_end, symbol_count,
                 sample_count, artifact_path, metrics_json, notes)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(experiment_id) DO UPDATE SET
                status=excluded.status,
                completed_at=excluded.completed_at,
                config_json=excluded.config_json,
                dataset_fingerprint=excluded.dataset_fingerprint,
                dataset_start=excluded.dataset_start,
                dataset_end=excluded.dataset_end,
                symbol_count=excluded.symbol_count,
                sample_count=excluded.sample_count,
                artifact_path=excluded.artifact_path,
                metrics_json=excluded.metrics_json,
                notes=excluded.notes
            """,
            (
                identifier,
                experiment_type,
                status,
                now,
                now if status in {"done", "failed", "stopped"} else None,
                SCHEMA_VERSION,
                FEATURE_VERSION,
                config.get("code_version"),
                stable_json(config),
                dataset_fingerprint,
                dataset_start,
                dataset_end,
                len(set(symbols or [])),
                sample_count,
                artifact_path,
                stable_json(metrics or {}),
                notes,
            ),
        )
        conn.commit()
    finally:
        conn.close()
    return identifier


def record_universe_snapshot(snapshot: dict[str, Any]) -> str:
    """Persist compact, auditable membership evidence—not raw vendor data."""
    required = ("snapshot_id", "decision_date", "source", "input_fingerprint", "symbols", "selection")
    if any(key not in snapshot for key in required):
        raise ValueError("Incomplete point-in-time universe snapshot")
    source = dict(snapshot["source"] or {})
    available_at = str(source.get("available_at") or "")
    if not available_at:
        raise ValueError("Universe snapshot requires source availability")
    ensure_experiment_schema()
    conn = get_connection()
    try:
        conn.execute("""INSERT INTO research_universe_snapshots
            (snapshot_id, decision_date, available_at, source_json, selection_fingerprint, input_fingerprint, symbols_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(snapshot_id) DO NOTHING""", (
            str(snapshot["snapshot_id"]), str(snapshot["decision_date"]), available_at,
            stable_json(source), str((snapshot["selection"] or {}).get("fingerprint") or ""),
            str(snapshot["input_fingerprint"]), stable_json(sorted(set(snapshot["symbols"]))), utc_now_iso(),
        ))
        conn.commit()
    finally:
        conn.close()
    return str(snapshot["snapshot_id"])


def record_experiment_partitions(experiment_id: str, partitions: dict[str, Sequence[str]]) -> None:
    """Lock chronological development/holdout ranges used by an experiment."""
    ensure_experiment_schema()
    now = utc_now_iso()
    conn = get_connection()
    try:
        for name, raw_dates in partitions.items():
            dates = sorted({str(day)[:10] for day in raw_dates if str(day)})
            if not dates:
                continue
            conn.execute("""INSERT INTO research_experiment_partitions
                (experiment_id, partition_name, start_date, end_date, session_count, dataset_fingerprint, locked_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(experiment_id, partition_name) DO NOTHING""",
                (experiment_id, str(name), dates[0], dates[-1], len(dates), fingerprint(dates), now))
        conn.commit()
    finally:
        conn.close()
