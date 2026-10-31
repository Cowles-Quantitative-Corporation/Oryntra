"""Build immutable matched Gamma V1 input manifests and the declared portfolio family."""
from pathlib import Path
import json,copy
from qpo_gamma.identity import atomic_json,digest,register_design
from qpo_gamma.config import validate
ROOT=Path(__file__).resolve().parents[1]

def ref(path):
    path=Path(path).resolve();return {'path':str(path.relative_to(ROOT)),'sha256':digest(path)}

def build_manifest(*,native_only=False,forecast_study='forecast_v1r5'):
    out=ROOT/'regenerated/gamma_v1_inputs';out.mkdir(exist_ok=True)
    doc={'schema':'gamma-v1-inputs-v1','scope':'Static future-conditioned329-stock research; all compared arms share this manifest; no SEC/PIT claim','models':{}}
    paths={'admission':'eligibility/ADMISSION.json','prices':'eligibility/prices.csv.gz','spy':'eligibility/spy.csv.gz','beta_records':'eligibility/beta329/records.jsonl.gz','beta_source_plan':'eligibility/beta329/SOURCE_PLAN.json','beta_diagnostics':'eligibility/beta329/diagnostics.json','beta_integrity':'eligibility/beta329/RECOVERY_INTEGRITY.json'}
    for key,path in paths.items():doc[key]=ref(ROOT/path)
    schedule=json.loads((ROOT/'eligibility/native_v1a_cold/ARTIFACT_SCHEDULE.json').read_text())
    for row in schedule:row['artifact_path']=str(Path(row['artifact_path']).relative_to(ROOT))
    atomic_json(out/'NATIVE_COLD_SCHEDULE.json',schedule)
    doc['models']['native_cold']={'kind':'native','schedule':ref(out/'NATIVE_COLD_SCHEDULE.json'),'training':ref(ROOT/'eligibility/native_v1a_cold/TRAINING_DIAGNOSTICS.json')}
    if not native_only:
        study=ROOT/'studies'/forecast_study;plan=study/'REGISTERED_PLAN.json'
        for name in json.loads(plan.read_text())['variants']:
            if not (study/name/'COMPLETED.json').exists():raise ValueError('Forecast arm incomplete '+name)
            doc['models'][name]={'kind':'gamma','schedule':ref(study/name/'ARTIFACT_SCHEDULE.json'),'training':ref(plan)}
    path=out/('NATIVE_PILOT_INPUTS.json' if native_only else 'MATCHED_INPUTS.json');atomic_json(path,doc);return path

def configs():
    base=validate(json.loads((ROOT/'configs/expanded323_cold.json').read_text()));base['data'].update(target_assets=329,prices_file='eligibility/prices.csv.gz',spy_file='eligibility/spy.csv.gz');base['training'].update(artifact_mode='gamma_v1',model_variant='native_cold');base['study']['name']='gamma_v1_native_control'
    result=[base]
    def add(name,changes):
        c=copy.deepcopy(base);c['study']['name']=name
        for section,values in changes.items():c[section].update(values)
        result.append(validate(c))
    add('gamma_v1_state_off',{'state':{'v1a_policy':'off'}})
    add('gamma_v1_tail_only',{'state':{'v1a_policy':'tail_gamma_only'}})
    add('gamma_v1_rank_clipped',{'ranking':{'enabled':True}})
    add('gamma_v1_rank_signed',{'ranking':{'enabled':True,'nonnegative_slope':False}})
    add('gamma_v1_mean_shrinkage',{'ranking':{'enabled':True,'model':'native_shrinkage'}})
    add('gamma_v1_shrinkage_errors',{'ranking':{'enabled':True,'model':'native_shrinkage'},'errors':{'policy':'matched_as_issued'}})
    add('gamma_v1_native_errors',{'errors':{'policy':'matched_as_issued'}})
    add('gamma_v1_signed_errors',{'ranking':{'enabled':True,'nonnegative_slope':False},'errors':{'policy':'matched_as_issued'}})
    add('gamma_v1_supported_model',{'training':{'model_variant':'deployment_supported_warm_nocal'}})
    add('gamma_v1_supported_tail_only',{'training':{'model_variant':'deployment_supported_warm_nocal'},'state':{'v1a_policy':'tail_gamma_only'}})
    directory=ROOT/'configs/gamma_v1';directory.mkdir(exist_ok=True)
    for c in result:atomic_json(directory/(c['study']['name']+'.json'),c)
    return result

if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser();p.add_argument('--native-only',action='store_true');p.add_argument('--register',action='store_true');p.add_argument('--forecast-study',default='forecast_v1r5');a=p.parse_args()
    manifest=build_manifest(native_only=a.native_only,forecast_study=a.forecast_study);family=configs()
    print(manifest)
    if a.register:
        if a.native_only:raise ValueError('Full family requires final immutable model bundle')
        design=register_design(ROOT,family[0],family[1:],interactions=[['gamma_v1_native_control','gamma_v1_mean_shrinkage','gamma_v1_native_errors','gamma_v1_shrinkage_errors'],['gamma_v1_native_control','gamma_v1_rank_signed','gamma_v1_native_errors','gamma_v1_signed_errors'],['gamma_v1_native_control','gamma_v1_tail_only','gamma_v1_supported_model','gamma_v1_supported_tail_only']])
        print(design)
