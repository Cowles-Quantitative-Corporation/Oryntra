"""Disabled artificial reference-mean/full-risk entry point; no import-time data I/O.

Source authentication is snapshot/boundary validation, never continuous custody.
"""
import argparse
from copy import deepcopy
from dataclasses import asdict
import importlib
import json
from pathlib import Path
import sys
from backend.delta.research import reference_replay as rr
from backend.delta.research import mean_sensitivity as ms
from backend.delta.research import qualify_reference as q

SCHEMA='DELTA_MEAN_SENSITIVITY_REPLAY_V1'
OPERATION='ARTIFICIAL_REFERENCE_MEAN_FULL_RISK_APRIL'
LIMITS=rr.LIMITS
SQL=rr.SQL
BANK_ALIASES=ms.ALIASES
ALIASES=BANK_ALIASES|{'control_economic','control_receipt','cache','cache_receipt','benchmark_prices','benchmark_receipt','benchmark_returns'}
require=q.require

def runtime_pins():
    modules={'runner':sys.modules[__name__],'sensitivity':ms,'replay_helpers':rr,'publication':q,
             'reference_consumer':rr.rc,'reference_ledger':rr.ledger,'authentication':rr.sr}
    return {k:q.digest(Path(m.__file__).read_bytes())for k,m in modules.items()}

def check_config(config):
    require(type(config)is dict and set(config)=={'schema','operation','scope','sources','code_sources','code_root',
        'bank_context','days','symbols','expected_events','expected_anchors','resources','output_directory'},'CONFIG_SCHEMA')
    require(config['schema']==SCHEMA and config['operation']==OPERATION,'CONFIG_OPERATION')
    require(config['scope']in ('SYNTHETIC_FIXTURE_ONLY','CONSUMED_APRIL_SENSITIVITY_ONLY'),'CONFIG_SCOPE')
    require(config['resources']==LIMITS and all(type(v)is int for v in config['resources'].values()),'RESOURCE_CONFIG')
    require(type(config['sources'])is dict and set(config['sources'])==ALIASES,'SOURCE_CLOSURE')
    for s in config['sources'].values():rr._spec(s)
    require(type(config['code_sources'])is list and config['code_sources'],'CODE_CLOSURE')
    for s in config['code_sources']:rr._spec(s)
    require(len({s['path']for s in config['code_sources']})==len(config['code_sources']),'DUPLICATE_CODE')
    for field in ('code_root','output_directory'):
        require(type(config[field])is str and Path(config[field]).is_absolute(),'ABSOLUTE_PATH')
    days=config['days'];symbols=config['symbols'];ctx=config['bank_context']
    require(type(days)is list and days and days==sorted(set(days)),'DAYS')
    require(type(symbols)is list and len(symbols)==len(set(symbols))==7,'SYMBOLS')
    require(type(config['expected_events'])is int and config['expected_events']>0 and config['expected_anchors']==7*len(days),'EXPECTED_COUNTS')
    require(type(ctx)is dict and set(ctx)=={'scope','source_specs','evaluation_sessions','symbols'}
        and ctx['source_specs']=={k:config['sources'][k]for k in BANK_ALIASES}
        and ctx['evaluation_sessions']==days and ctx['symbols']==symbols,'BANK_CONTEXT')
    if config['scope']=='CONSUMED_APRIL_SENSITIVITY_ONLY':
        require(len(days)==21 and all(d.startswith('2026-04-')for d in days)and config['expected_events']==3525815
            and config['expected_anchors']==147 and ctx['scope']=='PINNED_FULL_R46_BANK','FIXED_APRIL_SCOPE')
        for alias,pin in {'control_economic':'d618ad9542012f30b422de766e2fb5005c86b4953e879572b36b5e16bbc894fc',
            'control_receipt':'a1f468715d75a6644da4b23423a641458f5e00886334895e4bd27a01205f20f6',
            'cache':'cc56d6cdc2969f86fdf075f9f5cf4f1d0324156fb4bce518c360dc89d1aa2bf6'}.items():
            require(config['sources'][alias]['sha256']==pin,'FIXED_CONTROL_CACHE')
    else:require(ctx['scope']=='SYNTHETIC_FIXTURE_ONLY','SYNTHETIC_BANK_SCOPE')
    return q.digest(q.encode(config))

def authorize(config,authority,pin):
    digest=check_config(config);pins=runtime_pins()
    require(type(authority)is dict and q.digest(q.encode(authority))==pin,'AUTHORITY_REQUIRED')
    require(set(authority)=={'schema','operation','scope','config_sha256','runtime_pins','one_attempt'}
        and authority==dict(schema=SCHEMA,operation=OPERATION,scope=config['scope'],config_sha256=digest,
                            runtime_pins=pins,one_attempt=True)and authority['one_attempt']is True,'AUTHORITY_BINDING')
    return digest,pins

def _load_runtime(config):
    native,*rest=rr._load_runtime(config)
    root=Path(config['code_root']);pins={Path(s['path']):s['sha256']for s in config['code_sources']}
    names={'full':'delta_conditional_open_proposal_v1','ledger':'delta_open_error_ledger_v1'}
    for k,n in names.items():require(pins.get(root/(n+'.py'))==ms.EXTRA_PINS[k],'FULL_CODE_CLOSURE')
    before=list(sys.path);sys.path.insert(0,str(root))
    try:
        modules={k:importlib.import_module(n)for k,n in names.items()}
        for k,n in names.items():require(Path(modules[k].__file__)==root/(n+'.py'),'FULL_LOADED_ROOT')
        b=ms.SensitivityBindings(native,**modules);b.verify()
        for module in tuple(sys.modules.values()):
            filename=getattr(module,'__file__',None)
            if filename and Path(filename).suffix=='.py'and Path(filename).is_relative_to(root):
                path=Path(filename);require(path in pins and q.digest(path.read_bytes())==pins[path],'FULL_TRANSITIVE_CODE_CLOSURE')
        return b,*rest
    finally:sys.path[:]=before

def comparison(report,control,days):
    base=rr.comparison(report,control,days)
    base.update(estimand='ARTIFICIAL_REFERENCE_MEAN_FULL_RISK_CONDITIONAL_INTERVENTION',
        artificial_uncertainty_proxy=True,calibrated_reference_risk=False,
        qualifications='Consumed April; artificial full-bank proxy; no fresh validation, additive Sharpe, calibrated reference risk or alpha claim.')
    if base['aggregate_performance']is None:
        base['primary_terminal_liquidation_difference_nano']=None
    else:
        base['status']='COMPLETE_ARTIFICIAL_MEAN_SENSITIVITY_COMPARISON'
        base['aggregate_performance']['sensitivity']=base['aggregate_performance'].pop('reference')
        for row in base['paired_daily_values']:row['sensitivity_closing']=row.pop('reference_closing')
        last=base['paired_daily_values'][-1]
        base['primary_terminal_liquidation_difference_nano']=last['sensitivity_closing']['liquidation']-last['full_closing']['liquidation']
    return base

def _failure(out,exc,guard):
    if getattr(exc,'_publication_committed',False):return
    try:
        uncertain=isinstance(exc,q.PublicationUncertain)or getattr(exc,'_publication_uncertain',False)
        body=q.encode(dict(schema=SCHEMA,status='PUBLICATION_UNCERTAIN'if uncertain else 'FAILED_EXECUTION_NO_COMPLETE',
            reason=(str(exc)if isinstance(exc,(q.QualificationFailure,rr.sr.SourceReadError))else type(exc).__name__)[:128],
            sample=guard.last,no_retry=True,authority='result.json only'))
        require(len(body)<=8192,'FAILURE_SIZE')
        try:size=sum(p.lstat().st_size for p in out.iterdir())
        except OSError:require(guard.failure_room_reserved,'FAILURE_FOOTPRINT_UNCERTAIN')
        else:require(size+len(body)<=guard.limits['output_bytes'],'FAILURE_OUTPUT_LIMIT')
        with(out/'failure.json').open('xb',buffering=0)as f:q.write_all(f,body)
    except BaseException:pass  # Best effort; never replace the original exception.


def run(config,authority=None,*,expected_authority_sha256=None,enabled=False):
    require(enabled is True,'SENSITIVITY_REPLAY_DISABLED')
    config,authority=deepcopy((config,authority));config_sha,pins=authorize(config,authority,expected_authority_sha256)
    out=Path(config['output_directory']);parent=out.parent
    if config['scope']!='SYNTHETIC_FIXTURE_ONLY':
        allowed=Path(__file__).resolve().parents[4]/'local-artifacts'
        require(parent.resolve()==allowed.resolve()and not parent.is_symlink(),'PRIVATE_DIRECT_OUTPUT')
    require(not out.is_symlink()and not out.exists(),'FRESH_EXCLUSIVE_OUTPUT')
    out.mkdir(exist_ok=False);guard=q.Guard(out,config['resources']);db=None
    try:
        guard.check('authorized');source_receipts={};raw={};code_receipts=[]
        for i,spec in enumerate(config['code_sources']):
            _,receipt=rr._authenticate(spec,guard,'code:'+str(i));code_receipts.append(receipt)
        for alias,spec in config['sources'].items():
            value,receipt=rr._authenticate(spec,guard,alias,collect=alias not in {'cache','benchmark_prices','benchmark_returns'})
            source_receipts[alias]=receipt
            if value is not None:raw[alias]=value
        control=q.decode(raw['control_economic']);receipt=q.decode(raw['control_receipt']);cache=q.decode(raw['cache_receipt'])
        require(receipt['economic_sha256']==config['sources']['control_economic']['sha256']
            and receipt['preserved_inputs_equal_before_after']is True and receipt['new_model_or_risk_policy']is False,'CONTROL_RECEIPT')
        require(cache['cache_sha256']==config['sources']['cache']['sha256']and cache['quotes']==config['expected_events'],'CACHE_RECEIPT')
        bindings,decode_event,typed,benchmark,manifest=_load_runtime(config)
        bank=ms.FullResidualBank(bindings,{k:raw[k]for k in BANK_ALIASES},config['bank_context'],enabled=True)
        consumer=ms.MeanOnlySensitivity(bank,enabled=True)
        all_days=sorted(consumer.calendar);settlement={d:all_days[all_days.index(d)+1]for d in config['days']}
        scenario=ms.make_sensitivity_scenario(consumer,settlement,config['days'],source_manifest_sha256=manifest,enabled=True)
        sessions=typed(bindings.native.core,consumer.calendar);path=Path(config['sources']['cache']['path']);db=rr.open_database(path)
        db.set_progress_handler(lambda:(guard.check('sqlite')or 0),10000)
        cursor=db.execute(SQL);columns=[x[0]for x in cursor.description]
        decoder_spec=next(s for s in config['code_sources']if Path(s['path']).name=='run_april_daily_comparison.py')
        stream=rr.Stream(config,columns,decoder_spec['sha256']);prior=None
        for row in cursor:
            event=decode_event(bindings.native.core,row);stream.accept(row,event)
            if prior is not None and event.session!=prior:scenario.close_day(prior)
            scenario.step(consumer,event,sessions=sessions);prior=event.session
            if stream.count%1000==0:guard.check('stream:'+str(stream.count))
        stream_receipt=stream.finish();scenario.close_day(prior)
        rr.sidecars(path);db.close();db=None;rr.sidecars(path)
        anchors=rr.verify_anchors(consumer,control,config['expected_anchors'])
        guard.check('before_benchmark');bench,evaluation=benchmark(config['days']);scenario.evaluation_benchmark=evaluation
        report=scenario.result(bench);paired=comparison(report,control,config['days']);guard.check('after_report')
        for i,spec in enumerate(config['code_sources']):
            _,final=rr._authenticate(spec,guard,'final_code:'+str(i))
            require(tuple(final[k]for k in ('device','inode','snapshot_mtime_ns','snapshot_ctime_ns'))==tuple(code_receipts[i][k]for k in ('device','inode','snapshot_mtime_ns','snapshot_ctime_ns')),'CODE_IDENTITY_CHANGED')
        for alias,spec in config['sources'].items():
            _,final=rr._authenticate(spec,guard,'final:'+alias)
            first=source_receipts[alias]
            require(tuple(final[k]for k in ('device','inode','snapshot_mtime_ns','snapshot_ctime_ns'))==tuple(first[k]for k in ('device','inode','snapshot_mtime_ns','snapshot_ctime_ns')),'SOURCE_IDENTITY_CHANGED')
        require(runtime_pins()==pins and check_config(config)==config_sha,'RUNTIME_CONFIG_CHANGED');rr.sidecars(path)
        guard.check('before_publish')
        result=dict(schema=SCHEMA,status=paired['status'],report=report,comparison=paired,stream=stream_receipt,
            anchors_matched=anchors,source_receipts=source_receipts,config_sha256=config_sha,runtime_pins=pins,
            resource_sample=guard.last,resource_limits='Cooperative/sampled; final link may finish later; no hard peak or crash guarantee.',
            historical_scope='CONSUMED_CONDITIONAL_DEVELOPMENT',
            source_guarantee='Snapshot/boundary authentication; not continuous custody.',
            artificial_uncertainty_proxy=True,calibrated_reference_risk=False,live_authorized=False,external_paper_authorized=False)
        return q.publish(out,result,guard)
    except BaseException as exc:
        if db is not None:
            try:db.close()
            except BaseException as cleanup:exc.add_note('SQLITE_CLOSE_FAILED:'+type(cleanup).__name__)
        _failure(out,exc,guard);raise

def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config',required=True);parser.add_argument('--authority',required=True)
    parser.add_argument('--authority-sha256',required=True);parser.add_argument('--execute',action='store_true')
    args=parser.parse_args(argv)
    require(args.execute,'SENSITIVITY_REPLAY_DISABLED')
    # Only these caller-designated control documents are read before authorization.
    config=q.decode(Path(args.config).read_bytes());authority=q.decode(Path(args.authority).read_bytes())
    result=run(config,authority,expected_authority_sha256=args.authority_sha256,enabled=True)
    print(json.dumps(dict(status=result['status'],events=result['stream']['events'],anchors=result['anchors_matched'])))


if __name__=='__main__':main()
