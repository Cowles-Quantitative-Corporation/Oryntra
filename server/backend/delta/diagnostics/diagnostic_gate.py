"""Pure reject-only D01 diagnostics. Every output is non-executable.

Only explicitly synthetic fixtures may exercise PASS_RESEARCH. Project profiles
remain NOT_EVALUABLE pending independent adapter/profile qualification.
"""
from copy import deepcopy
from decimal import Decimal, ROUND_CEILING
from itertools import product
from math import prod
from backend.delta.diagnostics.risk_math import digest, ns, envelope
from backend.delta.diagnostics.arithmetic_policy import controlled,SPEC,validate_external

D = Decimal


def decimal(value, *, nonnegative=True):
    if type(value) is not str or not 0<len(value)<=256:
        raise ValueError('exact decimal string required')
    v = D(value)
    if not v.is_finite() or len(v.as_tuple().digits)>192 or not -128<=v.as_tuple().exponent<=128 or (v and not -128<=v.adjusted()<=128) or (nonnegative and v < 0):
        raise ValueError('finite nonnegative decimal required')
    return v


@controlled
def fee(notional, spec):
    """Explicit one-parent aggregate branch; no fee at zero fill."""
    if spec['aggregation'] != 'ONE_PARENT_AGGREGATE_SYNTHETIC':
        raise ValueError('unqualified fee aggregation')
    rate, floor, quantum = (validate_external(spec[k]) for k in ('rate', 'minimum', 'quantum'))
    if min(rate,floor,quantum)<0:
        raise ValueError('negative fee inputs')
    if quantum <= 0 or rate >= 1:
        raise ValueError('fee domain')
    return ((max(notional*rate, floor)/quantum).to_integral_value(rounding=ROUND_CEILING)*quantum
            if notional > 0 else D(0))


@controlled
def normalized_cash(convention, cash, purchase_payable):
    cash, payable = validate_external(cash), validate_external(purchase_payable)
    if min(cash,payable)<0:raise ValueError('negative cash/payable')
    if convention == 'GROSS_CASH_PLUS_PURCHASE_PAYABLE':
        return cash - payable
    if convention == 'NATIVE_BUYS_ALREADY_DEBITED' and payable == 0:
        return cash
    raise ValueError('do not subtract payable twice from already-debited native cash')


@controlled
def intent_class(owned, delta):
    validate_external(owned);validate_external(delta)
    owned, delta = decimal(owned), decimal(delta, nonnegative=False)
    if delta > 0:
        return 'OPEN' if owned == 0 else 'INCREASE'
    if delta < 0:
        if -delta > owned:
            raise ValueError('unauthorized short')
        return 'CLOSE' if -delta == owned else 'DECREASE'
    return 'NO_CHANGE'


REQUIRED = ('profile_id', 'kind', 'capability_status', 'cash_convention',
            'feedback_mapping_id', 'source_rules_id', 'calendar_id', 'corporate_action_rule',
            'minimum_history', 'history_window', 'calibration_cut', 'evaluation_cut',
            'max_age_ns', 'valid_until_ns', 'clock_quality', 'covariance_tolerance',
            'max_states', 'exposure_cap', 'name_cap', 'issuer_cap', 'sector_cap',
            'volatility_cap', 'stress_cap', 'scenario_set_id', 'fee', 'registry',
            'reconciliation_rule', 'exception_owner', 'rearm_rule', 'policy_authority', 'execution_scope')


@controlled
def evaluate(snapshot, proposal, profile, cov, scenarios, evaluation_time):
    """All inputs are captured copies. No reservations, fills or caller mutations.

    State coverage is exact for the declared synthetic discrete *fixed limit-price*
    Cartesian product only. It is not a broker/fractional/future-path certificate.
    Pending sells use the no-fill branch only and receive no funding credit.
    """
    inputs = deepcopy(dict(snapshot=snapshot, proposal=proposal, profile=profile,arithmetic_policy=SPEC,
                           cov=cov, scenarios=scenarios, evaluation_time=evaluation_time))
    reasons, missing, metrics = [], [], {}
    result = dict(decision='NOT_EVALUABLE', reasons=reasons, missing=missing, metrics=metrics,
                  action_authorized=False, scope='DIAGNOSTIC_ONLY_NOT_TRADE_ADMISSION')
    try:
        result['input_hash'] = digest(inputs)
        result['arithmetic_policy']=deepcopy(SPEC)
        ns(evaluation_time)
        missing.extend('PROFILE:'+k for k in REQUIRED if profile.get(k) is None or profile.get(k) == '')
        if profile.get('kind') != 'SYNTHETIC_FIXTURE_ONLY':
            missing.append('PROJECT_PROFILE_AND_ADAPTER_NOT_QUALIFIED')
        if profile.get('capability_status') != 'ASSUMED_SCENARIO':
            missing.append('SIMULATION_CAPABILITY_PROFILE_REQUIRED')
        if profile.get('execution_scope') != 'FIXED_LIMIT_SYNTHETIC_GRID':
            missing.append('EXECUTION_PRICE_FEE_BRANCH_COVERAGE')
        if snapshot.get('reconciled') is not True:
            missing.append('RECONCILIATION')
        if profile.get('clock_quality') != 'SIMULATED_VERIFIED':
            missing.append('CLOCK_QUALITY')
        if missing:
            # Still expose directly established funding breach even with missing profile.
            if 'cash' in snapshot and 'purchase_payable' in snapshot and profile.get('cash_convention'):
                if normalized_cash(profile['cash_convention'], snapshot['cash'], snapshot['purchase_payable']) < 0:
                    reasons.append('DEFINITE_FUNDING_BREACH')
            return result
        for key in ('profile_id','feedback_mapping_id','source_rules_id','calendar_id','corporate_action_rule',
                    'scenario_set_id','reconciliation_rule','exception_owner','rearm_rule','policy_authority'):
            if type(profile[key]) is not str or not profile[key].strip():
                raise ValueError('typed nonempty profile identity:'+key)
        external=[snapshot['cash'],snapshot['purchase_payable']]
        external.extend(snapshot['holdings'].values())
        external.extend(m['price'] for m in snapshot['marks'].values())
        external.extend(r['amount'] for r in snapshot['receivables'])
        for o in snapshot['pending']+([] if proposal is None else [proposal]):
            external.extend(o[k] for k in ('quantity','step','limit'))
        external.extend(profile[k] for k in ('exposure_cap','name_cap','issuer_cap','sector_cap','volatility_cap','stress_cap'))
        for scenario in scenarios or []:
            for node in scenario['nodes']:external.extend(node.values())
        for value in external:validate_external(value)
        ns(profile['valid_until_ns']); ns(profile['max_age_ns'])
        if evaluation_time > profile['valid_until_ns']:
            missing.append('EXPIRED_PROFILE')
        snapshot_at=ns(snapshot['at_ns'])
        if snapshot_at > evaluation_time or evaluation_time-snapshot_at > profile['max_age_ns']:
            raise ValueError('future/stale snapshot')
        calibration_cut,evaluation_cut=ns(profile['calibration_cut']),ns(profile['evaluation_cut'])
        if not calibration_cut <= evaluation_cut <= evaluation_time:
            raise ValueError('future or inverted calibration/evaluation cut')
        cap_values = {k: decimal(profile[k]) for k in
                      ('exposure_cap', 'name_cap', 'issuer_cap', 'sector_cap', 'volatility_cap', 'stress_cap')}
        if any(v > 1 for v in cap_values.values()):
            raise ValueError('fixture cap fractions outside [0,1]')
        if type(profile['minimum_history']) is not int or profile['minimum_history'] < 2:
            raise ValueError('history sufficiency rule')
        if type(profile['history_window']) is not int or profile['history_window'] < profile['minimum_history']:
            raise ValueError('history window')
        if cov is None or not scenarios:
            missing.append('COVARIANCE_OR_SCENARIO_COVERAGE')
            return result
        if cov['observations'] < profile['minimum_history'] or cov['observations'] != profile['history_window']:
            missing.append('HISTORY_SUFFICIENCY_OR_WINDOW')
        if ns(cov['available_ns']) > evaluation_time:
            raise ValueError('future covariance')
        if cov['available_ns'] > calibration_cut:
            raise ValueError('risk history available after declared calibration cut')
        if cov['universe_hash'] != digest(cov['universe']):
            raise ValueError('covariance universe identity')
        cash = normalized_cash(profile['cash_convention'], snapshot['cash'], snapshot['purchase_payable'])
        owned = {s: decimal(q) for s, q in snapshot['holdings'].items()}
        marks, registry = {}, profile['registry']
        orders = deepcopy(snapshot['pending'])
        if proposal is not None:
            if proposal.get('side') != 'BUY':
                missing.append('CLOSE_CANCEL_REMEDIATION_NOT_CONFIGURED')
                return result
            orders.append(deepcopy(proposal))
        required = {s for s, q in owned.items() if q} | {o['symbol'] for o in orders}
        for s in required:
            mark = snapshot['marks'].get(s)
            if not mark or s not in registry or not all(mark.get(k) for k in ('instrument_id', 'publisher', 'lineage_hash')):
                missing.append('MARK_OR_CLASSIFICATION:'+s); continue
            if mark['instrument_id'] != registry[s]['instrument_id']:
                raise ValueError('instrument identity mismatch')
            price = decimal(mark['price'])
            if price <= 0:
                raise ValueError('positive mark')
            for k in ('event_ns', 'available_ns'):
                at = ns(mark[k])
                if at > evaluation_time or evaluation_time-at > profile['max_age_ns']:
                    missing.append('STALE_OR_FUTURE_MARK:'+s)
            if not mark['event_ns'] <= mark['available_ns'] <= snapshot_at:
                raise ValueError('mark observation/publication/snapshot chronology')
            marks[s] = price
        if missing:
            return result
        receivable = D(0)
        receivable_ids=set()
        for item in snapshot['receivables']:
            if item['kind'] not in ('EXECUTED_SALE', 'DIVIDEND', 'INTEREST') or not item.get('event_id'):
                raise ValueError('typed identified receivable required')
            if item['event_id'] in receivable_ids:
                raise ValueError('duplicate receivable identity')
            receivable_ids.add(item['event_id'])
            receivable += decimal(item['amount'])
        v0 = cash + receivable + sum(q*marks[s] for s,q in owned.items() if q)
        if v0 <= 0:
            raise ValueError('nonpositive baseline NAV')
        ids, buys, pending_sells = set(), [], []
        reserved = D(0)
        for o in orders:
            if not o['id'] or o['id'] in ids:
                raise ValueError('duplicate intent')
            ids.add(o['id'])
            if ns(o['at_ns']) > snapshot_at:
                raise ValueError('future pending/proposed order')
            q, step, limit = (decimal(o[k]) for k in ('quantity', 'step', 'limit'))
            if min(q, step, limit) <= 0 or q % step:
                raise ValueError('quantity grid/limit')
            if o['status'] not in ('OPEN', 'CANCEL_PENDING', 'SUBMIT_UNKNOWN'):
                raise ValueError('unreconciled terminal order in pending roster')
            if o['side'] == 'SELL':
                pending_sells.append(o)
                continue
            if o['side'] != 'BUY':
                raise ValueError('order side')
            cost = q*limit + fee(q*limit, profile['fee'])
            reserved += cost
            buys.append((o, int(q/step)))
        for s in {o['symbol'] for o in pending_sells}:
            if sum(decimal(o['quantity']) for o in pending_sells if o['symbol']==s) > owned.get(s,D(0)):
                reasons.append('PENDING_SELL_OWNERSHIP_BREACH')
        if reserved > cash:
            reasons.append('FULL_COMMITMENT_FUNDING_BREACH')
        count = prod(n+1 for _, n in buys)
        if type(profile['max_states']) is not int or not 1 <= profile['max_states'] <= 10000 or count > profile['max_states']:
            missing.append('STATE_DOMAIN_NOT_EXHAUSTIVELY_ENUMERABLE'); return result
        metrics.update(baseline_nav=str(v0), available_cash=str(cash-reserved), states=[],
                       coverage='EXACT_SYNTHETIC_FIXED_LIMIT_GRID_ONLY',
                       pending_sell_assumption='NO_FILL_NO_CASH_CREDIT', excluded_for_policy=0)
        for quantities in product(*(range(n+1) for _,n in buys)):
            holdings = dict(owned); costs = D(0)
            for ((o,_), units) in zip(buys,quantities):
                q = decimal(o['step'])*units
                holdings[o['symbol']] = holdings.get(o['symbol'],D(0)) + q
                notional = q*decimal(o['limit']); costs += notional+fee(notional,profile['fee'])
            x = {s:q*marks[s] for s,q in holdings.items() if q}
            nav = cash+receivable-costs+sum(x.values())
            if nav <= 0:
                raise ValueError('nonpositive NAV in possible state; complete assessment invalid')
            if costs > cash:
                reasons.append('POSSIBLE_STATE_FUNDING_BREACH')
            risk = envelope(cov,x,nav,profile['covariance_tolerance'])
            exposure = sum(x.values())/nav
            name = max(x.values(),default=D(0))/nav
            grouped = {}
            for group in ('issuer','sector'):
                totals = {}
                for s,value in x.items():
                    key = registry[s][group]
                    if not key: raise ValueError('unknown issuer/sector')
                    totals[key] = totals.get(key,D(0))+value
                grouped[group] = max(totals.values(),default=D(0))/nav
            losses = []
            for scenario in scenarios:
                if not scenario.get('id') or not scenario.get('horizon') or not scenario['nodes']:
                    raise ValueError('scenario identity/horizon/nodes')
                prices = dict(marks); nodes = [v0,nav]
                for changes in scenario['nodes']:
                    if not set(x) <= set(changes):
                        raise ValueError('incomplete joint scenario')
                    for s in x:
                        r = decimal(changes[s],nonnegative=False)
                        if r < -1: raise ValueError('long equity return below -100%')
                        prices[s] *= 1+r
                    nodes.append(cash+receivable-costs+sum(holdings[s]*prices[s] for s in x))
                terminal = nodes[-1]
                if scenario.get('liquidate'):
                    terminal -= sum(fee(holdings[s]*prices[s],profile['fee']) for s in x)
                    nodes.append(terminal)
                losses.append(dict(id=scenario['id'],total_loss=str((v0-terminal)/v0),
                                   conditional_loss=str((nav-terminal)/nav),
                                   path_loss=str(max(v0-v for v in nodes)/v0),nodes=[str(v) for v in nodes]))
            checks = dict(exposure=exposure,name=name,issuer=grouped['issuer'],sector=grouped['sector'],
                          volatility=D(str(risk['envelope'])),stress=max(D(v['path_loss']) for v in losses))
            for key,value in checks.items():
                if value > cap_values[key+'_cap']:
                    reasons.append('LIMIT:'+key)
            metrics['states'].append(dict(fill_units=list(quantities),nav=str(nav),risk=risk,
                                           fractions={k:str(v) for k,v in checks.items()},scenarios=losses))
        metrics['state_count'] = len(metrics['states'])
        reasons[:] = sorted(set(reasons))
        result['decision'] = 'NOT_EVALUABLE' if missing else ('REJECT' if reasons else 'PASS_RESEARCH')
        return result
    except (ValueError, KeyError, TypeError, ArithmeticError, OverflowError) as exc:
        missing.append('INVALID_OR_UNQUALIFIED_INPUT:'+str(exc))
        metrics.clear()
        result['decision'] = 'NOT_EVALUABLE'
        return result
