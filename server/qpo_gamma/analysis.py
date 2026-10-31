"""Paired attribution and conservative exploratory screens, not causal ranking."""
from pathlib import Path
import json
import numpy as np
import pandas as pd
from .config import delta, validate
from .identity import atomic_json, digest, verify_completed


CASH_COMPONENTS = ('CASH_INTEREST', 'TRANSACTION_COST')


def metrics(x, rf=0.04):
    x = np.asarray(x, float)
    if x.ndim != 1 or not len(x) or not np.isfinite(x).all() or (x <= -1).any():
        raise ValueError('Invalid daily returns')
    if not np.isfinite(rf) or rf <= -1:
        raise ValueError('Invalid reporting risk-free rate')
    equity = np.cumprod(1 + x)
    vol = np.std(x, ddof=0)
    excess = x - ((1 + rf) ** (1 / 252) - 1)
    return {
        'sessions': len(x), 'total_return': float(equity[-1] - 1),
        'CAGR': float(equity[-1] ** (252 / len(x)) - 1),
        'Sharpe': float(np.sqrt(252) * excess.mean() / vol) if vol > 1e-12 else None,
        'max_drawdown': float(np.min(equity / np.maximum.accumulate(np.r_[1, equity])[1:] - 1)),
        'annual_volatility': float(vol * np.sqrt(252)),
    }


def _validate_frame(frame, name):
    if (not len(frame) or not isinstance(frame.index, pd.DatetimeIndex)
            or frame.index.hasnans or not frame.index.is_unique
            or not frame.index.is_monotonic_increasing):
        raise ValueError(name + ' dates must be nonempty, unique and increasing')
    if not frame.columns.is_unique or not len(frame.columns):
        raise ValueError(name + ' columns must be nonempty and unique')
    if not np.isfinite(frame.to_numpy(dtype=float)).all():
        raise ValueError(name + ' must contain finite values; missing data cannot be zero-filled')


def _validate_targets(targets, calendar, stock_columns):
    _validate_frame(targets, 'Targets')
    if not targets.columns.equals(stock_columns):
        raise ValueError('Target stock identity/order mismatch')
    if targets.index[0] != calendar[0] or not targets.index.isin(calendar).all():
        raise ValueError('Targets must begin at the first ledger date and lie in its calendar')
    if (targets.to_numpy(dtype=float) < 0).any():
        raise ValueError('Targets must be nonnegative')


def daily_attribution(targets, returns, book, initial):
    _validate_frame(returns, 'Returns')
    _validate_frame(book.ledger, 'Ledger')
    _validate_targets(targets, returns.index, returns.columns)
    if not book.ledger.index.equals(returns.index):
        raise ValueError('Attribution return/ledger calendar mismatch')
    if not np.isfinite(initial) or initial <= 0:
        raise ValueError('Positive finite initial capital required')
    if any(s in returns.columns for s in CASH_COMPONENTS):
        raise ValueError('Stock symbols collide with cash/fee attribution components')
    rows = []
    prior = np.zeros(len(returns.columns))
    nav = initial
    for date, ret in returns.iterrows():
        ledger = book.ledger.loc[date]
        start = targets.loc[date].to_numpy() * (nav - ledger.cost_usd) if date in targets.index else prior
        pnl = start * ret.to_numpy()
        rows.append(np.r_[pnl, ledger.cash_interest_usd, -ledger.cost_usd])
        prior = start + pnl
        nav = float(ledger.equity_usd)
    frame = pd.DataFrame(rows, index=returns.index, columns=[*returns.columns, *CASH_COMPONENTS])
    if not np.allclose(frame.sum(axis=1).cumsum() + initial, book.ledger.equity_usd, atol=1e-8, rtol=1e-12):
        raise AssertionError('Daily dollar attribution does not reconcile')
    return frame


def paired_bootstrap(a, b, *, block, reps, seed, rf):
    a, b = np.asarray(a, float), np.asarray(b, float)
    if (a.ndim != 1 or a.shape != b.shape or type(block) is not int or block < 1
            or type(reps) is not int or reps < 1 or len(a) < block
            or not np.isfinite(a).all() or not np.isfinite(b).all()):
        raise ValueError('Invalid paired bootstrap inputs')
    metrics(a, rf)
    metrics(b, rf)
    rng = np.random.default_rng(seed)
    n, stats, null = len(a), [], []
    difference = b - a
    center = difference - difference.mean()
    for _ in range(reps):
        starts = rng.integers(n, size=int(np.ceil(n / block)))
        ix = ((starts[:, None] + np.arange(block)) % n).ravel()[:n]
        ma, mb = metrics(a[ix], rf), metrics(b[ix], rf)
        stats.append([mb[k] - ma[k] if ma[k] is not None and mb[k] is not None else np.nan
                      for k in ['CAGR', 'Sharpe', 'max_drawdown']])
        null.append(float(center[ix].mean()))
    value = {}
    for j, k in enumerate(['CAGR', 'Sharpe', 'max_drawdown']):
        vals = np.asarray(stats)[:, j]
        vals = vals[np.isfinite(vals)]
        value[k] = {'lower': float(np.quantile(vals, .025)), 'upper': float(np.quantile(vals, .975)),
                    'valid_replications': len(vals)} if len(vals) else None
    p = (1 + sum(abs(v) >= abs(difference.mean()) for v in null)) / (reps + 1)
    return {'intervals_95': value, 'paired_mean_return_pvalue': float(p),
            'block_sessions': block, 'replications': reps,
            'method': 'paired circular whole-path block bootstrap; dependent-stock attribution is not independently resampled',
            'interpretation': 'exploratory; finite blocks and prior strategy search limit calibration'}


def holm(pvalues):
    values = np.asarray(pvalues, float)
    if values.ndim != 1 or not np.isfinite(values).all() or ((values < 0) | (values > 1)).any():
        raise ValueError('Invalid p values')
    order = np.argsort(values)
    out = np.empty(len(values))
    last = 0.
    for i, j in enumerate(order):
        last = max(last, min(1., (len(values) - i) * values[j]))
        out[j] = last
    return out.tolist()


def _sector_label(value):
    """Missing CSV values and textual placeholders are never sector evidence."""
    if pd.isna(value):
        return 'UNKNOWN'
    label = str(value).strip().upper()
    if label in {'', 'UNKNOWN', 'NAN', 'NONE', 'NULL', 'NA', 'N/A', '<NA>', 'MISSING', 'UNCLASSIFIED'}:
        return 'UNKNOWN'
    return label


def breadth(stock_delta, sectors, evidence, *, cash_interest_delta_usd=0., transaction_cost_delta_usd=0.):
    """Contribution breadth, with stock-only and whole-book deletion stresses.

    transaction_cost_delta_usd is a signed P&L contribution (negative when the
    candidate pays more), not the positive expense delta exposed by compare().
    """
    x = pd.Series(stock_delta, dtype=float)
    if not x.index.is_unique or not np.isfinite(x.to_numpy()).all():
        raise ValueError('Stock attribution must be unique and finite')
    if not np.isfinite([cash_interest_delta_usd, transaction_cost_delta_usd]).all():
        raise ValueError('Cash and fee contribution deltas must be finite')
    active = x[abs(x) > 1e-8]
    positive = active[active > 0].sort_values(ascending=False, kind='stable')
    negative = active[active < 0]
    total_positive = float(positive.sum())
    shares = positive / total_positive if total_positive > 0 else positive * 0
    labels = [_sector_label(sectors.get(s)) for s in positive.index]
    known = {s for s in labels if s != 'UNKNOWN'}
    improving_fraction = len(positive) / len(active) if len(active) else 0.
    top1, top3 = float(shares.iloc[:1].sum()), float(shares.iloc[:3].sum())
    net = float(x.sum())
    cash_and_fees = float(cash_interest_delta_usd + transaction_cost_delta_usd)
    without1, without3 = net - float(positive.iloc[:1].sum()), net - float(positive.iloc[:3].sum())
    gates = {
        'minimum_supporting_stocks': len(positive) >= evidence['minimum_supporting_stocks'],
        'minimum_improving_fraction': improving_fraction >= evidence['minimum_improving_fraction'],
        'minimum_sector_breadth': len(known) >= evidence['minimum_sectors'],
        'top1_concentration': bool(total_positive > 0 and top1 <= evidence['top1_max_positive_share']),
        'top3_concentration': bool(total_positive > 0 and top3 <= evidence['top3_max_positive_share']),
        'positive_without_top1': without1 > 0, 'positive_without_top3': without3 > 0,
        'positive_total_without_top1': without1 + cash_and_fees > 0,
        'positive_total_without_top3': without3 + cash_and_fees > 0,
    }
    return {
        'supporting_stocks': len(positive), 'hurting_stocks': len(negative), 'changed_stocks': len(active),
        'support_definition': 'positive material paired realized stock dollar-contribution change; not forecast-loss improvement or independent replication',
        'improving_fraction': improving_fraction, 'known_supporting_sector_groups': len(known),
        'unknown_supporting_sectors': sum(s == 'UNKNOWN' for s in labels),
        'top1_positive_share': top1, 'top3_positive_share': top3, 'net_stock_delta_usd': net,
        # Preserve old stock-only fields, but explicitly distinguish whole-book totals.
        'fixed_path_without_top1_usd': without1, 'fixed_path_without_top3_usd': without3,
        'fixed_path_stock_only_without_top1_usd': without1,
        'fixed_path_stock_only_without_top3_usd': without3,
        'cash_interest_delta_usd': float(cash_interest_delta_usd),
        'transaction_cost_contribution_delta_usd': float(transaction_cost_delta_usd),
        'total_delta_usd': net + cash_and_fees,
        'fixed_path_total_without_top1_usd': without1 + cash_and_fees,
        'fixed_path_total_without_top3_usd': without3 + cash_and_fees,
        'removed_top1_symbols': list(positive.index[:1]), 'removed_top3_symbols': list(positive.index[:3]),
        'winner_removal_kind': 'arithmetic deletion of realized paired stock dollar contributions; stock-only fields exclude cash and fees; total fields retain unchanged realized cash-interest and fee deltas; NOT rerun/reoptimized exclusion portfolios',
        'gates': gates, 'descriptive_breadth_pass': all(gates.values()),
        'minimum_four_is_not_significance': True,
    }


def _load_arm(path):
    """Read a completed arm and bind every exported accounting table together."""
    path = Path(path)
    if not (path / 'COMPLETED.json').exists():
        raise ValueError('Paired attribution requires two completed arms')
    registration = json.loads((path / 'registration.json').read_text())
    config = validate(registration['config'])
    completion = verify_completed(path)
    if completion.get('trial_id') != registration['trial_id']:
        raise ValueError('Completion marker trial identity mismatch')
    ledger = pd.read_csv(path / 'ledger.csv.gz', index_col=0, parse_dates=True)
    attribution = pd.read_csv(path / 'daily_dollar_attribution.csv.gz', index_col=0, parse_dates=True)
    targets = pd.read_csv(path / 'targets.csv.gz', index_col=0, parse_dates=True)
    _validate_frame(ledger, 'Ledger')
    _validate_frame(attribution, 'Attribution')
    required = {'net_return', 'equity_usd', 'cash_interest_usd', 'cost_usd', 'turnover_L1', 'start_gross'}
    if not required <= set(ledger.columns) or not set(CASH_COMPONENTS) <= set(attribution.columns):
        raise ValueError('Missing required ledger or cash/fee attribution fields')
    if not attribution.index.equals(ledger.index):
        raise ValueError('Attribution/ledger calendar mismatch')
    stock_columns = attribution.columns[~attribution.columns.isin(CASH_COMPONENTS)]
    if not len(stock_columns):
        raise ValueError('Stock attribution is missing')
    _validate_targets(targets, ledger.index, stock_columns)
    if (ledger.equity_usd <= 0).any() or (ledger.cost_usd < 0).any():
        raise ValueError('Invalid equity or transaction costs')
    initial = config['execution']['initial_usd']
    if not np.allclose(attribution.sum(axis=1).cumsum() + initial, ledger.equity_usd, atol=1e-8, rtol=1e-12):
        raise ValueError('Exported attribution does not reconcile to equity')
    if (not np.allclose(attribution.CASH_INTEREST, ledger.cash_interest_usd, atol=1e-8, rtol=1e-12)
            or not np.allclose(attribution.TRANSACTION_COST, -ledger.cost_usd, atol=1e-8, rtol=1e-12)):
        raise ValueError('Exported cash/fee attribution does not reconcile')
    previous = np.r_[initial, ledger.equity_usd.to_numpy()[:-1]]
    if not np.allclose(ledger.net_return, ledger.equity_usd / previous - 1, atol=1e-12, rtol=1e-10):
        raise ValueError('Exported returns do not reconcile to equity')
    metrics(ledger.net_return, config['execution']['metric_rf'])
    return {'path': path, 'registration': registration, 'config': config,
            'ledger': ledger, 'attribution': attribution, 'targets': targets}


def _validate_pair(a, b):
    ra, rb, ca, cb = a['registration'], b['registration'], a['config'], b['config']
    for key in ['runtime_sha256', 'code_sha256', 'data_hashes', 'pilot_decisions', 'purpose']:
        if key not in ra or key not in rb or ra[key] != rb[key]:
            raise ValueError('Paired attribution requires identical code/data/runtime/evaluation scope: ' + key)
        if key != 'pilot_decisions' and not ra[key]:
            raise ValueError('Paired attribution requires recorded identity: ' + key)
    for section, key in [('study', 'seed'), ('study', 'label'), ('execution', 'initial_usd'), ('execution', 'metric_rf')]:
        if ca[section][key] != cb[section][key]:
            raise ValueError('Matched seed and reporting basis required: ' + section + '.' + key)
    if ca['evidence'] != cb['evidence']:
        raise ValueError('Paired attribution requires identical preregistered evidence rules')
    if not a['ledger'].index.equals(b['ledger'].index):
        raise ValueError('Paired daily calendar mismatch')
    if not a['attribution'].columns.equals(b['attribution'].columns):
        raise ValueError('Paired P&L stock identity/order mismatch')
    if (not a['targets'].index.equals(b['targets'].index)
            or not a['targets'].columns.equals(b['targets'].columns)):
        raise ValueError('Paired decision target identity/calendar mismatch')


def _load_sectors(root, arm):
    filename = arm['config']['data']['sectors_file']
    if not filename:
        return {}
    path = Path(root) / filename
    expected = arm['registration']['data_hashes'].get('sectors')
    if not expected or digest(path) != expected:
        raise ValueError('Sector labels differ from registered input identity')
    table = pd.read_csv(path)
    if not {'symbol', 'sector'} <= set(table.columns) or table.symbol.isna().any() or table.symbol.duplicated().any():
        raise ValueError('Sector labels require unique nonmissing symbols and a sector column')
    return {s: _sector_label(v) for s, v in zip(table.symbol, table.sector)}


def compare(root, base_path, candidate_path, output=None):
    root = Path(root)
    a, b = _load_arm(base_path), _load_arm(candidate_path)
    _validate_pair(a, b)
    ra, rb, ca, cb = a['registration'], b['registration'], a['config'], b['config']
    da, db, pa, pb = a['ledger'], b['ledger'], a['attribution'], b['attribution']
    diff = pb - pa
    stock_cols = [s for s in diff if s not in CASH_COMPONENTS]
    sectors = _load_sectors(root, b)
    e = cb['evidence']
    cash_delta, fee_contribution_delta = float(diff.CASH_INTEREST.sum()), float(diff.TRANSACTION_COST.sum())
    breadth_report = breadth(diff[stock_cols].sum(), sectors, e,
                            cash_interest_delta_usd=cash_delta, transaction_cost_delta_usd=fee_contribution_delta)
    ma, mb = metrics(da.net_return, cb['execution']['metric_rf']), metrics(db.net_return, cb['execution']['metric_rf'])
    metric_delta = {k: mb[k] - ma[k] if ma[k] is not None and mb[k] is not None else None for k in ma if k != 'sessions'}
    blocks = []
    for ix in np.array_split(np.arange(len(da)), min(len(da), e['time_blocks'])):
        x, y = metrics(da.net_return.iloc[ix], cb['execution']['metric_rf']), metrics(db.net_return.iloc[ix], cb['execution']['metric_rf'])
        blocks.append({'start': str(da.index[ix[0]]), 'end': str(da.index[ix[-1]]), 'sessions': len(ix),
                       'delta_total_return': y['total_return'] - x['total_return'], 'delta_CAGR': y['CAGR'] - x['CAGR']})
    changed = delta(ca, cb)
    positive_blocks = sum(x['delta_total_return'] > 0 for x in blocks)
    report = {
        'baseline_trial_id': ra['trial_id'], 'candidate_trial_id': rb['trial_id'], 'config_changes': changed,
        'single_setting_intervention': len(changed) == 1,
        'estimator_coupling': 'When errors.policy is matched_as_issued, changing the forecast also changes its own matured error distribution; this is a complete forecast-policy comparison, not a pure mean-only effect',
        'baseline': ma, 'candidate': mb, 'metric_deltas': metric_delta,
        'paired_mean_daily_return_delta': float((db.net_return - da.net_return).mean()),
        'dollar_delta_by_component': diff.sum().to_dict(), 'breadth': breadth_report,
        'time_blocks': blocks, 'positive_blocks': positive_blocks,
        'time_stability_pass': len(blocks) == e['time_blocks'] and positive_blocks >= e['minimum_positive_blocks'],
        'cash_interest_delta_usd': cash_delta, 'cost_delta_usd': -fee_contribution_delta,
        'transaction_cost_contribution_delta_usd': fee_contribution_delta,
        'total_dollar_delta_usd': float(diff.sum().sum()),
        'turnover_L1_delta': float(db.turnover_L1.sum() - da.turnover_L1.sum()),
        'mean_gross_delta': float(db.start_gross.mean() - da.start_gross.mean()), 'unpromoted': True,
        'causal_claim': 'Controlled code intervention on this fixed historical study only; not an independent causal forecast-skill or future-performance claim',
        'reoptimized_exclusion_test': 'not_run; fixed-path arithmetic must not be substituted',
        'untouched_confirmation': 'not_observed',
        'family_alpha': e['family_alpha'],
        'family_screen_status': 'not_evaluated_in_standalone_pair; requires complete declared design family',
    }
    if len(da) >= e['bootstrap_block_sessions']:
        report['uncertainty'] = paired_bootstrap(da.net_return, db.net_return,
                                               block=e['bootstrap_block_sessions'], reps=e['bootstrap_replications'],
                                               seed=cb['study']['seed'], rf=cb['execution']['metric_rf'])
    else:
        report['uncertainty'] = {'status': 'insufficient_sessions_for_registered_block_bootstrap'}
    sector_values = {}
    for symbol, value in diff[stock_cols].sum().items():
        label = sectors.get(symbol, 'UNKNOWN')
        sector_values[label] = sector_values.get(label, 0) + float(value)
    out = Path(output) if output else root / 'comparisons' / (ra['trial_id'][:12] + '_' + rb['trial_id'][:12])
    out.mkdir(parents=True, exist_ok=True)
    atomic_json(out / 'comparison.json', report)
    diff.sum().rename('delta_usd').to_csv(out / 'stock_and_cash_cost_deltas.csv')
    pd.DataFrame(blocks).to_csv(out / 'time_blocks.csv', index=False)
    atomic_json(out / 'sector_deltas.json', {'classification': 'supplied normalized labels, not implicitly historical PIT sectors', 'deltas_usd': sector_values})
    (b['targets'] - a['targets']).to_csv(out / 'decision_target_deltas.csv.gz', compression='gzip')
    return report, out


def interaction(values00, values10, values01, values11):
    arrays = [np.asarray(x, float) for x in [values00, values10, values01, values11]]
    if len({x.shape for x in arrays}) != 1 or not all(np.isfinite(x).all() for x in arrays):
        raise ValueError('Aligned finite four-cell outcomes required')
    return arrays[3] - arrays[1] - arrays[2] + arrays[0]
