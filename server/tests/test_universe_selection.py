import pytest

from backend.universe_selection import UniverseSelectionConfig, select_universe


def row(symbol, **overrides):
    base = {
        "symbol": symbol, "as_of_date": "2024-01-02", "available_at": "2024-01-02",
        "asset_type": "common_stock", "country": "US", "price": 25.0,
        "dollar_volume": 20_000_000.0, "market_cap": 5_000_000_000.0,
        "fundamental_available_at": "2024-01-02", "source": "fixture",
    }
    base.update(overrides)
    return base


def test_coarse_then_fine_selection_is_deterministic_and_auditable():
    result = select_universe([
        row("BBB", dollar_volume=30_000_000), row("AAA", dollar_volume=30_000_000),
        row("LATE", available_at="2024-01-03"), row("ILLQ", dollar_volume=100),
    ], "2024-01-02", UniverseSelectionConfig(maximum_coarse_count=10, maximum_fine_count=2))
    assert result["symbols"] == ["AAA", "BBB"]
    assert result["rejected"] == {"liquidity_ineligible": 1, "not_available_as_of_decision": 1}
    assert result["status"] == "research_only_not_connected_to_scanner_or_execution"
    assert len(result["fingerprint"]) == 64


def test_fine_fundamental_and_cap_rules_refuse_late_or_missing_data():
    config = UniverseSelectionConfig(maximum_coarse_count=10, maximum_fine_count=5,
                                     require_fundamentals=True, minimum_market_cap=1_000_000_000)
    result = select_universe([
        row("GOOD"), row("LATE", fundamental_available_at="2024-01-03"),
        row("SMALL", market_cap=99), row("MISS", fundamental_available_at=""),
    ], "2024-01-02", config)
    assert result["symbols"] == ["GOOD"]
    assert result["rejected"] == {
        "fundamentals_missing": 1,
        "fundamentals_not_available_as_of_decision": 1,
        "market_cap_ineligible": 1,
    }


def test_invalid_config_and_late_snapshot_are_rejected():
    with pytest.raises(ValueError, match="selection counts"):
        UniverseSelectionConfig(maximum_coarse_count=2, maximum_fine_count=3)
    result = select_universe([row("AAA", as_of_date="2024-01-03")], "2024-01-02")
    assert result["symbols"] == []
    assert result["rejected"] == {"not_available_as_of_decision": 1}
