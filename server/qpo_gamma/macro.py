"""Release-vintage selection and strictly additional exposure controls.

Observed ALFRED vintage dates establish eligibility only with explicit source
provenance and conservative availability timestamps. Latest revised values and
observation dates alone are never accepted as historical release availability.
"""
import json
import numpy as np
import pandas as pd
from .config import canonical, fingerprint

def utc(value):
    if value is None or isinstance(value,(int,float,bool)):raise ValueError('Explicit timestamp required')
    t=pd.Timestamp(value)
    if pd.isna(t):raise ValueError('Missing timestamp')
    return t.tz_localize('UTC') if t.tz is None else t.tz_convert('UTC')

def _vintage(row):
    """Validate a used observation and normalize its immutable input identity."""
    if not isinstance(row,dict) or not isinstance(row.get('series_id'),str) or not row['series_id'].strip():raise ValueError('Macro series identity required')
    if row.get('vintage_verified') is not True or not isinstance(row.get('source'),str) or 'ALFRED' not in row['source']:raise ValueError('Unverified macro vintage provenance')
    clocks={key:utc(row.get(key)) for key in ['observation_date','release_at','realtime_start','available_to_model_at']}
    if clocks['observation_date']>clocks['release_at']:raise ValueError('Macro observation follows its release')
    if clocks['available_to_model_at']<max(clocks['release_at'],clocks['realtime_start']):raise ValueError('Macro availability precedes release/vintage')
    value=row.get('value')
    if isinstance(value,(bool,str)) or not np.isscalar(value) or not np.isfinite(value):raise ValueError('Invalid macro value')
    normalized={**row,**{k:v.isoformat() for k,v in clocks.items()},'value':float(value)}
    return normalized,clocks

def select_vintages(records,cutoff,required_series):
    cutoff=utc(cutoff);out={};seen={}
    for row in records:
        series=row['series_id']
        if series not in required_series:continue
        # An archive may contain future revisions, which cannot affect this
        # selection even through validation of their later value/provenance.
        if utc(row.get('available_to_model_at'))>cutoff:continue
        normalized,clocks=_vintage(row)
        key=(clocks['observation_date'],clocks['realtime_start'])
        identity=(series,*key);content=canonical(normalized)
        if identity in seen and seen[identity]!=content:raise ValueError('Conflicting duplicate macro vintage')
        seen[identity]=content
        if series not in out or key>out[series][0]:out[series]=(key,dict(row))
    missing=set(required_series)-set(out)
    if missing:raise ValueError('Missing eligible macro series: '+','.join(sorted(missing)))
    return {k:v[1] for k,v in out.items()}

def select_exposure(records,cutoff,sessions,config):
    c=utc(cutoff);idx=pd.DatetimeIndex(sessions);idx=idx.tz_localize('UTC') if idx.tz is None else idx.tz_convert('UTC')
    if not idx.is_unique or not idx.is_monotonic_increasing or idx.hasnans:raise ValueError('Unique ascending macro source sessions required')
    eligible=[];seen={}
    for row in records:
        t=utc(row['information_cutoff'])
        if t>c:continue
        required=('producer','config_hash','training_hash','source_hash')
        if any(not isinstance(row.get(k),str) or not row[k].strip() for k in required):raise ValueError('Incomplete macro model lineage')
        vintages=row.get('vintages')
        if not isinstance(vintages,list) or not vintages:raise ValueError('Exact used macro vintage inputs required')
        train=utc(row['training_cutoff'])
        if train>=t:raise ValueError('Macro model training cutoff must precede decision')
        used={}
        for source in vintages:
            normalized,clocks=_vintage(source);series=normalized['series_id']
            if clocks['available_to_model_at']>t:raise ValueError('Used macro vintage is unavailable at exposure cutoff')
            if series in used:raise ValueError('Exact used macro inputs require one selected vintage per series')
            used[series]=normalized
        selected=select_vintages(vintages,t,list(used))
        selected_normalized={series:_vintage(source)[0] for series,source in selected.items()}
        if selected_normalized!=used:raise ValueError('Used macro inputs differ from eligible selected vintages')
        score=float(row['stress_score'])
        if not np.isfinite(score) or not 0<=score<=1:raise ValueError('Macro stress score must lie in [0,1]')
        normalized={**row,'information_cutoff':t.isoformat(),'training_cutoff':train.isoformat(),'stress_score':score,'vintages':[used[k] for k in sorted(used)]}
        content=canonical(normalized)
        if t in seen and seen[t]!=content:raise ValueError('Conflicting macro exposure observations at one cutoff')
        seen[t]=content
        eligible.append((t,{**row,'used_inputs_sha256':fingerprint(used),'used_input_series':sorted(used),'used_vintage_contract':'exact eligible selected inputs, not an archive'}))
    if not eligible:raise ValueError('No verified additional macro exposure observation')
    t,row=max(eligible,key=lambda x:x[0]);age=int(((idx>t)&(idx<=c)).sum())
    if age>config['max_age_sessions']:raise ValueError('Stale macro exposure observation')
    multiplier=1-config['maximum_cut']*float(row['stress_score'])
    return multiplier,{**row,'additional_multiplier':multiplier,'age_sessions':age,'existing_native_Beta_remains':True,'not_direct_alpha':True}
