from __future__ import annotations
import argparse, asyncio, json, sys
from pathlib import Path
APP_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP_DIR))
from backend.database import init_db, store_vai_training_run
from backend.routes.dev_tools import PatternLabRequest, _run_pattern_lab_core
from backend.vai2_model import train_vai2_from_lab_rows, get_vai2_model_status
from tools.cache_guard import backup_db, cache_counts, assert_not_shrunk
from tools.warm_cache_cli import DEFAULT_150


def _ascii_bar(pct: float, width: int = 34) -> str:
    pct = max(0.0, min(100.0, float(pct or 0)))
    filled = int(round((pct / 100.0) * width))
    return '[' + '#' * filled + '-' * (width - filled) + f'] {pct:6.2f}%'


async def _progress_monitor(job: dict, stop_event: asyncio.Event, label: str = 'Pattern Lab') -> None:
    last_len = 0
    while not stop_event.is_set():
        pct = float(job.get('progress_pct') or 0.0)
        phase = str(job.get('phase') or 'starting')
        ticker = str(job.get('current_ticker') or '-')
        ticker_i = int(job.get('current_ticker_index') or job.get('completed_tickers') or 0)
        ticker_total = int(job.get('total_tickers') or len(job.get('tickers') or []) or 0)
        test_i = int(job.get('current_test_index') or 0)
        test_total = int(job.get('current_ticker_tests') or 0)
        checks = int(job.get('completed_checks') or 0)
        total_checks = int(job.get('total_checks_estimated') or 0)
        detail = ticker
        if ticker_total:
            detail += f' {ticker_i}/{ticker_total}'
        if test_total:
            detail += f' | test {test_i}/{test_total}'
        if total_checks:
            detail += f' | checks {checks}/{total_checks}'
        line = f'\r{label} {_ascii_bar(pct)} | {phase} | {detail}'
        print(line.ljust(last_len), end='', flush=True)
        last_len = max(last_len, len(line))
        await asyncio.sleep(0.5)
    pct = float(job.get('progress_pct') or 100.0)
    line = f'\r{label} {_ascii_bar(pct)} | complete'
    print(line.ljust(last_len), flush=True)

def parse_tickers(text, max_tickers=150):
    out=[]; seen=set()
    for raw in str(text or '').replace('\n', ',').split(','):
        t=''.join(ch for ch in raw.upper().strip() if ch.isalnum() or ch in '.-')
        if t and t not in seen:
            out.append(t); seen.add(t)
        if len(out)>=max_tickers: break
    return out

async def main_async(args):
    init_db()
    before=cache_counts()
    backup=backup_db('before_vai2_train') if args.backup_first else None
    tickers=parse_tickers(args.tickers, args.max_tickers)
    if args.training150:
        tickers=parse_tickers(DEFAULT_150, args.max_tickers)
    if args.ticker_file:
        tickers=parse_tickers(Path(args.ticker_file).read_text(encoding='utf-8'), args.max_tickers)
    if not tickers:
        raise SystemExit('No tickers supplied. Use --tickers or --ticker-file.')
    req=PatternLabRequest(tickers=tickers, period=args.period, horizon_days=args.horizon, step=args.step, min_history=args.min_history, max_tests_per_ticker=args.max_tests_per_ticker, data_source=args.data_source, engine_modes=['official'])
    print('VAI2.2 HEADLESS TRAINING START')
    print(f'Tickers={len(tickers)} period={args.period} horizon={args.horizon} step={args.step} max_tests/ticker={args.max_tests_per_ticker} data={args.data_source}')
    job={}
    stop_event=asyncio.Event()
    monitor=asyncio.create_task(_progress_monitor(job, stop_event, label='VAI2.2 Pattern Lab'))
    try:
        lab=await _run_pattern_lab_core(req, job=job)
    finally:
        stop_event.set()
        await monitor
    rows=(lab.get('rows') or {}).get('official') or []
    result=train_vai2_from_lab_rows(rows, horizon_days=args.horizon, min_samples=args.min_samples, force_promote=args.force_promote, run_label=args.label)
    terminal=result.get('terminal_output') or json.dumps(result, indent=2, default=str)
    print('\n'+terminal)
    try:
        model=(result.get('model') or {})
        store_vai_training_run(result.get('status') or ('trained' if result.get('ok') else 'failed'), int(model.get('samples') or len(rows)), args.horizon, model.get('threshold'), model.get('validation') or result, terminal)
    except Exception:
        pass
    after=cache_counts()
    guard=assert_not_shrunk(before, after, backup) if backup else {'before':before,'after':after,'ok': after['ohlcv_rows']>=before['ohlcv_rows']}
    print('\nCACHE GUARD')
    print(json.dumps(guard, indent=2, default=str))
    print('\nPROMOTED MODEL STATUS')
    print(json.dumps(get_vai2_model_status(), indent=2, default=str)[:4000])
    log_dir=APP_DIR/'data'/'training_logs'
    log_dir.mkdir(parents=True, exist_ok=True)
    run_id = (result.get('model') or {}).get('run_id','latest')
    log=(log_dir/f'vai2_train_{run_id}.txt')
    log.write_text(terminal+'\n\nCACHE GUARD\n'+json.dumps(guard, indent=2, default=str), encoding='utf-8')
    print(f'Log saved: {log}')
    return 0 if result.get('ok') else 2

def main():
    ap=argparse.ArgumentParser(description='Headless VAI2.2 trainer.')
    ap.add_argument('--tickers', default='AAPL,MSFT,NVDA,TSLA,AMZN,META,GOOGL,AMD,AVGO,JPM,V,XOM,CVX,UNH,LLY,JNJ,WMT,COST,HD,MCD,NKE,CAT,BA,RTX,NEE,PLTR,CRWD,SPY,QQQ,SMH')
    ap.add_argument('--ticker-file', default='')
    ap.add_argument('--training150', action='store_true')
    ap.add_argument('--max-tickers', type=int, default=150)
    ap.add_argument('--period', default='5y')
    ap.add_argument('--horizon', type=int, default=10)
    ap.add_argument('--step', type=int, default=3)
    ap.add_argument('--min-history', type=int, default=90)
    ap.add_argument('--max-tests-per-ticker', type=int, default=120)
    ap.add_argument('--data-source', default='cache_only', choices=['cache_only','cache_first','api_first'])
    ap.add_argument('--min-samples', type=int, default=300)
    ap.add_argument('--force-promote', action='store_true')
    ap.add_argument('--backup-first', action='store_true', default=True)
    ap.add_argument('--label', default='headless')
    args=ap.parse_args()
    return asyncio.run(main_async(args))
if __name__=='__main__': raise SystemExit(main())
