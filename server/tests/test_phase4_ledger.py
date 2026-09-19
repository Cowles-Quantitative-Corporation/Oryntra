import os

import pytest

from backend import database
from backend.phase4_ledger import (
    create_candidate, get_candidate, list_candidates, mark_portfolio, pending_orders,
    reconcile_latest, record_decision, record_simulated_fill, set_candidate_status,
)


def _user():
    conn = database.get_connection()
    try:
        cur = conn.execute("INSERT INTO users(email,password_salt,password_hash) VALUES ('phase4@example.com','00','00')")
        conn.commit()
        return int(cur.lastrowid)
    finally:
        conn.close()


def test_phase4_candidate_freezes_manifest_and_forward_ledger_reconciles(monkeypatch, tmp_path):
    monkeypatch.setattr(database, "DB_PATH", str(tmp_path / "phase4.db"))
    database.init_db()
    user_id = _user()
    candidate = create_candidate(
        user_id=user_id, label="Forward test", alpha_model="tba8", construction="phase15",
        risk_model="v203", phase3_enabled=True, benchmark="spy_qqq_equal",
        universe=["AAPL", "MSFT"], initial_nav=100_000,
    )
    assert candidate["manifest"]["research_only"] is True
    assert candidate["config_fingerprint"] == candidate["manifest"]["config_fingerprint"]
    assert list_candidates(user_id)[0]["id"] == candidate["id"]

    decision = record_decision(
        candidate_id=candidate["id"], user_id=user_id, as_of_date="2026-09-17",
        dataset_fingerprint="a" * 64, approved_weights={"AAPL": .50, "MSFT": .30},
        reference_closes={"AAPL": 100.0, "MSFT": 200.0}, nav_before=100_000,
        diagnostics={"risk": "v203"}, scheduled_for="2026-09-18",
    )
    assert len(decision["orders"]) == 2
    orders = pending_orders(candidate["id"], through_date="2026-09-18")
    assert len(orders) == 2
    for order in orders:
        record_simulated_fill(order_id=order["id"], fill_date="2026-09-18", fill_price=order["reference_close"])
    assert pending_orders(candidate["id"], through_date="2026-09-18") == []

    mark = mark_portfolio(
        candidate_id=candidate["id"], as_of_date="2026-09-18",
        close_prices={"AAPL": 101.0, "MSFT": 198.0}, benchmark_return=.004,
    )
    assert mark["nav"] > 99_000
    assert 0 < mark["gross_exposure"] < 1
    reconciliation = reconcile_latest(candidate_id=candidate["id"], tolerance=.02)
    assert reconciliation["status"] in {"matched", "differences_explained"}
    assert {row["symbol"] for row in reconciliation["rows"]} == {"AAPL", "MSFT"}

    paused = set_candidate_status(candidate["id"], user_id, "PAUSED")
    assert paused["status"] == "PAUSED"
    assert get_candidate(candidate["id"], user_id=user_id)["status"] == "PAUSED"


def test_phase4_marks_and_reconciliation_are_forward_immutable(monkeypatch, tmp_path):
    monkeypatch.setattr(database, "DB_PATH", str(tmp_path / "immutable.db"))
    database.init_db()
    user_id = _user()
    candidate = create_candidate(
        user_id=user_id, label="Immutable", alpha_model="tba8", construction="phase15",
        risk_model="v203", phase3_enabled=True, benchmark="spy_qqq_equal",
        universe=["AAPL", "MSFT"], initial_nav=100_000,
    )
    decision = record_decision(
        candidate_id=candidate["id"], user_id=user_id, as_of_date="2026-09-17",
        dataset_fingerprint="b" * 64, approved_weights={"AAPL": .50, "MSFT": .30},
        reference_closes={"AAPL": 100.0, "MSFT": 200.0}, nav_before=100_000,
        scheduled_for="2026-09-18",
    )
    for order in decision["orders"]:
        record_simulated_fill(order_id=order["id"], fill_date="2026-09-18", fill_price=order["reference_close"])
    first = mark_portfolio(
        candidate_id=candidate["id"], as_of_date="2026-09-18",
        close_prices={"AAPL": 101.0, "MSFT": 198.0}, benchmark_return=.004,
    )
    recon1 = reconcile_latest(candidate_id=candidate["id"], tolerance=.02)
    second = mark_portfolio(
        candidate_id=candidate["id"], as_of_date="2026-09-18",
        close_prices={"AAPL": 150.0, "MSFT": 250.0}, benchmark_return=.20,
    )
    recon2 = reconcile_latest(candidate_id=candidate["id"], tolerance=.00001)
    assert second["immutable_existing"] is True
    assert second["nav"] == first["nav"]
    assert recon2["immutable_existing"] is True
    assert recon2["rows"] == recon1["rows"]


def test_phase4_fill_cannot_precede_schedule_or_backdate_into_frozen_mark(monkeypatch, tmp_path):
    monkeypatch.setattr(database, "DB_PATH", str(tmp_path / "fill-guard.db"))
    database.init_db()
    user_id = _user()
    candidate = create_candidate(
        user_id=user_id, label="Fill guard", alpha_model="tba8", construction="phase15",
        risk_model="v203", phase3_enabled=True, benchmark="spy_qqq_equal",
        universe=["AAPL", "MSFT"], initial_nav=100_000,
    )
    decision = record_decision(
        candidate_id=candidate["id"], user_id=user_id, as_of_date="2026-09-17",
        dataset_fingerprint="c" * 64, approved_weights={"AAPL": .50},
        reference_closes={"AAPL": 100.0}, nav_before=100_000, scheduled_for="2026-09-18",
    )
    order = decision["orders"][0]
    with pytest.raises(ValueError, match="scheduled next-open"):
        record_simulated_fill(order_id=order["id"], fill_date="2026-09-17", fill_price=100.0)
    record_simulated_fill(order_id=order["id"], fill_date="2026-09-18", fill_price=100.0)
    mark_portfolio(candidate_id=candidate["id"], as_of_date="2026-09-18", close_prices={"AAPL": 100.0})

    decision2 = record_decision(
        candidate_id=candidate["id"], user_id=user_id, as_of_date="2026-09-18",
        dataset_fingerprint="d" * 64, approved_weights={"AAPL": .40},
        reference_closes={"AAPL": 100.0}, nav_before=100_000, scheduled_for="2026-09-19",
    )
    with pytest.raises(ValueError, match="scheduled next-open"):
        record_simulated_fill(order_id=decision2["orders"][0]["id"], fill_date="2026-09-18", fill_price=100.0)
