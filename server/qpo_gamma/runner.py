"""Thin isolated intervention adapter around the frozen native QPO engine."""
from pathlib import Path
from dataclasses import replace
import os,sys,json,time,traceback
import numpy as np
import pandas as pd
from .config import validate,fingerprint,effective_controls
from .identity import verify_frozen,register,digest,atomic_json,result_artifact_hashes,verify_completed
from .signals import build_panel,validate_prices
from .analysis import metrics,daily_attribution
from .macro import select_exposure

ROOT=Path(__file__).resolve().parents[1]

def configure_runtime(root):
    root=Path(root)
    for key in ['OMP_NUM_THREADS','OPENBLAS_NUM_THREADS','MKL_NUM_THREADS','NUMEXPR_NUM_THREADS']:os.environ[key]='1'
    from threadpoolctl import threadpool_limits
    threadpool_limits(limits=1)
    import torch
    torch.set_num_threads(1)
    os.environ.update(ORYNTRA_IBKR_KILL_SWITCH='true',QOPTIMA_IBM_HARDWARE_ENABLED='false',ORYNTRA_SEC_PIT_ALLOW_API='false',ORYNTRA_BETA_ALLOW_API='false',ORYNTRA_DATA_DIR=str(root/'runtime_scratch'))
    sys.path.insert(0,str(root))

def run(config,*,root=ROOT,pilot_decisions=0,research_inputs=None):
    root=Path(root).resolve();config=validate(config);configure_runtime(root);frozen=verify_frozen(root)
    if config['data']['require_pit_membership']:raise ValueError('No authenticated historical membership panel supplied; cannot certify PIT universe')
    if config['data']['require_sec']:raise ValueError('SEC feature integration is unavailable in this price-only Alpha adapter; preserve separate strict old-Gamma baseline')
    expanded=None
    if config['data']['universe']!='baseline183':
        if research_inputs is None:raise ValueError('Expanded replay requires verified universe-specific inputs')
        if config['training']['artifact_mode']=='gamma_v1':
            from gamma_v1.inputs import load_gamma_inputs as load_expanded_inputs
        else:
            from .expanded_inputs import load_expanded_inputs
        expanded=load_expanded_inputs(root,research_inputs,config)
    else:
        if research_inputs is not None or config['training']['artifact_mode'].startswith('regenerated'):raise ValueError('Regenerated inputs cannot be relabeled frozen baseline183')
        from .source_admission import require_frozen_replay_sources
        require_frozen_replay_sources(root, config)
    prices_path=root/config['data']['prices_file'];spy_path=root/config['data']['spy_file']
    prices=pd.read_csv(prices_path,index_col=0,parse_dates=True);validate_prices(prices)
    if len(prices.columns)!=config['data']['target_assets']:raise ValueError('Required stock count is not met exactly')
    spy=pd.read_csv(spy_path,index_col=0,parse_dates=True).iloc[:,0]
    start=pd.Timestamp(config['data']['start']);end=pd.Timestamp(config['data']['end']);first=prices.index.get_loc(start);last=prices.index.get_loc(end)
    lookback=config['portfolio']['lookback'];h=config['portfolio']['rebalance']
    if first<max(lookback,config['data']['minimum_history'])+1:raise ValueError('Insufficient requested warmup')
    # Archived Beta observations are tied to the original 120-decision grid.
    if start!=pd.Timestamp('2016-09-09') or end!=pd.Timestamp('2026-09-18'):raise ValueError('Archived Beta replay supports the frozen ten-year grid only; use --pilot for engineering scope')
    returns=prices.pct_change(fill_method=None).iloc[first-lookback:last+1]
    if pilot_decisions:
        if type(pilot_decisions) is not int or not 1<=pilot_decisions<=120:raise ValueError('Invalid pilot decisions')
        returns=returns.iloc[:lookback+pilot_decisions*h]
    hashes={'prices':digest(prices_path),'spy':digest(spy_path),'archived_beta':digest(root/'inputs/archived_beta.json'),'frozen_manifest':digest(root/'FROZEN_INPUTS.json')}
    if expanded:hashes.update(expanded['hashes'])
    if config['data']['sectors_file']:hashes['sectors']=digest(root/config['data']['sectors_file'])
    if config['macro']['records_file']:hashes['macro_records']=digest(root/config['macro']['records_file'])
    out,registration=register(root,config,hashes,purpose='engineering_pilot' if pilot_decisions else 'full_historical_exploration',pilot_decisions=pilot_decisions)
    if (out/'COMPLETED.json').exists():return out,verify_completed(out)
    atomic_json(out/'effective_config.json',config)
    atomic_json(out/'effective_controls.json',effective_controls(config))
    start_clock=time.monotonic()
    from backend.qoptima import backtest_overlay as engine
    from backend.qoptima.replay_checkpoint import _atomic_document
    from .archived_beta import BetaObservationAdapter
    from .audit_storage import install
    from .legacy_accounting import replay,annual_values
    if expanded:
        from .expanded_inputs import CausalBetaAdapter
        beta=CausalBetaAdapter(expanded['files']['beta_records'],hashes['beta_records'],expanded['admission']['eligible_symbols'])
        schedule=expanded['schedule']
    else:
        beta=BetaObservationAdapter(root/'inputs/archived_beta.json',hashes['archived_beta'])
        schedule_mode='cold' if config['training']['artifact_mode']=='frozen_cold' else 'warm'
        schedules=json.loads((root/'inputs/v1a_schedules.json').read_text())
        schedule=[dict(row,artifact_path=str(root/'inputs/v1a'/schedule_mode/Path(row['artifact_path']).name)) for row in schedules[schedule_mode]]
    beta.install()
    from gamma_v1.risk_adapter import install_state_policy
    state_policy=install_state_policy(config['state']['v1a_policy'])
    artifact_hashes={str(Path(row['artifact_path']).relative_to(root)):digest(row['artifact_path']) for row in schedule}
    first_cutoff=returns.index[lookback-2]
    # Preserve the archived baseline's first-decision 251-row prefix exactly.
    # Subsequent decisions have a full lookback. This is recorded, not repaired.
    first_mu=returns.iloc[:lookback-1].mean()*126.0
    panel=build_panel(prices,spy,config,native_overrides={first_cutoff:first_mu})
    panel.save(out/'forecasts')
    native_make=engine._make_problem;pending_calls=out/'adapter_calls_pending.json';calls=json.loads(pending_calls.read_text()) if pending_calls.exists() else [];macro_records=json.loads((root/config['macro']['records_file']).read_text()) if config['macro']['mode']!='off' else []
    def intervention(hist,symbols,current,**kwargs):
        problem,calibration=native_make(hist,symbols,current,**kwargs)
        cutoff=pd.Timestamp(kwargs['gamma_feature_cutoff']);issued=panel.at(cutoff,symbols)
        difference=float(np.max(abs(problem.expected_returns-issued['native_mu_annual'])))
        if difference>1e-11:raise AssertionError('As-issued native mean fails prefix identity: '+str(difference))
        if problem.covariance_provenance['scoring_horizon']!='linear_annual_equivalent_not_compound_return':raise AssertionError('Native score basis changed')
        p=config['portfolio'];e=config['errors'];changes={};audit={'cutoff':str(cutoff),'native_mean_max_error':difference,'history_rows':len(hist),'training_mode':config['training']['artifact_mode'],'forecast_panel_identity':panel.identity,'ranking_enabled':config['ranking']['enabled'],'delta_horizon':issued['delta_horizon'].tolist(),'symbols':symbols,'error_policy':e['policy'],'additional_macro':None}
        if config['ranking']['enabled']:changes['expected_returns']=issued['mu_annual']
        if e['policy']=='matched_as_issued':
            errors=panel.errors_at(cutoff,symbols)
            changes.update(forecast_error_samples=errors['samples'],v1a_forecast_error_information_cutoffs=errors['information_cutoffs'],v1a_forecast_error_realized_at=errors['realized_at'],v1a_forecast_error_covariance_window=e['covariance_window'],v1a_forecast_error_min_history=e['covariance_min_history'],v1a_forecast_error_covariance_floor=e['covariance_floor'],v1a_quantile_block_stride=1)
            audit['errors']={k:v for k,v in errors.items() if k!='samples'};audit['errors']['records']=len(errors['samples'])
        else:audit['errors']={'available':False,'reason':'frozen native baseline has no forecast-error ledger; not zero calibrated risk'}
        if config['macro']['mode']!='off':
            multiplier,meta=select_exposure(macro_records,cutoff,prices.index,config['macro']);changes['target_gross']=problem.target_gross*multiplier;audit['additional_macro']=meta
        if changes:
            provenance=dict(problem.covariance_provenance);provenance['qpo_gamma_research']=audit;changes['covariance_provenance']=provenance;problem=replace(problem,**changes)
        if e['policy']=='matched_as_issued':
            from backend.qoptima.v1a.residual_tail import standardized_conservative_portfolio_tail
            support=standardized_conservative_portfolio_tail(problem.forecast_error_samples,np.ones(len(symbols))/len(symbols),beta=p['full_qrs_beta'],confidence=p['quantile_confidence'],covariance_window=e['covariance_window'],min_history=e['covariance_min_history'],covariance_floor=e['covariance_floor'],information_cutoffs=problem.v1a_forecast_error_information_cutoffs,realized_at=problem.v1a_forecast_error_realized_at)
            if not support.get('available'):raise ValueError('Matched error estimator unavailable; cannot interpret missing support as zero risk')
            audit['error_support_probe']={k:support.get(k) for k in ['available','standardized_samples','tail_method','fallback_used','historical_scale_clock']}
        calls[:]=[a for a in calls if a['cutoff']!=audit['cutoff']];calls.append(audit);calls.sort(key=lambda a:a['cutoff']);atomic_json(pending_calls,calls)
        return problem,calibration
    engine._make_problem=intervention
    kw=json.loads((root/'inputs/native_configuration.json').read_text());p=config['portfolio'];x=config['execution']
    kw.update(lookback=lookback,rebalance=h,max_name=p['max_name'],replay_qscan_assets=p['qscan_assets'],replay_bits=p['bits'],full_qrs_exact_variable_cap=p['qscan_assets']*p['bits'],replay_min_positive_positions=p['min_positions'],risk_aversion=p['risk_aversion'],turnover_penalty=p['turnover_penalty'],alpha=p['resilience_alpha'],target_gross_override=p['target_gross'],correlation_blend=p['correlation_blend'],full_qrs_beta=p['full_qrs_beta'],full_qrs_quantile_confidence=p['quantile_confidence'],v1a_risk_budget_annual=p['risk_budget'],v1a_scale_tolerance=p['scale_tolerance'],risk_free_rate=x['metric_rf'],cash_lending_rate=x['cash_annual'],borrowing_rate=x['borrow_annual'],transaction_cost_bps=x['cost_bps'],seed=config['study']['seed'],returns=returns,forecast_records=[],gamma_beta_records=[],gamma_session_index=prices.index,v1a_artifact_schedule=schedule,replay_checkpoint_directory=str(out/'checkpoints'),replay_checkpoint_resume=True,replay_checkpoint_context={'trial_id':registration['trial_id'],'config_hash':fingerprint(config),'data_hashes':hashes,'artifact_hashes':artifact_hashes,'code_sha256':registration['code_sha256']})
    store=install(out)
    beta_pending=out/'beta_calls_pending.json';previous_beta=json.loads(beta_pending.read_text()) if beta_pending.exists() else []
    def merged_beta():
        # One selected scheduled decision per cutoff; resumed attempts replace
        # an uncommitted audit at that cutoff rather than duplicating counters.
        records={r['cutoff']:r for r in previous_beta+beta.calls}
        return [records[k] for k in sorted(records)]
    def progress(row):
        atomic_json(beta_pending,merged_beta())
        value={**row,'elapsed_seconds':time.monotonic()-start_clock}
        with (out/'progress.jsonl').open('a') as f:f.write(json.dumps(value,default=str)+'\n')
        if row.get('stage')=='walk_forward':print(json.dumps(value),flush=True)
    try:
        result=engine._walk_forward_method(**kw,progress_callback=progress);_atomic_document(out/'result.json.gz',result);full=store.hydrate(result)
        daily=pd.DataFrame(result['daily_returns']['qoptima']);daily=pd.Series(daily.net_return.to_numpy(),index=pd.to_datetime(daily.date))
        scored=returns.iloc[lookback:]
        if not daily.index.equals(scored.index):raise AssertionError('Native replay omitted evaluation sessions')
        targets=pd.DataFrame(0.,index=daily.index[::h],columns=prices.columns)
        decisions=[]
        for row in full['rebalances']:
            date=pd.Timestamp(row['decision_date'])
            for holding in row['qoptima_target_weights']:targets.loc[date,holding['symbol']]=holding['weight']
            risk=row.get('gamma_risk205') or {};rejected=[e for e in row.get('rejection_policy_events',[]) if e.get('portfolio_path')=='qoptima']
            if not rejected and not (risk.get('final_qrm') or {}).get('passes_qrm_structural_gates'):raise AssertionError('Accepted final book lacks QRM admission')
            decisions.append({'date':str(date),'feature_cutoff':row['feature_cutoff'],'target_gross':float(targets.loc[date].sum()),'target_max_name':float(targets.loc[date].max()),'rejected':bool(rejected),'rejection_events':rejected,'final_qrm':risk.get('final_qrm'),'state_before':risk.get('state_before'),'state_after':risk.get('state_after'),'search':{k:row['replay'].get(k) for k in ['status','policy','winner_id','counts','selected_indices','pre_overlay_qrs']}})
        book=replay(targets,scored,cost_bps=x['cost_bps'],lending_annual=x['cash_annual'],borrowing_annual=x['borrow_annual'],initial=x['initial_usd'],fee_convention=x['fee_convention'])
        error=float(np.max(abs(book.ledger.net_return-daily)))
        if error>1e-12:raise AssertionError('Independent book/native return mismatch')
        targets.to_csv(out/'targets.csv.gz',compression='gzip');book.ledger.to_csv(out/'ledger.csv.gz',compression='gzip');book.holdings.to_csv(out/'daily_holdings.csv.gz',compression='gzip');book.contributions.rename('dollar_contribution').to_csv(out/'contributions.csv');annual_values(book.ledger).to_csv(out/'annual_values.csv',index=False)
        daily_attribution(targets,scored,book,x['initial_usd']).to_csv(out/'daily_dollar_attribution.csv.gz',compression='gzip')
        atomic_json(out/'decisions.json',decisions);atomic_json(out/'adapter_calls.json',calls);atomic_json(out/'beta_calls.json',merged_beta())
        from .scorecard import make_scorecard
        forecast_scorecard=make_scorecard(panel,[d['feature_cutoff'] for d in decisions],out/'scorecard')
        summary={**metrics(daily,x['metric_rf']),'ending_usd':float(book.ledger.equity_usd.iloc[-1]),'trial_id':registration['trial_id'],'decisions':len(decisions),'assets':len(prices.columns),'rejections':sum(d['rejected'] for d in decisions),'forced_cash_sessions':int(result['rejection_policy_audit']['forced_cash_sessions']),'independent_ledger_max_error':error,'elapsed_seconds':time.monotonic()-start_clock,'frozen_inputs_verified':frozen,'V1A_artifact_hashes':artifact_hashes,'first_native_history_rows':lookback-1,'scope':'conditional archived Beta; actual V1A inference; original static survivor universe','unpromoted':True,'error_policy':config['errors']['policy'],'training_artifacts_regenerated_this_run':False,'not_original_gamma_ledger':True}
        if expanded:
            summary['scope']='new-source static-survivor research; regenerated V1A and causal Beta; native state-overlay placement preserved'
            summary['input_regeneration_manifest']=expanded['manifest']
        summary['forecast_scorecard']=forecast_scorecard
        from .parity import native_noop,verify_parity
        if expanded is None and native_noop(config):
            expected=pd.read_csv(root/'inputs/baseline_daily_returns.csv',index_col=0,parse_dates=True).iloc[:,0].loc[daily.index]
            oldtargets=pd.read_csv(root/'inputs/baseline_targets.csv.gz',index_col=0,parse_dates=True).loc[targets.index]
            parity=verify_parity(daily,targets,book,expected,oldtargets,scored,x,calls)
            atomic_json(out/'BASELINE_PARITY.json',parity);summary['baseline_parity']=parity
        summary['comparison_artifact_sha256']=result_artifact_hashes(out)
        atomic_json(out/'COMPLETED.json',summary);print(json.dumps(summary,indent=2),flush=True);return out,summary
    except Exception as exc:
        atomic_json(out/'FAILED.json',{'error':str(exc),'traceback':traceback.format_exc(),'elapsed_seconds':time.monotonic()-start_clock,'completed_calls':len(calls)});raise
    finally:
        engine._make_problem=native_make;store.uninstall();beta.uninstall();state_policy()
