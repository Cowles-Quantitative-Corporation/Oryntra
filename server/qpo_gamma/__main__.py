import argparse,json
from pathlib import Path
from .config import defaults,load,schema,validate,delta,fingerprint,FIELDS
from .identity import atomic_json,verify_frozen,register_design

def main():
    p=argparse.ArgumentParser(description='QPO-based Gamma research platform; no trading or network execution')
    sub=p.add_subparsers(dest='command',required=True)
    s=sub.add_parser('schema');s.add_argument('--out',default='configs/schema.json')
    sub.add_parser('verify-frozen')
    sub.add_parser('coverage')
    s=sub.add_parser('init-config');s.add_argument('path');s.add_argument('--name',default='cold_baseline')
    s=sub.add_parser('validate');s.add_argument('config')
    s=sub.add_parser('diff');s.add_argument('baseline');s.add_argument('candidate')
    s=sub.add_parser('register-design');s.add_argument('baseline');s.add_argument('variants',nargs='+')
    s=sub.add_parser('run');s.add_argument('config');s.add_argument('--pilot',type=int,default=0);s.add_argument('--research-inputs')
    s=sub.add_parser('compare');s.add_argument('baseline');s.add_argument('candidate');s.add_argument('--out')
    s=sub.add_parser('report-design');s.add_argument('design');s.add_argument('runs',nargs='+')
    sub.add_parser('status')
    args=p.parse_args();root=Path(__file__).resolve().parents[1]
    if args.command=='schema':atomic_json(root/args.out,schema());print(args.out)
    elif args.command=='verify-frozen':print(json.dumps(verify_frozen(root),indent=2))
    elif args.command=='init-config':
        c=defaults();c['study']['name']=args.name;atomic_json(root/args.path,c);print(args.path)
    elif args.command=='validate':
        c=load(args.config);print(json.dumps({'valid':True,'fingerprint':fingerprint(c),'expanded_field_count':len(FIELDS),'effective_config':c},indent=2))
    elif args.command=='diff':print(json.dumps(delta(load(args.baseline),load(args.candidate)),indent=2))
    elif args.command=='register-design':print(register_design(root,load(args.baseline),[load(v) for v in args.variants]))
    elif args.command=='coverage':
        from .universe import audit_snapshot
        print(json.dumps({k:v for k,v in audit_snapshot(root).items() if k!='rows'},indent=2))
    elif args.command=='run':
        from .runner import run
        out,_=run(load(args.config),root=root,pilot_decisions=args.pilot,research_inputs=args.research_inputs);print(out)
    elif args.command=='compare':
        from .analysis import compare
        report,out=compare(root,args.baseline,args.candidate,args.out);print(out);print(json.dumps(report,indent=2))
    elif args.command=='status':
        rows=[]
        for path in sorted((root/'runs').glob('*/registration.json')):
            r=json.loads(path.read_text());rows.append({'trial_id':r['trial_id'],'name':r['config']['study']['name'],'scope':r['purpose'],'state':'completed' if (path.parent/'COMPLETED.json').exists() else 'failed' if (path.parent/'FAILED.json').exists() else 'registered_or_running','path':str(path.parent)})
        print(json.dumps(rows,indent=2))
    elif args.command=='report-design':
        from .design_report import report_design
        out,_=report_design(root,args.design,args.runs);print(out)

if __name__=='__main__':main()
