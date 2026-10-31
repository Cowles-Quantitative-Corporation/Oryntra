"""Strict, versioned research controls. Unknown and unsafe settings fail closed."""
from __future__ import annotations
from copy import deepcopy
from hashlib import sha256
import json, math, re
from pathlib import Path,PurePosixPath
from datetime import date

VERSION = 'qpo-gamma-config-v1'
# path: (default, type, bounds/choices, description)
FIELDS = {
 'study.name': ('cold_baseline', str, None, 'Stable research arm label'),
 'study.seed': (560205, int, (0, 2**31-1), 'Optimizer and statistical reproducibility seed'),
 'study.label': ('exploratory', str, ['exploratory','confirmation'], 'Confirmation requires an untouched period declaration'),
 'data.universe': ('baseline183', str, ['baseline183','expanded300','expanded500','custom'], 'Data gate never silently substitutes a smaller universe'),
 'data.target_assets': (183, int, (6,500), 'Required universe size'),
 'data.prices_file': ('inputs/final_prices.csv.gz',str,None,'Adjusted source-calendar close panel'),
 'data.spy_file': ('inputs/final_spy.csv.gz',str,None,'Matched benchmark closes'),
 'data.sectors_file': ('',str,None,'Optional dated sector labels; unknown sectors cannot pass breadth'),
 'data.start': ('2016-09-09',str,None,'First scored return session'),
 'data.end': ('2026-09-18',str,None,'Last scored return session'),
 'data.require_sec': (False,bool,None,'Enable only with complete verified SEC lineage; not needed by price-only rank adapter'),
 'data.require_pit_membership': (False,bool,None,'Strict true rejects present-day/static survivor universes'),
 'data.minimum_history': (252,int,(252,1260),'Minimum causal history before first decision'),
 'data.liquidity_window': (63,int,(21,252),'Trailing observed sessions for liquidity screen'),
 'data.minimum_dollar_volume': (1000000.0,float,(0,1e10),'USD median daily volume threshold, unadjusted price alignment required for admission'),
 'training.artifact_mode': ('frozen_cold',str,['frozen_cold','frozen_warm_experimental','regenerated_cold','regenerated_warm','gamma_v1'],'Regenerated cold artifacts reproduce baseline; warm remains an explicit experimental alternative'),
 'training.model_variant': ('native_cold',str,None,'Gamma V1 registered model variant; legacy modes ignore it'),
 'state.v1a_policy': ('native',str,['native','off','tail_gamma_only'],'Separately identified downstream V1A state intervention; upstream inference and QRS remain'),
 'training.rank_window': (504,int,(126,1260),'Matured-origin cross-sectional calibration window'),
 'training.rank_min_days': (126,int,(42,504),'Minimum completed training origins'),
 'training.rank_ridge': (0.0001,float,(0,100),'Daily cross-sectional slope regularizer'),
 'training.target': ('native_residual',str,['native_residual','relative_return'],'Incremental outcome relative to as-issued native mean or raw cross-sectional return'),
 'ranking.model': ('composite_residual',str,['composite_residual','native_shrinkage'],'Fixed residual-mean model; shrinkage is a separately registered simple comparator'),
 'ranking.enabled': (False,bool,None,'Add calibrated Alpha-feature signal to native mean'),
 'ranking.strength': (0.25,float,(0,1),'Multiplier on calibrated horizon return; no implied expected benefit'),
 'ranking.maximum_horizon_delta': (0.05,float,(0,0.20),'Symmetric hard bound before equal-weight centering'),
 'ranking.include_momentum_21': (True,bool,None,'Reuse original Alpha 21-session rank feature'),
 'ranking.include_momentum_63': (True,bool,None,'Reuse original Alpha 63-session rank feature'),
 'ranking.include_momentum_126': (True,bool,None,'Reuse original Alpha 126-session rank feature'),
 'ranking.include_residual_momentum_21': (True,bool,None,'Benchmark-residual 21-session feature'),
 'ranking.include_residual_momentum_63': (True,bool,None,'Benchmark-residual 63-session feature'),
 'ranking.include_short_reversion': (True,bool,None,'Original Alpha short-term reversal feature'),
 'ranking.include_low_volatility': (True,bool,None,'Original Alpha low-volatility feature'),
 'ranking.nonnegative_slope': (True,bool,None,'Preserve suppression of nonpositive robust calibration'),
 'errors.policy': ('legacy_unavailable',str,['legacy_unavailable','matched_as_issued'],'Legacy absence is explicit; matched branch requires each arm\'s own causal issued errors'),
 'errors.window_origins': (60,int,(30,120),'Maximum non-overlapping completed error origins'),
 'errors.minimum_origins': (30,int,(30,120),'Fail closed on insufficient matched error support'),
 'errors.covariance_window': (60,int,(12,120),'Prior-only error covariance window'),
 'errors.covariance_min_history': (12,int,(6,60),'Native V1A standardized-error history'),
 'errors.covariance_floor': (1e-8,float,(1e-12,1e-3),'PSD regularization; no zero-fill missing errors'),
 'macro.mode': ('off',str,['off','verified_exposure'],'Additional macro exposure overlay; existing archived Beta still remains'),
 'macro.records_file': ('',str,None,'Verified PIT macro exposure records with model/lineage identity'),
 'macro.maximum_cut': (0.10,float,(0,0.30),'Maximum incremental gross reduction; cannot increase exposure'),
 'macro.max_age_sessions': (5,int,(0,21),'Maximum stale age for externally generated macro decision'),
 'portfolio.lookback': (252,int,(252,756),'Causal covariance/native-mean lookback'),
 'portfolio.rebalance': (21,int,[21],'Frozen V1A artifact horizon and decision grid'),
 'portfolio.max_name': (0.30,float,(0.05,0.30),'Target cap; drift and final risk policy are audited separately'),
 'portfolio.target_gross': (1.0,float,(0.1,1.0),'No leverage escalation'),
 'portfolio.qscan_assets': (6,int,(4,12),'Adjustable active set'),
 'portfolio.bits': (2,int,(1,3),'Per-name grid bits; exact state bound also enforced'),
 'portfolio.min_positions': (0,int,(0,20),'Minimum positive positions'),
 'portfolio.risk_aversion': (3.0,float,(0.1,20),'Native objective penalty'),
 'portfolio.turnover_penalty': (0.20,float,(0,5),'Native turnover objective penalty'),
 'portfolio.resilience_alpha': (1.0,float,(0,1),'Existing resilience model weight'),
 'portfolio.correlation_blend': (0.65,float,[0.65],'Preserved QRM stress policy'),
 'portfolio.risk_budget': (0.15,float,[0.15],'Preserved native annual risk budget'),
 'portfolio.scale_tolerance': (0.03,float,[0.03],'Preserved native gross tolerance'),
 'portfolio.full_qrs_beta': (0.10,float,(0.01,0.25),'Lower-tail quantile level; separately identified risk experiment'),
 'portfolio.quantile_confidence': (0.90,float,(0.8,0.99),'Conservative error-tail order-statistic confidence'),
 'execution.initial_usd': (1000.0,float,(1,1e9),'Reporting capital, fractional shares'),
 'execution.cost_bps': (5.0,float,(0,100),'Per-event L1 transaction cost'),
 'execution.cash_annual': (0.0,float,(0,0.10),'Cash yield'),
 'execution.borrow_annual': (0.04,float,(0,0.30),'Borrowing financing rate'),
 'execution.metric_rf': (0.04,float,(0,0.15),'Metric-only excess return risk-free rate'),
 'execution.fee_convention': ('legacy_pre_cost_weight_L1',str,['legacy_pre_cost_weight_L1'],'Freeze book convention; exact-dollar accounting is a separately labeled diagnostic'),
 'evidence.minimum_supporting_stocks': (4,int,(4,100),'Descriptive minimum, never independent confirmation'),
 'evidence.minimum_improving_fraction': (0.50,float,(0.5,1),'Among stocks with material changed contribution'),
 'evidence.minimum_sectors': (3,int,(2,11),'Unknown sectors do not count'),
 'evidence.time_blocks': (5,int,(3,10),'Contiguous nonoverlapping stability blocks'),
 'evidence.minimum_positive_blocks': (3,int,(2,10),'Minimum improving paired return blocks'),
 'evidence.top1_max_positive_share': (0.35,float,(0.05,0.5),'Concentration of positive contribution improvement'),
 'evidence.top3_max_positive_share': (0.65,float,(0.1,0.8),'Top-three positive contribution concentration'),
 'evidence.bootstrap_block_sessions': (126,int,(21,504),'Paired circular whole-path resampling blocks'),
 'evidence.bootstrap_replications': (1000,int,(200,10000),'Seeded exploratory bootstrap'),
 'evidence.family_alpha': (0.05,float,(0.001,0.10),'Holm family-wise testing threshold'),
 'evidence.future_holdout_start': ('2026-10-05',str,None,'Not a claim that future observations already exist'),
}

def flatten(d, prefix=''):
    out={}
    for k,v in d.items():
        p=prefix+'.'+k if prefix else k
        if isinstance(v,dict): out.update(flatten(v,p))
        else: out[p]=v
    return out

def unflatten(d):
    out={}
    for path,value in d.items():
        node=out;parts=path.split('.')
        for p in parts[:-1]: node=node.setdefault(p,{})
        node[parts[-1]]=value
    return out

def canonical(d): return json.dumps(d,sort_keys=True,separators=(',',':'),allow_nan=False)
def fingerprint(d): return sha256(canonical(d).encode()).hexdigest()
def defaults(): return unflatten({k:deepcopy(v[0]) for k,v in FIELDS.items()})

def validate(document):
    if not isinstance(document,dict): raise ValueError('Configuration must be an object')
    sections={path.split('.')[0] for path in FIELDS}
    for section,values in document.items():
        if section not in sections:raise ValueError('Unknown config section: '+str(section))
        if not isinstance(values,dict):raise ValueError('Config section must be an object: '+section)
        for key,value in values.items():
            path=section+'.'+str(key)
            if path not in FIELDS:raise ValueError('Unknown config field: '+path)
            if isinstance(value,dict):raise ValueError('Scalar setting required: '+path)
    actual=flatten(document);unknown=set(actual)-set(FIELDS)
    if unknown: raise ValueError('Unknown config fields: '+', '.join(sorted(unknown)))
    all_values={k:deepcopy(v[0]) for k,v in FIELDS.items()};all_values.update(actual)
    for p,v in all_values.items():
        _,kind,limit,_=FIELDS[p]
        valid=(type(v) is bool if kind is bool else type(v) is int if kind is int else type(v) in (int,float) and math.isfinite(v) if kind is float else type(v) is str)
        if not valid: raise ValueError('Invalid type/nonfinite '+p)
        if isinstance(limit,tuple) and not limit[0]<=v<=limit[1]: raise ValueError('Out of range '+p)
        if isinstance(limit,list) and v not in limit: raise ValueError('Unsupported '+p)
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,80}',all_values['study.name']): raise ValueError('Invalid arm name')
    for key in ['data.start','data.end','evidence.future_holdout_start']:
        try:date.fromisoformat(all_values[key])
        except ValueError as exc:raise ValueError('ISO YYYY-MM-DD date required: '+key) from exc
    if all_values['data.start']>all_values['data.end']:raise ValueError('Evaluation start follows end')
    for key in ['data.prices_file','data.spy_file','data.sectors_file','macro.records_file']:
        path=PurePosixPath(all_values[key])
        if path.is_absolute() or '..' in path.parts:raise ValueError('Bundle-relative input path required: '+key)
    if all_values['portfolio.qscan_assets']*all_values['portfolio.bits']>18: raise ValueError('Exact search capped at 18 binary variables')
    if all_values['portfolio.min_positions']>all_values['data.target_assets']: raise ValueError('Minimum positions exceeds available assets')
    if all_values['portfolio.max_name']*all_values['data.target_assets']<all_values['portfolio.target_gross']: raise ValueError('Aggregate name caps cannot fund target gross')
    if all_values['training.rank_min_days']>all_values['training.rank_window']: raise ValueError('Calibration support exceeds window')
    if all_values['errors.minimum_origins']>all_values['errors.window_origins']: raise ValueError('Error support exceeds window')
    if all_values['evidence.minimum_positive_blocks']>all_values['evidence.time_blocks']: raise ValueError('Positive blocks exceed total')
    if all_values['macro.mode']!='off' and not all_values['macro.records_file']: raise ValueError('Verified macro records required')
    if all_values['study.label']=='confirmation' and all_values['data.start']<all_values['evidence.future_holdout_start']: raise ValueError('Inspected historical period cannot be relabeled untouched confirmation')
    if all_values['data.universe']=='expanded300' and all_values['data.target_assets']<300: raise ValueError('300-stock study cannot shrink silently')
    if all_values['data.universe']=='expanded500' and all_values['data.target_assets']!=500: raise ValueError('500-stock study requires 500')
    if (all_values['ranking.enabled'] or all_values['errors.policy']=='matched_as_issued') and not any(all_values[k] for k in FIELDS if k.startswith('ranking.include_')): raise ValueError('Ranking or matched-error support requires active features')
    if all_values['ranking.model']=='native_shrinkage' and all_values['training.target']!='native_residual':raise ValueError('Native shrinkage has a fixed native-residual target')
    return unflatten(all_values)

def load(path): return validate(json.loads(Path(path).read_text()))
def delta(a,b):
    a,b=flatten(validate(a)),flatten(validate(b))
    return {k:{'before':a[k],'after':b[k]} for k in sorted(a) if a[k]!=b[k] and k!='study.name'}

def schema():
    root={'$schema':'https://json-schema.org/draft/2020-12/schema','$id':VERSION,'type':'object','additionalProperties':False,'properties':{}}
    types={str:'string',bool:'boolean',int:'integer',float:'number'}
    for path,(default,kind,limits,description) in FIELDS.items():
        section,key=path.split('.');parent=root['properties'].setdefault(section,{'type':'object','additionalProperties':False,'properties':{}})
        entry={'type':types[kind],'default':default,'description':description}
        if isinstance(limits,tuple):entry.update(minimum=limits[0],maximum=limits[1])
        elif isinstance(limits,list):entry['enum']=limits
        parent['properties'][key]=entry
    return root

def effective_controls(config):
    c=validate(config);rows=[]
    for path,value in flatten(c).items():
        section=path.split('.')[0];status='active';reason='Consumed by execution or evidence calculation'
        if path in ['portfolio.risk_aversion','portfolio.turnover_penalty']:
            status='diagnostic_only';reason='Residual-budget winner uses Full-QRS, not quadratic objective_utility'
        elif path=='training.model_variant' and c['training']['artifact_mode']!='gamma_v1':
            status='inactive';reason='Only used by Gamma V1 registered input manifest'
        elif path in ['data.liquidity_window','data.minimum_dollar_volume']:
            status='inactive_in_frozen_baseline';reason='Expanded-universe admission parameters only; raw-price/share-volume validation must pass before use'
        elif path=='ranking.nonnegative_slope' and c['ranking']['model']=='native_shrinkage':
            status='inactive';reason='Native shrinkage has an inherent coefficient range[0,1]; this flag controls only the composite model'
        elif section=='ranking' and not c['ranking']['enabled']:
            status='support_diagnostics_only' if c['errors']['policy']=='matched_as_issued' else 'inactive';reason='Mean increment disabled; matched errors still use the common ranking-readiness mask' if c['errors']['policy']=='matched_as_issued' else 'Mean increment disabled'
        elif path.startswith('training.rank_') or path=='training.target':
            if not c['ranking']['enabled'] and c['errors']['policy']=='legacy_unavailable':status='inactive';reason='Ranking and matched error readiness are both disabled'
        elif section=='errors' and c['errors']['policy']=='legacy_unavailable' and path!='errors.policy':status='inactive';reason='Explicit legacy missing-ledger policy'
        elif section=='macro' and c['macro']['mode']=='off' and path!='macro.mode':status='inactive';reason='No additional macro overlay; existing native Beta stays active'
        elif section=='study' or path in ['data.sectors_file','evidence.future_holdout_start']:status='metadata_or_validation';reason='Identity, reporting or admission control'
        rows.append({'path':path,'value':value,'status':status,'reason':reason})
    return rows
