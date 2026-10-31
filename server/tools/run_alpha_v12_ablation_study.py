#!/usr/bin/env python3
"""Alpha V1.2 architecture ablation on a frozen yearly protocol.

This runner intentionally exposes the key hypotheses instead of searching a
large hyperparameter grid.  It compares the original Alpha family, the failed
V1.1 implementation, V1.2 Alt and the final V1.2 Hybrid.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.phase4_control import build_control_config
from backend.universal_yearly_protocol import SeededYearProtocol, run_seeded_yearly_trials
from tools.run_v202_paired_risk_study import load_inputs, summarize


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--bars-csv", type=Path, required=True)
    p.add_argument("--risk-free-csv", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--declared-years", required=True, help="Comma-separated years already declared before viewing results")
    p.add_argument("--seed", type=int, default=20260919)
    p.add_argument("--warmup-sessions", type=int, default=800)
    a = p.parse_args()
    if a.output.exists():
        p.error("Output exists; preserve prior evidence and choose a new path")
    years = sorted({int(v) for v in a.declared_years.split(",") if v.strip()})
    if not years:
        p.error("Declare at least one year")

    stocks, market_closes, benchmark, risk_free = load_inputs(a.bars_csv, a.risk_free_csv)
    protocol = SeededYearProtocol(seed=a.seed, selected_years=len(years), warmup_sessions=a.warmup_sessions)
    ids = ["tba8", "alpha_v1", "alpha_v11", "alpha_v12_alt", "alpha_v12"]
    reports = {}
    for model_id in ids:
        cfg = build_control_config(alpha_model=model_id, construction="phase2", risk_model="v203", phase3_enabled=True)
        reports[model_id] = run_seeded_yearly_trials(
            stocks, cfg, benchmark, risk_free,
            protocol=protocol, market_closes=market_closes, declared_years=years,
        )
    reference = [t["dataset_fingerprint"] for t in reports["tba8"]["trials"]]
    if any([t["dataset_fingerprint"] for t in report["trials"]] != reference for report in reports.values()):
        raise RuntimeError("V1.2 ablation arms did not use identical datasets")
    result = {
        "status": "research_only",
        "declared_years": years,
        "summaries": {model_id: summarize(report) for model_id, report in reports.items()},
        "architecture_questions": [
            "Does V1.2 Alt repair the V1.1 execution/configuration failure without any TBA anchor?",
            "Does adding the fixed TBA8 anchor improve return/Sharpe without restoring TBA8-sized drawdowns?",
            "Does either V1.2 branch dominate V1 only because of one year or one risk regime?",
        ],
        "dataset_fingerprints": reference,
        "qualification": "Architecture ablation only; not a parameter search and not promotion evidence without a fresh holdout.",
    }
    a.output.parent.mkdir(parents=True, exist_ok=True)
    a.output.write_text(json.dumps(result, indent=2, allow_nan=False) + "\n")
    print(json.dumps(result, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
