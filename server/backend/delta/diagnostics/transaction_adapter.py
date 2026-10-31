"""Synthetic-only pure-gate -> mocked atomic reservation bridge.

This is not called by v1.4 or any broker. It provides inspectable B06 contract
evidence, not acceptance of the B02/B03 trade-affecting adapter.
"""
from copy import deepcopy
from backend.delta.diagnostics.diagnostic_gate import evaluate,decimal
from backend.delta.diagnostics.risk_math import digest,ns
from backend.delta.diagnostics.arithmetic_policy import controlled,SPEC


@controlled
def admit_synthetic(ledger,market_snapshot,proposal,profile,cov,scenarios,*,evaluation_time,commit_time):
    market_snapshot,proposal,profile,cov,scenarios=deepcopy((market_snapshot,proposal,profile,cov,scenarios))
    ns(evaluation_time);ns(commit_time)
    if commit_time < evaluation_time:
        raise ValueError('commit precedes evaluation')
    state=ledger.snapshot()
    snapshot=deepcopy(market_snapshot)
    snapshot.update(cash=state['cash'],purchase_payable=state['payable'],receivables=[],
                    holdings=state['holdings'],pending=[])
    if state['receivable']!='0':
        return dict(decision='NOT_EVALUABLE',missing=['TYPED_RECEIVABLE_MAPPING'],action_authorized=False)
    for ident,o in state['orders'].items():
        if o['status']=='TERMINAL':continue
        if decimal(o['notional'])>0:
            return dict(decision='NOT_EVALUABLE',missing=['PARTIAL_PARENT_FEE_MAPPING'],action_authorized=False)
        snapshot['pending'].append(dict(id=ident,symbol=o['symbol'],side='BUY',quantity=o['remaining'],
                                        step=o.get('step','1'),limit=o['limit'],at_ns=state['clock'],
                                        status='CANCEL_PENDING' if o['status']=='CANCEL_PENDING' else 'OPEN'))
    first=evaluate(snapshot,proposal,profile,cov,scenarios,evaluation_time)
    if first['decision']!='PASS_RESEARCH':return first
    # Re-evaluate the *same source snapshot* at the explicit later clock. Do not
    # refresh an old source by assigning commit time to its timestamps.
    final=evaluate(snapshot,proposal,profile,cov,scenarios,commit_time)
    if final['decision']!='PASS_RESEARCH':return final
    if state['latch']:
        return dict(decision='NOT_EVALUABLE',missing=['INCIDENT_LATCH'],action_authorized=False)
    event=dict(id='mock-admission:'+proposal['id'],kind='ADMIT',at_ns=commit_time,
               expected_version=state['version'],context_hash=digest(state),
               intent_id=proposal['id'],symbol=proposal['symbol'],quantity=proposal['quantity'],
               limit=proposal['limit'],step=proposal['step'],execution_quantum=proposal['step'],fee=deepcopy(profile['fee']),
               quote_ns=min(min(x['event_ns'],x['available_ns']) for x in snapshot['marks'].values()),
               max_age_ns=profile['max_age_ns'],valid_until_ns=profile['valid_until_ns'],
               clock_quality=profile['clock_quality'],profile_kind=profile['kind'],
               risk_decision_hash=digest(final),source_snapshot_hash=digest(snapshot),
               policy_hash=digest(profile),capability_hash=digest(dict(status=profile['capability_status'],scope=profile['execution_scope'])),
               covariance_hash=digest(cov),scenario_hash=digest(scenarios),
               arithmetic_policy=deepcopy(SPEC),mode='SYNTHETIC_MOCK_RESERVATION_ONLY')
    ledger.event(event)
    return dict(final,mock_committed=True,reservation_event=event,action_authorized=False)
