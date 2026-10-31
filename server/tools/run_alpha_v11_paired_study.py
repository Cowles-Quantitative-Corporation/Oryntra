#!/usr/bin/env python3
"""Frozen-panel comparison for Alpha V1.1 before any Model B work.

All arms share the same Phase 2 construction, Risk V2.0.3, Phase 3, execution,
years and datasets.  The intended differences are the alpha model only.
"""
from __future__ import annotations
import argparse, hashlib, json, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.phase4_control import build_control_config
from backend.universal_yearly_protocol import SeededYearProtocol, eligible_years, run_seeded_yearly_trials, select_seeded_years
from tools.run_v202_paired_risk_study import load_inputs, summarize


def _delta(a: dict, b: dict) -> dict:
    keys = (
        'mean_excess_sharpe','minimum_annual_excess_sharpe','mean_annual_alpha_pct',
        'mean_max_drawdown_pct','mean_annualized_turnover','mean_gross_exposure',
    )
    return {k: float(a[k] - b[k]) for k in keys}


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument('--bars-csv', type=Path, required=True)
    p.add_argument('--risk-free-csv', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--seed', type=int, default=20260919)
    p.add_argument('--years', type=int, default=12)
    p.add_argument('--warmup-sessions', type=int, default=756)
    p.add_argument('--declared-years')
    p.add_argument('--evidence-label', default='development', choices=('development','fresh_holdout'))
    a = p.parse_args()
    if a.output.exists():
        p.error('Output exists; preserve prior evidence and choose a new path')
    stocks, market_closes, benchmark, risk_free = load_inputs(a.bars_csv, a.risk_free_csv)
    declared = [int(v) for v in a.declared_years.split(',') if v.strip()] if a.declared_years else None
    protocol = SeededYearProtocol(seed=a.seed, selected_years=len(declared) if declared else a.years, warmup_sessions=a.warmup_sessions)
    availability = eligible_years(stocks, benchmark, risk_free, protocol)
    if declared:
        available = {row['year'] for row in availability if row['eligible']}
        missing = sorted(set(declared) - available)
        if missing:
            p.error('Declared years unavailable: ' + ','.join(map(str, missing)))
        years = sorted(declared)
    else:
        years = [row['year'] for row in select_seeded_years(availability, protocol)]

    configs = {
        'tba8': build_control_config(alpha_model='tba8', construction='phase2', risk_model='v203', phase3_enabled=True),
        'minerva_v1': build_control_config(alpha_model='minerva_v1', construction='phase2', risk_model='v203', phase3_enabled=True),
        'alpha_v1': build_control_config(alpha_model='alpha_v1', construction='phase2', risk_model='v203', phase3_enabled=True),
        'alpha_v11': build_control_config(alpha_model='alpha_v11', construction='phase2', risk_model='v203', phase3_enabled=True),
    }
    reports = {
        name: run_seeded_yearly_trials(stocks, cfg, benchmark, risk_free, protocol=protocol, market_closes=market_closes, declared_years=years)
        for name, cfg in configs.items()
    }
    fingerprints = {name: [t['dataset_fingerprint'] for t in report['trials']] for name, report in reports.items()}
    reference = fingerprints['tba8']
    if any(values != reference for values in fingerprints.values()):
        raise RuntimeError('Alpha V1.1 paired arms did not use identical datasets')
    summaries = {name: summarize(report) for name, report in reports.items()}
    result = {
        'status': 'research_only',
        'evidence_label': a.evidence_label,
        'protocol': {
            'seed': a.seed, 'declared_years': years, 'construction': 'phase2', 'risk': 'v203', 'phase3': True,
            'only_intended_difference': 'alpha model',
        },
        'summaries': summaries,
        'alpha_v11_minus_alpha_v1': _delta(summaries['alpha_v11'], summaries['alpha_v1']),
        'alpha_v11_minus_tba8': _delta(summaries['alpha_v11'], summaries['tba8']),
        'alpha_v11_minus_minerva_v1': _delta(summaries['alpha_v11'], summaries['minerva_v1']),
        'dataset_fingerprints': reference,
        'input_fingerprint': hashlib.sha256(a.bars_csv.read_bytes() + a.risk_free_csv.read_bytes()).hexdigest(),
        'promotion_question': 'Does Alpha V1.1 improve robustness versus Alpha V1 while preserving or improving economics under identical downstream layers?',
        'qualification': 'Do not tune V1.1 to this development panel. Fix causal/mathematical defects only, then freeze and use a fresh point-in-time holdout.',
    }
    a.output.parent.mkdir(parents=True, exist_ok=True)
    a.output.write_text(json.dumps(result, indent=2, allow_nan=False) + '\n')
    print(json.dumps(result, indent=2, allow_nan=False))


if __name__ == '__main__':
    main()
