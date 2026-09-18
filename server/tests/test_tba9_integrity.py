import numpy as np
import pandas as pd

from backend import database
from backend.historical_universe import build_universe_snapshot
from backend.tba9_integrity import run_tba9_integrity_research
from backend.universe_selection import UniverseSelectionConfig


def _histories(n=900):
    dates = pd.bdate_range("2017-01-03", periods=n)
    rng = np.random.default_rng(45)
    output = {}
    for symbol, drift in (("AAA", .0006), ("BBB", .0004), ("CCC", .0002)):
        close = 100 * np.exp(np.cumsum(rng.normal(drift, .01, n)))
        opening = np.r_[close[0], close[:-1]]
        output[symbol] = pd.DataFrame({"Open": opening, "High": np.maximum(opening, close) * 1.01,
                                       "Low": np.minimum(opening, close) * .99, "Close": close,
                                       "Volume": 20_000_000}, index=dates)
    return output


def _snapshots(dates):
    snapshots = []
    for day in dates[::20]:
        symbols = ("AAA", "BBB") if day < dates[-100] else ("AAA", "CCC")
        records = [{"symbol": symbol, "as_of_date": str(day.date()), "available_at": str(day.date()),
                    "asset_type": "common_stock", "country": "US", "price": 20,
                    "dollar_volume": 50_000_000} for symbol in symbols]
        snapshots.append(build_universe_snapshot(
            records, decision_date=day.date(), source_name="fixture", source_version="v1",
            source_as_of=day.date(), available_at=day.date(),
            config=UniverseSelectionConfig(maximum_coarse_count=10, maximum_fine_count=10),
        ))
    return snapshots


def test_tba9_requires_pit_membership_and_locks_the_holdout(monkeypatch, tmp_path):
    monkeypatch.setattr(database, "DB_PATH", str(tmp_path / "research.db"))
    histories = _histories()
    dates = next(iter(histories.values())).index
    market = pd.Series(.0002, index=dates)
    report = run_tba9_integrity_research(
        histories, universe_snapshots=_snapshots(dates), benchmark_returns=market,
        risk_free=pd.Series(0.0, index=dates), development_end=str(dates[759].date()),
        holdout_start=str(dates[760].date()), named_benchmarks={"SPY": market, "QQQ": market * 1.1},
        include_diagnostics=True,
    )
    integrity = report["research_integrity"]
    assert integrity["holdout_locked_before_scoring"] is True
    assert integrity["point_in_time_universe"]["holdout_eligible_observation_pct"] < 100
    assert report["universe"]["eligibility"]["mode"] == "point_in_time_mask"
    assert integrity["benchmark_suite"]["benchmarks"]["SPY"]["sessions"] == 140
    assert integrity["ablation"]["status"] == "descriptive_not_selection"
    assert set(integrity["ablation"]["runs"]) == {
        "tba8_without_integrity_contract", "without_score_persistence", "without_confidence_capacity",
    }
    assert integrity["parameter_sensitivity"]["status"] == "descriptive_not_selection"
