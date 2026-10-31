"""Train-only native-mean shrinkage benchmark; forecast diagnostic, not a trade arm."""
from pathlib import Path
import json
import numpy as np
import pandas as pd
from gamma_v1.forecast_report import bootstrap_interval
from qpo_gamma.identity import atomic_json,digest

def run(root):
    root=Path(root);out=root/'studies/shrinkage_benchmark';out.mkdir(exist_ok=True)
    atomic_json(out/'REGISTERED_PLAN.json',{'created_utc':pd.Timestamp.now(tz='UTC').isoformat(),'source_sha256':digest(__file__),'prices_sha256':digest(root/'eligibility/prices.csv.gz'),'rule':'Daily centered cross-sectional slope of deployment return on native forecast; median of504 fully matured past slopes; clip coefficient to[0,1]; fixed25% intervention;5% horizon correction bound with equal-weight recenter/common scaling','primary':'Mean return forecast loss versus native and signed composite; no portfolio or parameter selection','scope':'Exploratory, fixed future-conditioned329-stock panel'})
    p=pd.read_csv(root/'eligibility/prices.csv.gz',index_col=0,parse_dates=True);native=p.pct_change(fill_method=None).rolling(252,min_periods=252).mean()*10.5
    y=p.shift(-22)/p.shift(-1)-1;nc=native.sub(native.mean(axis=1),axis=0);yc=y.sub(y.mean(axis=1),axis=0)
    good=nc.notna()&yc.notna();slope=(nc.where(good)*yc.where(good)).sum(axis=1)/(nc.where(good).pow(2).sum(axis=1)+.0001);slope=slope.where(good.sum(axis=1)>=6)
    coef=slope.shift(22).rolling(504,min_periods=126).median().clip(0,1)
    correction=nc.mul(.25*(coef-1),axis=0).clip(-.05,.05);correction=correction.sub(correction.mean(axis=1),axis=0)
    maximum=correction.abs().max(axis=1);correction=correction.mul(np.minimum(1,.05/maximum),axis=0)
    ref=root/'studies/ranking_value_r2';n=pd.read_csv(ref/'native_issued_horizon.csv.gz',index_col=0,parse_dates=True);signed=pd.read_csv(ref/'signed_issued_horizon.csv.gz',index_col=0,parse_dates=True)
    # Preserve the exact first-decision native251-row exception in issued mean.
    shrink=n+correction.loc[n.index];truth=y.loc[n.index];mse={name:((truth-v)**2).mean(axis=1) for name,v in [('native',n),('signed',signed),('shrinkage',shrink)]}
    comparisons=[]
    for a,b in [('native','shrinkage'),('signed','shrinkage')]:
        d=mse[b]-mse[a];comparisons.append({'baseline':a,'candidate':b,'MSE_difference':float(d.mean()),'paired_block95_descriptive':bootstrap_interval(d),'improving_time_blocks':int(sum(x.mean()<0 for x in np.array_split(d,5)))})
    result={'MSE':{name:float(v.mean()) for name,v in mse.items()},'comparisons':comparisons,'coefficient_range':[float(coef.loc[n.index].min()),float(coef.loc[n.index].max())],'warning':'No claim of independent learned information or executable portfolio benefit. Parameterization fixed before computing this diagnostic.'}
    atomic_json(out/'COMPLETED.json',result);shrink.to_csv(out/'shrinkage_issued_horizon.csv.gz',compression='gzip');print(json.dumps(result,indent=2))

if __name__=='__main__':run(Path(__file__).resolve().parents[1])
