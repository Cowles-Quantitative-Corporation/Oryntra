import pandas as pd

from backend.minerva_corporate import build_quality_and_observed_panels


def test_quality_panel_uses_only_available_facts_and_retains_an_observed_mask():
    dates = pd.bdate_range("2020-01-01", periods=10)
    facts = [
        {"ticker": "AAA", "metric": "revenue_growth_yoy", "value": 30, "available_at": "2020-01-07T00:00:00Z"},
        {"ticker": "AAA", "metric": "operating_margin", "value": 20, "available_at": "2020-01-07T00:00:00Z"},
        {"ticker": "BBB", "metric": "revenue_growth_yoy", "value": 10, "available_at": "2020-01-08T00:00:00Z"},
        {"ticker": "BBB", "metric": "operating_margin", "value": 5, "available_at": "2020-01-08T00:00:00Z"},
    ]
    score, observed = build_quality_and_observed_panels(facts, dates, ["AAA", "BBB"])
    assert not observed.loc[:"2020-01-06"].any().any()
    assert observed.loc["2020-01-07", "AAA"] and not observed.loc["2020-01-07", "BBB"]
    assert score.loc["2020-01-08", "AAA"] > score.loc["2020-01-08", "BBB"]
