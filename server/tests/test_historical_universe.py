from backend.historical_universe import build_universe_snapshot, materialize_universe_schedule
from backend import database
from backend.research_experiments import record_experiment, record_experiment_partitions, record_universe_snapshot
from backend.universe_selection import UniverseSelectionConfig


def _row(symbol, **overrides):
    row = {
        "symbol": symbol, "as_of_date": "2020-01-02", "available_at": "2020-01-02",
        "asset_type": "common_stock", "country": "US", "price": 20, "dollar_volume": 20_000_000,
        "source": "fixture",
    }
    row.update(overrides)
    return row


def _snapshot(day, symbols, *, available_at=None):
    return build_universe_snapshot(
        [_row(symbol, as_of_date=day, available_at=available_at or day) for symbol in symbols],
        decision_date=day, source_name="fixture-provider", source_version="v1",
        source_as_of=day, available_at=available_at or day,
        config=UniverseSelectionConfig(maximum_coarse_count=10, maximum_fine_count=10),
    )


def test_historical_schedule_never_uses_a_future_membership_snapshot():
    january = _snapshot("2020-01-02", ["AAA"])
    february = _snapshot("2020-02-03", ["BBB"])
    schedule = materialize_universe_schedule([february, january], ["2020-01-03", "2020-02-04"])
    first, second = schedule["schedule"]
    assert first["symbols"] == ["AAA"]
    assert second["symbols"] == ["BBB"]
    assert first["snapshot_id"] == january["snapshot_id"]
    assert second["snapshot_id"] == february["snapshot_id"]


def test_future_source_availability_and_stale_membership_are_rejected():
    try:
        _snapshot("2020-01-02", ["AAA"], available_at="2020-01-03")
        assert False, "future availability should be rejected"
    except ValueError as error:
        assert "availability" in str(error)
    january = _snapshot("2020-01-02", ["AAA"])
    schedule = materialize_universe_schedule([january], ["2020-03-02"], max_staleness_days=31)
    assert schedule["schedule"][0]["status"] == "stale_snapshot"
    assert schedule["schedule"][0]["symbols"] == []


def test_snapshot_and_chronological_partitions_are_durably_locked(monkeypatch, tmp_path):
    monkeypatch.setattr(database, "DB_PATH", str(tmp_path / "research.db"))
    snapshot = _snapshot("2020-01-02", ["AAA", "BBB"])
    snapshot_id = record_universe_snapshot(snapshot)
    experiment_id = record_experiment(
        experiment_type="quant_strategy", status="done", config={"code_version": "test"},
        dataset_fingerprint="dataset", symbols=["AAA", "BBB"], sample_count=4,
    )
    record_experiment_partitions(experiment_id, {
        "development": ["2020-01-02", "2020-01-03"],
        "holdout": ["2020-01-06", "2020-01-07"],
    })
    conn = database.get_connection()
    try:
        stored_snapshot = conn.execute("SELECT snapshot_id, symbols_json FROM research_universe_snapshots").fetchone()
        partitions = conn.execute("SELECT partition_name, start_date, end_date FROM research_experiment_partitions ORDER BY partition_name").fetchall()
    finally:
        conn.close()
    assert stored_snapshot["snapshot_id"] == snapshot_id
    assert [(row["partition_name"], row["start_date"], row["end_date"]) for row in partitions] == [
        ("development", "2020-01-02", "2020-01-03"), ("holdout", "2020-01-06", "2020-01-07"),
    ]
