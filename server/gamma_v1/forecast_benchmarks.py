"""Fixed historical empirical benchmarks and honest matched forecast summaries."""
import json,hashlib
from pathlib import Path
import numpy as np
import pandas as pd
from gamma_v1.contracts import market_target,admitted_labels,tail_metrics,fz0_numpy
from qpo_gamma.identity import atomic_json,digest

def run(root):
    root=Path(root);out=root/'studies/forecast_benchmarks';out.mkdir(exist_ok=True)
    plan={'schema':'gamma-v1-empirical-benchmarks-v1','created_utc':pd.Timestamp.now(tz='UTC').isoformat(),'prices_sha256':digest(root/'eligibility/prices.csv.gz'),'target':'deploymentc+2..c+22','alpha':.05,'first_fold_cutoff':'2016-09-02','refit_sessions':252,'variants':['fold_all_matured_empirical','fold_training_only_empirical'],'rules':'quantilelinear and empiricalmean oflabels<=quantile; strictmaturitypurge fortrainingonly; no choices from evaluationreturns','reporting':'commonorigins120decisions and daily; threeequal40-decisioneras plus originalfive24-decisionblocks; paired6-decisioncircularblockbootstrap1000 descriptive; notconfirmatory','source_sha256':digest(__file__)}
    atomic_json(out/'REGISTERED_PLAN.json',plan)
    prices=pd.read_csv(root/'eligibility/prices.csv.gz',index_col=0,parse_dates=True,float_precision='round_trip');r=prices.pct_change(fill_method=None).iloc[1:]
    y,m=market_target(r);first=r.index.get_loc(pd.Timestamp('2016-09-02'));cuts=list(range(first,len(r),252));allrows=[]
    for j,cut in enumerate(cuts):
        cutoff=r.index[cut];end=r.index[min(cut+252,len(r)-1)];hist=admitted_labels(y,m,cutoff).dropna()
        # Native features start after63 observed returns, as in learned arms.
        hist=hist.loc[r.index[62]:]
        split=len(hist)-max(30,int(round(len(hist)*.2)));train=hist.iloc[:split];train=train[m.reindex(train.index)<hist.index[split]]
        for name,data in [('fold_all_matured_empirical',hist),('fold_training_only_empirical',train)]:
            q=float(data.quantile(.05));es=float(data[data<=q].mean())
            if es>=0:raise ValueError('Empirical benchmark outside FZ0 domain')
            for origin,truth in y[(y.index>cutoff)&(y.index<=end)].dropna().items():
                allrows.append({'variant':name,'origin':origin,'fold':j,'realized':truth,'var':q,'es':es,'training_labels':len(data)})
    pd.DataFrame(allrows).to_csv(out/'forecasts.csv.gz',index=False,compression='gzip')
    atomic_json(out/'COMPLETED.json',{'variants':plan['variants'],'rows':len(allrows),'status':'completed'})

if __name__=='__main__':run(Path(__file__).resolve().parents[1])
