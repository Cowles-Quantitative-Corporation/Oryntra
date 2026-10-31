"""Fail-closed contracts for separately regenerated expanded research inputs."""
from pathlib import Path
import json
import pandas as pd
from .identity import digest


def checked_file(root, record):
    root=Path(root).resolve()
    if not isinstance(record,dict) or set(record)!={'path','sha256'}:raise ValueError('Explicit path/hash input reference required')
    path=(root/record['path']).resolve()
    if not path.is_relative_to(root) or not path.is_file():raise ValueError('Research input must be an existing bundle-relative file')
    if digest(path)!=record['sha256']:raise ValueError('Regenerated input hash mismatch: '+record['path'])
    return path



def verify_v1a_training(path,row,name,training,prices,prepared=None):
    """Recompute the admitted fold's feature/label identity and inspect weights metadata."""
    import numpy as np
    from backend.qoptima.v1a.inference import load_v1a_artifact
    from backend.qoptima.v1a.cache_identity import fold_training_identity,cache_metadata_matches
    from backend.qoptima.v1a.features import build_default_market_features
    from backend.qoptima.preset_backtest import _forward_market_target
    _,metadata,_,_=load_v1a_artifact(path)
    identity=metadata.get('training_cache_identity',{});payload=identity.get('payload',{})
    mode=name.removeprefix('regenerated_')
    if payload.get('source_hashes',{}).get('initializer_mode')!=mode:raise ValueError('V1A artifact initializer differs from scheduled variant')
    if row.get('spec_hash')!=metadata.get('spec_hash') or pd.Timestamp(row['artifact_information_cutoff'])!=pd.Timestamp(metadata['artifact_information_cutoff']):raise ValueError('V1A schedule metadata differs from actual artifact')
    members=training.get('native_diagnostics',{}).get('artifacts',[])
    if str(Path(path).resolve()) not in {str(Path(r['path']).resolve()) for r in members}:raise ValueError('V1A artifact absent from source-bound training diagnostics')
    if not cache_metadata_matches(metadata,identity):raise ValueError('V1A artifact cache/spec integrity failed')
    cutoff=pd.Timestamp(payload['information_cutoff']);h=metadata['config']['horizon_sessions']
    prepared={} if prepared is None else prepared
    if 'returns' not in prepared:
        prepared['returns']=prices.pct_change(fill_method=None).iloc[1:]
        prepared['features']=build_default_market_features(prepared['returns'])
        prepared['targets']={}
    returns=prepared['returns']
    features=prepared['features'].loc[:cutoff]
    if h not in prepared['targets']:prepared['targets'][h]=_forward_market_target(returns,h)
    targets=prepared['targets'][h].loc[:cutoff]
    maturities=pd.Series([returns.index[i+h-1] if i+h-1<len(returns) else pd.NaT for i in range(len(returns))],index=returns.index).reindex(targets.index)
    targets=targets.where(maturities.notna() & (maturities<=cutoff))
    reproduced=fold_training_identity(features,targets,config=metadata['config'],information_cutoff=cutoff,target_maturities=maturities,source_hashes=payload['source_hashes'],execution=payload['execution'])
    if reproduced['identity_sha256']!=identity['identity_sha256']:raise ValueError('V1A artifact was trained on different admitted features or labels')


def load_expanded_inputs(root, manifest_path, config):
    root=Path(root).resolve();path=Path(manifest_path).resolve()
    if not path.is_relative_to(root):raise ValueError('Expanded manifest must remain in research bundle')
    doc=json.loads(path.read_text())
    if doc.get('schema')!='qpo-expanded-inputs-v1':raise ValueError('Unsupported expanded input schema')
    if config['training']['artifact_mode'] not in ['regenerated_cold','regenerated_warm']:raise ValueError('Expanded study requires explicitly regenerated training mode')
    files={key:checked_file(root,doc[key]) for key in ['admission','prices','spy','beta_records','beta_source_plan','beta_diagnostics','beta_integrity']}
    for key,setting in [('prices','prices_file'),('spy','spy_file')]:
        if (root/config['data'][setting]).resolve()!=files[key]:raise ValueError('Config/source manifest path mismatch')
    admission=json.loads(files['admission'].read_text())
    if admission.get('status')!='accepted_static_survivor_research':raise ValueError('Expanded admission is not accepted')
    symbols=admission.get('eligible_symbols')
    if not isinstance(symbols,list) or len(set(symbols))!=len(symbols) or not 300<=len(symbols)<=500 or 'SPY' in symbols:raise ValueError('300–500 unique nonbenchmark equities required')
    if len(symbols)!=config['data']['target_assets']:raise ValueError('Expanded declared asset count mismatch')
    if admission.get('prices_sha256')!=doc['prices']['sha256']:raise ValueError('Admission is bound to different prices')
    for key in ['issuer_deduplication_verified','liquidity_basis_verified']:
        if admission.get(key) is not True:raise ValueError('Unmet expanded admission gate: '+key)
    for key in ['security_evidence','corporate_action_audit','liquidity_audit']:
        checked_file(root,admission[key])
    beta_plan=json.loads(files['beta_source_plan'].read_text());beta_diagnostics=json.loads(files['beta_diagnostics'].read_text())
    if beta_plan.get('stock_symbols')!=symbols or beta_plan.get('admission_sha256')!=doc['admission']['sha256'] or beta_plan.get('new_stock_panel_sha256')!=doc['prices']['sha256']:raise ValueError('Causal Beta producer inputs differ from admitted source/universe')
    if beta_diagnostics.get('sources',{}).get('stock_symbols')!=symbols:raise ValueError('Causal Beta diagnostics universe differs')
    beta_integrity=json.loads(files['beta_integrity'].read_text())
    if beta_integrity.get('source_plan_sha256')!=doc['beta_source_plan']['sha256'] or beta_integrity.get('diagnostics_sha256')!=doc['beta_diagnostics']['sha256']:raise ValueError('Beta completion receipt does not bind source plan/diagnostics')
    if beta_integrity.get('compressed_complete_records_sha256')!=doc['beta_records']['sha256'] or beta_integrity.get('record_content_sha256_without_generation_time')!=beta_diagnostics.get('generation',{}).get('record_content_sha256') or beta_integrity.get('records')!=beta_diagnostics.get('records_emitted'):raise ValueError('Beta output is not bound to native generation integrity')
    for key,source_key in [('market_database_sha256','market_database'),('macro_database_sha256','macro_database')]:
        source_path=Path(beta_diagnostics['sources'][source_key]).resolve()
        if not source_path.is_relative_to(root) or digest(source_path)!=beta_plan.get(key):raise ValueError('Beta source database differs from bound input plan')
    prices=pd.read_csv(files['prices'],index_col=0,parse_dates=True,float_precision='round_trip')
    columns=prices.columns.tolist()
    if columns!=symbols:raise ValueError('Admitted ordered symbols differ from price panel')
    mode=config['training']['artifact_mode']
    variants=doc.get('v1a_schedules',{})
    if mode not in variants:raise ValueError('Requested regenerated initializer schedule missing')
    variant_files={name:checked_file(root,ref) for name,ref in variants.items()}
    provenance_files={name:checked_file(root,ref) for name,ref in doc.get('v1a_training_provenance',{}).items()}
    if set(provenance_files)!=set(variant_files):raise ValueError('Every initializer requires source-bound training provenance')
    for name,provenance_path in provenance_files.items():
        spec=json.loads(provenance_path.read_text()).get('spec',{})
        expected_initializer='original_cold_random_softplus' if name=='regenerated_cold' else 'training_empirical_tail_zero_weights_v1'
        if spec.get('prices_sha256')!=doc['prices']['sha256'] or spec.get('admission_sha256')!=doc['admission']['sha256'] or spec.get('symbols')!=symbols or spec.get('initializer')!=expected_initializer:raise ValueError('Regenerated training provenance does not bind admitted source/initializer')
    schedule=json.loads(variant_files[mode].read_text())
    if not isinstance(schedule,list) or not schedule:raise ValueError('Regenerated V1A schedule required')
    cutoffs=[];artifacts={}
    for row in schedule:
        artifact=checked_file(root,{'path':row['artifact_path'],'sha256':row['artifact_sha256']})
        cutoffs.append(pd.Timestamp(row['artifact_information_cutoff']))
        artifacts[str(artifact.relative_to(root))]=row['artifact_sha256']
        row['artifact_path']=str(artifact)
    if cutoffs!=sorted(set(cutoffs)):raise ValueError('V1A schedule cutoffs must be unique and ascending')
    if cutoffs[0]>=pd.Timestamp(config['data']['start']):raise ValueError('First V1A artifact must precede evaluation')
    hashes={key:doc[key]['sha256'] for key in files};hashes['expanded_manifest']=digest(path)
    # Hash every registered initializer input, not only the selected arm, so a
    # matched cold/warm comparison shares the same complete source bundle.
    training_context={}
    for name,variant_path in variant_files.items():
        hashes['v1a_schedule:'+name]=digest(variant_path)
        hashes['v1a_training:'+name]=digest(provenance_files[name])
        for row in json.loads(variant_path.read_text()):
            artifact=checked_file(root,{'path':row['artifact_path'],'sha256':row['artifact_sha256']})
            verify_v1a_training(artifact,row,name,json.loads(provenance_files[name].read_text()),prices,training_context)
            hashes['v1a:'+str(artifact.relative_to(root))]=row['artifact_sha256']
    return {'files':files,'schedule':schedule,'hashes':hashes,'admission':admission,'manifest':doc}


class CausalBetaAdapter:
    """Feed causal generated states into unchanged native Beta modifier semantics.

    The surrounding native engine still owns post-selection versus in-search
    policy placement and the selected-decision persistence counter.
    """
    def __init__(self,path,expected_sha256,expected_symbols=None):
        if digest(path)!=expected_sha256:raise ValueError('Causal Beta file changed')
        from backend.qoptima.gamma_forecast import _validated_beta_records,REGIME_NAMES
        from .streaming_records import iter_records
        from hashlib import sha256
        self.records=[];self.names=REGIME_NAMES;self.calls=[]
        for number,record in enumerate(iter_records(path)):
            accepted,rejected=_validated_beta_records([record])
            if rejected or not accepted:raise ValueError('Causal Beta source/clock validation failed')
            if expected_symbols is not None:
                used=[r['ticker'] for r in record.get('market_provenance',[]) if r.get('used_in_stock_context')]
                if sorted(used)!=sorted(expected_symbols):raise ValueError('Causal Beta record has different stock-context universe')
            parsed=accepted[0]
            parsed['record']={k:v for k,v in record.items() if k not in ['macro_provenance','market_provenance']}
            parsed['full_record_index']=number
            parsed['full_record_sha256']=sha256(json.dumps(record,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()
            self.records.append(parsed)
        if not self.records:raise ValueError('No causal Beta records')

    def beta(self,beta_state,*,control,previous_persistence_rebalances=0,current_session=None,v203_predictive_triggered=False):
        if beta_state is not None:raise ValueError('Do not mix externally selected Beta states with adapter')
        cutoff=pd.to_datetime(current_session,utc=True)
        eligible=[r for r in self.records if r['times']['available_at']<=cutoff and r['times']['as_of']<=cutoff]
        if not eligible:raise ValueError('Required causal Beta state unavailable at decision cutoff')
        parsed=max(eligible,key=lambda r:(r['times']['as_of'],r['times']['available_at']));record=parsed['record']
        state={**record,'as_of_session':record['as_of'],**{'p_'+name:float(v) for name,v in zip(self.names,parsed['p'])}}
        result=self._original(state,control=control,previous_persistence_rebalances=previous_persistence_rebalances,current_session=current_session,v203_predictive_triggered=v203_predictive_triggered)
        if not result.get('available'):raise ValueError('Required causal Beta unavailable: '+str(result.get('reason')))
        # Preserve explicit native gate-blocked states (e.g. uncalibrated annual
        # fit) and their unchanged-gross policy; do not invent an abort rule.
        self.calls.append({'cutoff':str(current_session),'source_as_of':record['as_of'],'available_at':record['available_at'],'artifact_id':record['artifact_id'],'full_record_index':parsed['full_record_index'],'full_record_sha256':parsed['full_record_sha256'],'previous_counter':int(previous_persistence_rebalances),'next_counter':result['persistence_rebalances'],'triggered':result['triggered'],'reason':result['reason'],'gross_modifier':result['gross_modifier'],'blockers':result.get('blockers',[]),'calibrated':record.get('calibrated'),'neutral_source':result.get('neutral_stress_source'),'state_change_source':'native optional field; absent remains native default, never invented'})
        return result

    def install(self):
        import backend.risk.v204 as module
        self._module=module;self._original=module.beta_regime_modifier_v204;module.beta_regime_modifier_v204=self.beta

    def uninstall(self):self._module.beta_regime_modifier_v204=self._original
