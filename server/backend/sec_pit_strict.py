"""Opt-in SEC accounting semantics; legacy feature equations remain separate.

This is a source-validation policy, not a predictive model. Companyfacts does
not expose complete XBRL accounting contexts. Exact issuer/tag/unit continuity
is enforced and that remaining scope limitation is retained in every result.
Debt aggregation additionally requires an explicit, causally available scope
contract; unavailable accounting evidence is never guessed from tag spelling.
"""
from __future__ import annotations

from dataclasses import asdict
import hashlib
import itertools
import json
import math
import re
from datetime import date, datetime
from numbers import Real
from typing import Any
import numpy as np
import pandas as pd

VERSION = 'sec_accounting_strict_v1'
DIAGNOSTIC_FEATURES = ('cash_to_assets', 'net_margin', 'capex_to_revenue', 'operating_cash_conversion')
DEFINITIONS = {
    'cash_to_assets': 'sec_cash_over_same_instant_assets_v1',
    'net_margin': 'sec_net_income_over_same_interval_revenue_v1',
    'capex_to_revenue': 'sec_observed_absolute_capex_over_same_interval_revenue_v1',
    'operating_cash_conversion': 'sec_ocf_over_positive_net_income_v1',
}
SCOPE_LIMITATION = 'Companyfacts lacks complete consolidation/dimension/accounting-context fields; exact issuer/namespace/concept/unit continuity is checked, not independently attested accounting comparability.'
SUPPORTED_CURRENCIES=frozenset('USD EUR GBP CAD AUD NZD JPY CNY HKD CHF SEK NOK DKK INR KRW TWD SGD BRL MXN ZAR ILS PLN'.split())


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)


def validate_options(options=None):
    options = dict(options or {})
    unknown = set(options)-{'diagnostic_features', 'composite_policy', 'monetary_unit', 'debt_scope_contracts'}
    if unknown:
        raise ValueError(f'Unknown strict SEC options: {sorted(unknown)}')
    if not isinstance(options.get('diagnostic_features', False), bool):
        raise ValueError('diagnostic_features must be boolean')
    if options.get('composite_policy', 'legacy_complete') not in {'legacy_complete', 'observed_min2'}:
        raise ValueError('Unknown strict SEC composite_policy')
    unit = options.get('monetary_unit')
    if unit is not None and unit not in SUPPORTED_CURRENCIES:
        raise ValueError('monetary_unit must be a supported explicitly named currency; no conversion is performed')
    if not isinstance(options.get('debt_scope_contracts', {}), dict):
        raise ValueError('debt_scope_contracts must map tickers to scope contracts')
    return options


def _result(status='INVALID', reason='unavailable', *, value=None, interval=None, unit=None,
            formula=None, facts=(), **extra):
    if status=='VALID' and (value is None or not math.isfinite(float(value))):
        status,reason,value='INVALID','nonfinite_arithmetic',None
    constituents = sorted({ _json(asdict(f)): asdict(f) for f in facts }.values(), key=_json)
    return {'status':status, 'reason':reason, 'value':value, 'interval':interval, 'unit':unit,
            'formula':formula, 'formula_version':VERSION, 'constituents':constituents,
            'accounting_scope_limitation':SCOPE_LIMITATION, **extra}


def _known_time(fact):
    timestamp = pd.Timestamp(fact.accepted or fact.filed)
    return timestamp.tz_localize('UTC') if timestamp.tzinfo is None else timestamp.tz_convert('UTC')


def _versions(facts, session):
    """Retain distinct semantic concepts/units; resolve knowledge-time ties as sets."""
    groups = {}
    cutoff = pd.Timestamp(session).tz_localize(None).normalize()
    for fact in facts:
        if pd.Timestamp(fact.eligible_session).normalize() > cutoff:
            continue
        key = (fact.ticker, fact.cik, fact.canonical, fact.namespace, fact.tag, fact.unit, fact.start, fact.end)
        groups.setdefault(key, []).append(fact)
    output = []
    for group in groups.values():
        # Normalization may clamp several old disclosure dates to the first
        # supplied session. Preserve their actual knowledge dates. Within one
        # public date neither an accession nor an imputed midnight establishes
        # semantic supersession of incompatible declarations.
        latest = max(_known_time(f).normalize() for f in group)
        tied = [f for f in group if _known_time(f).normalize() == latest]
        # Order is immaterial; accession ordering is not a supersession policy.
        tied = sorted({_json(asdict(f)): f for f in tied}.values(), key=lambda f:_json(asdict(f)))
        bad = len({f.value for f in tied}) != 1 or any(not math.isfinite(f.value) or getattr(f,'source_value_status','VALID')!='VALID' for f in tied)
        if any(f.end_ts > _known_time(f).tz_localize(None).normalize() or _known_time(f).tz_localize(None).normalize()>cutoff
               or (f.start_ts is not None and f.start_ts > f.end_ts) for f in tied):
            bad = True
        annual_evidence=sorted({_json(asdict(f)):asdict(f) for f in group if _annual(f)}.values(),key=_json)
        # A later comparative republication may update an amount but cannot
        # erase the interval's previously established own-period identity.
        phase_evidence=sorted({_json(asdict(f)):asdict(f) for f in group
                               if _own_period_phase(f) is not None
                               and f.start_ts <= f.end_ts <= _known_time(f).tz_localize(None).normalize() <= cutoff}.values(),key=_json)
        output.append({'fact':tied[0], 'facts':tied, 'conflict':bad,'annual_period_evidence':annual_evidence,
                       'own_period_phase_evidence':phase_evidence})
    return sorted(output, key=lambda r:_json(asdict(r['fact'])))


def _scope(f):
    return f.ticker, f.cik, f.namespace, f.tag, f.unit


def _calendar_anniversary(prior, current):
    """Consecutive-year calendar endpoints, including February month ends.

    DateOffset clips February 29 into February 28, but adding one year to
    February 28 cannot recover February 29. Only that exact month-end pair
    supplements the existing directional anniversary rule; nearby days do not.
    """
    return (prior + pd.DateOffset(years=1) == current or
            (current.year == prior.year + 1 and prior.month == current.month == 2
             and prior.is_month_end and current.is_month_end))


def _year(start, end):
    if start is None or end < start:
        return False
    days = (end-start).days+1
    return (start+pd.DateOffset(years=1)-pd.Timedelta(days=1) == end
            or days in {364,371})


def _annual(f):
    return bool(f.start_ts is not None and f.form in {'10-K','10-K/A','20-F','20-F/A','40-F','40-F/A'} and _year(f.start_ts, f.end_ts))


def _annual_record(record):
    return bool(_annual(record['fact']) or (record.get('annual_period_evidence') and
        record['fact'].form not in {'10-KT','10-KT/A','10-QT','10-QT/A'}))


def _own_period_phase(f):
    """fp is economic phase only for the filing's own endpoint, never a compare."""
    phase=getattr(f,'reported_fiscal_period',None)
    year=getattr(f,'reported_fiscal_year',None)
    if (f.form not in {'10-Q','10-Q/A'} or f.start is None
        or getattr(f,'filing_report_date',None)!=f.end or phase not in {'Q1','Q2','Q3'}
        or not isinstance(year,int) or isinstance(year,bool)
        or getattr(f,'source_value_status','VALID')!='VALID'
        or not math.isfinite(f.value)):
        return None
    return year,int(phase[1])


def _evidenced_ytd_phase_pair(annual,current,prior):
    """Recognize an evidenced hybrid calendar without allowing gaps/tolerances.

    Callers still require exact annual/YTD anchors and a supported complete
    trailing year. Both intervals must independently have been the own period
    of an eligible ordinary quarterly filing, in consecutive fiscal years.
    """
    if any(r['fact'].form in {'10-KT','10-KT/A','10-QT','10-QT/A'}
           for r in (annual,current,prior)):
        return None
    phases=[]
    for record in (current,prior):
        evidence=record.get('own_period_phase_evidence',[])
        identities={(f['reported_fiscal_year'],int(f['reported_fiscal_period'][1])) for f in evidence}
        if len(identities)!=1:
            return None
        phases.append(next(iter(identities)))
    (current_year,current_phase),(prior_year,prior_phase)=phases
    if current_year!=prior_year+1 or current_phase!=prior_phase:
        return None
    return {'policy':'causal_own_report_date_ytd_phase_v1','phase':current_phase,
            'current_fiscal_year':current_year,'prior_fiscal_year':prior_year,
            'current_evidence':current['own_period_phase_evidence'],
            'prior_evidence':prior['own_period_phase_evidence']}


def _quarter(f):
    if f.start_ts is None or f.form in {'10-KT','10-KT/A','10-QT','10-QT/A'}:
        return False
    return (f.start_ts+pd.DateOffset(months=3)-pd.Timedelta(days=1) == f.end_ts
            or (f.end_ts-f.start_ts).days+1 in {91,98})


def _phase(f, annual=None):
    if f.start_ts is None or f.form in {'10-KT','10-KT/A','10-QT','10-QT/A'}:
        return None
    if annual is not None and (annual.end_ts-annual.start_ts).days+1 in {364,371}:
        days=(f.end_ts-f.start_ts).days+1
        for phase in (1,2,3):
            if days in {91*phase,91*phase+7}:return ('weekly',phase)
    for phase in (1,2,3):
        if f.start_ts+pd.DateOffset(months=3*phase)-pd.Timedelta(days=1) == f.end_ts:
            return ('calendar',phase)
        days=(f.end_ts-f.start_ts).days+1
        # A reported extra week can be in the current or prior YTD. The
        # assembled trailing interval must still pass the independent 52/53
        # week/year check; reported amounts are never scaled to 365 days.
        if days in {91*phase,91*phase+7}:
            return ('weekly',phase)
    return None


def _choose(candidates, metric, endpoint):
    if any(c['reason']=='nonfinite_arithmetic' for c in candidates):
        return _result(reason='nonfinite_arithmetic',interval=[None,endpoint])
    candidates=[c for c in candidates if c['status']=='VALID']
    if not candidates:
        return _result(reason='latest_target_has_no_supported_contiguous_construction', interval=[None,endpoint])
    identities = {(tuple(c['interval']),c['unit'],tuple(sorted({(f['namespace'],f['tag']) for f in c['constituents']}))) for c in candidates}
    values = [c['value'] for c in candidates]
    # This is arithmetic equality tolerance, not an accounting rounding waiver.
    if len(identities)!=1 or any(not math.isclose(values[0],v,rel_tol=1e-12,abs_tol=1e-9) for v in values[1:]):
        result=_result(reason='conflicting_supported_representations', interval=[None,endpoint])
        result['alternatives']=candidates
        return result
    order={'reported_annual':0,'annual_plus_current_ytd_minus_prior_ytd':1,'four_exactly_contiguous_quarters':2}
    selected=min(candidates,key=lambda c:(order.get(c['formula'],99),_json(c)))
    selected['equivalent_constructions']=len(candidates)
    return selected


def ttm(records, metric, options):
    rows=[r for r in records if r['fact'].canonical==metric]
    endpoint=max((r['fact'].end for r in rows),default=None)
    if options.get('monetary_unit'):
        rows=[r for r in rows if r['fact'].unit==options['monetary_unit']]
    if not rows:
        return _result(reason='no_eligible_flow')
    if max(r['fact'].end for r in rows)!=endpoint or any(r['fact'].unit not in SUPPORTED_CURRENCIES for r in rows if r['fact'].end==endpoint):
        return _result(reason='unsupported_or_missing_target_currency',interval=[None,endpoint])
    if any(r['fact'].form in {'10-KT','10-KT/A','10-QT','10-QT/A'} for r in rows if r['fact'].end==endpoint):
        return _result(reason='fiscal_transition_target_requires_explicit_context',interval=[None,endpoint],facts=[f for r in rows if r['fact'].end==endpoint for f in r['facts']])
    if any(r['conflict'] for r in rows if r['fact'].end==endpoint):
        return _result(reason='unresolved_latest_fact_conflict', interval=[None,endpoint], facts=[f for r in rows if r['fact'].end==endpoint for f in r['facts']])
    candidates=[]
    valid=[r for r in rows if not r['conflict']]
    for r in valid:
        f=r['fact']
        if f.end==endpoint and _annual_record(r):
            candidates.append(_result('VALID','supported',value=f.value,interval=[f.start,f.end],unit=f.unit,
                formula='reported_annual',facts=r['facts'],annual_period_evidence=r['annual_period_evidence']))
    if candidates:
        return _choose(candidates,metric,endpoint)
    annual_rows=[r for r in valid if _annual_record(r)]
    current_rows=[r for r in valid if r['fact'].end==endpoint and r['fact'].start_ts is not None]
    annual_by_end={}
    by_start={}
    for r in annual_rows:annual_by_end.setdefault((_scope(r['fact']),r['fact'].end_ts),[]).append(r)
    for r in valid:by_start.setdefault((_scope(r['fact']),r['fact'].start_ts),[]).append(r)
    triples=((a,c,p) for c in current_rows
             for a in annual_by_end.get((_scope(c['fact']),c['fact'].start_ts-pd.Timedelta(days=1)),[])
             for p in by_start.get((_scope(a['fact']),a['fact'].start_ts),[]))
    for a,c,p in triples:
        af,cf,pf=(x['fact'] for x in (a,c,p))
        if not _annual_record(a) or cf.end!=endpoint or cf.start_ts is None or pf.start_ts is None:
            continue
        if not (_scope(af)==_scope(cf)==_scope(pf)):
            continue
        if cf.start_ts!=af.end_ts+pd.Timedelta(days=1) or pf.start_ts!=af.start_ts or pf.end_ts>=af.end_ts:
            continue
        phase_evidence=None
        if _phase(cf,af) is None or _phase(cf,af)!=_phase(pf,af):
            phase_evidence=_evidenced_ytd_phase_pair(a,c,p)
            if phase_evidence is None:
                continue
        if not _year(pf.end_ts+pd.Timedelta(days=1),cf.end_ts):
            continue
        candidates.append(_result('VALID','supported',value=float(af.value+cf.value-pf.value),
            interval=[str((pf.end_ts+pd.Timedelta(days=1)).date()),cf.end],unit=cf.unit,
            formula='annual_plus_current_ytd_minus_prior_ytd',facts=[f for r in (a,c,p) for f in r['facts']],
            annual_period_evidence=a['annual_period_evidence'],duration_days=(cf.end_ts-pf.end_ts).days,
            **({'fiscal_phase_evidence':phase_evidence} if phase_evidence is not None else {})))
    # A four-quarter fallback must tile one complete interval exactly.
    quarters=[r for r in valid if _quarter(r['fact'])]
    def backwards(chain):
        if len(chain)==4:
            chronological=list(reversed(chain));first,last=chronological[0]['fact'],chronological[-1]['fact']
            if _year(first.start_ts,last.end_ts):
                candidates.append(_result('VALID','supported',value=float(sum(r['fact'].value for r in chronological)),
                    interval=[first.start,last.end],unit=first.unit,formula='four_exactly_contiguous_quarters',
                    facts=[f for r in chronological for f in r['facts']]))
            return
        last=chain[-1]['fact']
        for r in quarters:
            f=r['fact']
            if _scope(f)==_scope(last) and f.end_ts+pd.Timedelta(days=1)==last.start_ts:
                backwards([*chain,r])
    for q in quarters:
        if q['fact'].end==endpoint:backwards([q])
    return _choose(candidates,metric,endpoint)


def instant(records, metric, options, tag=None):
    rows=[r for r in records if r['fact'].canonical==metric and r['fact'].start is None and (tag is None or r['fact'].tag==tag)]
    endpoint=max((r['fact'].end for r in rows),default=None)
    if options.get('monetary_unit'):
        rows=[r for r in rows if r['fact'].unit==options['monetary_unit']]
    if not rows:return _result(reason='no_eligible_instant')
    if max(r['fact'].end for r in rows)!=endpoint or any(r['fact'].unit not in SUPPORTED_CURRENCIES for r in rows if r['fact'].end==endpoint):
        return _result(reason='unsupported_or_missing_target_currency',interval=[endpoint,endpoint])
    end=max(r['fact'].end for r in rows);rows=[r for r in rows if r['fact'].end==end]
    if any(r['conflict'] for r in rows) or len({_scope(r['fact']) for r in rows})!=1:
        return _result(reason='ambiguous_instant_scope_or_units',interval=[end,end],facts=[f for r in rows for f in r['facts']])
    f=rows[0]['fact']
    return _result('VALID','supported',value=f.value,interval=[end,end],unit=f.unit,
        formula='reported_instant',facts=[f for r in rows for f in r['facts']])


def _compatible_results(left,right,same_interval=True):
    if left['status']!='VALID' or right['status']!='VALID':return 'dependency_unavailable'
    if left['unit']!=right['unit'] or left['unit'] not in SUPPORTED_CURRENCIES:return 'incompatible_monetary_units'
    if same_interval is True and left['interval']!=right['interval']:return 'incompatible_flow_intervals'
    if same_interval is False and left['interval'][1]!=right['interval'][1]:return 'incompatible_target_instants'
    issuers=lambda r:{(f['ticker'],f['cik'],f['namespace']) for f in r['constituents']}
    if issuers(left)!=issuers(right) or len(issuers(left))!=1:return 'incompatible_issuer_scope'
    return None


def _ratio(left,right,definition,same_interval=False,positive_denominator=False,negate_abs=False,absolute_numerator=False):
    reason=_compatible_results(left,right,same_interval)
    if not reason and (right['value']<=0 if positive_denominator else abs(right['value'])<=1e-12):reason='invalid_denominator'
    evidence={'definition_id':definition,'numerator':left,'denominator':right,
              'latest_target_interval':left['interval'],'status':'INVALID' if reason else 'VALID','reason':reason or 'supported'}
    value=None
    if not reason:
        num=abs(left['value']) if absolute_numerator else left['value']
        value=float(num/right['value']);value=-abs(value) if negate_abs else value
        if not math.isfinite(value):
            value=None;evidence.update(status='INVALID',reason='nonfinite_arithmetic')
    evidence['value']=value
    return evidence


def _difference(left,right,formula,absolute_right=False):
    reason=_compatible_results(left,right,True)
    if reason:return _result(reason=reason,interval=left['interval'],dependencies=[left,right])
    value=float(left['value']-(abs(right['value']) if absolute_right else right['value']))
    if not math.isfinite(value):return _result(reason='nonfinite_arithmetic',interval=left['interval'],dependencies=[left,right])
    return {**left,'value':value,
            'formula':formula,'constituents':left['constituents']+right['constituents'],'dependencies':[left,right]}


def growth(records,metric,options):
    rows=[r for r in records if r['fact'].canonical==metric]
    target=max((r['fact'].end for r in rows),default=None)
    if options.get('monetary_unit'):rows=[r for r in rows if r['fact'].unit==options['monetary_unit']]
    if not rows:return {'status':'INVALID','value':None,'reason':'no_eligible_flow','latest_target_interval':None}
    end=max(r['fact'].end for r in rows);current=[r for r in rows if r['fact'].end==end]
    if end!=target or any(r['fact'].unit not in SUPPORTED_CURRENCIES for r in current):
        return {'status':'INVALID','value':None,'reason':'unsupported_or_missing_target_currency','latest_target_interval':[None,target]}
    if any(r['fact'].form in {'10-KT','10-KT/A','10-QT','10-QT/A'} for r in current):
        return {'status':'INVALID','value':None,'reason':'fiscal_transition_target_requires_explicit_context','latest_target_interval':[None,end]}
    # Establish economic type before prior-period/version selection.
    annual=[r for r in current if _annual_record(r)]
    if annual:current=annual
    else:
        longest=max((r['fact'].duration_days or -1) for r in current)
        current=[r for r in current if (r['fact'].duration_days or -1)==longest]
    results=[]
    for c in current:
        cf=c['fact']
        if c['conflict'] or cf.start_ts is None:continue
        for p in rows:
            pf=p['fact']
            if p['conflict'] or pf.start_ts is None or _scope(cf)!=_scope(pf) or pf.end_ts>=cf.end_ts:continue
            if pf.form in {'10-KT','10-KT/A','10-QT','10-QT/A'}:continue
            if _annual_record(c)!=_annual_record(p):continue
            if not _annual_record(c) and (_phase(cf) is None or _phase(pf) is None or _phase(cf)[1]!=_phase(pf)[1]):continue
            calendar=(pf.start_ts+pd.DateOffset(years=1)==cf.start_ts and _calendar_anniversary(pf.end_ts,cf.end_ts))
            weekly=((cf.end_ts-pf.end_ts).days in {364,371} and (cf.start_ts-pf.start_ts).days in {364,371}
                    and abs((cf.duration_days or 0)-(pf.duration_days or 0)) in {0,7}
                    and (_annual_record(c) or (_phase(cf)[1]==_phase(pf)[1] and
                        ((cf.end_ts-cf.start_ts).days+1)%7==0 and ((pf.end_ts-pf.start_ts).days+1)%7==0)))
            if not (calendar or weekly) or pf.value==0:continue
            value=float(cf.value/pf.value-1)
            if not math.isfinite(value):continue
            results.append({'status':'VALID','value':value,'reason':'supported',
                'definition_id':'sec_comparable_revenue_growth_v1','latest_target_interval':[cf.start,cf.end],
                'current':asdict(cf),'prior':asdict(pf),'annual_period_evidence':c['annual_period_evidence']+p['annual_period_evidence'],
                'period_type':'annual' if _annual_record(c) else f'{_phase(cf)[0]}_quarter_or_ytd_phase_{_phase(cf)[1]}',
                'calendar_policy':'exact_calendar_year_shift' if calendar else 'coherent_52_53_week_shift'})
    if len({_json(r) for r in results})==1:return results[0]
    return {'status':'INVALID','value':None,'reason':'unavailable_or_ambiguous_comparable_prior_period','latest_target_interval':[None,end], 'alternatives':results}


def debt_amount(records, session, options):
    debt=[r for r in records if r['fact'].canonical=='debt']
    if not debt:return _result(reason='no_eligible_debt',policy_scope_id='D2_verified_long_term_borrowings')
    ticker=debt[0]['fact'].ticker;contract=options.get('debt_scope_contracts',{}).get(ticker)
    diagnostic={'policy_scope_id':'D2_verified_long_term_borrowings','source_concepts_preserved':sorted({r['fact'].tag for r in debt})}
    required={'eligible_session','evidence_id','amount_basis','lease_scope','short_term_scope','consolidation_scope','tags','components_disjoint','numerical_tolerance','rounding_precision_source'}
    if not isinstance(contract,dict) or not required <= set(contract):return _result(reason='verified_debt_scope_contract_required',**diagnostic)
    if (pd.Timestamp(contract['eligible_session'])>pd.Timestamp(session) or contract['amount_basis']!='carrying'
        or contract['lease_scope']!='excluded' or contract['short_term_scope']!='excluded'
        or not contract['consolidation_scope'] or not contract['evidence_id'] or not contract['rounding_precision_source']):
        return _result(reason='unsupported_or_not_yet_available_debt_scope',**diagnostic)
    tags=contract['tags'];tol=float(contract['numerical_tolerance'])
    if not isinstance(tags,dict) or set(tags)-{'current','noncurrent','total'} or not math.isfinite(tol) or tol<0:
        raise ValueError('Invalid debt scope tags/tolerance')
    roles={'current':{'LongTermDebtCurrent'},'noncurrent':{'LongTermDebtNoncurrent'},'total':{'LongTermDebt'}}
    if (len(set(tags.values()))!=len(tags) or any(tag not in roles[role] for role,tag in tags.items())):
        return _result(reason='invalid_or_overlapping_debt_role_mapping',scope_contract=contract,**diagnostic)
    selected={key:instant(records,'debt',options,tag) for key,tag in tags.items()}
    intended=[r for r in debt if r['fact'].tag in set(tags.values())]
    latest_end=max((r['fact'].end for r in intended),default=None)
    if any(value['reason']=='ambiguous_instant_scope_or_units' and value['interval'][1]==latest_end for value in selected.values()):
        return _result(reason='unresolved_debt_representation_conflict',scope_contract=contract,representations=selected,**diagnostic)
    direct=selected.get('total');current=selected.get('current');noncurrent=selected.get('noncurrent');combined=None
    if current and noncurrent and contract['components_disjoint'] is True and not _compatible_results(current,noncurrent,True):
        combined={**current,'value':float(current['value']+noncurrent['value']), 'formula':'verified_disjoint_current_plus_noncurrent',
                  'constituents':current['constituents']+noncurrent['constituents']}
        if not math.isfinite(combined['value']):return _result(reason='nonfinite_arithmetic',**diagnostic)
    available=[x for x in (direct,combined) if x and x['status']=='VALID' and x['interval'][1]==latest_end]
    diagnostic.update(scope_contract=contract,representations=selected,components_sum=combined,
                      evidence_authenticity_independently_verified=False)
    if not available:return _result(reason='debt_scope_missing_compatible_components',**diagnostic)
    if len(available)==2:
        reason=_compatible_results(direct,combined,True);residual=float(direct['value']-combined['value'])
        if not math.isfinite(residual):return _result(reason='nonfinite_arithmetic',**diagnostic)
        diagnostic['reconciliation_residual']=residual
        if reason or abs(residual)>tol:return _result(reason='unresolved_debt_representation_conflict',**diagnostic)
    return {**available[0],**diagnostic,'reason':'verified_declared_scope','formula':available[0]['formula']}


def _value_session(value):
    """Canonical explicit session dates; never numeric epoch coercion."""
    if isinstance(value,str):
        if re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}",value) is None:
            raise ValueError('Value contract session must be YYYY-MM-DD')
    elif not isinstance(value,(date,datetime,pd.Timestamp)):
        raise ValueError('Value contract session must be an explicit date')
    result=pd.Timestamp(value)
    if pd.isna(result) or result.tz is not None or result!=result.normalize():
        raise ValueError('Value contract requires a timezone-naive calendar-session date')
    return result


def _value_positive_real(value):
    if isinstance(value,(bool,np.bool_)) or not isinstance(value,Real):
        raise ValueError('Value contract amount must be a positive finite real')
    value=float(value)
    if not math.isfinite(value) or value<=0:
        raise ValueError('Value contract amount must be a positive finite real')
    return value


def strict_market_cap(records, session, history):
    """Admit only explicitly sourced, point-in-time raw common-equity contracts.

    This validates a caller's evidence contract; it does not independently attest
    vendor authenticity. Daily raw quotes and corporate-action coverage have
    their own availability sessions. No adjusted quote, ADR, mixed share class,
    guessed split, or stale quote/shares can silently become market cap.
    """
    reason='verified_unadjusted_price_shares_contract_unavailable'
    invalid=lambda why: _result(reason=why,definition_id='pit_raw_common_equity_market_cap_v1')
    if history is None or not isinstance(history,pd.DataFrame) or history.empty:return invalid(reason)
    contracts=history.attrs.get('sec_value_contracts')
    if history.attrs.get('price_basis')!='unadjusted' or not isinstance(contracts,list):return invalid(reason)
    day=pd.Timestamp(session).normalize()
    required={'schema','ticker','cik','currency','eligible_session','evidence_id','share_fact_accession',
              'share_fact_end','share_fact_tag','share_fact_value','shares_basis','equity_scope','split_events'}
    eligible=[]
    for contract in contracts:
        if not isinstance(contract,dict) or not required<=set(contract):continue
        try:
            if _value_session(contract['eligible_session'])<=day:eligible.append(contract)
        except (ValueError,TypeError,OverflowError):return invalid('invalid_value_contract_session')
    if not eligible:return invalid('no_causally_available_value_contract')
    latest=max(_value_session(c['eligible_session']) for c in eligible)
    current=[c for c in eligible if _value_session(c['eligible_session'])==latest]
    try:
        if len({_json(c) for c in current})!=1:return invalid('ambiguous_value_contract')
    except (ValueError,TypeError):return invalid('invalid_value_contract_payload')
    c=current[0]
    if (c['schema']!='pit_raw_common_equity_v1' or c['shares_basis']!='as_reported_at_fact_end'
        or c['equity_scope']!='single_common_equity_class' or not isinstance(c['currency'],str) or c['currency'] not in SUPPORTED_CURRENCIES
        or not c['evidence_id'] or not isinstance(c['split_events'],list)):
        return invalid('unsupported_value_scope_or_basis')
    try:
        _value_session(c['share_fact_end']);_value_positive_real(c['share_fact_value'])
    except (ValueError,TypeError,OverflowError):return invalid('invalid_share_contract_basis')
    shares=[r for r in records if r['fact'].canonical=='shares_outstanding' and r['fact'].start is None]
    if not shares:return invalid('shares_unavailable')
    newest=max(r['fact'].end for r in shares)
    shares=[r for r in shares if r['fact'].end==newest]
    if len(shares)!=1 or shares[0]['conflict']:return invalid('ambiguous_latest_share_fact')
    f=shares[0]['fact']
    if (f.ticker!=c['ticker'] or f.cik!=c['cik'] or f.unit!='shares' or f.namespace not in {'us-gaap','dei'}
        or f.form not in {'10-K','10-K/A','10-Q','10-Q/A'} or f.accession!=c['share_fact_accession']
        or f.end!=c['share_fact_end'] or f.tag!=c['share_fact_tag'] or f.value!=c['share_fact_value']
        or not math.isfinite(f.value) or f.value<=0):return invalid('share_contract_does_not_match_latest_fact')
    if not 0 <= (day-f.end_ts).days <= 200:return invalid('stale_or_future_share_basis')
    columns={'Close','available_session','actions_complete_through','actions_available_session'}
    if not columns<=set(history.columns) or not isinstance(history.index,pd.DatetimeIndex) or history.index.has_duplicates or not history.index.is_monotonic_increasing:
        return invalid('raw_quote_availability_or_action_coverage_missing')
    try:
        history_past=history.loc[history.index.normalize()<=day]
        available=history_past['available_session'].map(_value_session)
        action_available=history_past['actions_available_session'].map(_value_session)
        mask=(available<=day)&(action_available<=day)
        quotes=history_past.loc[mask]
        if quotes.empty:return invalid('no_causally_available_raw_quote')
        price_day=pd.Timestamp(quotes.index[-1]).normalize();row=quotes.iloc[-1]
        if (_value_session(row['available_session'])<=price_day or
            _value_session(row['actions_available_session'])<price_day):
            return invalid('raw_quote_availability_must_follow_its_session')
        if f.end_ts>price_day:return invalid('raw_quote_precedes_share_basis')
        if not 0 <= (day-price_day).days <= 7:return invalid('stale_raw_quote')
        if _value_session(row['actions_complete_through'])!=price_day:return invalid('incomplete_corporate_action_coverage')
        price=_value_positive_real(row['Close'])
        if not math.isfinite(price) or price<=0:return invalid('invalid_raw_quote')
        factor=1.;seen=set();used=[]
        for split in c['split_events']:
            if not {'effective_session','eligible_session','ratio','evidence_id'}<=set(split):return invalid('incomplete_split_evidence')
            effective=_value_session(split['effective_session'])
            if effective<=f.end_ts or effective>price_day:continue
            eligible_date=_value_session(split['eligible_session'])
            if eligible_date>day:return invalid('split_not_yet_available')
            if effective in seen:return invalid('duplicate_or_conflicting_split')
            ratio=_value_positive_real(split['ratio'])
            if not math.isfinite(ratio) or ratio<=0 or not split['evidence_id']:return invalid('invalid_split_evidence')
            seen.add(effective);factor*=ratio;used.append(split)
        value=price*f.value*factor
        if not math.isfinite(value) or value<=0:return invalid('nonfinite_market_cap')
    except (ValueError,TypeError,KeyError,OverflowError):return invalid('invalid_value_contract_dates_or_numbers')
    return _result('VALID','verified_declared_scope',value=value,interval=[str(price_day.date())]*2,
        unit=c['currency'],formula='raw_close_x_reported_shares_x_known_post_basis_split_factors',facts=[f],
        price=price,price_session=str(price_day.date()),price_available_session=str(row['available_session']),
        split_factor=factor,split_events=used,scope_contract=c,
        evidence_authenticity_independently_verified=False)


def value_ratio(numerator,market_cap,definition):
    if market_cap['status']!='VALID':
        # An unavailable market-cap contract invalidates value independently of
        # numerator history. Keep that state compact rather than duplicating
        # full accounting trees at every unrelated filing event.
        return {'definition_id':definition,'latest_target_interval':None,
                'status':'INVALID','value':None,'reason':market_cap['reason']}
    evidence={'definition_id':definition,'numerator':numerator,'denominator':market_cap,
              'latest_target_interval':numerator.get('interval'),'status':'INVALID','value':None,
              'reason':market_cap['reason'] if market_cap['status']!='VALID' else 'value_numerator_unavailable'}
    if market_cap['status']!='VALID' or numerator['status']!='VALID':return evidence
    issuers=lambda result:{(f['ticker'],f['cik']) for f in result['constituents']}
    if numerator['unit']!=market_cap['unit'] or issuers(numerator)!=issuers(market_cap):
        return {**evidence,'reason':'incompatible_value_currency_or_issuer'}
    value=float(numerator['value']/market_cap['value'])
    if not math.isfinite(value):return {**evidence,'reason':'nonfinite_arithmetic'}
    return {**evidence,'status':'VALID','reason':'supported_pit_market_cap','value':value}


def derive(facts, session, history=None, options=None):
    from .sec_pit import FLOW_METRICS, QUALITY_FEATURES, VALUE_FEATURES
    options=validate_options(options);records=_versions(facts,session)
    if len({(r['fact'].ticker,r['fact'].cik) for r in records})>1:
        raise ValueError('Strict SEC feature derivation requires one issuer')
    flows={m:ttm(records,m,options) for m in FLOW_METRICS}
    inst={m:instant(records,m,options) for m in ('assets','equity','cash')}
    debt=debt_amount(records,session,options)
    fcf=_difference(flows['operating_cash_flow'],flows['capex'],'observed_ocf_minus_absolute_observed_capex',True)
    accrual=_difference(flows['net_income'],flows['operating_cash_flow'],'net_income_minus_operating_cash_flow')
    features={}
    pairs={'gross_margin':(flows['gross_profit'],flows['revenue'],True),
        'gross_profitability':(flows['gross_profit'],inst['assets'],False),
        'cashflow_profitability':(flows['operating_cash_flow'],inst['assets'],False),
        'fcf_profitability':(fcf,inst['assets'],False),'roa':(flows['net_income'],inst['assets'],False),
        'roe':(flows['net_income'],inst['equity'],False),'debt_to_assets':(debt,inst['assets'],True),
        'accrual_quality':(accrual,inst['assets'],False)}
    for name,(num,den,same) in pairs.items():
        features[name]=_ratio(num,den,f'sec_{name}_v1',same,negate_abs=name=='accrual_quality')
    features['revenue_growth_yoy']=growth(records,'revenue',options)
    assets=inst['assets'];prior_assets=_result(reason='no_comparable_prior_assets')
    if assets['status']=='VALID':
        end=pd.Timestamp(assets['interval'][1]);prior_rows=[r for r in records if r['fact'].canonical=='assets' and
            (_calendar_anniversary(r['fact'].end_ts,end) or (end-r['fact'].end_ts).days in {364,371})]
        if len({r['fact'].end for r in prior_rows})>1:
            prior_assets=_result(reason='ambiguous_prior_year_asset_instant')
        else:prior_assets=instant(prior_rows,'assets',options)
    growth_assets=_ratio(assets,prior_assets,'sec_same_scope_asset_growth_v1',same_interval=None)
    # Asset growth deliberately compares corresponding different-year instants.
    if growth_assets['status']=='VALID':
        value=float(assets['value']/prior_assets['value']-1)
        growth_assets.update(status='VALID' if math.isfinite(value) else 'INVALID',reason='supported' if math.isfinite(value) else 'nonfinite_arithmetic',value=value if math.isfinite(value) else None)
    features['asset_growth_yoy']=growth_assets
    market_cap=strict_market_cap(records,session,history)
    for name,numerator in {'book_to_price':inst['equity'],'earnings_yield':flows['net_income'],
                           'fcf_yield':fcf,'sales_yield':flows['revenue']}.items():
        features[name]=value_ratio(numerator,market_cap,f'sec_{name}_pit_raw_market_cap_v1')
    if options.get('diagnostic_features',False):
        features['cash_to_assets']=_ratio(inst['cash'],inst['assets'],DEFINITIONS['cash_to_assets'],True,True)
        features['net_margin']=_ratio(flows['net_income'],flows['revenue'],DEFINITIONS['net_margin'],True,True)
        features['capex_to_revenue']=_ratio(flows['capex'],flows['revenue'],DEFINITIONS['capex_to_revenue'],True,True,absolute_numerator=True)
        features['operating_cash_conversion']=_ratio(flows['operating_cash_flow'],flows['net_income'],DEFINITIONS['operating_cash_conversion'],True,True)
    values={name:float(f['value']) for name,f in features.items() if f['status']=='VALID' and math.isfinite(float(f['value']))}
    diag={'semantics_version':VERSION,'eligible_fact_versions':len(records),'flows':flows,'instant':inst,
          'features':features,'debt':debt,'quality_component_names':list(QUALITY_FEATURES),
          'diagnostic_feature_names':list(DIAGNOSTIC_FEATURES) if options.get('diagnostic_features') else [],
          'market_cap':market_cap['value'],'market_cap_diagnostics':market_cap,'value_basis_status':market_cap['reason'],
          'accounting_scope_limitation':SCOPE_LIMITATION}
    return values,diag


def build(facts_by_symbol,sessions,histories=None,options=None):
    from .sec_pit import _clean_sessions, _rank_composite, QUALITY_FEATURES, VALUE_FEATURES
    options=validate_options(options);idx=_clean_sessions(sessions);symbols=sorted(facts_by_symbol)
    names=(*QUALITY_FEATURES,*VALUE_FEATURES,*(DIAGNOSTIC_FEATURES if options.get('diagnostic_features') else ()))
    panels={name:pd.DataFrame(np.nan,index=idx,columns=symbols) for name in names}
    validity={name:pd.DataFrame(False,index=idx,columns=symbols) for name in names}
    events={symbol:{name:[] for name in names} for symbol in symbols};issuer={};states={}
    for symbol in symbols:
        facts=facts_by_symbol[symbol];event_dates={pd.Timestamp(f.eligible_session).normalize() for f in facts if pd.Timestamp(f.eligible_session).normalize()<=idx[-1]} if len(idx) else set()
        contract=options.get('debt_scope_contracts',{}).get(symbol)
        if len(idx) and isinstance(contract,dict) and contract.get('eligible_session'):
            contract_date=pd.Timestamp(contract['eligible_session']).normalize()
            if contract_date<=idx[-1]:event_dates.add(contract_date)
        raw_history=(histories or {}).get(symbol)
        if isinstance(raw_history,pd.DataFrame) and isinstance(raw_history.attrs.get('sec_value_contracts'),list):
            # Value prices, split eligibility, quote staleness and share aging
            # are daily states; never forward-fill a filing-day market cap.
            event_dates.update(pd.Timestamp(day).normalize() for day in idx)
        event_dates=sorted(event_dates)
        previous={};last_valid={};latest={}
        for i,event in enumerate(event_dates):
            _,latest=derive(facts,event,(histories or {}).get(symbol),options)
            end=event_dates[i+1] if i+1<len(event_dates) else None
            active=(idx>=event)&((idx<end) if end is not None else True)
            for name,record in latest['features'].items():
                fingerprint=hashlib.sha256(_json(record).encode()).hexdigest()
                states.setdefault(fingerprint,record)
                event_status=record['status'] if previous.get(name)!=fingerprint else 'NO_NEW_INFORMATION'
                if record['status']=='VALID':
                    panels[name].loc[active,symbol]=record['value'];validity[name].loc[active,symbol]=True
                    if event_status=='VALID':last_valid[name]={'value':record['value'],'eligible_session':str(event.date()),'target_interval':record.get('latest_target_interval')}
                events[symbol][name].append({'eligible_session':str(event.date()),'event_status':event_status,
                    'resolved_status':record['status'],'feature_state_id':fingerprint,'last_valid':last_valid.get(name),
                    'last_valid_age_days':(event-pd.Timestamp(last_valid[name]['eligible_session'])).days if name in last_valid else None})
                previous[name]=fingerprint
        issuer[symbol]={'facts':len(facts),'latest':{
            'semantics_version':VERSION,'eligible_fact_versions':latest.get('eligible_fact_versions',0),
            'feature_statuses':{name:{'status':r['status'],'reason':r['reason'],'latest_target_interval':r.get('latest_target_interval')}
                                for name,r in latest.get('features',{}).items()},
            'constituent_lineage_location':'feature_events + feature_states'}}
    counts={key:sum(panels[n].notna().astype(int) for n in nameset) for key,nameset in [('quality',QUALITY_FEATURES),('value',VALUE_FEATURES)]}
    def composite(names,negative=()):
        if options.get('composite_policy','legacy_complete')=='legacy_complete':return _rank_composite(panels,names,negative=set(negative))
        total=pd.DataFrame(0.,index=idx,columns=symbols);count=total.copy()
        for name in names:
            ranked=panels[name].rank(axis=1,pct=True).mul(2).sub(1)
            total+=(-ranked if name in negative else ranked).fillna(0);count+=panels[name].notna().astype(int)
        return total.div(count.where(count>=2))
    return {'quality':composite(QUALITY_FEATURES,{'asset_growth_yoy','debt_to_assets'}),'value':composite(VALUE_FEATURES),
        'features':panels,'feature_validity':validity,'feature_events':events,'feature_states':states,'component_counts':counts,
        'metadata':{'mode':'sec_pit','semantics_version':VERSION,'numeric_panel_contract':'resolved_dense_v1',
            'composite_policy':options.get('composite_policy','legacy_complete'),'quality_component_names':list(QUALITY_FEATURES),
            'value_component_names':list(VALUE_FEATURES),'diagnostic_feature_names':list(DIAGNOSTIC_FEATURES) if options.get('diagnostic_features') else [],
            'issuer_diagnostics':issuer,'missing_values_preserved':True,'invalidation_clears_before_ranking':True,
            'accounting_scope_limitation':SCOPE_LIMITATION,'scope_options':options,
            'normalized_fact_observations':sum(len(f) for f in facts_by_symbol.values()),
            'pit_policy':'Original next-session-after-disclosure eligibility; no same-day relaxation'}}
