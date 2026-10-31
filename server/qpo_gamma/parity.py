"""Expanded no-intervention parity checks against the frozen native outputs."""
import numpy as np
from .config import defaults,flatten
from .legacy_accounting import replay


def native_noop(config):
    """Identify native semantics independently of the user's arm label."""
    expected=flatten(defaults());actual=flatten(config)
    keys=[k for k in expected if k.startswith(('portfolio.','execution.'))]
    keys+=['study.seed','training.artifact_mode','ranking.enabled','errors.policy','macro.mode']
    return all(actual[k]==expected[k] for k in keys)


def _max_error(a,b):
    if hasattr(a,'index') and not a.index.equals(b.index):raise AssertionError('Frozen no-op calendar differs')
    if hasattr(a,'columns') and not a.columns.equals(b.columns):raise AssertionError('Frozen no-op symbols/fields differ')
    x=np.asarray(a,dtype=float);y=np.asarray(b,dtype=float)
    if x.shape!=y.shape or not np.isfinite(x).all() or not np.isfinite(y).all():raise AssertionError('Frozen no-op values invalid')
    return float(np.max(abs(x-y))) if x.size else 0.


def verify_parity(daily,targets,book,expected,oldtargets,returns,execution,calls):
    reference=replay(oldtargets,returns,cost_bps=execution['cost_bps'],lending_annual=execution['cash_annual'],borrowing_annual=execution['borrow_annual'],initial=execution['initial_usd'],fee_convention=execution['fee_convention'])
    numerical=list(book.ledger.select_dtypes(include='number').columns)
    report={'max_daily_error':_max_error(daily,expected),'max_target_error':_max_error(targets,oldtargets),'max_holdings_dollar_error':_max_error(book.holdings,reference.holdings),'max_ledger_field_error':_max_error(book.ledger[numerical],reference.ledger[numerical]),'max_contribution_dollar_error':_max_error(book.contributions,reference.contributions),'native_mean_max_error':max((r['native_mean_max_error'] for r in calls),default=None),'decisions':len(targets),'sessions':len(daily),'input_scoring_provenance':'Each adapter call verifies native prefix mean and horizon-linear score convention; frozen source/input hashes verified before run','reference_book':'Independently replayed frozen native targets on the same return calendar; not a second solver run','tolerances':{'daily_return_and_target':1e-12,'native_mean':1e-11,'ledger_and_holdings_usd':1e-8}}
    if report['max_daily_error']>1e-12 or report['max_target_error']>1e-12:raise AssertionError('Frozen cold no-op return/target parity failed')
    if max(report[k] for k in ['max_holdings_dollar_error','max_ledger_field_error','max_contribution_dollar_error'])>1e-8:raise AssertionError('Frozen cold no-op book parity failed')
    if report['native_mean_max_error'] is None or report['native_mean_max_error']>1e-11:raise AssertionError('Frozen no-op input evidence missing')
    return report
