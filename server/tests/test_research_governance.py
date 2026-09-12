from __future__ import annotations

import unittest

import pandas as pd

from backend.pattern_analyzer import analyze_patterns
from backend.research_governance import quant_research_governance, scanner_research_governance


class ResearchGovernanceTests(unittest.TestCase):
    def setUp(self):
        self.history = pd.DataFrame(
            {
                "Open": [99.5, 100.5, 101.5], "High": [101.0, 102.0, 103.0],
                "Low": [99.0, 100.0, 101.0], "Close": [100.0, 101.0, 102.5],
                "Volume": [1000, 1200, 1100],
            },
            index=pd.date_range("2025-01-02", periods=3, freq="B"),
        )

    def test_scanner_contract_is_stable_and_distinguishes_vai2_validation(self):
        v8 = scanner_research_governance("v8", self.history)
        vai2 = scanner_research_governance("vai2", self.history)
        self.assertEqual(v8["schema_version"], "research-governance-v1")
        self.assertEqual(v8["data_lineage"]["observations"], 3)
        self.assertEqual(v8["data_lineage"]["fingerprint"], scanner_research_governance("v8", self.history)["data_lineage"]["fingerprint"])
        self.assertIn("untouched test", vai2["validation_requirement"])
        self.assertEqual(v8["portfolio_scope"], "not_applicable_single_symbol_scanner")

    def test_pattern_report_exposes_contract_without_changing_engine_output_shape(self):
        report = analyze_patterns(self.history, {"price": 102.5}, mode="v8")
        self.assertEqual(report["research_governance"]["model"]["id"], "v8")
        self.assertIn("patterns", report["advanced_patterns"])

    def test_quant_contract_marks_costs_as_assumptions(self):
        report = quant_research_governance({"model": "v8_balanced"}, {"symbols": ["SPY"], "start": "2025-01-02", "end": "2025-01-06", "sessions": 3})
        self.assertEqual(report["model"]["family"], "portfolio_research_simulator")
        self.assertIn("scenario assumptions", report["execution_scope"])


if __name__ == "__main__":
    unittest.main()
