import numpy as np
import pandas as pd
import pytest
import json

from backend.universal_engine import UniversalConfig
from backend.universal_market_context import MarketContextConfig
from backend.universal_yearly_protocol import SeededYearProtocol, eligible_years, run_seeded_yearly_trials, select_seeded_years


def _histories():
    dates = pd.bdate_range("2015-01-01", "2025-12-31")
    price = 100 * np.exp(np.arange(len(dates)) * .0002)
    frame = pd.DataFrame({"Open": price, "High": price * 1.01, "Low": price * .99, "Close": price, "Volume": 1e7}, index=dates)
    return {f"S{number:03d}": frame.copy() for number in range(6)}


def test_seeded_protocol_requires_complete_availability_and_selects_reproducibly():
    histories = _histories()
    dates = next(iter(histories.values())).index
    benchmark = pd.Series(.0001 + .00001 * np.sin(np.arange(len(dates))), index=dates)
    cash = pd.Series(.00001, index=dates)
    protocol = SeededYearProtocol(seed=7, selected_years=3, minimum_year_spacing=2, minimum_symbols=5,
                                  warmup_sessions=253, earliest_year=2016)
    rows = eligible_years(histories, benchmark, cash, protocol)
    chosen = select_seeded_years(rows, protocol)
    assert len(chosen) == 3
    assert [row["year"] for row in chosen] == [row["year"] for row in select_seeded_years(rows, protocol)]
    assert all(abs(left["year"] - right["year"]) >= 2 for index, left in enumerate(chosen) for right in chosen[index + 1:])
    json.dumps(rows)
    with pytest.raises(ValueError, match="annual-study universe"):
        SeededYearProtocol(maximum_symbols_per_trial=1)


def test_seeded_protocol_refuses_to_relax_spacing_or_missing_cash():
    histories = _histories()
    dates = next(iter(histories.values())).index
    benchmark = pd.Series(.0001, index=dates)
    cash = pd.Series(.00001, index=dates)
    protocol = SeededYearProtocol(selected_years=10, minimum_year_spacing=3, minimum_symbols=5,
                                  warmup_sessions=253, earliest_year=2021)
    with pytest.raises(ValueError, match="Insufficient eligible years"):
        select_seeded_years(eligible_years(histories, benchmark, cash, protocol), protocol)
    cash.loc["2021-02-01"] = np.nan
    rows = eligible_years(histories, benchmark, cash, SeededYearProtocol(selected_years=1, minimum_symbols=5,
                                                                           warmup_sessions=253, earliest_year=2021))
    assert not next(row for row in rows if row["year"] == 2021)["eligible"]


def test_annual_trials_return_one_cash_funded_observation_per_selected_year():
    histories = _histories()
    dates = next(iter(histories.values())).index
    benchmark = pd.Series(.0001 + .00001 * np.sin(np.arange(len(dates))), index=dates)
    cash = pd.Series(.00001, index=dates)
    protocol = SeededYearProtocol(seed=2, selected_years=2, minimum_year_spacing=2, minimum_symbols=5,
                                  warmup_sessions=253, earliest_year=2020)
    report = run_seeded_yearly_trials(histories, UniversalConfig(entry_threshold=.99, cost_bps=0, impact_bps=0),
                                      benchmark, cash, protocol=protocol)
    assert len(report["trials"]) == 2
    assert report["selected_years"] == [trial["year"] for trial in report["trials"]]
    assert all(trial["symbols"] == sorted(trial["symbols"]) for trial in report["trials"])
    assert all(trial["symbol_selection_seed"] == report["protocol"]["seed"] + trial["year"] for trial in report["trials"])
    # A no-entry configuration should earn only the explicit cash series; each
    # trial is independently opened at the configured initial equity.
    assert all(0 < trial["performance"]["total_return_pct"] < 1 for trial in report["trials"])


def test_annual_trials_require_explicit_market_closes_when_market_context_is_enabled():
    histories = _histories()
    dates = next(iter(histories.values())).index
    benchmark = pd.Series(.0001 + .00001 * np.sin(np.arange(len(dates))), index=dates)
    cash = pd.Series(.00001, index=dates)
    protocol = SeededYearProtocol(selected_years=1, minimum_symbols=5, warmup_sessions=253, earliest_year=2022)
    configuration = UniversalConfig(entry_threshold=.99, market_context=MarketContextConfig(enabled=True))
    with pytest.raises(ValueError, match="SPY and QQQ"):
        run_seeded_yearly_trials(histories, configuration, benchmark, cash, protocol=protocol)


def test_declared_chunk_still_requires_an_eligible_year():
    histories = _histories()
    dates = next(iter(histories.values())).index
    benchmark = pd.Series(.0001 + .00001 * np.sin(np.arange(len(dates))), index=dates)
    cash = pd.Series(.00001, index=dates)
    protocol = SeededYearProtocol(selected_years=1, minimum_symbols=5, warmup_sessions=253, earliest_year=2020)
    report = run_seeded_yearly_trials(histories, UniversalConfig(entry_threshold=.99), benchmark, cash,
                                      protocol=protocol, declared_years=[2023])
    assert report["selection_method"] == "declared_chunk_of_a_previously_recorded_protocol"
    with pytest.raises(ValueError, match="unavailable"):
        run_seeded_yearly_trials(histories, UniversalConfig(entry_threshold=.99), benchmark, cash,
                                 protocol=protocol, declared_years=[2010])
