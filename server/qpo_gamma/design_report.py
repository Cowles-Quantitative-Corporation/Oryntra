"""Declared contrasts with complete arm status; no selective successful-arm report."""
from pathlib import Path
import json
import numpy as np
import pandas as pd
from .analysis import compare,holm,interaction,metrics
from .identity import atomic_json,digest

def report_design(root,design_path,run_paths):
    root=Path(root);design_path=Path(design_path);design=json.loads(design_path.read_text());runs={}
    configured={c['study']['name']:c for c in design['configs']}
    for path in run_paths:
        p=Path(path);r=json.loads((p/'registration.json').read_text());name=r['config']['study']['name']
        if name in runs:raise ValueError('Ambiguous multiple runs for one arm')
        if name not in configured:raise ValueError('Run was not a declared arm')
        if r['config']!=configured[name]:raise ValueError('Run configuration differs from preregistration')
        for key in ['code_sha256','runtime_sha256']:
            if key in design and r.get(key)!=design[key]:raise ValueError('Run '+key+' differs from design')
        runs[name]=(p,r)
    statuses=[]
    for name in configured:
        if name not in runs:statuses.append({'arm':name,'status':'not_run','trial_id':None});continue
        p,r=runs[name]
        status='completed' if (p/'COMPLETED.json').exists() else 'failed' if (p/'FAILED.json').exists() else 'registered_or_running'
        row={'arm':name,'status':status,'trial_id':r['trial_id'],'registered_before_trial':design['registered_utc']<=r['created_utc']}
        if status=='failed':row['failure']=json.loads((p/'FAILED.json').read_text())
        statuses.append(row)
    complete={r['arm'] for r in statuses if r['status']=='completed'}
    all_complete=len(complete)==len(configured);results=[];pvalues=[]
    for row in design['contrasts']:
        names=[row['baseline'],row['candidate']]
        if not set(names)<=complete:
            results.append({'baseline':names[0],'candidate':names[1],'status':'unavailable','missing_or_failed_arms':[n for n in names if n not in complete]});pvalues.append(None);continue
        r,out=compare(root,runs[names[0]][0],runs[names[1]][0])
        results.append({'baseline':names[0],'candidate':names[1],'status':'completed','comparison':str(out.relative_to(root)),'config_changes':r['config_changes'],'metric_deltas':r['metric_deltas'],'breadth':r['breadth'],'time_stability_pass':r['time_stability_pass']});pvalues.append((r.get('uncertainty') or {}).get('paired_mean_return_pvalue'))
    if all_complete and pvalues and all(v is not None for v in pvalues):
        for r,p,padj in zip(results,pvalues,holm(pvalues)):r.update(raw_exploratory_pvalue=p,holm_adjusted_exploratory_pvalue=padj)
    pairs=[]
    for names in design.get('interactions',[]):
        if not set(names)<=complete:
            pairs.append({'cell_order_00_10_01_11':names,'status':'unavailable','missing_or_failed_arms':[n for n in names if n not in complete]});continue
        ledgers=[pd.read_csv(runs[n][0]/'ledger.csv.gz',index_col=0,parse_dates=True) for n in names]
        if not all(x.index.equals(ledgers[0].index) for x in ledgers):raise ValueError('Interaction calendars differ')
        returns=[x.net_return.to_numpy() for x in ledgers];rf=configured[names[0]]['execution']['metric_rf'];stats=[metrics(x,rf) for x in returns]
        effects={'A_given_B_off':float((returns[1]-returns[0]).mean()),'A_given_B_on':float((returns[3]-returns[2]).mean()),'B_given_A_off':float((returns[2]-returns[0]).mean()),'B_given_A_on':float((returns[3]-returns[1]).mean())}
        effects['factorial_average_A']=(effects['A_given_B_off']+effects['A_given_B_on'])/2
        effects['factorial_average_B']=(effects['B_given_A_off']+effects['B_given_A_on'])/2
        pairs.append({'cell_order_00_10_01_11':names,'status':'completed','metric_interactions':{k:stats[3][k]-stats[1][k]-stats[2][k]+stats[0][k] for k in ['CAGR','total_return','max_drawdown']},'mean_daily_return_interaction':float(interaction(*returns).mean()),'mean_daily_return_effects':effects,'not_mechanism_proof':True})
        compare(root,runs[names[2]][0],runs[names[3]][0])
    doc={'schema':'paired-design-report-v2','design_sha256':digest(design_path),'arm_statuses':statuses,'contrasts':results,'interactions':pairs,'all_declared_arm_statuses_reported':True,'all_arms_completed':all_complete,'family_inference_available':all_complete and bool(pvalues) and all(v is not None for v in pvalues),'multiplicity_limits':'Holm applies only to the complete declared paired mean-return family under bootstrap assumptions; not all previous model searches, selected metrics or survivorship bias. Historical evidence remains exploratory.','promotion':'none; untouched future confirmation and data gates remain unmet'}
    out=root/'reports'/('DESIGN_RESULTS_'+design_path.stem[:12]+'.json');atomic_json(out,doc);return out,doc
