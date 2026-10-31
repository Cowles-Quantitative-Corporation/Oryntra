"""Offline source-basis boundary. Acquired bars are never baseline admission."""
from pathlib import Path
import numpy as np
import pandas as pd
from .identity import digest


def require_frozen_replay_sources(root, config):
    """Frozen training/Beta may only consume their original price source bytes."""
    root = Path(root)
    for key, filename in [('prices_file', 'final_prices.csv.gz'), ('spy_file', 'final_spy.csv.gz')]:
        supplied = root / config['data'][key]
        original = root / 'inputs' / filename
        if digest(supplied) != digest(original):
            raise ValueError('Changed source basis requires regenerated matched baseline, V1A and causal Beta/eligibility; frozen replay rejected: ' + key)


def inspect_bars(raw, adjusted):
    """Validate unchanged-volume yfinance OHLC derivative, without filling gaps.

    Inputs must retain the provider Date/Datetime index. Date-only session labels
    are derived from local timestamp calendar dates, not UTC date conversion.
    Return diagnostics, not an eligibility or point-in-time certificate.
    """
    raw = raw.copy(); adjusted = adjusted.copy()
    required = ['Open', 'High', 'Low', 'Close', 'Adj Close', 'Volume', 'Dividends', 'Stock Splits']
    missing = [c for c in required if c not in raw]
    if missing:
        raise ValueError('Missing original provider columns: ' + ', '.join(missing))
    if not raw.index.equals(adjusted.index):
        raise ValueError('Raw and derivative provider timestamps differ')
    if not raw.index.is_unique or not raw.index.is_monotonic_increasing:
        raise ValueError('Duplicate or unordered provider timestamps')
    dates = pd.Index([pd.Timestamp(v).date().isoformat() for v in raw.index])
    if not dates.is_unique:
        raise ValueError('Multiple provider rows for one local session date')
    values = raw[required].apply(pd.to_numeric, errors='raise')
    finite = np.isfinite(values.to_numpy(float)).all(axis=1)
    positive = (values[['Open','High','Low','Close','Adj Close']] > 0).all(axis=1)
    valid_volume = values.Volume >= 0
    valid_actions = (values.Dividends >= 0) & (values['Stock Splits'] >= 0)
    valid_ohlc = (values.High >= values[['Open','Close','Low']].max(axis=1)) & (values.Low <= values[['Open','Close','High']].min(axis=1))
    factor = values['Adj Close'] / values.Close
    for col in ['Open', 'High', 'Low', 'Close', 'Volume']:
        if col not in adjusted:
            raise ValueError('Missing adjusted derivative column: ' + col)
        expected = values[col] if col == 'Volume' else values[col] * factor
        if not np.allclose(adjusted[col].to_numpy(float), expected.to_numpy(float), rtol=1e-9, atol=1e-10, equal_nan=True):
            raise ValueError('Derivative transformation mismatch: ' + col)
    valid = finite & positive & valid_volume & valid_actions & valid_ohlc
    return {'rows': len(raw), 'valid_rows': int(valid.sum()), 'invalid_session_dates': dates[~valid].tolist(), 'session_dates': dates.tolist(), 'first_session': dates[0] if len(dates) else None, 'last_session': dates[-1] if len(dates) else None, 'volume_basis': 'provider volume preserved; compatibility for liquidity unverified', 'source_basis': 'new acquisition; not frozen183', 'admitted': False}


def inspect_panel(symbol_bars, benchmark_bars, *, start, end, warmup=252, benchmark_symbol="SPY"):
    """Performance-independent coverage audit of separately validated derivatives.

    All supplied symbols remain in the report. Missing sessions are not filled.
    The benchmark defines the actual session calendar; coverage is not training
    eligibility, security classification, liquidity proof, or PIT membership.
    """
    if type(warmup) is not int or warmup < 0:
        raise ValueError('Nonnegative integer warmup required')
    calendar = pd.DatetimeIndex(benchmark_bars.index)
    if not calendar.is_unique or not calendar.is_monotonic_increasing:
        raise ValueError('Invalid benchmark session calendar')
    first, last = calendar.get_loc(pd.Timestamp(start)), calendar.get_loc(pd.Timestamp(end))
    if first < warmup + 1 or last < first:
        raise ValueError('Insufficient benchmark history or reversed study dates')
    required = calendar[first-warmup-1:last+1]
    rows = []
    for symbol, bars in sorted(symbol_bars.items()):
        if symbol == benchmark_symbol:
            rows.append({'symbol': symbol, 'complete_prices': False, 'status': 'benchmark_only_excluded_from_stock_count', 'missing_sessions': None})
            continue
        if bars is None:
            rows.append({'symbol': symbol, 'complete_prices': False, 'status': 'acquisition_failed_or_missing', 'missing_sessions': len(required)})
            continue
        if not bars.index.is_unique or not bars.index.is_monotonic_increasing:
            raise ValueError('Invalid symbol session calendar: ' + symbol)
        values = bars.Close.reindex(required).to_numpy(float)
        good = np.isfinite(values) & (values > 0)
        rows.append({'symbol': symbol, 'complete_prices': bool(good.all()), 'status': 'coverage_only', 'missing_sessions': int((~good).sum())})
    count = sum(row['complete_prices'] for row in rows)
    return {'candidate_count': sum(row['symbol'] != benchmark_symbol for row in rows), 'input_symbol_count': len(rows), 'benchmark_symbol': benchmark_symbol, 'complete_price_count': count, 'target300_price_coverage': count >= 300, 'target500_price_coverage': count >= 500, 'required_start': str(required[0].date()), 'required_end': str(required[-1].date()), 'rows': rows, 'replay_ready': False, 'blockers': ['Verify security types and issuer/share-class identities', 'Verify compatible price/share-volume liquidity basis', 'Declare static-survivor versus authentic PIT membership scope', 'Regenerate universe/source-specific V1A and causal Beta/eligibility inputs', 'Replay matched baseline and candidates on the same new source basis']}
