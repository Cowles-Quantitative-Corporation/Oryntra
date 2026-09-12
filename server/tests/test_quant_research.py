import unittest

import numpy as np
import pandas as pd

from backend.quant_research import MODEL_PROFILES, QuantConfig, _correlation_stress_report, evaluate_strategies


class QuantResearchTests(unittest.TestCase):
    def test_long_only_trend_momentum_research_profile_is_explicit_and_bounded(self):
        config = QuantConfig(
            model="v1_long_only_trend_momentum_research",
            strategies=("time_series_trend", "cross_sectional_momentum"),
            long_short=False,
        )
        self.assertIn(config.model, MODEL_PROFILES)
        self.assertEqual(config.strategy_allocations_pct(), {"time_series_trend": 60.0, "cross_sectional_momentum": 40.0})
        self.assertFalse(config.effective_long_short())
        self.assertTrue(QuantConfig(model=config.model, long_short=True).effective_long_short())

    def test_regime_profile_returns_auditable_diagnostics(self):
        index = pd.bdate_range("2023-01-02", periods=320)
        histories = {}
        for seed, ticker in enumerate(("SPY", "QQQ", "IWM", "GLD"), start=1):
            returns = np.random.default_rng(seed).normal(.0003, .012, len(index))
            histories[ticker] = pd.DataFrame({"Close": 100 * np.exp(np.cumsum(returns))}, index=index)
        report = evaluate_strategies(histories, QuantConfig(model="v8_regime_diversified"))
        self.assertEqual(report["validation"]["status"], "chronological_holdout")
        self.assertGreater(report["validation"]["development"]["average_gross_exposure"], 0)
        self.assertGreater(report["validation"]["holdout"]["average_gross_exposure"], 0)
        self.assertIn("defensive_low_volatility", [item["id"] for item in report["results"]])
        self.assertTrue(report["portfolio_risk"]["latest_positions"])
        self.assertEqual(len(report["data_quality"]["symbols"]), 4)
        heatmap = report["visual_diagnostics"]["correlation"]
        self.assertEqual(heatmap["symbols"], ["SPY", "QQQ", "IWM", "GLD"])
        self.assertEqual(len(heatmap["values"]), 4)
        self.assertEqual(len(heatmap["values"][0]), 4)
        stress = report["visual_diagnostics"]["correlation_stress"]
        self.assertEqual(stress["status"], "available")
        self.assertEqual([item["id"] for item in stress["scenarios"]], ["moderate_convergence", "severe_convergence"])
        self.assertTrue(report["visual_diagnostics"]["monthly_returns"]["years"])
        self.assertGreater(len(report["visual_diagnostics"]["performance"]["equity_curve"]), 1)

    def test_correlation_convergence_increases_long_only_portfolio_risk(self):
        index = pd.bdate_range("2024-01-02", periods=126)
        left = np.tile([.012, -.008, .010, -.006], 32)[:len(index)]
        right = np.tile([-.010, .007, -.009, .006], 32)[:len(index)]
        returns = pd.DataFrame({"AAA": left, "BBB": right}, index=index)
        held = pd.DataFrame({"AAA": .5, "BBB": .5}, index=index)
        report = _correlation_stress_report(held, returns)
        self.assertEqual(report["status"], "available")
        moderate, severe = report["scenarios"]
        self.assertGreater(moderate["stressed_annualized_volatility_pct"], moderate["baseline_annualized_volatility_pct"])
        self.assertGreater(severe["stressed_annualized_volatility_pct"], moderate["stressed_annualized_volatility_pct"])


if __name__ == "__main__":
    unittest.main()
