#!/usr/bin/env python3
"""Run one private control-plane configuration on a frozen annual-study panel."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.phase4_control import BENCHMARKS, build_control_config
from backend.universal_yearly_protocol import SeededYearProtocol, eligible_years, run_seeded_yearly_trials, select_seeded_years
from tools.run_v202_paired_risk_study import summarize


def load_inputs(bars_path: Path, risk_free_path: Path, benchmark_id: str):
    bars = pd.read_csv(bars_path)
    required = {"date", "ticker", "open", "high", "low", "close", "volume"}
    if not required.issubset(bars.columns):
        raise ValueError("Bars missing fields: " + ", ".join(sorted(required - set(bars.columns))))
    bars["date"] = pd.to_datetime(bars["date"])
    histories = {
        symbol: group.set_index("date").sort_index().rename(columns={"open":"Open","high":"High","low":"Low","close":"Close","volume":"Volume"})
        for symbol, group in bars.groupby("ticker")
    }
    components = BENCHMARKS[benchmark_id]["components"]
    missing = [symbol for symbol in components if symbol not in histories]
    if missing:
        raise ValueError("Bars missing benchmark symbols: " + ", ".join(missing))
    benchmark = None
    for symbol, weight in components.items():
        leg = histories[symbol]["Close"].pct_change(fill_method=None) * float(weight)
        benchmark = leg if benchmark is None else benchmark.add(leg, fill_value=0.0)
    cash = pd.read_csv(risk_free_path)
    risk_free = pd.Series(cash["return_daily"].to_numpy(float), index=pd.to_datetime(cash["date"]), dtype=float)
    stocks = {symbol: history for symbol, history in histories.items() if symbol not in {"SPY", "QQQ"}}
    market_closes = pd.DataFrame({symbol: histories[symbol]["Close"] for symbol in {"SPY","QQQ"} if symbol in histories})
    return stocks, market_closes, benchmark, risk_free


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--bars-csv', type=Path, required=True)
    p.add_argument('--risk-free-csv', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--alpha-model', choices=('minerva_v1','tba8'), default='tba8')
    p.add_argument('--construction', choices=('phase15','phase2'), default='phase15')
    p.add_argument('--risk-model', choices=('none','v1','v201','v202','v203'), default='v203')
    p.add_argument('--phase3', choices=('on','off'), default='on')
    p.add_argument('--benchmark', choices=tuple(BENCHMARKS), default='spy_qqq_equal')
    p.add_argument('--seed', type=int, default=20260918)
    p.add_argument('--years', type=int, default=12)
    p.add_argument('--warmup-sessions', type=int, default=756)
    p.add_argument('--declared-years')
    a=p.parse_args()
    if a.output.exists():
        p.error('Output exists; choose a new output path')
    stocks, market_closes, benchmark, risk_free = load_inputs(a.bars_csv, a.risk_free_csv, a.benchmark)
    config = build_control_config(alpha_model=a.alpha_model, construction=a.construction, risk_model=a.risk_model, phase3_enabled=a.phase3=='on')
    declared = [int(v) for v in a.declared_years.split(',') if v.strip()] if a.declared_years else None
    protocol=SeededYearProtocol(seed=a.seed, selected_years=len(declared) if declared else a.years, warmup_sessions=a.warmup_sessions)
    availability=eligible_years(stocks, benchmark, risk_free, protocol)
    years=sorted(declared) if declared else [row['year'] for row in select_seeded_years(availability, protocol)]
    report=run_seeded_yearly_trials(stocks, config, benchmark, risk_free, protocol=protocol, market_closes=market_closes if {'SPY','QQQ'}.issubset(market_closes.columns) else None, declared_years=years)
    result={
        'status':'research_only', 'configuration': {'alpha_model':a.alpha_model,'construction':a.construction,'risk_model':a.risk_model,'phase3_enabled':a.phase3=='on','benchmark':a.benchmark},
        'config_fingerprint': config.fingerprint, 'protocol': {'seed':a.seed,'declared_years':years},
        'summary': summarize(report), 'trials': report['trials'],
        'input_fingerprint': hashlib.sha256(a.bars_csv.read_bytes()+a.risk_free_csv.read_bytes()).hexdigest(),
        'qualification':'Private research control-plane study; not a release or performance claim.'
    }
    a.output.parent.mkdir(parents=True, exist_ok=True)
    a.output.write_text(json.dumps(result,indent=2,allow_nan=False)+'\n')
    print(json.dumps(result,indent=2,allow_nan=False))
if __name__=='__main__': main()
