from types import SimpleNamespace
import numpy as np
import pandas as pd

from backend import control_research


def _frame(index, seed):
    rng = np.random.default_rng(seed)
    close = 100 * np.cumprod(1 + rng.normal(.0003, .01, len(index)))
    return pd.DataFrame({"Open": close, "High": close*1.01, "Low": close*.99, "Close": close, "Volume": 1_000_000.0}, index=index)


def test_interactive_backtest_passes_availability_dated_quality_and_rf_metadata(monkeypatch):
    index = pd.bdate_range("2022-01-03", periods=700)
    symbols = ["AAA", "BBB", "CCC", "SPY", "QQQ"]
    histories = {s: _frame(index, i + 1) for i, s in enumerate(symbols)}
    monkeypatch.setattr(control_research, "_fetch_histories", lambda **kw: ({s: histories[s].copy() for s in kw["symbols"]}, {s: {"provider":"test"} for s in kw["symbols"]}))
    monkeypatch.setattr(control_research, "build_control_risk_free", lambda idx, **kw: (pd.Series(0.0001, index=idx), {"mode":"test_rf"}))
    quality = pd.DataFrame(np.nan, index=index, columns=["AAA","BBB","CCC"])
    quality.iloc[200:] = [[-1, 0, 1]] * (len(index)-200)
    repo = SimpleNamespace(factor_panel=lambda tickers, idx, fill_missing=False: (quality.reindex(idx), {"coverage_pct": 70.0, "missing_values_preserved": True}))
    monkeypatch.setattr(control_research, "get_corporate_repository", lambda: repo)
    captured = []
    def fake_run(histories_arg, config, benchmark_returns, risk_free, evaluation_start, evaluation_end, **kwargs):
        captured.append(kwargs.get("factor_quality_scores"))
        idx = benchmark_returns.loc[pd.Timestamp(evaluation_start):pd.Timestamp(evaluation_end)].index
        return {"engine":"x","engine_version":"1","config_fingerprint":config.fingerprint,"dataset_fingerprint":"d","universe":{},
                "results":[{}],"daily_returns":[{"date":str(d.date()),"net_return":0.0002} for d in idx],"portfolio_risk":{},"latest_signals":{},"methodology":{}}
    monkeypatch.setattr(control_research, "run_universal", fake_run)
    result = control_research.run_interactive_backtest(1, {"universe":["AAA","BBB","CCC"],"models":["alpha_v11"],"provider":"yfinance","evaluation_period":"1y","risk_free_mode":"fred_dgs3mo","model_benchmark":"SPY/QQQ","comparison_benchmarks":["SPY"]})
    assert captured and captured[0] is not None
    assert captured[0].isna().any().any()  # unavailable history was not neutral-zero imputed
    assert result["risk_free"]["mode"] == "test_rf"
    assert result["point_in_time_corporate"]["missing_values_preserved"] is True
