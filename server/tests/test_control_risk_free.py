import numpy as np
import pandas as pd

from backend import control_risk_free


def test_manual_constant_percent_is_unambiguous():
    idx = pd.bdate_range("2026-01-02", periods=10)
    rf, meta = control_risk_free.build_control_risk_free(idx, mode="manual_constant", annual_pct=4.0)
    expected = (1.04 ** (1 / 252)) - 1
    assert np.allclose(rf.to_numpy(), expected)
    assert meta["annual_pct"] == 4.0


def test_fred_proxy_uses_strictly_prior_observation(monkeypatch, tmp_path):
    idx = pd.DatetimeIndex(["2026-01-05", "2026-01-06", "2026-01-07"])
    source = pd.Series(
        [3.0, 9.0, 4.0],
        index=pd.DatetimeIndex(["2026-01-02", "2026-01-05", "2026-01-06"]),
        name="DGS3MO",
    )
    monkeypatch.setattr(control_risk_free, "_load_fred", lambda force_refresh=False: (source, tmp_path / "rf.csv", False))
    rf, meta = control_risk_free.build_control_risk_free(idx, mode="fred_dgs3mo")
    # Jan 5 must use Jan 2's 3%, not Jan 5's 9%; Jan 6 uses Jan 5's 9%.
    expected_yields = np.array([0.03, 0.09, 0.04])
    expected = np.power(1 + expected_yields, 1 / 252) - 1
    np.testing.assert_allclose(rf.to_numpy(), expected)
    assert "strictly before" in meta["lag_rule"]


def test_zero_mode():
    idx = pd.bdate_range("2026-01-02", periods=5)
    rf, meta = control_risk_free.build_control_risk_free(idx, mode="zero")
    assert (rf == 0).all()
    assert meta["mode"] == "zero"
