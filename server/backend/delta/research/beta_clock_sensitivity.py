"""Disabled frozen-fit current-Beta mechanism check, never a forecast consumer.

Both probability and score-interaction blocks change jointly for the original
seven-asset cross section. Training, normalization and parameters stay fixed.
Nonzero response establishes no predictive or clock-policy improvement.
Source authentication and empirical authority belong to the private runner.
"""
from copy import deepcopy
import hashlib
import json
import math
import numpy as np
from .reference_ledger import stamp

SYMBOLS = ('AAPL','MSFT','AMZN','GOOGL','JPM','XOM','WMT')
FEATURES = ('momentum_21','momentum_63','momentum_126','residual_momentum_21',
            'residual_momentum_63','short_reversion','low_volatility','value','quality')
NATIVE_SHA = '63cf2c4d2d38c1f37ef7d51a4a40c12561ce3d5e2747158d8902711393699d9b'
WRITER_SHA = 'a96e0f70a1ba2e02c6c44541aa2aab74c75013212add363a8bd52e9e6b78cce2'
VERSION = '0.4.5-entry-open-target-diagnostic'
ATOL = 1e-15
TRACE_FIELDS = frozenset(('origin','information_cutoff','target_end','symbols',
    'economic_feature_names','design_column_names','current_score','centered_score',
    'alpha_prior_means','Beta_probabilities','Beta_observed','current_design',
    'ridge_coefficients','ridge_center','ridge_scale','ridge_penalty',
    'clipped_design_with_intercept','pre_tanh','OOF_base_scale','residual_kappa',
    'reference_mean','actual_centered_correction','actual_relative','actual_absolute',
    'return_units','not_causal_profit_attribution'))
QUALIFICATION = ('ARTIFICIAL_FIXED_FIT_INPUT_SENSITIVITY; original-clock training remains '
    'fixed; event-specific mechanism only, no predictive improvement, corrected '
    'clock policy, admission, economic value, alpha or Sharpe implication.')


def require(ok, code):
    if not ok: raise ValueError('NOT_EVALUABLE:'+code)


def digest(value, *, ascii=True):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),
        ensure_ascii=ascii,allow_nan=False).encode()).hexdigest()


def design_names():
    return (['economic_rank:'+f for f in FEATURES]+['economic_observed:'+f for f in FEATURES]
        +['Beta_probability_'+str(i) for i in range(4)]+['Beta_observed']
        +['centered_Alpha_score_x_Beta_'+str(i) for i in range(4)])


def array(value, shape):
    def numeric(v):
        if type(v) is list:return all(numeric(x) for x in v)
        return type(v) in (int,float)
    require(numeric(value),'NUMERIC_NO_BOOL_OR_COERCION')
    try:a=np.asarray(value,dtype=float)
    except (TypeError,ValueError,OverflowError) as exc:raise ValueError('NOT_EVALUABLE:NUMERIC_ARRAY') from exc
    require(a.shape==shape and np.isfinite(a).all(),'FINITE_SHAPE')
    return a


def scalar(value):return float(array(value,()))


def close(a,b,code):
    require(a.shape==b.shape and np.allclose(a,b,rtol=0,atol=ATOL),code)
    return float(np.max(np.abs(a-b))) if a.size else 0.


def simplex(value):
    p=array(value,(4,))
    require(np.all((p>=0)&(p<=1)) and abs(float(p.sum())-1)<=1e-8,'SIMPLEX')
    return p


def reconstruct(t, design):
    center=array(t['ridge_center'],(27,));scale=array(t['ridge_scale'],(27,))
    coef=array(t['ridge_coefficients'],(28,));alpha=array(t['alpha_prior_means'],(7,))
    require(np.all(scale>0),'POSITIVE_NORMALIZATION_SCALE')
    base=scalar(t['OOF_base_scale']);kappa=scalar(t['residual_kappa']);reference=scalar(t['reference_mean'])
    require(base>=.0001 and kappa==.25 and scalar(t['ridge_penalty'])==1.,'FROZEN_FIT_PARAMETERS')
    try:
        with np.errstate(over='raise',invalid='raise',divide='raise'):
            z=np.column_stack((np.ones(7),np.clip((design-center)/scale,-8,8)))
            pre=z@coef;raw=kappa*base*np.tanh(pre);correction=raw-raw.mean()
            relative=alpha+correction;relative=relative-relative.mean();absolute=relative+reference
    except FloatingPointError as exc:raise ValueError('NOT_EVALUABLE:NONFINITE_INTERMEDIATE') from exc
    require(all(np.isfinite(v).all() for v in (z,pre,raw,correction,relative,absolute))
            and np.all(absolute>-1),'VALID_FROZEN_OUTPUT')
    return dict(z=z,pre=pre,correction=correction,relative=relative,absolute=absolute)


def analyze(trace, observation, record, binding):
    """Pure supplied-data analysis. The runner must authenticate all source bytes.

    No target, outcome, native forecast issuance or sizing function is accepted.
    JSON-compatible input objects are left unchanged.
    """
    t,o,r,b=deepcopy((trace,observation,record,binding))
    require(type(t)is dict and set(t)==TRACE_FIELDS,'SELECTIVE_TRACE_FIELDS')
    require(b['native_sha256']==NATIVE_SHA and b['writer_sha256']==WRITER_SHA,'SOURCE_VERSION')
    require(t['symbols']==list(SYMBOLS) and t['economic_feature_names']==list(FEATURES)
            and t['design_column_names']==design_names(),'ORDERED_ASSET_FEATURE_SCHEMA')
    require(t['origin']==o['origin']==b['origin'] and t['information_cutoff']==o['cutoff']==b['cutoff']
            and o['cohort']==b['cohort'] and o['prefix']==b['prefix']
            and t['target_end']==b['target_end'],'ORIGIN_COHORT_BINDING')
    require(t['return_units']=='simple_period_return' and t['not_causal_profit_attribution']is True,'TRACE_SCOPE')
    require(type(b['frontier_ns'])is int and b['frontier_ns']>stamp(b['cutoff']+'T00:00:00Z')
            and b['frontier_ns']<stamp(b['origin']+'T00:00:00Z'),'FRONTIER')
    require(not any(k in r for k in ('realized_outcome','realized_at')),'PREDICTION_ONLY')
    # Frozen producer explicitly uses naive exchange-session LABELS here.
    # They are not observed publication timestamps or the prior-close frontier.
    m=r['metadata'];require(r['model_id']=='gamma_entry_open_target_research_v1'
        and r['model_version']==VERSION and r['created_at']==b['origin']+'T00:00:00'
        and r['information_cutoff']==b['cutoff']+'T00:00:00'
        and r['horizon_sessions']==5 and r['units']=='period_return'
        and m['return_convention']=='next_session_open_to_horizon_close'
        and m['target_origin']==b['origin']+'T00:00:00'
        and m['target_start']==b['entry']+'T00:00:00' and m['target_end']==b['target_end']+'T00:00:00'
        and m['alpha_feature_names']==list(FEATURES) and m['residual_status']=='bounded_ridge_OOF_residual'
        and type(r['values'])is dict and set(r['values'])==set(SYMBOLS),'FORECAST_IDENTITY')
    require(digest(r,ascii=False)==b['record_sha256'],'ORIGINAL_RECORD_DIGEST')
    require(o['observed']is True and scalar(t['Beta_observed'])==1.,'OBSERVED_BETA_REQUIRED')
    old=simplex(t['Beta_probabilities']);require(np.array_equal(old,simplex(o['probabilities'])),'OLD_STATE_VECTOR')
    state=o['state'];alt=o['prior_close_diagnostic']
    require(type(state)is dict and type(alt)is dict and alt['policy_adopted']is False
        and type(alt['cutoff_ns'])is int and alt['cutoff_ns']==b['frontier_ns']
        and state['artifact_id']==alt['artifact_id'] and state['as_of']==b['old_asof']
        and alt['state_as_of']==b['new_asof'],'STATE_ARTIFACT_FRONTIER')
    require(np.array_equal(simplex(state['probabilities']),old)
        and digest(m['beta_provenance'])==state['native_beta_provenance_sha256'],'STATE_PROVENANCE')
    midnight=stamp(b['cutoff']+'T00:00:00Z')
    require(all(stamp(state[k])<=midnight for k in ('as_of','available_at','information_cutoff'))
            and stamp(state['as_of'])<=stamp(alt['state_as_of'])<=b['frontier_ns'],'STATE_CLOCKS')
    new=simplex(alt['probabilities'])
    require(abs(float(np.abs(new-old).sum())-scalar(alt['L1_difference']))<=1e-12,'RECORDED_VECTOR_DISTANCE')
    x=array(t['current_design'],(7,27));score=array(t['centered_score'],(7,));current=array(t['current_score'],(7,))
    close(score,current-current.mean(),'CENTERED_SCORE')
    require(np.all((x[:,9:18]==0)|(x[:,9:18]==1)) and np.all(np.abs(x[:,:9])<=1+ATOL)
            and np.all(x[:,:9][x[:,9:18]==0]==0),'ECONOMIC_FEATURE_DOMAINS')
    require(np.array_equal(x[:,18:22],np.tile(old,(7,1))) and np.all(x[:,22]==1.),'PROBABILITY_BLOCK')
    close(x[:,23:27],score[:,None]*old[None,:],'INTERACTION_BLOCK')
    original=reconstruct(t,x)
    errors=[close(original[k],array(t[field],shape),'SAVED_BASELINE_'+k) for k,field,shape in
        [('z','clipped_design_with_intercept',(7,28)),('pre','pre_tanh',(7,)),
         ('correction','actual_centered_correction',(7,)),('relative','actual_relative',(7,)),
         ('absolute','actual_absolute',(7,))]]
    errors.append(close(original['absolute'],array([r['values'][s]for s in SYMBOLS],(7,)),'RECORDED_FORECAST_BASELINE'))
    close(original['relative'],array([m['relative_means'][s]for s in SYMBOLS],(7,)),'RECORDED_RELATIVE_BASELINE')
    close(array(t['alpha_prior_means'],(7,)),array([m['alpha_prior_means'][s]for s in SYMBOLS],(7,)),'FROZEN_ALPHA')
    require(scalar(t['reference_mean'])==scalar(m['reference_mean'])
            and scalar(t['OOF_base_scale'])==scalar(m['OOF_baseline_error_scale']),'FROZEN_REFERENCE_SCALE')
    alternative=x.copy();alternative[:,18:22]=new;alternative[:,23:27]=score[:,None]*new[None,:]
    require(np.array_equal(alternative[:,:18],x[:,:18]) and np.array_equal(alternative[:,22],x[:,22]),'UNCHANGED_OTHER_INPUTS')
    changed=reconstruct(t,alternative);delta=changed['absolute']-original['absolute']
    msft=SYMBOLS.index('MSFT');gap=scalar(m['reference_mean'])-float(r['values']['MSFT'])
    ratio=None if gap==0 else float(delta[msft])/gap
    require(ratio is None or math.isfinite(ratio),'FINITE_GAP_RATIO')
    rows=[dict(symbol=s,old_absolute=float(original['absolute'][i]),alternative_absolute=float(changed['absolute'][i]),
        delta_absolute=float(delta[i]),old_relative=float(original['relative'][i]),alternative_relative=float(changed['relative'][i]))
        for i,s in enumerate(SYMBOLS)]
    frozen={k:t[k]for k in ('ridge_coefficients','ridge_center','ridge_scale','ridge_penalty',
        'residual_kappa','OOF_base_scale','alpha_prior_means','reference_mean')}
    return dict(schema='DELTA_BETA_CLOCK_FROZEN_FIT_MECHANISM_V1',outputs=rows,
        baseline_maximum_absolute_error=max(errors),maximum_absolute_mean_change=float(np.max(np.abs(delta))),
        exactly_invariant=bool(np.all(delta==0)),invariant_within_1e_15=bool(np.all(np.abs(delta)<=ATOL)),
        msft_delta=float(delta[msft]),established_reference_minus_full_msft_gap=gap,
        msft_delta_divided_by_established_gap=ratio,
        ratio_interpretation='Descriptive mean-gap ratio only; not a share of Sharpe explained.',
        frozen_parameter_sha256=digest(frozen),replaced_columns=design_names()[18:22]+design_names()[23:27],
        origin=b['origin'],old_state_asof=state['as_of'],alternative_state_asof=alt['state_as_of'],
        availability='Inherited accepted R81 reconstructed selection; publication unattested; no new available_at fabricated.',
        qualification=QUALIFICATION,model_fit=False,targets_used=False,policy_replay=False,live_authorized=False)
