"""Static, auditable build map for the next Universal V2 research stages."""
from __future__ import annotations

from typing import Any

from .universal_position_policy import policy_contract
from .universal_market_context import market_context_contract
from .universal_taxonomy import taxonomy_contract
from .minerva import minerva_contract


def research_blueprint() -> dict[str, Any]:
    """Return only contracts and gates, never a claim that a workstream is live."""
    return {
        "schema_version": "universal-v2-blueprint-v1",
        "maturity": "research_foundation",
        "current_engine": "universal_v2",
        "minerva": minerva_contract(),
        "shared_contract": "Scanner evidence, Pattern Lab observations and portfolio targets must call the same causal signal implementation.",
        "position_policy": policy_contract(),
        "market_context": market_context_contract(),
        "taxonomy": taxonomy_contract(),
        "workstreams": [
            _item("position_lifecycle", "Position lifecycle & adaptive exits", "ledger_integrated_research_only",
                  ["entry plan", "state transitions", "precommitted stops/limits", "next-open dynamic directives"],
                  ["initial/trailing stop volatility", "profit ladder", "time stop", "signal-decay threshold", "spike trim"],
                  ["no stop widening", "no intraday hindsight", "cost and capacity ledger stays authoritative"],
                  "Run only through a frozen manifest and same-ledger out-of-sample evaluation; no default is promoted."),
            _item("market_context_overlay", "Cross-stock market context overlay", "ledger_integrated_research_only",
                  ["completed SPY and equal-weight returns", "dated breadth", "rolling correlation/dispersion", "source-manifest identifier"],
                  ["lookback", "stress thresholds", "confirmation count", "risk-on/caution/risk-off exposure multipliers"],
                  ["one red index day cannot liquidate the book", "close-only observations", "next-open execution", "fixed ledger costs/capacity"],
                  "A frozen study may derive completed-close SPY/QQQ, breadth and correlation observations; promote no setting without walk-forward evidence."),
            _item("multi_family_taxonomy", "Multi-family stock taxonomy & shock mapping", "catalog_ready_snapshot_required",
                  ["dated top-25,000 eligible-common-equity universe", "stable security identifier", "source-backed leaf memberships", "family benchmark returns", "validity intervals"],
                  ["membership confidence floor", "sensitivity estimation window", "family cap", "shock-pressure threshold"],
                  ["today's constituents never classify historical dates", "parent/child families do not double count", "membership and sensitivity remain distinct"],
                  "Build a provider-dated snapshot with complete coverage, then validate residual and shock pressure on held-out dates."),
            _item("cross_sectional_selection", "Residual strength & cross-sectional selection", "data_contract_required",
                  ["point-in-time sector/industry classification", "benchmark and sector returns", "liquid-universe membership history"],
                  ["residual lookback", "rank breadth", "number of holdings", "sector cap"],
                  ["no ticker identity feature", "membership and delisting history required"],
                  "Compare against raw momentum on the identical cash/share ledger."),
            _item("regime_exposure", "Regime-aware exposure", "data_contract_required",
                  ["completed market returns", "dated macro observations with availability timestamps", "correlation/dispersion history"],
                  ["exposure multiplier", "regime thresholds", "de-risk duration"],
                  ["no forecasted macro data", "regime labels must be known before allocation"],
                  "Test only whether it improves net alpha and drawdown across held-out regimes."),
            _item("events", "Point-in-time earnings & corporate events", "data_contract_required",
                  ["event timestamp", "availability timestamp", "source URL", "ticker identity history", "revision lineage"],
                  ["event horizon", "surprise/guidance transform", "post-event hold period"],
                  ["no restated data without as-of history", "events unavailable at trade time are excluded"],
                  "Build a separate event sleeve before combining it with price signals."),
            _item("portfolio_diversification", "Sector, beta & correlation-aware portfolio", "foundation_partial",
                  ["sector classification", "rolling beta", "covariance and fill data"],
                  ["sector cap", "risk contribution cap", "correlation stress", "cash reserve"],
                  ["long-only cash funding", "capacity limit", "no leverage by default"],
                  "Measure marginal risk contribution; do not treat stock count as diversification."),
            _item("meta_label", "Out-of-fold setup filter", "data_contract_required",
                  ["chronological labels", "purge horizon", "out-of-fold upstream predictions", "feature schema fingerprint"],
                  ["acceptance threshold", "calibration", "minimum sample size"],
                  ["no in-sample score reuse", "no LLM/identity leakage", "frozen test set"],
                  "Use it to reject weak existing setups, not to invent unsupported predicted returns."),
        ],
        "selection_scorecard": [
            "Calendar-year beta-adjusted alpha against explicit daily SPY/QQQ and cash returns",
            "HAC uncertainty and complete-year consistency gates",
            "Net alpha after fixed, declared costs and hard capacity fills",
            "Turnover, participation, drawdown, sector concentration and correlation-stress diagnostics",
            "Closed-episode payoff and win rate, with open positions excluded",
        ],
        "astra_handoff": [
            "Choose one workstream and write a bounded manifest before seeing its results.",
            "For market context, freeze market/breadth/correlation calculations and test target scaling before any exit integration.",
            "For taxonomy, build source-dated snapshots through build_universal_taxonomy.py; do not infer 25,000 memberships from names or use a current snapshot in the past.",
            "Keep accounting, causality, benchmark, cash-return and capacity invariants fixed.",
            "Tune only declared knobs on development dates; record every trial.",
            "Freeze the winner, then use later dates and a distinct predeclared universe once.",
            "Promote nothing automatically; retain failures and report data limitations.",
        ],
    }


def _item(identifier: str, title: str, readiness: str, inputs: list[str], knobs: list[str], invariants: list[str], next_gate: str) -> dict[str, Any]:
    return {"id": identifier, "title": title, "readiness": readiness, "inputs": inputs,
            "tunable_knobs": knobs, "fixed_invariants": invariants, "next_gate": next_gate}
