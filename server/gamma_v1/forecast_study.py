"""Predeclared walk-forward tail forecast study. No portfolio-return optimization."""
from pathlib import Path
import os,sys,json,time,argparse,hashlib
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT)]
for k in ['OMP_NUM_THREADS','OPENBLAS_NUM_THREADS','MKL_NUM_THREADS','NUMEXPR_NUM_THREADS']:os.environ[k]='1'
import numpy as np
import pandas as pd
import torch
from threadpoolctl import threadpool_limits
from backend.qoptima.v1a.config import V1AConfig
from backend.qoptima.v1a.features import build_default_market_features
from backend.qoptima.v1a.model import V1ANet
from gamma_v1.model import SupportedV1ANet,initialize_heads
from gamma_v1.publishing import supported_tail_publication
from gamma_v1.training import train_v1a,save_v1a_artifact
from gamma_v1.contracts import market_target,legacy_target,admitted_labels,admitted_maturities,tail_metrics
from qpo_gamma.identity import atomic_json,digest,runtime_identity
from qpo_gamma.config import fingerprint

def frame_hash(x):return hashlib.sha256(pd.util.hash_pandas_object(x,index=True).values.tobytes()).hexdigest()

# Frozen before forecast or portfolio outcomes are computed. Every arm reported.
VARIANTS={
 'legacy_cold_calibrated':{'target':'legacy','head':'legacy','warm':False,'calibrate':True},
 'legacy_warm_calibrated':{'target':'legacy','head':'legacy','warm':True,'calibrate':True},
 'legacy_warm_nocal':{'target':'legacy','head':'legacy','warm':True,'calibrate':False},
 'deployment_warm_nocal':{'target':'deployment','head':'legacy','warm':True,'calibrate':False},
 'legacy_supported_warm_nocal':{'target':'legacy','head':'supported','warm':True,'calibrate':False},
 'deployment_supported_warm_nocal':{'target':'deployment','head':'supported','warm':True,'calibrate':False},
}

def run(prices_path,output,only=None):
    torch.set_num_threads(1);threadpool_limits(limits=1)
    out=Path(output).resolve();out.mkdir(parents=True,exist_ok=True)
    dependencies=[ROOT/'gamma_v1'/f for f in ['__init__.py','contracts.py','model.py','training.py','publishing.py','forecast_study.py']] + [ROOT/'backend/qoptima/v1a'/f for f in ['config.py','features.py','model.py','prototypes.py']]
    source={str(p.relative_to(ROOT)):digest(p) for p in dependencies}
    runtime=runtime_identity()
    plan={'schema':'gamma-v1-forecast-study-v1','created_utc':pd.Timestamp.now(tz='UTC').isoformat(),'prices_sha256':digest(prices_path),'variants':VARIANTS,'source':source,'runtime':runtime,'runtime_sha256':fingerprint(runtime),'seed':560205,'first_training_cutoff':'2016-09-02','refit_sessions':252,'max_epochs':300,'patience':35,'primary':'paired deployment-target FZ0 and quantile loss at fixed120 decision origins; diagnostics daily','contrasts':[['legacy_warm_nocal','deployment_warm_nocal'],['legacy_warm_nocal','legacy_supported_warm_nocal'],['deployment_warm_nocal','deployment_supported_warm_nocal'],['legacy_supported_warm_nocal','deployment_supported_warm_nocal'],['legacy_cold_calibrated','legacy_warm_calibrated'],['legacy_warm_calibrated','legacy_warm_nocal']],'promotion':'none; repeatedly inspected2016–2026 is exploratory, not untouched holdout','selection':'all declared arms retained; no training configuration selected using evaluation wealth','support_assumption':'FZ0 negative conditional ES; report nearzero boundary predictions and unsupported outcomes','scores':'lower FZ0/pinball is better; coverage and ES moment descriptive;120 origins gives scarce5%tails','failure_policy':'record failed arm; never silently replace it'}
    planpath=out/'REGISTERED_PLAN.json'
    if planpath.exists():
        old=json.loads(planpath.read_text())
        for key in ['prices_sha256','variants','source','runtime_sha256','seed']:
            if old[key]!=plan[key]:raise ValueError('Immutable forecast study plan changed: '+key)
    else:
        atomic_json(planpath,plan)
        import zipfile
        with zipfile.ZipFile(out/'source_snapshot.zip','x',compression=zipfile.ZIP_DEFLATED) as z:
            for filename in source:z.write(ROOT/filename,filename)
    prices=pd.read_csv(prices_path,index_col=0,parse_dates=True,float_precision='round_trip')
    if not np.isfinite(prices.to_numpy()).all() or (prices<=0).any().any():raise ValueError('Dense positive source required')
    r=prices.pct_change(fill_method=None).iloc[1:]
    features=build_default_market_features(r)
    targets={'legacy':legacy_target(r),'deployment':market_target(r)}
    idx=r.index;first=idx.get_loc(pd.Timestamp('2016-09-02'))
    if first+1<1512:raise ValueError('Initial1512-return contract unmet')
    cuts=list(range(first,len(idx),252));origins=idx[(idx>pd.Timestamp('2016-09-02')) & (idx<=pd.Timestamp('2026-08-18'))]
    # Exact original replay feature-cutoff phase. c observes close, scored c+2.
    first_scored=prices.index.get_loc(pd.Timestamp('2016-09-09'))
    decision_origins=set(prices.index[first_scored-2:prices.index.get_loc(pd.Timestamp('2026-09-18'))+1:21])
    summaries={}
    for name,spec in VARIANTS.items():
        if only and name not in only:continue
        arm=out/name;arm.mkdir(exist_ok=True)
        if (arm/'COMPLETED.json').exists():summaries[name]=json.loads((arm/'COMPLETED.json').read_text());continue
        start=time.monotonic();forecast_rows=[];schedule=[]
        try:
            for j,cut in enumerate(cuts):
                cutoff=idx[cut];end=idx[min(cut+252,len(idx)-1)]
                target,maturity=targets[spec['target']]
                f=features.loc[:cutoff];y=admitted_labels(target,maturity,cutoff)
                joined=f.join(y.rename('__target__')).dropna()
                valn=max(30,int(round(len(joined)*.2)));split=len(joined)-valn
                trainmask=maturity.reindex(joined.index).iloc[:split]<joined.index[split]
                labels=joined.__target__.iloc[:np.flatnonzero(trainmask)[-1]+1].to_numpy()
                initial={};base=SupportedV1ANet if spec['head']=='supported' else V1ANet
                class Factory(base):
                    def __init__(self,*a,**kw):
                        super().__init__(*a,**kw)
                        if spec['warm']:initial.update(initialize_heads(self,labels,supported=spec['head']=='supported'))
                cfg=V1AConfig(seed=560205+j,max_epochs=300,patience=35)
                identity={'features':frame_hash(f),'labels':frame_hash(y),'maturities':frame_hash(admitted_maturities(y,maturity)),'cutoff':str(cutoff),'variant':spec,'seed':cfg.seed,'source':source,'runtime_sha256':fingerprint(runtime)}
                path=arm/f'fold_{j:02d}_{cutoff.date()}.pt'
                result=train_v1a(f,y,config=cfg,target_maturities=maturity,information_cutoff=cutoff,model_factory=Factory,calibrate_gap=spec['calibrate'])
                result['metadata'].update(gamma_v1_variant=name,gamma_v1_head=spec['head'],gamma_v1_target=spec['target'],gamma_v1_identity=identity,gamma_v1_initialization=initial)
                # Rehash extended metadata; the original native trainer hash is not reused.
                result['metadata'].pop('spec_hash',None);result['metadata']['spec_hash']=hashlib.sha256(json.dumps(result['metadata'],sort_keys=True,default=str).encode()).hexdigest()
                save_v1a_artifact(result,path);atomic_json(path.with_suffix('.json'),result['metadata'])
                schedule.append({'artifact_path':str(path.relative_to(ROOT)),'artifact_sha256':digest(path),'artifact_information_cutoff':str(cutoff),'spec_hash':result['metadata']['spec_hash']})
                predict=features[(features.index>cutoff)&(features.index<=end)]
                mean=np.array(result['metadata']['feature_mean']);std=np.array(result['metadata']['feature_std']);x=(predict.to_numpy()-mean)/std
                with torch.no_grad():pred=result['model'](torch.tensor(x,dtype=torch.float64))
                v=pred['var'].numpy();e=pred['es'].numpy();ec=v-result['es_calibration']*(v-e)
                if spec['head']=='supported':ec=supported_tail_publication(v,e,result['es_calibration'])
                truth=targets['deployment'][0].reindex(predict.index)
                for date,yy,vv,ee,raw in zip(predict.index,truth,v,ec,e):
                    if not np.isfinite(yy):continue
                    forecast_rows.append({'origin':date,'fold':j,'artifact_cutoff':cutoff,'maturity':targets['deployment'][1].loc[date],'is_decision':date in decision_origins,'realized':yy,'var':vv,'es':ee,'raw_es':raw,'calibration':result['es_calibration']})
                atomic_json(arm/'ARTIFACT_SCHEDULE.json',schedule)
                print(json.dumps({'variant':name,'fold':j+1,'total_folds':len(cuts),'seconds':time.monotonic()-start,'best_epoch':result['metadata']['best_epoch'],'training_rows':result['metadata']['training_rows'],'validation_fz0':result['metadata']['best_validation_fz0']}),flush=True)
            rows=pd.DataFrame(forecast_rows);rows.to_csv(arm/'forecasts.csv.gz',index=False,compression='gzip')
            decisions=rows[rows.is_decision];summary={'variant':name,'spec':spec,'daily':tail_metrics(rows.realized,rows['var'],rows.es),'decisions':tail_metrics(decisions.realized,decisions['var'],decisions.es),'nearzero_es_predictions':int((rows.es> -1e-6).sum()),'seconds':time.monotonic()-start,'folds':len(schedule)}
            atomic_json(arm/'COMPLETED.json',summary);summaries[name]=summary
        except Exception as exc:
            import traceback
            failed={'variant':name,'status':'failed','error':str(exc),'traceback':traceback.format_exc(),'seconds':time.monotonic()-start}
            atomic_json(arm/'FAILED.json',failed);summaries[name]=failed;print(json.dumps(failed),flush=True)
    atomic_json(out/'ALL_ARM_RESULTS.json',summaries)

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--prices',required=True);p.add_argument('--output',required=True);p.add_argument('--only',nargs='*');a=p.parse_args();run(a.prices,a.output,a.only)
