"""Run the complete registered family at bounded concurrency with durable status."""
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor,as_completed
import argparse,json,os,subprocess,sys,time,threading
from qpo_gamma.identity import atomic_json,code_identity,digest
ROOT=Path(__file__).resolve().parents[1]

def run(design_path,manifest_path,workers=2):
    design_path=Path(design_path).resolve();manifest_path=Path(manifest_path).resolve();design=json.loads(design_path.read_text())
    out=ROOT/'studies'/('portfolio_family_'+design_path.stem[:12]);out.mkdir(exist_ok=True)
    guard=design['code_sha256'];source_input=digest(manifest_path)
    if code_identity(ROOT)['sha256']!=guard:raise ValueError('Source changed after family registration')
    lock=threading.Lock();status={'design':str(design_path),'manifest':str(manifest_path),'manifest_sha256':source_input,'max_parallel_processes':workers,'arms':{},'all_succeeded':False}
    def execute(config):
        name=config['study']['name'];log=out/(name+'.log');started=time.monotonic()
        if code_identity(ROOT)['sha256']!=guard or digest(manifest_path)!=source_input:raise ValueError('Immutable family source/input changed before '+name)
        with lock:
            status['arms'][name]={'state':'running','started_utc':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()),'log':str(log)};atomic_json(out/'STATUS.json',status)
        cmd=[sys.executable,'-m','qpo_gamma','run',str(ROOT/'configs/gamma_v1'/(name+'.json')),'--research-inputs',str(manifest_path)]
        with log.open('a') as stream:result=subprocess.run(cmd,cwd=ROOT,env=os.environ.copy(),stdout=stream,stderr=subprocess.STDOUT)
        with lock:
            status['arms'][name].update(state='process_succeeded' if result.returncode==0 else 'failed',exit_code=result.returncode,elapsed_seconds=time.monotonic()-started)
            atomic_json(out/'STATUS.json',status)
        return name,result.returncode
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures={pool.submit(execute,c):c['study']['name'] for c in design['configs']}
        for future in as_completed(futures):
            name=futures[future]
            try:result=future.result();print(json.dumps({'arm':result[0],'exit_code':result[1]}),flush=True)
            except Exception as exc:
                with lock:status['arms'][name]={'state':'failed_before_execution','error':str(exc)};atomic_json(out/'STATUS.json',status)
                print(json.dumps({'arm':name,'error':str(exc)}),flush=True)
    paths=[]
    for config in design['configs']:
        name=config['study']['name'];matched=[]
        for path in (ROOT/'runs').glob(name+'_*/registration.json'):
            registration=json.loads(path.read_text())
            if registration['code_sha256']==guard and registration['purpose']=='full_historical_exploration' and registration['config']==config:matched.append(path.parent)
        if len(matched)>1:raise ValueError('Ambiguous registered arm results')
        if matched:paths.append(matched[0]);status['arms'][name]['run_path']=str(matched[0])
    from qpo_gamma.design_report import report_design
    report_path,report=report_design(ROOT,design_path,paths)
    status['all_succeeded']=report['all_arms_completed'];status['design_report']=str(report_path);atomic_json(out/'STATUS.json',status)
    print(json.dumps(status,indent=2),flush=True)

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('design');p.add_argument('manifest');p.add_argument('--workers',type=int,choices=[1,2],default=2);a=p.parse_args();run(a.design,a.manifest,a.workers)
