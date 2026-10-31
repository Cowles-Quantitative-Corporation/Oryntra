"""Matched forecast evidence with descriptive dependence-aware intervals."""
from pathlib import Path
import json
import numpy as np
import pandas as pd
from gamma_v1.contracts import tail_metrics,fz0_numpy
from qpo_gamma.identity import atomic_json,digest

def bootstrap_interval(values,seed=560205,length=6,reps=1000):
    a=np.asarray(values,float);rng=np.random.default_rng(seed);n=len(a);est=[]
    for _ in range(reps):
        starts=rng.integers(n,size=int(np.ceil(n/length)))
        ix=np.concatenate([(np.arange(length)+s)%n for s in starts])[:n]
        est.append(a[ix].mean())
    return list(map(float,np.quantile(est,[.025,.975])))

def run(root):
    root=Path(root);study=root/'studies/forecast_v1r5';out=root/'reports/forecast_review';out.mkdir(exist_ok=True)
    from gamma_v1.forecast_seal import verify
    verify(study)
    panels={p.parent.name:pd.read_csv(p,parse_dates=['origin']).set_index('origin') for p in study.glob('*/forecasts.csv.gz')}
    reference=panels['legacy_warm_calibrated'];origins=reference[reference.is_decision].index
    benchmarks=pd.read_csv(root/'studies/forecast_benchmarks/forecasts.csv.gz',parse_dates=['origin'])
    panels.update({name:group.set_index('origin') for name,group in benchmarks.groupby('variant')})
    scores={};losses={};rows=[]
    for name,panel in panels.items():
        p=panel.reindex(origins)
        if p[['realized','var','es']].isna().any().any():raise ValueError('Unmatched forecast origins')
        scores[name]=tail_metrics(p.realized,p['var'],p.es)
        from scipy.stats import beta
        hit=(p.realized<=p['var']).to_numpy();count=int(hit.sum());n=len(hit)
        scores[name]['VaR_breach_count']=count
        scores[name]['iid_only_Clopper_Pearson95']=[0.0 if count==0 else float(beta.ppf(.025,count,n-count+1)),1.0 if count==n else float(beta.ppf(.975,count+1,n-count))]
        scores[name]['max_breaches_in_12_origins']=int(pd.Series(hit).rolling(12,min_periods=1).sum().max())
        scores[name]['coverage_warning']='Nonoverlapping holdings are not independent observations; binomial interval is an IID reference only'
        scores[name]['five_time_blocks']=[tail_metrics(q.realized,q['var'],q.es) for q in np.array_split(p,5)]
        losses[name]=fz0_numpy(p.realized,p['var'],p.es)
        rows.append({'variant':name,**{k:v for k,v in scores[name].items() if k!='five_time_blocks'}})
    contrasts=[]
    declared=json.loads((study/'REGISTERED_PLAN.json').read_text())['contrasts']
    declared += [[b,n] for b in ['fold_all_matured_empirical','fold_training_only_empirical'] for n in panels if not n.startswith('fold_')]
    for baseline,candidate in declared:
        d=losses[candidate]-losses[baseline]
        contrasts.append({'baseline':baseline,'candidate':candidate,'candidate_minus_baseline_FZ0':float(d.mean()),'descriptive_paired_block95_interval':bootstrap_interval(d),'positive_improvement_blocks':int(sum(np.mean(q)<0 for q in np.array_split(d,5))),'block_means':list(map(float,[np.mean(q) for q in np.array_split(d,5)]))})
    result={'scope':'same329stocks,120deployments; evaluated repeatedhistoricalsurvivorpanel; noconfirmatoryclaim','observations':len(origins),'scores':scores,'contrasts':contrasts,'bootstrap':'6decisioncircularblocks1000draws, descriptive dependenceassumptions; no postselection significance','warnings':['Originalnativepurge21 is separate; six diagnostics use strictmaturitypurge20legacy/22deployment','5%tail means only6expectedbreaches across120origins; coverage uncertainty islarge','Correctness/support fixes can worsen forecasting; no automaticpromotion','Label is daily-rebalanced equalweightmarket simple return, not actualQPOportfolio outcome','Allperiodliquidity andfullhistoryavailability imply future-conditioneduniverse selection']}
    atomic_json(out/'FORECAST_REPORT.json',result);pd.DataFrame(rows).to_csv(out/'FORECAST_SCORECARD.csv',index=False);pd.DataFrame(contrasts).to_csv(out/'PAIRED_CONTRASTS.csv',index=False)
    text=['Gamma V1 forecast evidence','',result['scope'],'']
    for row in rows:text.append(f"{row['variant']}: FZ0={row['fz0_mean']:.6f}, pinball={row['pinball_mean']:.6f}, coverage={row['var_coverage']:.1%}, ES below-100%={row['es_support_violations']}/120")
    text+=['','Warnings:']+result['warnings'];(out/'FORECAST_REPORT.txt').write_text('\n'.join(text)+'\n')
    print(json.dumps({'scores':rows,'contrasts':contrasts},indent=2))

if __name__=='__main__':run(Path(__file__).resolve().parents[1])
