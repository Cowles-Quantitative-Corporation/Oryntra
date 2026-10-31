"""Seal completed forecast evidence without changing model fits or parameters."""
from pathlib import Path
import json,zipfile,hashlib
from qpo_gamma.identity import atomic_json,digest

def seal(directory):
    directory=Path(directory).resolve();plan=json.loads((directory/'REGISTERED_PLAN.json').read_text())
    with zipfile.ZipFile(directory/'source_snapshot.zip') as z:
        for name,expected in plan['source'].items():
            if hashlib.sha256(z.read(name)).hexdigest()!=expected:raise ValueError('Training source snapshot mismatch')
    for variant in plan['variants']:
        arm=directory/variant
        if not (arm/'COMPLETED.json').exists():raise ValueError('Incomplete arm '+variant)
        for row in json.loads((arm/'ARTIFACT_SCHEDULE.json').read_text()):
            p=Path(__file__).resolve().parents[1]/row['artifact_path']
            if digest(p)!=row['artifact_sha256']:raise ValueError('Artifact schedule hash mismatch')
    files={str(p.relative_to(directory)):digest(p) for p in sorted(directory.rglob('*')) if p.is_file() and p.name!='FINAL_RECEIPT.json'}
    receipt={'schema':'gamma-v1-forecast-completion-v1','plan_sha256':digest(directory/'REGISTERED_PLAN.json'),'files':files,'status':'all_six_declared_arms_complete_and_byte_verified','scope':'Accidental mutation detection, not a signature against an adversary rewriting all manifests'}
    atomic_json(directory/'FINAL_RECEIPT.json',receipt);return receipt

def verify(directory):
    directory=Path(directory);receipt=json.loads((directory/'FINAL_RECEIPT.json').read_text())
    for name,expected in receipt['files'].items():
        if digest(directory/name)!=expected:raise ValueError('Sealed forecast changed '+name)
    return receipt

if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser();p.add_argument('directory');a=p.parse_args();print(json.dumps({'files':len(seal(a.directory)['files'])}))
