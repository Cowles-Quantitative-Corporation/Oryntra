"""Prespecified signed train-only residual mean contrast; never tune to P&L."""
from pathlib import Path
import json
import numpy as np
import pandas as pd
from scipy.stats import spearmanr
from qpo_gamma.config import validate
from qpo_gamma.signals import build_panel
from qpo_gamma.identity import atomic_json,digest

def run(root):
    root=Path(root);out=root/'studies/ranking_value_r2';out.mkdir(exist_ok=True)
    atomic_json(out/'REGISTERED_PLAN.json',{'created_utc':pd.Timestamp.now(tz='UTC').isoformat(),'hypothesis':'Allow signed rolling median residual calibration, with unchanged features and fixed strength0.25/bound0.05','primary':'paired full and crosssectioncentered deploymentreturn MSE; nativeprediction versus clippedranking versus signedranking','selection':'None; all120origins and329stocks, everynegativeeffect retained','prices_sha256':digest(root/'eligibility/prices.csv.gz'),'source_sha256':digest(__file__)})
    prices=pd.read_csv(root/'eligibility/prices.csv.gz',index_col=0,parse_dates=True);spy=pd.read_csv(root/'eligibility/spy.csv.gz',index_col=0,parse_dates=True).iloc[:,0]
    cfg=validate(json.loads((root/'configs/expanded323_cold.json').read_text()));cfg['data'].update(universe='expanded300',target_assets=329,prices_file='eligibility/prices.csv.gz',spy_file='eligibility/spy.csv.gz');cfg['ranking']['enabled']=True
    first=prices.index.get_loc(pd.Timestamp(cfg['data']['start']));last=prices.index.get_loc(pd.Timestamp(cfg['data']['end']));cutoffs=prices.index[np.arange(first,last+1,21)-2]
    returns=prices.pct_change(fill_method=None).iloc[first-252:last+1];first_mu=returns.iloc[:251].mean()*126
    clipped=build_panel(prices,spy,cfg,native_overrides={cutoffs[0]:first_mu});cfg['ranking']['nonnegative_slope']=False;signed=build_panel(prices,spy,cfg,native_overrides={cutoffs[0]:first_mu})
    panels={'native':clipped.native/12,'clipped':clipped.issued/12,'signed':signed.issued/12};y=clipped.realized.loc[cutoffs];summary={};loss={}
    for name,panel in panels.items():
        pred=panel.loc[cutoffs];err=y-pred;yc=y.sub(y.mean(axis=1),axis=0);pc=pred.sub(pred.mean(axis=1),axis=0)
        loss[name]=(err*err).mean(axis=1);ic=[spearmanr(y.loc[t],pred.loc[t]).statistic for t in cutoffs]
        summary[name]={'mse':float((err*err).to_numpy().mean()),'centered_mse':float(((yc-pc)**2).to_numpy().mean()),'mean_crosssection_rank_ic':float(np.nanmean(ic)),'observations':int(err.size),'decision_origins':len(cutoffs)}
        pred.to_csv(out/f'{name}_issued_horizon.csv.gz',compression='gzip')
    stock_gain=((y-panels['native'].loc[cutoffs])**2).mean()-((y-panels['signed'].loc[cutoffs])**2).mean()
    stock_gain.rename('native_minus_signed_MSE').to_csv(out/'all_stock_MSE_differences.csv')
    diag=signed.diagnostics.loc[cutoffs];diag['max_signed_delta']=signed.delta.loc[cutoffs].abs().max(axis=1);diag.to_csv(out/'decision_diagnostics.csv')
    result={'scores':summary,'improved_stock_MSE_count':int((stock_gain>0).sum()),'worsened_stock_MSE_count':int((stock_gain<0).sum()),'clipped_native_max_delta':float(clipped.delta.loc[cutoffs].abs().to_numpy().max()),'signed_nonzero_origins':int((diag.max_signed_delta>0).sum()),'raw_slope_range':[float(diag.raw_signed_slope.min()),float(diag.raw_signed_slope.max())],'warnings':['Stock errors are correlated, so four improvements are not independent replications','Train-only fitting is causal inside a future-conditioned survivor universe','Forecast improvement is distinct from portfolio contribution and does not establish tradable value']}
    atomic_json(out/'COMPLETED.json',result);print(json.dumps(result,indent=2))

if __name__=='__main__':run(Path(__file__).resolve().parents[1])
