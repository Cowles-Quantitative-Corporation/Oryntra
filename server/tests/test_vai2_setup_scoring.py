import copy
import unittest
from unittest.mock import patch

from backend.setup_detector import _apply_vai_2_0_engine_adjustments


class Vai2SetupScoringTests(unittest.TestCase):
    def test_promoted_prediction_uses_decision_edge_in_score_and_explanation(self):
        results = {
            "BREAKOUT": {"score": 50, "direction": "LONG", "rules": []},
            "NO_TRADE": {"score": 20, "direction": "NEUTRAL", "rules": []},
        }
        prediction = {
            "trained": True,
            "decision": "TRADE",
            "probability": 80,
            "expected_return_pct": 3,
            "stop_probability_pct": 20,
            "decision_edge": 0.7,
            "min_decision_edge": 0.2,
            "suggested_position_size_pct": 1.5,
            "grade": "B",
        }
        with patch("backend.setup_detector._apply_official_v7_engine_adjustments", return_value=copy.deepcopy(results)), patch("backend.vai2_model.predict_vai2_setup", return_value=prediction):
            adjusted = _apply_vai_2_0_engine_adjustments(results, {}, {})
        rules = " ".join(adjusted["BREAKOUT"]["rules"])
        self.assertIn("edge 0.700", rules)
        self.assertEqual(adjusted["BREAKOUT"]["score"], 91.6)


if __name__ == "__main__":
    unittest.main()
