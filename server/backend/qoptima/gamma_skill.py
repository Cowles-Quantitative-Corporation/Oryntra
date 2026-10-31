"""Outcome-based forecast diagnostics, never fed back into portfolio decisions."""
from __future__ import annotations
import math
import numpy as np
import pandas as pd


def _rank_ic(x,y):
    x=pd.Series(x).rank().to_numpy();y=pd.Series(y).rank().to_numpy()
    return None if np.std(x)<=1e-12 or np.std(y)<=1e-12 else float(np.corrcoef(x,y)[0,1])


def _fractional_tail_mean(signal, realized, mass, *, upper):
    remaining=float(mass);weighted=0.0
    for value in sorted(set(map(float,signal)),reverse=upper):
        group=np.asarray(signal)==value;take=min(remaining,int(group.sum()))
        weighted+=take*float(np.asarray(realized)[group].mean());remaining-=take
        if remaining<=1e-12:break
    return weighted/mass


def forecast_skill_scorecard(records, *, evaluation_start, evaluation_end, session_index, seed=41, bootstrap_reps=2000):
    """Use only issued, complete, matured horizon outcomes on declared eval dates.

    Intervals resample consecutive forecast dates in blocks at least three
    forecast origins long and at least the overlap horizon. They are descriptive
    uncertainty, not selection-adjusted significance or a confirmatory holdout.
    """
    idx=pd.DatetimeIndex(session_index);idx=idx.tz_localize(None)
    if idx.has_duplicates or not idx.is_monotonic_increasing or idx.hasnans:
        raise ValueError('Forecast skill requires a unique increasing explicit session calendar')
    start=pd.Timestamp(evaluation_start).tz_localize(None);end=pd.Timestamp(evaluation_end).tz_localize(None)
    rows=[];excluded={};seen=set()
    for record in records:
        meta=record.get('metadata') or {};origin=pd.Timestamp(meta.get('target_origin',record['created_at'])).tz_localize(None)
        if not start<=origin<=end:continue
        if origin not in idx:raise ValueError('Forecast origin missing from supplied session calendar')
        if origin in seen:raise ValueError('Forecast scorecard refuses duplicate origins')
        seen.add(origin)
        outcome=record.get('realized_outcome');maturity=record.get('realized_at')
        horizon=record.get('horizon_sessions')
        if isinstance(horizon,bool) or not isinstance(horizon,(int,np.integer)) or horizon<=0:
            raise ValueError('Forecast horizon must be a positive integer')
        origin_position=int(idx.get_loc(origin))
        if maturity is not None:
            realized=pd.Timestamp(maturity).tz_localize(None)
            if origin_position+horizon>=len(idx) or realized!=idx[origin_position+horizon]:
                raise ValueError('Forecast realization does not match exact trading-session horizon')
            if meta.get('target_end') is not None and pd.Timestamp(meta['target_end']).tz_localize(None)!=realized:
                raise ValueError('Forecast target end and realization differ')
        if not outcome or maturity is None or pd.Timestamp(maturity).tz_localize(None)>end:
            excluded[str(origin)]='unmatured_or_missing_outcome';continue
        symbols=list(record['values']);y=np.asarray([outcome.get(s,np.nan) for s in symbols],float)
        f=np.asarray([record['values'][s] for s in symbols],float)
        a=np.asarray([meta.get('alpha_prior_means',{}).get(s,np.nan) for s in symbols],float)
        ref=float(meta.get('reference_mean',np.nan))
        if len(y)<6 or not np.isfinite(np.r_[y,f,a,ref]).all():
            excluded[str(origin)]='incomplete_fixed_origin_basket';continue
        relative=y-y.mean();final_relative=f-ref
        count=max(1,len(y)//5)
        spread=None if np.ptp(final_relative)<=1e-12 else float(_fractional_tail_mean(final_relative,y,count,upper=True)-_fractional_tail_mean(final_relative,y,count,upper=False))
        rows.append({'origin':str(origin),'maturity':str(maturity),'horizon_sessions':int(record['horizon_sessions']),
            'assets':len(symbols),'final_rank_ic':_rank_ic(final_relative,relative),'alpha_rank_ic':_rank_ic(a,relative),
            'top_minus_bottom_quintile_return':spread,
            'reference_mse':float(np.mean((y-ref)**2)), 'alpha_mse':float(np.mean((y-ref-a)**2)),
            'final_mse':float(np.mean((y-f)**2)),
            'incremental_alpha_mse_improvement':float(np.mean((y-ref)**2)-np.mean((y-ref-a)**2)),
            'incremental_residual_mse_improvement':float(np.mean((y-ref-a)**2)-np.mean((y-f)**2))})
    rows.sort(key=lambda r:r['origin']);origins=[pd.Timestamp(r['origin']) for r in rows]
    positions=np.array([idx.searchsorted(o) for o in origins]);spacing=float(np.median(np.diff(positions))) if len(positions)>1 else 1.
    horizon=max([r['horizon_sessions'] for r in rows] or [1])
    overlapping_origins=max([int(np.sum((positions>=pos)&(positions<pos+row['horizon_sessions']))) for pos,row in zip(positions,rows)] or [1])
    block=max(3,overlapping_origins)
    rng=np.random.default_rng(seed);summary={}
    keys=['final_rank_ic','alpha_rank_ic','top_minus_bottom_quintile_return','reference_mse','alpha_mse','final_mse','incremental_alpha_mse_improvement','incremental_residual_mse_improvement']
    # Common resampling indices preserve alignment between all feature arms.
    draws=[];n=len(rows)
    if n>=2*block:
        for _ in range(bootstrap_reps):
            chosen=[]
            while len(chosen)<n:
                first=int(rng.integers(0,max(1,n-block+1)));chosen.extend(range(first,min(n,first+block)))
            draws.append(chosen[:n])
    for key in keys:
        values=np.array([np.nan if r[key] is None else r[key] for r in rows]);finite=values[np.isfinite(values)]
        estimates=[]
        for draw in draws:
            sample=values[draw];sample=sample[np.isfinite(sample)]
            if len(sample):estimates.append(float(sample.mean()))
        summary[key]={'mean':float(finite.mean()) if len(finite) else None,'dates_observed':len(finite),
            'descriptive_95pct_block_interval':np.quantile(estimates,[.025,.975]).tolist() if estimates and len(finite)>=max(12,2*block) else None,
            'interval_support':'sufficient_descriptive_dates' if len(finite)>=max(12,2*block) else 'insufficient_finite_dates'}
    return {'schema':'gamma_forecast_skill_v1','purpose':'historical_walkforward_exploration_not_confirmatory_validation',
        'evaluation_start':str(start),'evaluation_end':str(end),'scored_origins':len(rows),'excluded':excluded,
        'block_origins':block,'maximum_overlapping_forecast_origins':overlapping_origins,'median_origin_spacing_sessions':spacing,'horizon_sessions':horizon,
        'bootstrap_replications':len(draws),'bootstrap_seed':seed,'summary':summary,'rows':rows,
        'limitations':['static supplied universe survivorship unresolved','model changes motivated by this historical sample; an untouched confirmatory sample is still required',
            'overlap and serial dependence addressed only by descriptive fixed-block resampling; no multiple-testing correction',
            'complete-basket selection can be nonrandom when historical delistings or missing outcomes occur',
            'no traded long-short portfolio or transaction-cost-adjusted Alpha is implied by ranking spreads']}
