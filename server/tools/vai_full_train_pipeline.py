from __future__ import annotations
import argparse, subprocess, sys
from pathlib import Path
APP_DIR=Path(__file__).resolve().parents[1]
PY=sys.executable

def run(cmd):
    print('\n$ '+' '.join(map(str,cmd)))
    p=subprocess.run(cmd, cwd=APP_DIR)
    if p.returncode!=0: raise SystemExit(p.returncode)

def main():
    ap=argparse.ArgumentParser(description='Overnight VAI2.2 training pipeline.')
    ap.add_argument('--ticker-file', default='')
    ap.add_argument('--tickers', default='')
    ap.add_argument('--training150', action='store_true')
    ap.add_argument('--warm', action='store_true')
    ap.add_argument('--period', default='5y')
    ap.add_argument('--delay', type=float, default=13.0)
    ap.add_argument('--step', type=int, default=3)
    ap.add_argument('--max-tests-per-ticker', type=int, default=120)
    ap.add_argument('--horizons', default='5,10,20')
    ap.add_argument('--data-source', default='cache_only')
    args=ap.parse_args()
    common=[]
    if args.training150: common += ['--training150']
    if args.ticker_file: common += ['--ticker-file', args.ticker_file]
    elif args.tickers: common += ['--tickers', args.tickers]
    if args.warm:
        warm=[PY,'tools/warm_cache_cli.py','--period',args.period,'--delay',str(args.delay)]
        if args.training150: warm.append('--training150')
        warm += common
        run(warm)
    for h in [x.strip() for x in args.horizons.split(',') if x.strip()]:
        cmd=[PY,'tools/train_vai2_cli.py','--period',args.period,'--horizon',h,'--step',str(args.step),'--max-tests-per-ticker',str(args.max_tests_per_ticker),'--data-source',args.data_source,'--label',f'h{h}_step{args.step}']+common
        run(cmd)
    print('\nVAI2.2 OVERNIGHT PIPELINE COMPLETE')
    return 0
if __name__=='__main__': raise SystemExit(main())
