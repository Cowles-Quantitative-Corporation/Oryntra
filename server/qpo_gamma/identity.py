"""Immutable registry and byte identities; no silent resume across changes."""
import hashlib,json,os,platform,sys,zipfile,importlib.metadata
from datetime import datetime,timezone
from pathlib import Path
from .config import canonical,fingerprint

def digest(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as f:
        for b in iter(lambda:f.read(1024*1024),b''):h.update(b)
    return h.hexdigest()

def code_identity(root):
    root=Path(root)
    rows={str(p.relative_to(root)):digest(p) for name in ['qpo_gamma','gamma_v1','backend'] for p in sorted((root/name).rglob('*')) if p.is_file() and p.suffix in ('.py','.json','.csv') and '__pycache__' not in p.parts}
    return {'sha256':fingerprint(rows),'files':rows}

def atomic_json(path,data):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    tmp=path.with_suffix(path.suffix+'.tmp');tmp.write_text(json.dumps(data,indent=2,allow_nan=False,default=str));os.replace(tmp,path)

def verify_frozen(root):
    root=Path(root);m=json.loads((root/'FROZEN_INPUTS.json').read_text())
    for p,row in m.items():
        if digest(root/p)!=row['sha256']:raise ValueError('Frozen source/input changed: '+p)
    return {'verified_files':len(m),'manifest_sha256':digest(root/'FROZEN_INPUTS.json')}

def runtime_identity():
    versions={}
    for package in ['numpy','pandas','scipy','scikit-learn','torch','joblib','pydantic']:
        try:versions[package]=importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:versions[package]='unavailable'
    return {'python':sys.version,'platform':platform.platform(),'dependencies':versions,'numeric_thread_environment':{k:os.environ.get(k) for k in ['OMP_NUM_THREADS','OPENBLAS_NUM_THREADS','MKL_NUM_THREADS','NUMEXPR_NUM_THREADS']}}

def register(root,config,data_hashes,*,purpose='full',pilot_decisions=0):
    root=Path(root);code=code_identity(root);runtime=runtime_identity()
    identity={'config':config,'data_hashes':data_hashes,'code_sha256':code['sha256'],'runtime_sha256':fingerprint(runtime),'purpose':purpose,'pilot_decisions':pilot_decisions}
    trial_id=fingerprint(identity);out=root/'runs'/(config['study']['name']+'_'+trial_id[:12]);out.mkdir(parents=True,exist_ok=True)
    path=out/'registration.json'
    doc={'trial_id':trial_id,**identity,'created_utc':datetime.now(timezone.utc).isoformat(),'code':code,'runtime':runtime,'status':'registered'}
    if path.exists():
        old=json.loads(path.read_text())
        if old['trial_id']!=trial_id:raise ValueError('Immutable trial identity conflict')
        return out,old
    with path.open('x') as f:f.write(json.dumps(doc,indent=2,allow_nan=False))
    with zipfile.ZipFile(out/'source_snapshot.zip','x',compression=zipfile.ZIP_DEFLATED) as z:
        for name,expected in code['files'].items():
            data=(root/name).read_bytes()
            if hashlib.sha256(data).hexdigest()!=expected:raise ValueError('Code changed while registration was being captured')
            z.writestr(name,data)
    return out,doc

def register_design(root,baseline,variants,interactions=()):
    from .config import delta,validate
    configs=[validate(baseline)]+[validate(v) for v in variants]
    names=[c['study']['name'] for c in configs]
    if len(set(names))!=len(names):raise ValueError('Duplicate study arm')
    contrasts=[]
    for c in configs[1:]:
        changes=delta(configs[0],c)
        contrasts.append({'baseline':names[0],'candidate':c['study']['name'],'changes':changes,'one_factor':len(changes)==1})
    by_name=dict(zip(names,configs))
    for pair in interactions:
        if len(pair)!=4 or len(set(pair))!=4 or not set(pair)<=set(names):raise ValueError('Interaction requires distinct registered 00,10,01,11 arm names')
        c00,c10,c01,c11=[by_name[n] for n in pair]
        a,b=delta(c00,c10),delta(c00,c01)
        if not a or not b or set(a)&set(b):raise ValueError('Factorial interventions must be nonempty and disjoint')
        if delta(c00,c11)!={**a,**b}:raise ValueError('Factorial 11 cell must combine exactly the two declared interventions')
    doc={'schema':'paired-design-v2','code_sha256':code_identity(root)['sha256'],'runtime_sha256':fingerprint(runtime_identity()),'existing_completed_trials_at_registration':[p.parent.name for p in sorted((Path(root)/'runs').glob('*/COMPLETED.json'))],'scope':'fixed historical exploratory factorial; prior pilots disclosed; no untouched claim','primary_estimand':'paired net daily portfolio-return mean; dollar attribution and forecast loss are separate descriptive outcomes','registered_utc':datetime.now(timezone.utc).isoformat(),'configs':configs,'contrasts':contrasts,'interactions':list(interactions),'correlation_is_not_causal_importance':True,'promotion_requires_untouched_confirmation':True}
    design_id=fingerprint({k:v for k,v in doc.items() if k!='registered_utc'});path=Path(root)/'designs'/(design_id+'.json');path.parent.mkdir(exist_ok=True)
    if not path.exists():
        with path.open('x') as f:f.write(json.dumps(doc,indent=2))
    return path


COMPARISON_ARTIFACTS = ('registration.json', 'effective_config.json', 'ledger.csv.gz',
                        'daily_dollar_attribution.csv.gz', 'targets.csv.gz')


def result_artifact_hashes(directory):
    """Seal comparison inputs before publishing COMPLETED; logs may keep growing."""
    directory = Path(directory)
    return {name: digest(directory/name) for name in COMPARISON_ARTIFACTS}


def verify_completed(directory):
    directory = Path(directory)
    summary = json.loads((directory/'COMPLETED.json').read_text())
    hashes = summary.get('comparison_artifact_sha256')
    if not isinstance(hashes, dict) or set(hashes) != set(COMPARISON_ARTIFACTS):
        raise ValueError('Completed run lacks sealed comparison artifacts; rerun under current integrity contract')
    for name, expected in hashes.items():
        if digest(directory/name) != expected:
            raise ValueError('Completed run artifact changed: ' + name)
    registration = json.loads((directory/'registration.json').read_text())
    identity = {key: registration[key] for key in ('config', 'data_hashes', 'code_sha256', 'runtime_sha256', 'purpose', 'pilot_decisions')}
    if fingerprint(identity) != registration['trial_id']:
        raise ValueError('Registration trial fingerprint mismatch')
    if summary.get('trial_id') != registration['trial_id']:
        raise ValueError('Completion and registration trial identities differ')
    if json.loads((directory/'effective_config.json').read_text()) != registration['config']:
        raise ValueError('Effective configuration differs from registration')
    return summary
