"""Explicit local staged runner. Input manifest carries accepted file hashes.

No raw/Parquet reader. TRAIN command cannot open EVAL value receipts. EVAL needs
an existing complete model freeze; this is a synchronous fail-closed research
workflow, not crash durability, a sandbox, or authority to access new cohorts.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import resource
import shutil
import time
from datetime import datetime, timezone
from . import activity_magnitude as a


def sha(b):return hashlib.sha256(b).hexdigest()

class Guard:
    def __init__(self,out):self.start=time.monotonic();self.out=out;self.maxrss=0
    def __call__(self):
        rss=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        # macOS ru_maxrss is bytes; on Linux it is KiB.
        import sys
        if sys.platform!='darwin':rss*=1024
        self.maxrss=max(self.maxrss,rss)
        a.require(time.monotonic()-self.start<180,'180s cooperative wall stop')
        a.require(rss<=512*1024**2,'512MiB reported peak RSS stop')
        a.require(shutil.disk_usage(self.out).free>=2*1024**3,'2GiB disk reserve')
        total=sum(p.stat().st_size for p in self.out.iterdir() if p.is_file())
        a.require(total<=64*1024**2,'64MiB aggregate output stop')
    def receipt(self):return dict(elapsed_seconds=time.monotonic()-self.start,reported_peak_RSS_bytes=self.maxrss,
                                  hard_enforcement=False,wall_seconds=180,RSS_limit_bytes=512*1024**2)

def read(spec,guard):
    guard();p=Path(spec['path']);a.require(p.stat().st_size<=16*1024**2,'bounded saved JSON')
    data=p.read_bytes();a.require(sha(data)==spec['sha256'],'saved input hash mismatch');guard()
    return json.loads(data)

def write(out,name,v,guard):
    guard();data=a.canonical(v);p=out/name
    a.require(not p.exists() and len(data)<=32*1024**2,'new bounded output only')
    total=sum(q.stat().st_size for q in out.iterdir() if q.is_file())
    a.require(total+len(data)<=64*1024**2,'aggregate publication bytes')
    with p.open('xb') as f:f.write(data)
    guard();return dict(path=str(p),sha256=sha(data))

def features(manifest,guard):
    a.require(len(manifest['days'])==21 and [d['session'] for d in manifest['days']]==list(a.TRAIN+a.EVAL),'fixed21 date ordering')
    a.require(sum(len(d['expected_available_ids']) for d in manifest['days'][:10])==4560 and sum(len(d['expected_available_ids']) for d in manifest['days'][10:])==4994,'manifest fixed4560/4994 before cache reads')
    rows=[];exclusions=[];seen=set()
    for day in manifest['days']:
        report=read(day['feature'],guard);meta=read(day['metadata'],guard)
        a.require(report['native_ordinary_parity'] is True and report['source_sha256']==day['raw_sha256'],'accepted ordinary/source parity')
        native={n['origin_id']:n for n in report['ordinary']['origins']}
        side={n['origin_id']:n for n in report['recorded_sidecar']['observed_origins']}
        mm=meta['master'];records=meta['endpoints']['records'];eligible=[]
        a.require(len(mm)==462 and len({(m['symbol'],m['scheduled_ns']) for m in mm})==462,'complete master slots')
        for m in mm:
            a.require(m['session']==day['session'] and m['symbol'] in a.NAMES,'master day/name')
        for m in records:
            if m['status']!='AVAILABLE_METADATA_ONLY':
                exclusions.append({k:m[k] for k in ('candidate_id','symbol','session','status')});continue
            a.require(m['native_id'] in native and m['native_id'] in side,'exact feature join')
            r=a.feature_row(native[m['native_id']],side[m['native_id']],m)
            master=[x for x in mm if x.get('candidate_id')==r['candidate_id']]
            a.require(len(master)==1 and master[0]['feature_eligible'] is True
                      and master[0]['context_sha256']==r['context_sha256'],'master/context binding')
            a.require(r['candidate_id'] not in seen and r['session']==day['session'],'unique expected day/id')
            seen.add(r['candidate_id']);rows.append(r);eligible.append(r['candidate_id'])
        expected=day['expected_available_ids']
        a.require(eligible==expected,'unchanged ordered common cohort')
    a.require(sum(r['session'] in a.TRAIN for r in rows)==4560 and sum(r['session'] in a.EVAL for r in rows)==4994,'fixed4560/4994')
    return rows,exclusions

def values(manifest,rows,stage,guard):
    dates=a.TRAIN if stage=='TRAIN' else a.EVAL
    rr={r['candidate_id']:r for r in rows if r['session'] in dates};out={}
    for day in manifest['days']:
        if day['session'] not in dates:continue  # before resolving/opening value path
        spec=day[stage+'_values'];data=read(spec,guard)
        a.require(data['session']==day['session'],'value file day')
        expected={k for k,r in rr.items() if r['session']==day['session']}
        a.require(len(data['values'])==len(expected) and {v['candidate_id'] for v in data['values']}==expected,'complete paired daily values')
        for v in data['values']:
            k=v['candidate_id'];a.require(k not in out,'duplicate target');out[k]=a.target(v,rr[k],stage)
    a.require(set(out)==set(rr),'complete stage target set');return out

def train(manifest,out,guard):
    rows,exclusions=features(manifest,guard)
    features_spec=write(out,'features.json',dict(rows=rows,endpoint_exclusions=exclusions),guard)
    yy=values(manifest,rows,'TRAIN',guard)
    train_rows=[r for r in rows if r['session'] in a.TRAIN]
    models=a.fit(train_rows,yy);a.validate_models(models)
    model_spec=write(out,'fourteen-models.json',models,guard)
    reopened=read(model_spec,guard);a.validate_models(reopened)
    a.require(a.canonical(reopened)==a.canonical(models),'14 model reopen equality')
    bindings=write(out,'TRAIN-magnitudes.json',yy,guard)
    freeze=dict(schema='delta.activity-magnitude.freeze.v1',status='COMPLETE_REOPENED14',configuration_sha256=a.digest(a.CONFIG),
                features=features_spec,models=model_spec,train_targets=bindings,manifest_sha256=a.digest(manifest),
                frozen_utc=datetime.now(timezone.utc).isoformat(),EVAL_value_access_in_runner=False,
                historical_live_issuance=False,access_deviation=manifest['access_deviation'],
                counts=dict(TRAIN=4560,EVAL_feature_only=4994,models=14),resources=guard.receipt())
    return write(out,'model-freeze.json',freeze,guard)

def evaluate(manifest,out,freeze_spec,guard,disposition_spec=None):
    freeze=read(freeze_spec,guard)
    a.require(freeze['status']=='COMPLETE_REOPENED14' and freeze['manifest_sha256']==a.digest(manifest)
              and freeze['configuration_sha256']==a.digest(a.CONFIG),'exact complete TRAIN freeze')
    a.require(disposition_spec is not None,'EVAL access deviation awaits disposition')
    disposition=read(disposition_spec,guard)
    a.require(disposition.get('action')=='ALLOW_FIXED_EVAL_AFTER_DISCLOSED_SCHEMA_ACCESS'
              and disposition.get('model_freeze_sha256')==freeze_spec['sha256']
              and disposition.get('manifest_sha256')==a.digest(manifest),'exact deviation continuation receipt')
    models=read(freeze['models'],guard);a.validate_models(models)
    allrows=read(freeze['features'],guard)['rows'];rows=[r for r in allrows if r['session'] in a.EVAL]
    yy=values(manifest,rows,'EVAL',guard)  # only after complete frozen model validation
    result=a.score(rows,yy,models);result['model_freeze']=freeze_spec;result['resources']=guard.receipt()
    qualify_deviation(result,manifest,disposition_spec)
    return write(out,'score.json',result,guard)

def qualify_deviation(result,manifest,disposition_spec):
    result['qualification']='CONTAMINATED_DESCRIPTIVE_REUSED_APRIL_DEVELOPMENT_ONLY'
    result['access_order_limitation']={
        'established_event':'COMPLETE_APRIL16_EVAL_VALUE_JSON_PARSED_BEFORE_NEW14_MODEL_FREEZE',
        'original_author_record':manifest['access_deviation'],
        'history_evidence_limit':'KEYS_COUNTS_ONLY_AND_NO_INFLUENCE_ARE_AUTHOR_ACCOUNT_NOT_INDEPENDENTLY_PROVEN',
        'clean_prospective_or_untouched_evidence':False,
        'continuation_receipt':disposition_spec,
        'permitted_use':'ONE_FIXED_DESCRIPTIVE_EVAL_NO_REFIT_SELECTION_OR_ECONOMIC_CLAIM'}
    return result


def main():
    p=argparse.ArgumentParser();p.add_argument('stage',choices=['TRAIN','EVAL']);p.add_argument('--manifest',required=True)
    p.add_argument('--manifest-sha256',required=True);p.add_argument('--output',required=True);p.add_argument('--freeze');p.add_argument('--freeze-sha256');p.add_argument('--eval-disposition');p.add_argument('--eval-disposition-sha256')
    args=p.parse_args();out=Path(args.output);out.mkdir(exist_ok=False,parents=False);g=Guard(out)
    try:
        manifest=read(dict(path=args.manifest,sha256=args.manifest_sha256),g)
        a.require(manifest['configuration_sha256']==a.digest(a.CONFIG),'exact fixed config')
        result=train(manifest,out,g) if args.stage=='TRAIN' else evaluate(manifest,out,dict(path=args.freeze,sha256=args.freeze_sha256),g,
            dict(path=args.eval_disposition,sha256=args.eval_disposition_sha256) if args.eval_disposition else None)
        g();print(json.dumps(result))
    except BaseException as e:
        # Evidence is best-effort and never claims COMPLETE after a failed run.
        try:
            (out/'failure.json').write_bytes(a.canonical(dict(status='FAILED_NO_RESULT',type=type(e).__name__,reason=str(e),resources=g.receipt())))
        except BaseException:pass
        raise
if __name__=='__main__':main()
