"""Hash-bound Gamma V1 input contract, distinct from the frozen323 manifest."""
from pathlib import Path
import json
import pandas as pd
from qpo_gamma.identity import digest
from qpo_gamma.expanded_inputs import checked_file,verify_v1a_training

def load_gamma_inputs(root,manifest_path,config):
    root=Path(root).resolve();doc=json.loads(Path(manifest_path).read_text())
    if doc.get('schema')!='gamma-v1-inputs-v1':raise ValueError('Gamma V1 manifest required')
    keys=['admission','prices','spy','beta_records','beta_source_plan','beta_diagnostics','beta_integrity']
    files={key:checked_file(root,doc[key]) for key in keys}
    admission=json.loads(files['admission'].read_text());symbols=admission['eligible_symbols']
    if admission.get('status')!='accepted_static_survivor_research':raise ValueError('Unaccepted Gamma V1 admission')
    if len(symbols)!=config['data']['target_assets'] or not 300<=len(symbols)<=500 or 'SPY' in symbols or len(set(symbols))!=len(symbols):raise ValueError('Gamma V1 stock count/identity mismatch')
    if admission['prices_sha256']!=doc['prices']['sha256'] or admission['spy_sha256']!=doc['spy']['sha256']:raise ValueError('Admission source differs')
    for key in ['issuer_deduplication_verified','liquidity_basis_verified']:
        if not admission[key]:raise ValueError('Admission gate failed '+key)
    for key in ['security_evidence','corporate_action_audit','liquidity_audit']:checked_file(root,admission[key])
    prices=pd.read_csv(files['prices'],index_col=0,parse_dates=True,float_precision='round_trip')
    if prices.columns.tolist()!=symbols:raise ValueError('Ordered symbols mismatch')
    for key,setting in [('prices','prices_file'),('spy','spy_file')]:
        if (root/config['data'][setting]).resolve()!=files[key]:raise ValueError('Config prices mismatch')
    bp=json.loads(files['beta_source_plan'].read_text());bd=json.loads(files['beta_diagnostics'].read_text());bi=json.loads(files['beta_integrity'].read_text())
    if bp['stock_symbols']!=symbols or bp['admission_sha256']!=doc['admission']['sha256'] or bp['new_stock_panel_sha256']!=doc['prices']['sha256']:raise ValueError('Beta binding mismatch')
    if bd['sources']['stock_symbols']!=symbols:raise ValueError('Beta source universe mismatch')
    for key,source_key in [('market_database_sha256','market_database'),('macro_database_sha256','macro_database')]:
        path=Path(bd['sources'][source_key]).resolve()
        if not path.is_relative_to(root) or digest(path)!=bp[key]:raise ValueError('Beta source database mismatch')
    if bi['source_plan_sha256']!=doc['beta_source_plan']['sha256'] or bi['diagnostics_sha256']!=doc['beta_diagnostics']['sha256'] or bi['compressed_complete_records_sha256']!=doc['beta_records']['sha256'] or bi['record_content_sha256_without_generation_time']!=bd['generation']['record_content_sha256']:raise ValueError('Beta receipt mismatch')
    variant=config['training']['model_variant']
    if variant not in doc['models']:raise ValueError('Unregistered model variant')
    hashes={key:doc[key]['sha256'] for key in keys};hashes['expanded_manifest']=digest(manifest_path)
    selected=None
    for name,entry in doc['models'].items():
        if entry.get('kind') not in ('native','gamma'):raise ValueError('Unknown model entry kind')
        schedule_path=checked_file(root,entry['schedule']);training_path=checked_file(root,entry['training'])
        hashes['schedule:'+name]=digest(schedule_path);hashes['training:'+name]=digest(training_path)
        schedule=json.loads(schedule_path.read_text());training=json.loads(training_path.read_text())
        if not schedule:raise ValueError('Empty artifact schedule')
        if entry['kind']=='native':
            spec=training['spec']
            if spec['prices_sha256']!=doc['prices']['sha256'] or spec['admission_sha256']!=doc['admission']['sha256'] or spec['symbols']!=symbols:raise ValueError('Native training source mismatch')
        else:
            if training['prices_sha256']!=doc['prices']['sha256'] or name not in training['variants']:raise ValueError('Gamma training source mismatch')
        context={};cutoffs=[]
        for row in schedule:
            path=checked_file(root,{'path':row['artifact_path'],'sha256':row['artifact_sha256']})
            hashes['v1a:'+str(path.relative_to(root))]=row['artifact_sha256']
            if name==variant:
                if entry['kind']=='native':verify_v1a_training(path,row,'regenerated_cold',training,prices,context)
                else:verify_gamma_training(path,row,name,training,prices,context)
            row['artifact_path']=str(path);cutoffs.append(pd.Timestamp(row['artifact_information_cutoff']))
        if cutoffs!=sorted(set(cutoffs)) or cutoffs[0]>=pd.Timestamp(config['data']['start']):raise ValueError('Invalid causal artifact cutoffs')
        if name==variant:selected=schedule
    return {'files':files,'schedule':selected,'hashes':hashes,'admission':admission,'manifest':doc}

def verify_gamma_training(path,row,name,plan,prices,prepared):
    from backend.qoptima.v1a.inference import load_v1a_artifact
    from backend.qoptima.v1a.features import build_default_market_features
    from gamma_v1.contracts import market_target,legacy_target,admitted_labels,admitted_maturities
    from gamma_v1.forecast_study import frame_hash
    _,meta,calibration,_=load_v1a_artifact(path)
    import hashlib
    unhashed={key:value for key,value in meta.items() if key!='spec_hash'}
    actual_header_hash=hashlib.sha256(json.dumps(unhashed,sort_keys=True,default=str).encode()).hexdigest()
    if actual_header_hash!=meta.get('spec_hash'):raise ValueError('Gamma metadata self-hash mismatch')
    if meta.get('gamma_v1_variant')!=name or meta['spec_hash']!=row['spec_hash'] or meta['artifact_information_cutoff']!=row['artifact_information_cutoff']:raise ValueError('Gamma artifact identity mismatch')
    if meta['gamma_v1_identity']['source']!=plan['source']:raise ValueError('Gamma training source identity mismatch')
    if meta['gamma_v1_identity'].get('runtime_sha256')!=plan.get('runtime_sha256'):raise ValueError('Gamma training runtime identity mismatch')
    declared=plan['variants'][name]
    import math
    if not math.isfinite(calibration) or calibration<=0:raise ValueError('Invalid calibration scalar')
    if not declared['calibrate'] and calibration!=1.0:raise ValueError('No-calibration artifact carries nonidentity scalar')
    if meta['es_gap_calibration']['selected_c']!=calibration:raise ValueError('Calibration metadata differs from actual scalar')
    if declared['calibrate'] and not meta['config']['es_calibration_min']<=calibration<=meta['config']['es_calibration_max']:raise ValueError('Calibration scalar outside declared bounds')
    if meta['gamma_v1_head']!=declared['head'] or meta['gamma_v1_target']!=declared['target'] or meta['gamma_v1_identity']['variant']!=declared:raise ValueError('Artifact semantic variant mismatch')
    for filename,expected in plan['source'].items():
        if filename.endswith(('contracts.py','model.py','config.py','features.py','prototypes.py','publishing.py')) and digest(Path(__file__).resolve().parents[1]/filename)!=expected:raise ValueError('Active forecast dependency differs from artifact training source')
    if 'returns' not in prepared:
        prepared['returns']=prices.pct_change(fill_method=None).iloc[1:]
        prepared['features']=build_default_market_features(prepared['returns'])
    target,maturity=(legacy_target if plan['variants'][name]['target']=='legacy' else market_target)(prepared['returns'])
    cutoff=pd.Timestamp(row['artifact_information_cutoff']);f=prepared['features'].loc[:cutoff];y=admitted_labels(target,maturity,cutoff)
    from backend.qoptima.v1a.config import V1AConfig
    cutpos=prepared['returns'].index.get_loc(cutoff);firstpos=prepared['returns'].index.get_loc(pd.Timestamp(plan['first_training_cutoff']))
    fold,remainder=divmod(cutpos-firstpos,plan['refit_sessions'])
    if remainder or fold<0:raise ValueError('Artifact cutoff outside declared refit grid')
    expected=V1AConfig(seed=plan['seed']+fold,max_epochs=plan['max_epochs'],patience=plan['patience']).as_dict()
    if meta['config']!=expected or meta.get('signed_control'):raise ValueError('Artifact configuration/seed mismatch')
    identity=meta['gamma_v1_identity']
    if identity['features']!=frame_hash(f) or identity['labels']!=frame_hash(y) or identity['maturities']!=frame_hash(admitted_maturities(y,maturity)):raise ValueError('Gamma artifact features/target identity mismatch')
