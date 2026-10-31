"""Private Oryntra Control routes for CQC Model C / Beta V1."""
from __future__ import annotations
import json, os
from pathlib import Path
import pandas as pd
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
from ..beta_v1 import BetaV1Config, beta_v1_alpha_context, beta_v1_contract, beta_v1_risk_payload, beta_v1_service
from ..internal_access import require_control_operator
from ..market_repository import get_market_repository
from ..provider_credentials import decrypted_credentials
from ..models.beta.features import BetaFeatureEngine
from ..models.beta.config import BETA_V1_CONFIG, BETA_TRAINING_CODE_REVISION, BETA_CANDIDATE_SCHEMA_VERSION
from ..research_universe import BROAD_US_LIQUID_UNIVERSE
from ..phase4_jobs import submit_job
try:
    from ..research_experiments import record_experiment
except Exception:
    record_experiment=None
router=APIRouter(); _SERVICE=None
_ROOT=Path(os.getenv('ORYNTRA_BETA_ROOT',str(Path(__file__).resolve().parents[2]/'data'/'beta_v1')))
_raw_allow_api=os.getenv('ORYNTRA_BETA_ALLOW_API')
_ALLOW_API=(_raw_allow_api.strip().lower() in {'1','true','yes','on'}) if _raw_allow_api is not None else os.getenv('ORYNTRA_CONTROL_LOCAL_MODE','0').strip().lower() in {'1','true','yes','on'}
class BetaRunRequest(BaseModel):
    universe:list[str]=Field(default_factory=lambda:list(BROAD_US_LIQUID_UNIVERSE[:60]),min_length=12,max_length=250)
    min_train_sessions:int=Field(default=756,ge=252); calibration_sessions:int=Field(default=126,ge=40); test_sessions:int=Field(default=126,ge=40); final_holdout_sessions:int=Field(default=252,ge=60)
class BetaSyncRequest(BaseModel): force_full:bool=False
class BetaConsensusCSVRequest(BaseModel): csv_text:str=Field(min_length=1,max_length=5_000_000)
class BetaIncrementalBacktestRequest(BaseModel):
    universe:list[str]=Field(default_factory=lambda:list(BROAD_US_LIQUID_UNIVERSE[:60]),min_length=12,max_length=250)
    provider:str="auto"
    construction:str="phase15"
    phase3_enabled:bool=True
    fundamentals_mode:str="sec_pit"
    evaluation_period:str="5y"
    model_benchmark:str="SPY/QQQ/IWM/VXUS"
    portfolio_budget:float=100000.0
    force_refresh:bool=False
    force_refresh_risk_free:bool=False
    bootstrap_reps:int=Field(default=500,ge=100,le=5000)

def _fred_key(user_id:int|None=None):
    key=os.getenv('FRED_API_KEY')
    if key or user_id is None:return key
    try:return decrypted_credentials(int(user_id)).get('fred')
    except Exception:return None

def _provider_keys(user_id:int|None=None):
    if user_id is None:return {}
    try:return decrypted_credentials(int(user_id))
    except Exception:return {}

def _service(user_id:int|None=None):
    global _SERVICE
    key=_fred_key(user_id)
    if _SERVICE is None:_SERVICE=beta_v1_service(_ROOT,fred_api_key=key,strict_pit=True,config=BetaV1Config(enabled=True),experiment_recorder=record_experiment)
    else:_SERVICE.macro.fred_api_key=key
    return _SERVICE

def _history_basis(repo, symbol, history):
    attrs=dict(getattr(history,'attrs',{}) or {})
    basis=str(attrs.get('price_basis') or '').strip().lower()
    if basis in {'adjusted','adjusted_close','total_return','adjusted_or_total_return'}:
        return 'adjusted_close' if basis!='total_return' else 'total_return'
    if attrs.get('all_adjusted') is True:
        return 'adjusted_close'
    try:
        summary=repo.cache_summary(symbol)
    except Exception:
        summary={}
    adjusted=summary.get('all_adjusted')
    return 'adjusted_close' if adjusted is not None and bool(adjusted) else 'unadjusted_close'

def _load_explicit_adjusted(repo, symbol):
    loader=getattr(repo,'load_adjusted',None)
    if not callable(loader):return None
    try:
        frame=loader(symbol,period='all')
    except TypeError:
        frame=loader(symbol)
    except Exception:
        return None
    return frame if frame is not None and not frame.empty else None

def _close(symbol,minimum=756,user_id:int|None=None,*,refresh_if_needed:bool=True):
    """Return a canonical adjusted close series suitable for Beta.

    Legacy Control databases can contain mixed adjusted/unadjusted rows.  Beta
    therefore prefers rows explicitly marked adjusted, and if those are too short
    it refreshes the canonical cache (when private-local API access is enabled)
    instead of silently training on unadjusted prices.
    """
    repo=get_market_repository(); keys=_provider_keys(user_id)
    def fetch(force_refresh=False,allow_api=None):
        return repo.get_history(
            symbol,period='all',minimum_bars=minimum,
            allow_api=(_ALLOW_API if allow_api is None else bool(allow_api)),
            force_refresh=bool(force_refresh),provider_preference='auto',
            provider_api_keys=keys,allow_platform_provider_keys=False,
        )
    # A cache-only read is enough for preflight; training may use API fallback.
    r=fetch(False, _ALLOW_API if refresh_if_needed else False)
    h=r.history
    explicit=_load_explicit_adjusted(repo,symbol)
    if explicit is not None and len(explicit)>=int(minimum):
        return explicit['Close'].astype(float).rename(symbol),'adjusted_close'
    basis=_history_basis(repo,symbol,h)
    if basis in {'adjusted_close','total_return'} and len(h)>=int(minimum):
        return h['Close'].astype(float).rename(symbol),basis
    if refresh_if_needed and _ALLOW_API:
        # get_history() returns a fresh cache immediately when it is long enough,
        # even if legacy rows are unadjusted. Force a provider refresh in that case.
        r=fetch(True,True); h=r.history
        explicit=_load_explicit_adjusted(repo,symbol)
        if explicit is not None and len(explicit)>=int(minimum):
            return explicit['Close'].astype(float).rename(symbol),'adjusted_close'
        basis=_history_basis(repo,symbol,h)
        if basis in {'adjusted_close','total_return'} and len(h)>=int(minimum):
            return h['Close'].astype(float).rename(symbol),basis
    adjusted_bars=int(len(explicit)) if explicit is not None else (int(len(h)) if basis in {'adjusted_close','total_return'} else 0)
    total_bars=int(len(h)) if h is not None else 0
    if adjusted_bars<int(minimum):
        raise ValueError(
            f'{symbol} has only {adjusted_bars} explicitly adjusted daily bars; Beta requires at least {int(minimum)}. '
            f'Canonical cache contains {total_bars} total bars. '
            + ('Control attempted an adjusted provider refresh but still could not obtain enough history.' if refresh_if_needed and _ALLOW_API else 'Adjusted market-data API refresh is not enabled for this operation.')
        )
    raise ValueError(f'{symbol} does not have verified adjusted/total-return market history for Beta.')

def _market(user_id:int|None=None,*,refresh_if_needed:bool=True,minimum:int=2520):
    ss=[]; bases={}; bars={}
    for s in BetaFeatureEngine.MARKET_TICKERS:
        x,b=_close(s,minimum,user_id=user_id,refresh_if_needed=refresh_if_needed);ss.append(x);bases[s]=b;bars[s]=int(x.notna().sum())
    p=pd.concat(ss,axis=1).sort_index().dropna(how='all'); good=all(x in {'adjusted_close','total_return'} for x in bases.values())
    p.attrs.update({
        'provenance':'oryntra_canonical',
        'price_basis':'adjusted_or_total_return' if good else 'unadjusted_close',
        'series_price_basis':bases,'series_bars':bars,
        'minimum_required_bars':int(minimum),'verified_adjusted':bool(good),
    })
    return p

def _market_preflight(user_id:int|None=None,minimum:int=2520):
    rows={}; ready=True
    for s in BetaFeatureEngine.MARKET_TICKERS:
        try:
            x,b=_close(s,minimum,user_id=user_id,refresh_if_needed=False)
            rows[s]={'ready':True,'bars':int(x.notna().sum()),'price_basis':b}
        except Exception as exc:
            ready=False; rows[s]={'ready':False,'error':str(exc)}
    return {'ready':bool(ready),'minimum_required_bars':int(minimum),'series':rows,'api_refresh_available':bool(_ALLOW_API)}

def _context(universe,market,user_id:int|None=None):
    ss=[]
    for s in universe:
        try:x,_=_close(s,756,user_id=user_id,refresh_if_needed=True);ss.append(x)
        except Exception:continue
    if len(ss)<12:raise ValueError('Beta Alpha-context requires at least 12 usable adjusted histories')
    stocks=pd.concat(ss,axis=1).sort_index(); bench=market['SPY'].reindex(stocks.index).pct_change(fill_method=None)
    out=beta_v1_alpha_context(stocks,benchmark_returns=bench,config=BetaV1Config(enabled=True))
    out.attrs.update({'provenance':'oryntra_canonical_adjusted_context','adjusted_security_histories':int(len(ss)),'tba_anchor_consumed':False,'alpha_forecast_consumed':False})
    return out

@router.get('/beta/contract')
def contract(request:Request):require_control_operator(request);return beta_v1_contract(BetaV1Config(enabled=True))
@router.get('/beta/artifact-status')
def artifact_status(request:Request):
    user=require_control_operator(request);return _service(user['id']).artifact_status().to_dict()
@router.get('/beta/diagnostics')
def diagnostics(request:Request):
    user=require_control_operator(request);return _service(user['id']).diagnostics()
@router.get('/beta/policy')
def policy(request:Request):
    user=require_control_operator(request);return _service(user['id']).grade()
@router.get('/beta/operations-status')
def operations_status(request:Request):
    user=require_control_operator(request);uid=user['id'];svc=_service(uid);st=svc.artifact_status().to_dict();pit=svc.macro.integrity_report();market=_market_preflight(uid);policy=svc.grade()
    # Before a candidate exists, artifact_status has no saved training provenance.
    # Report the live canonical market preflight instead of incorrectly displaying NO.
    if not st.get('trained'):
        st['real_market_data']=bool(market['ready'])
        blockers=list(st.get('blockers') or [])
        if market['ready']:
            blockers=[x for x in blockers if x!='no_real_adjusted_oryntra_market_data']
        elif 'no_real_adjusted_oryntra_market_data' not in blockers:
            blockers.append('no_real_adjusted_oryntra_market_data')
        st['blockers']=blockers
    promotion={}
    if svc.promotion_path.exists():
        try: promotion=json.loads(svc.promotion_path.read_text())
        except Exception: promotion={}
    return {'artifact_status':st,'policy':policy,'pit':pit,'market_preflight':market,'fred_key_configured':bool(svc.macro.fred_api_key),'consensus_estimates':svc.consensus.count(),'consensus_coverage':svc.surprises.coverage_report(metrics=BETA_V1_CONFIG['consensus']['required_candidate_metrics']),'allow_market_api':_ALLOW_API,'beta_root':str(_ROOT),'runtime_build':os.getenv('ORYNTRA_CONTROL_BUILD','unknown'),'training_code_revision':BETA_TRAINING_CODE_REVISION,'candidate_schema_version':BETA_CANDIDATE_SCHEMA_VERSION,'research_backtest_ready':bool(policy.get('research_backtest_eligible',False) and st.get('candidate_diagnostics_match')),'incremental_backtest':promotion}
@router.post('/beta/sync')
def sync(payload:BetaSyncRequest,request:Request):
    user=require_control_operator(request);svc=_service(user['id'])
    if not svc.macro.fred_api_key:raise HTTPException(400,'Save a FRED API key in Settings before syncing real FRED/ALFRED PIT data.')
    return svc.start_macro_sync(force_full=payload.force_full)
@router.get('/beta/sync/{job_id}')
def sync_status(job_id:str,request:Request):
    user=require_control_operator(request)
    try:return _service(user['id']).sync_job_status(job_id)
    except KeyError as e:raise HTTPException(404,'Unknown Beta sync job') from e
@router.post('/beta/consensus-csv')
def consensus_csv(payload:BetaConsensusCSVRequest,request:Request):
    user=require_control_operator(request)
    try:return _service(user['id']).import_consensus_csv(payload.csv_text)
    except Exception as e:raise HTTPException(400,str(e)) from e
def _compact_training_response(result: dict) -> dict:
    """Return the small, stable response contract used by the native Control UI.

    ``run_study`` persists the full multi-thousand-line diagnostics report to disk.
    Returning that entire report from the synchronous HTTP training call made the
    WebKit client vulnerable to a silent JSON-parse fallback that displayed ``{}``.
    The UI only needs a compact completion receipt; detailed diagnostics remain
    available from /beta/diagnostics and diagnostics.json.
    """
    if not isinstance(result, dict):
        raise ValueError('Beta training produced an invalid response object')
    summary=result.get('summary') if isinstance(result.get('summary'),dict) else {}
    holdout=result.get('final_holdout')
    if not isinstance(holdout,dict):
        holdout=summary.get('final_holdout') if isinstance(summary.get('final_holdout'),dict) else {}
    holdout_diag=holdout.get('diagnostics') if isinstance(holdout.get('diagnostics'),dict) else holdout
    artifact_status=result.get('artifact_status') if isinstance(result.get('artifact_status'),dict) else {}
    policy=result.get('policy_evaluation') if isinstance(result.get('policy_evaluation'),dict) else {}
    robustness=summary.get('robustness') if isinstance(summary.get('robustness'),dict) else {}
    artifact_id=result.get('artifact_id') or artifact_status.get('candidate_artifact_id') or (summary.get('artifact') or {}).get('artifact_id')
    winner=result.get('winner') or result.get('winner_name') or summary.get('winner')
    if not artifact_id or not winner:
        raise ValueError('Beta training completed without a candidate artifact identity or winner')
    return {
        'ok': True,
        'response_contract': 'beta_training_completion_v2',
        'winner': winner,
        'winner_family': result.get('winner_family') or summary.get('winner_family'),
        'artifact_id': artifact_id,
        'training_code_revision': result.get('training_code_revision') or artifact_status.get('training_code_revision') or BETA_TRAINING_CODE_REVISION,
        'candidate_schema_version': result.get('candidate_schema_version') or artifact_status.get('candidate_schema_version') or BETA_CANDIDATE_SCHEMA_VERSION,
        'holdout': holdout_diag,
        'calibration': holdout.get('calibration') if isinstance(holdout,dict) else None,
        'robustness': robustness,
        'artifact_status': artifact_status,
        'production_frozen_eligible': bool(policy.get('production_frozen_eligible',False)),
        'policy_blockers': list(policy.get('blockers') or []),
    }

@router.post('/beta/run')
def run(payload:BetaRunRequest,request:Request):
    user=require_control_operator(request);uid=user['id'];svc=_service(uid)
    pit=svc.macro.integrity_report()
    try:
        # 5.4.1-5.4.5 could persist a database containing the daily FRED series
        # while all revisable ALFRED macro rows were absent.  Repair missing
        # required series once here so upgrading users are not trapped behind a
        # stale "READY" database created by the old output_type=3 parser.
        if (not pit.get('pit_integrity') or not pit.get('real_fred_provenance')) and svc.macro.fred_api_key and pit.get('missing_required_series'):
            svc.repair_missing_required_history()
            pit=svc.macro.integrity_report()
        if not pit.get('pit_integrity') or not pit.get('real_fred_provenance'):
            missing=', '.join(pit.get('missing_required_series') or []) or 'unknown series'
            raise HTTPException(400,f'Real FRED/ALFRED PIT macro data are not complete. Missing required series: {missing}. Run Beta macro sync again.')
        # This path deliberately repairs legacy/mixed caches by fetching verified
        # adjusted histories before any Beta model fitting is allowed.
        m=_market(uid,refresh_if_needed=True,minimum=2520);c=_context(payload.universe,m,uid)
        readiness=svc.training_readiness(m,stock_context=c,min_train_sessions=payload.min_train_sessions,calibration_sessions=payload.calibration_sessions,final_holdout_sessions=payload.final_holdout_sessions)
        repair_blockers={'insufficient_labeled_session_history','insufficient_independent_macro_events'}
        if repair_blockers.intersection(set(readiness.get('blockers') or [])):
            # Failed/partial syncs from earlier 5.4.x builds can leave every series
            # present (integrity READY) while the target anchors lack enough historical
            # vintages. Repair only the independent label anchors from full history.
            repair=svc.repair_target_history()
            readiness=svc.training_readiness(m,stock_context=c,min_train_sessions=payload.min_train_sessions,calibration_sessions=payload.calibration_sessions,final_holdout_sessions=payload.final_holdout_sessions)
            readiness['target_history_repair']=repair
        if not readiness.get('ready_for_walkforward'):
            raise ValueError(svc.format_training_readiness_error(readiness))
        result=svc.run_study(m,stock_context=c,min_train_sessions=payload.min_train_sessions,calibration_sessions=payload.calibration_sessions,test_sessions=payload.test_sessions,final_holdout_sessions=payload.final_holdout_sessions)
        return _compact_training_response(result)
    except HTTPException:raise
    except ValueError as e:raise HTTPException(400,str(e)) from e
    except Exception as e:raise HTTPException(500,str(e)) from e
@router.post('/beta/incremental-backtest')
def incremental_backtest(payload:BetaIncrementalBacktestRequest,request:Request):
    user=require_control_operator(request);svc=_service(user['id']);st=svc.artifact_status().to_dict();policy=svc.grade()
    if not st.get('trained') or not st.get('candidate_diagnostics_match'):
        raise HTTPException(400,'Train a current matching Beta candidate before running the incremental portfolio backtest.')
    if not bool(policy.get('research_backtest_eligible',False)):
        blockers=[x for x in policy.get('blockers',[]) if x not in {'consensus_surprise_coverage','strategy_return_alignment_declared','incremental_A_plus_C_or_A_plus_B_plus_C'}]
        raise HTTPException(400,'Beta is not research-backtest ready: '+(', '.join(blockers) if blockers else 'candidate probability/robustness gates are not complete'))
    spec=payload.model_dump()
    try:return submit_job(user['id'],'beta_incremental_backtest',spec)
    except ValueError as e:raise HTTPException(400,str(e)) from e

@router.get('/beta/state')
def state(request:Request):
    user=require_control_operator(request);uid=user['id']
    try:
        m=_market(uid,refresh_if_needed=True,minimum=2520);c=_context(BROAD_US_LIQUID_UNIVERSE[:60],m,uid);s=_service(uid).latest_state(m,stock_context=c)
        return beta_v1_risk_payload(s,service=_service(uid))
    except Exception as e:raise HTTPException(503,str(e)) from e
