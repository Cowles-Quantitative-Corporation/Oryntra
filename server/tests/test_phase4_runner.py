import pytest

from backend import database
from backend.phase4_ledger import create_candidate
from backend.phase4_runner import validate_frozen_candidate


def _user(email="phase4-runner@example.com"):
    conn = database.get_connection()
    try:
        cur = conn.execute("INSERT INTO users(email,password_salt,password_hash) VALUES (?,?,?)", (email, "00", "00"))
        conn.commit()
        return int(cur.lastrowid)
    finally:
        conn.close()


def test_frozen_candidate_refuses_to_continue_after_code_fingerprint_change(monkeypatch, tmp_path):
    monkeypatch.setattr(database, "DB_PATH", str(tmp_path / "runner.db"))
    database.init_db()
    user_id = _user()
    candidate = create_candidate(
        user_id=user_id,
        label="Frozen",
        alpha_model="tba8",
        construction="phase15",
        risk_model="v203",
        phase3_enabled=True,
        benchmark="spy_qqq_equal",
        universe=["AAPL", "MSFT"],
    )
    # The just-created candidate is valid under the current research code.
    config = validate_frozen_candidate(candidate)
    assert config.fingerprint == candidate["config_fingerprint"]

    monkeypatch.setattr("backend.phase4_runner.code_fingerprint", lambda: "f" * 64)
    with pytest.raises(ValueError, match="code fingerprint differs"):
        validate_frozen_candidate(candidate)
