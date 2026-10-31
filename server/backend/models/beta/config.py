from __future__ import annotations

"""Frozen software/research specification for CQC Beta / Model C v1.

All promotion thresholds are explicitly a *proposed internal research policy*,
not an inherited or authoritative CQC grading formula. They are configurable and
are never presented as an official letter grade or trading authorization.
"""

BETA_CANDIDATE_SCHEMA_VERSION = 2
BETA_TRAINING_CODE_REVISION = "beta-v1.2.2-control-5.4.8"
BETA_CALIBRATION_CONTRACT = "rank_preserving_temperature_v1"

BETA_V1_CONFIG = {
    "model_id": "beta_v1",
    "display_name": "Beta V1 — Probabilistic Macro Regime",
    "role": "model_c",
    "version": "beta-v1",
    "software_revision": "1.2.2",
    "decision_time": "NYSE regular-session open",
    "states": [
        "growth_up_inflation_up",
        "growth_up_inflation_down",
        "growth_down_inflation_up",
        "growth_down_inflation_down",
    ],
    "target_definition": {
        "semantics": "directional acceleration: growth rising/falling x inflation rising/falling",
        "target_only": True,
        "growth_anchor_series": ["INDPRO", "PAYEMS", "UNRATE", "GDPC1"],
        "inflation_anchor_series": ["CPIAUCSL", "PCEPI"],
        "candidate_excluded_anchor_series": ["INDPRO", "PAYEMS", "UNRATE", "GDPC1", "CPIAUCSL", "PCEPI"],
        "candidate_surprise_metrics": ["core_cpi", "core_pce", "retail_sales", "initial_claims"],
        "note": (
            "Economic labels use dedicated PIT anchor series that are excluded from candidate inputs. "
            "Candidate surprise inputs also exclude those anchor metrics. This prevents Beta from being "
            "graded on transformations of the same release series it consumes as predictors."
        ),
    },
    "candidate_selection": {
        "development_primary": "event_session_walk_forward_oos_log_loss",
        "tie_breakers": ["brier", "ece"],
        "final_holdout": "untouched_tail_after_winner_and_hyperparameters_are_selected",
        "strategy_performance_used_for_candidate_selection": False,
    },
    "pit": {
        "fred_output_type": 1,
        "daily_market_fred_output_type": 1,
        "revision_policy": "revisable macro releases use ALFRED vintages; explicitly allowlisted daily market series use their FRED observation date and become usable only on the next NYSE session",
        "fred_timestamp_precision": (
            "FRED realtime_start is date precision, not an exact intraday timestamp; absent a separately "
            "sourced exact timestamp Beta conservatively admits the observation at the next NYSE session"
        ),
        "market_close_policy": "daily market features use only the previous completed session",
        "missingness": "preserve until candidate-specific train-only imputation",
    },
    "independence": {
        "forbidden_forecast_inputs": [
            "tba8_forecast", "tba9_forecast", "alpha_v1_forecast",
            "alpha_v11_forecast", "alpha_v12_forecast", "minerva_forecast", "model_a_forecast", "model_b_forecast", "portfolio_weights",
        ]
    },
    "walk_forward": {
        "default_min_train_sessions": 756,
        "default_calibration_sessions": 126,
        "default_test_sessions": 126,
        "default_final_holdout_sessions": 252,
        "minimum_train_macro_events": 48,
        "minimum_calibration_macro_events": 12,
        "minimum_test_macro_events": 8,
        "minimum_holdout_macro_events": 12,
    },
    "consensus": {
        "required_for_downstream_promotion": True,
        "required_candidate_metrics": ["core_cpi", "core_pce", "retail_sales", "initial_claims"],
        "minimum_growth_metrics": 2,
        "minimum_inflation_metrics": 2,
        "minimum_matched_events": 48,
        "minimum_event_coverage": 0.20,
    },
    "market_data": {
        "promotion_requires_adjusted_or_total_return_prices": True,
        "unadjusted_prices_allowed_for_research_only": False,
    },
    "strategy_return_alignment": {
        "return_convention": "open_to_close_same_session",
        "beta_decision_time": "NYSE regular-session open",
        "strategy_return_definition": "Oryntra Universal realized net return from the session open transaction to that session close, indexed by the decision/trading session",
        "strategy_execution_timing": "prior completed-close signal -> current session open transaction -> current session close mark",
        "causal": True,
        "research_backtest_without_consensus_allowed": True,
        "note": "Consensus-surprise coverage remains a production-freeze gate, but it does not block causal research backtesting or the incremental portfolio study.",
    },
    "proposed_promotion_policy": {
        "policy_name": "cqc_beta_v1_proposed_2026_09",
        "authoritative": False,
        "description": (
            "Proposed research gates captured from the current CQC design discussion. "
            "Change this policy explicitly rather than silently changing grading thresholds."
        ),
        "probability": {
            "holdout_log_loss_below_uniform": True,
            "holdout_brier_below_uniform": True,
            "holdout_ece_max": 0.20,
        },
        "robustness": {
            "minimum_neighboring_variants": 2,
            "max_relative_log_loss_deterioration": 0.15,
        },
        "incremental_portfolio": {
            "sharpe_improvement_min": 0.05,
            "max_drawdown_improvement_min": 0.02,
            "cagr_sacrifice_max": 0.01,
            "bootstrap_reps": 500,
            "bootstrap_min_probability_positive_sharpe": 0.75,
            "bootstrap_min_probability_nonworse_drawdown": 0.60,
            "bootstrap_min_probability_cagr_within_tolerance": 0.75,
            "must_pass_one_of": ["A_plus_C", "A_plus_B_plus_C"],
            "a_plus_b_baseline_weights": [0.5, 0.5],
        },
    },
    "artifact_policy": {
        "production_frozen_requires_real_data": True,
        "real_market_provenance": "oryntra_canonical",
        "real_macro_provenance": "ALFRED/FRED output_type=1 historical real-time-period revisions for revisable releases plus FRED output_type=1 observation-dated ingestion for allowlisted daily market series",
        "synthetic_or_fixture_runs_are_never_marked_production_frozen": True,
    },
    "research_only": True,
    "live_trading_authorization": False,
}
