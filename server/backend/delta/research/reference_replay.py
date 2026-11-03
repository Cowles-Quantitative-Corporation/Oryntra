"""Disabled reference-only consumed-development runner; no import-time source I/O.

An exact reviewed receipt is required before source reads. This is conditional
forecast-plus-own-risk policy research, never a live or external-paper adapter.
"""
import argparse
from copy import deepcopy
from dataclasses import asdict
import hashlib
import importlib
import json
import os
from pathlib import Path
import sqlite3
import stat
import sys
import time

from backend.delta.research import qualify_reference as q
from backend.delta.research import reference_consumer as rc
from backend.delta.research import reference_ledger as ledger
from backend.delta.volume import source_reader as sr

SCHEMA='DELTA_REFERENCE_REPLAY_V1'
OPERATION='RECORDED_REFERENCE_OWN_RISK_APRIL_REPLAY'
LIMITS=dict(wall_seconds=360,rss_bytes=512*1024**2,output_bytes=128*1024**2,disk_reserve_bytes=2*1024**3)
SQL='select * from quotes order by source_index'
QUAL_ALIASES=('attempts','errors','outcomes','r46_result','r46_audit')
ALIASES={'qualification','qualification_config','records','calendar','control_economic','control_receipt',
         'cache','cache_receipt','benchmark_prices','benchmark_receipt','benchmark_returns'}|{'qualification_'+a for a in QUAL_ALIASES}
MODULES=dict(projection='delta_open_entry_anchor_projection_v1',fees='delta_broker_fee_admission_robust_mean_v1',
    policy='delta_broker_fee_policy_v1',family='run_new7_conditional_April_family_v1',account='delta_multi_asset_account_broker_v1')
require=q.require


def runtime_pins():
    return {name:q.digest(Path(module.__file__).read_bytes()) for name,module in
            [('runner',sys.modules[__name__]),('consumer',rc),('ledger',ledger),('publication',q),('authentication',sr)]}


def _spec(value):
    require(type(value)is dict and set(value)=={'path','bytes','sha256'},'SOURCE_SPEC')
    require(type(value['path'])is str and Path(value['path']).is_absolute(),'SOURCE_PATH')
    require(type(value['bytes'])is int and value['bytes']>0,'SOURCE_SIZE')
    require(type(value['sha256'])is str and len(value['sha256'])==64
            and all(c in '0123456789abcdef'for c in value['sha256']),'SOURCE_HASH_SPEC')


def check_config(config):
    require(type(config)is dict and set(config)=={'schema','operation','scope','sources','code_sources','code_root',
        'acceptance','days','symbols','expected_events','expected_anchors','resources','output_directory'},'CONFIG_SCHEMA')
    require(config['schema']==SCHEMA and config['operation']==OPERATION,'CONFIG_OPERATION')
    require(config['scope']in ('SYNTHETIC_FIXTURE_ONLY','CONSUMED_APRIL_REFERENCE_ONLY'),'CONFIG_SCOPE')
    require(config['resources']==LIMITS and all(type(v)is int for v in config['resources'].values()),'RESOURCE_CONFIG')
    require(type(config['sources'])is dict and set(config['sources'])==ALIASES,'SOURCE_CLOSURE')
    for value in config['sources'].values():_spec(value)
    require(type(config['code_sources'])is list and config['code_sources'],'CODE_CLOSURE')
    for value in config['code_sources']:_spec(value)
    require(len({v['path']for v in config['code_sources']})==len(config['code_sources']),'DUPLICATE_CODE')
    require(type(config['code_root'])is str and Path(config['code_root']).is_absolute(),'CODE_ROOT')
    require(type(config['output_directory'])is str and Path(config['output_directory']).is_absolute(),'OUTPUT_PATH')
    require(type(config['days'])is list and config['days']==sorted(set(config['days']))and config['days'],'DAYS')
    require(type(config['symbols'])is list and len(config['symbols'])==len(set(config['symbols']))==7,'SYMBOLS')
    require(type(config['expected_events'])is int and config['expected_events']>0
            and config['expected_anchors']==7*len(config['days']),'EXPECTED_COUNTS')
    a=config['acceptance'];require(type(a)is dict and a.get('evaluation_sessions')==config['days']
        and a.get('symbols')==config['symbols']and a.get('qualification_sha256')==config['sources']['qualification']['sha256']
        and a.get('config_sha256')==config['sources']['qualification_config']['sha256'],'QUALIFICATION_CONTEXT')
    if config['scope']=='CONSUMED_APRIL_REFERENCE_ONLY':
        require(len(config['days'])==21 and all(d.startswith('2026-04-')for d in config['days'])
            and config['expected_events']==3525815 and config['expected_anchors']==147,'FIXED_APRIL_SCOPE')
        require(config['sources']['control_economic']['sha256']=='d618ad9542012f30b422de766e2fb5005c86b4953e879572b36b5e16bbc894fc'
            and config['sources']['control_receipt']['sha256']=='a1f468715d75a6644da4b23423a641458f5e00886334895e4bd27a01205f20f6'
            and config['sources']['cache']['sha256']=='cc56d6cdc2969f86fdf075f9f5cf4f1d0324156fb4bce518c360dc89d1aa2bf6'
            and config['sources']['qualification']['sha256']=='435bad38c407a598dca370fbb8bfa3da0076839aa5c3f4e6e301bd706f3209e2'
            and config['sources']['qualification_config']['sha256']=='149bc64dcfe81017202d43184d9912eae61261ad48dab2365f5b8c5401856aab','FIXED_CONTROL_CACHE_QUALIFICATION')
    return q.digest(q.encode(config))


def authorize(config,authority,pin):
    config_sha=check_config(config);pins=runtime_pins()
    require(type(authority)is dict and q.digest(q.encode(authority))==pin,'AUTHORITY_REQUIRED')
    require(set(authority)=={'schema','operation','scope','config_sha256','runtime_pins','one_attempt'}
        and authority['schema']==SCHEMA and authority['operation']==OPERATION and authority['scope']==config['scope']
        and authority['config_sha256']==config_sha and authority['runtime_pins']==pins
        and authority['one_attempt']is True,'AUTHORITY_BINDING')
    return config_sha,pins


def _authenticate(spec,guard,alias,collect=False):
    # Reuse consumed-byte hashing for JSON and the repaired pathname/deadline
    # authenticator for the final snapshot. Neither hands SQLite a live fd.
    before=Path(spec['path']).lstat()
    raw=q.read_source(spec,guard,alias)if collect else None
    def probe():
        guard.check('authenticate:'+alias)
        return sr.ResourceSample(time.monotonic_ns(),guard.last['observed_process_peak_rss_bytes'],
                                 __import__('shutil').disk_usage(guard.out).free)
    receipt=sr.authenticate_source(Path(spec['path']),expected_sha256=spec['sha256'],expected_bytes=spec['bytes'],
        deadline_ns=int((guard.start+guard.limits['wall_seconds'])*10**9),probe=probe,enabled=True)
    require((before.st_dev,before.st_ino)==(receipt.device,receipt.inode),'SOURCE_IDENTITY_BETWEEN_READS')
    if raw is not None:require(q.digest(raw)==spec['sha256'],'CONSUMED_BYTES')
    return raw,dict(asdict(receipt),snapshot_mtime_ns=before.st_mtime_ns,snapshot_ctime_ns=before.st_ctime_ns)


def sidecars(path):
    for suffix in ('-wal','-shm','-journal'):
        candidate=Path(str(path)+suffix)
        try:candidate.lstat()
        except FileNotFoundError:continue
        raise q.QualificationFailure('SQLITE_SIDECAR_PRESENT')


def open_database(path):
    sidecars(path)
    db=sqlite3.connect(path.as_uri()+'?mode=ro',uri=True)
    try:
        db.execute('pragma query_only=on');db.execute('pragma cache_size=-8192')
        require(db.execute('pragma journal_mode').fetchone()[0].lower()=='delete','SQLITE_JOURNAL_MODE')
        sidecars(path);db.row_factory=sqlite3.Row
        return db
    except BaseException:
        db.close();raise


class Stream:
    def __init__(self,config,columns,decoder_sha):
        self.days=config['days'];self.symbols=config['symbols'];self.expected=config['expected_events']
        self.columns=tuple(columns);self.count=0;self.last_index=-1;self.last_clock=-1;self.prior=None;self.completed=[]
        header=dict(schema=SCHEMA,cache_sha256=config['sources']['cache']['sha256'],sql=SQL,
                    columns=list(columns),decoder_sha256=decoder_sha)
        self.raw=hashlib.sha256(q.encode(header));self.events=hashlib.sha256(q.encode(header))
    @staticmethod
    def append(h,value):
        payload=q.encode(value);h.update(len(payload).to_bytes(8,'big'));h.update(payload)
    def accept(self,row,event):
        require(type(event.source_index)is int and event.source_index>self.last_index,'STREAM_INDEX_ORDER')
        require(event.available_ns>=self.last_clock,'STREAM_CLOCK_ORDER')
        require(event.symbol in self.symbols and event.session in self.days,'STREAM_ROSTER')
        require(event.event_ns<=event.available_ns,'STREAM_FUTURE_EVENT')
        if self.prior is not None and event.session!=self.prior:
            self.completed.append(self.prior)
            require(self.days[:len(self.completed)]==self.completed,'STREAM_SESSION_ORDER')
        elif self.prior is None:require(event.session==self.days[0],'STREAM_INITIAL_SESSION')
        require(event.session not in self.completed,'STREAM_SESSION_REENTRY')
        self.append(self.raw,[row[k]for k in self.columns])
        self.append(self.events,asdict(event))
        self.last_index=event.source_index;self.last_clock=event.available_ns;self.prior=event.session;self.count+=1
        require(self.count<=self.expected,'STREAM_EXTRA_ROWS')
    def finish(self):
        require(self.prior is not None and self.count==self.expected,'STREAM_TRUNCATED')
        require(self.completed+[self.prior]==self.days,'STREAM_SESSION_COMPLETENESS')
        return dict(events=self.count,sessions=self.completed+[self.prior],raw_row_sha256=self.raw.hexdigest(),
            decoded_event_sha256=self.events.hexdigest(),digest_scope='NEW_RUN_PROVENANCE_NOT_PREEXISTING_CONTROL_DIGEST')


def verify_anchors(consumer,control,expected):
    rows=control['anchor_manifest'];require(len(rows)==expected,'CONTROL_ANCHOR_COUNT')
    old={(r['session'],r['symbol']):r for r in rows}
    require(len(old)==expected and set(old)==set(consumer.candidates),'ANCHOR_ROSTER')
    for key,(_,forecast,_,_,_)in consumer.candidates.items():
        r=old[key];a=forecast.anchor
        pairs={'base_record_sha256':forecast.base_record_sha256,'source_index':a.source_index,
            'source_quote_identity':a.source_quote_identity,'anchor_price_nano':a.price_nano,
            'anchor_available_ns':a.observed_at_ns,'forecast_information_cutoff_ns':forecast.forecast_information_cutoff_ns,
            'modeled_forecast_available_ns':forecast.modeled_forecast_available_ns,'target_exit_ns':forecast.target_exit_ns}
        require(all(r[k]==v for k,v in pairs.items()),'ANCHOR_SOURCE_CLOCK_PRICE')
        require(forecast.identity!=r['projection_identity'],'DISTINCT_REFERENCE_IDENTITY')
    return expected


def comparison(report,control,days):
    require([r['session']for r in report['daily']]==days,'REPORT_DAY_ROSTER')
    if report['missing_recorded_days']:
        require(not report['metrics']and not report.get('evaluation_metrics'),'PARTIAL_PERIOD_METRICS')
        return dict(status='COMPARISON_NOT_EVALUABLE_MISSING_MARKS',aggregate_performance=None,
                    missing_recorded_days=report['missing_recorded_days'])
    require(report['status']=='COMPLETE_CONDITIONAL_DEVELOPMENT_REPLAY'
            and not control['missing_recorded_days']and [r['session']for r in control['daily']]==days,'COMPLETE_PAIRED_MARKS')
    pairs=[]
    for left,right in zip(report['daily'],control['daily']):
        require(all(x.get(b)is not None for x in (left,right)for b in ('opening','closing')),'COMPLETE_PAIRED_MARKS')
        pairs.append(dict(session=left['session'],reference_closing={m:left['closing'][m]['value_nano']for m in ('mid','liquidation')},
            full_closing={m:right['closing'][m]['value_nano']for m in ('mid','liquidation')}))
    return dict(status='COMPLETE_DESCRIPTIVE_POLICY_COMPARISON',aggregate_performance=dict(
        reference=report['metrics'],full=control['metrics']),paired_daily_values=pairs,
        estimand='FORECAST_PLUS_OWN_RISK_POLICY_NOT_MEAN_ONLY_ALPHA',
        qualifications='Consumed conditional April; n21 dependent days; no fresh OOS, statistical adoption or alpha claim. Exposure differs endogenously.')


def _load_runtime(config):
    root=Path(config['code_root']);pins={Path(s['path']):s['sha256']for s in config['code_sources']}
    for alias,name in MODULES.items():require(pins.get(root/(name+'.py'))==rc.PINS[alias],'FIXED_NATIVE_BINDING')
    require(root/'run_april_daily_comparison.py'in pins and root/'daily_gamma_inputs.py'in pins
            and root/'run_delta_open_target_causal_April_replay_v1.py'in pins,'NATIVE_HELPER_CLOSURE')
    before=list(sys.path);sys.path.insert(0,str(root));sys.dont_write_bytecode=True
    try:
        modules={a:importlib.import_module(n)for a,n in MODULES.items()}
        cache_module=importlib.import_module('delta_development_cache')
        require(Path(cache_module.__file__)==root/'delta_development_cache.py','LOADED_CODE_ROOT')
        for alias,name in MODULES.items():require(Path(modules[alias].__file__)==root/(name+'.py'),'LOADED_CODE_ROOT')
        require(Path(modules['family'].old.__file__)==root/'run_april_daily_comparison.py','LOADED_DECODER_ROOT')
        core,_=cache_module.load_core()
        native=importlib.import_module('run_delta_open_target_causal_April_replay_v1')
        calendar=importlib.import_module('daily_gamma_inputs')
        require(Path(native.__file__)==root/'run_delta_open_target_causal_April_replay_v1.py'
                and Path(calendar.__file__)==root/'daily_gamma_inputs.py','LOADED_HELPER_ROOT')
        bindings=rc.FrozenBindings(core=core,**modules);bindings.verify()
        for module in tuple(sys.modules.values()):
            filename=getattr(module,'__file__',None)
            if filename and Path(filename).suffix=='.py'and Path(filename).is_relative_to(root):
                path=Path(filename);require(path in pins and q.digest(path.read_bytes())==pins[path],'TRANSITIVE_CODE_CLOSURE')
        require(Path(core.__file__)in pins,'OFFICIAL_CORE_CLOSURE')
        for alias,name in [('benchmark_prices','SPY-adjusted-April.csv'),('benchmark_receipt','receipt.json'),('benchmark_returns','matched-returns.csv')]:
            require(Path(config['sources'][alias]['path'])==root/'spy-April-adjusted-benchmark-v1'/name,'BENCHMARK_HELPER_PATH')
        return bindings,native.OLD.row_event,calendar.typed_session_calendar,native.benchmark,native.OLD.MANIFEST
    finally:sys.path[:]=before


def _failure(out,exc,guard):
    if getattr(exc,'_publication_committed',False):return
    try:
        uncertain=isinstance(exc,q.PublicationUncertain)or getattr(exc,'_publication_uncertain',False)
        body=q.encode(dict(schema=SCHEMA,status='PUBLICATION_UNCERTAIN'if uncertain else 'FAILED_EXECUTION_NO_COMPLETE',
            reason=(str(exc)if isinstance(exc,(q.QualificationFailure,sr.SourceReadError))else type(exc).__name__)[:128],
            sample=guard.last,no_retry=True,authority='result.json only'))
        require(len(body)<=8192,'FAILURE_SIZE')
        try:size=sum(p.lstat().st_size for p in out.iterdir())
        except OSError:require(guard.failure_room_reserved,'FAILURE_FOOTPRINT_UNCERTAIN')
        else:require(size+len(body)<=guard.limits['output_bytes'],'FAILURE_OUTPUT_LIMIT')
        with(out/'failure.json').open('xb',buffering=0)as f:q.write_all(f,body)
    except BaseException:pass  # Best effort; never replace the original exception.


def run(config,authority=None,*,expected_authority_sha256=None,enabled=False):
    require(enabled is True,'REFERENCE_REPLAY_DISABLED')
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
            _,receipt=_authenticate(spec,guard,'code:'+str(i));code_receipts.append(receipt)
        for alias,spec in config['sources'].items():
            value,receipt=_authenticate(spec,guard,alias,collect=alias not in {'cache','benchmark_prices','benchmark_returns'})
            source_receipts[alias]=receipt
            if value is not None:raw[alias]=value
        require(q.digest(raw['qualification_config'])==config['acceptance']['config_sha256'],'QUAL_CONFIG_HASH')
        qc=q.decode(raw['qualification_config'])
        require(set(qc['sources'])=={'records','calendar'}|set(QUAL_ALIASES),'QUALIFIED_SOURCE_ROSTER')
        for alias,spec in qc['sources'].items():
            own=config['sources'][alias if alias in ('records','calendar')else 'qualification_'+alias]
            require(spec==own and config['acceptance']['source_pins'][alias]==spec['sha256'],'QUALIFIED_SOURCE_CLOSURE')
        control=q.decode(raw['control_economic']);receipt=q.decode(raw['control_receipt']);cache=q.decode(raw['cache_receipt'])
        require(receipt['economic_sha256']==config['sources']['control_economic']['sha256']
            and receipt['preserved_inputs_equal_before_after']is True and receipt['new_model_or_risk_policy']is False,'CONTROL_RECEIPT')
        require(cache['cache_sha256']==config['sources']['cache']['sha256']and cache['quotes']==config['expected_events'],'CACHE_RECEIPT')
        bindings,decode_event,typed,benchmark,manifest=_load_runtime(config)
        consumer=rc.ReferenceConsumer(bindings,raw['qualification'],raw['records'],raw['calendar'],config['acceptance'],enabled=True)
        all_days=sorted(consumer.calendar);settlement={d:all_days[all_days.index(d)+1]for d in config['days']}
        scenario=rc.make_reference_scenario(consumer,settlement,config['days'],source_manifest_sha256=manifest,enabled=True)
        sessions=typed(bindings.core,consumer.calendar);path=Path(config['sources']['cache']['path']);db=open_database(path)
        db.set_progress_handler(lambda:(guard.check('sqlite')or 0),10000)
        cursor=db.execute(SQL);columns=[x[0]for x in cursor.description]
        decoder_spec=next(s for s in config['code_sources']if Path(s['path']).name=='run_april_daily_comparison.py')
        stream=Stream(config,columns,decoder_spec['sha256']);prior=None
        for row in cursor:
            event=decode_event(bindings.core,row);stream.accept(row,event)
            if prior is not None and event.session!=prior:scenario.close_day(prior)
            scenario.step(consumer,event,sessions=sessions);prior=event.session
            if stream.count%1000==0:guard.check('stream:'+str(stream.count))
        stream_receipt=stream.finish();scenario.close_day(prior)
        sidecars(path);db.close();db=None;sidecars(path)
        anchors=verify_anchors(consumer,control,config['expected_anchors'])
        guard.check('before_benchmark');bench,evaluation=benchmark(config['days']);scenario.evaluation_benchmark=evaluation
        report=scenario.result(bench);paired=comparison(report,control,config['days']);guard.check('after_report')
        for i,spec in enumerate(config['code_sources']):
            _,final=_authenticate(spec,guard,'final_code:'+str(i))
            require(tuple(final[k]for k in ('device','inode','snapshot_mtime_ns','snapshot_ctime_ns'))==tuple(code_receipts[i][k]for k in ('device','inode','snapshot_mtime_ns','snapshot_ctime_ns')),'CODE_IDENTITY_CHANGED')
        for alias,spec in config['sources'].items():
            _,final=_authenticate(spec,guard,'final:'+alias)
            first=source_receipts[alias]
            require(tuple(final[k]for k in ('device','inode','snapshot_mtime_ns','snapshot_ctime_ns'))==tuple(first[k]for k in ('device','inode','snapshot_mtime_ns','snapshot_ctime_ns')),'SOURCE_IDENTITY_CHANGED')
        require(runtime_pins()==pins and check_config(config)==config_sha,'RUNTIME_CONFIG_CHANGED');sidecars(path)
        guard.check('before_publish')
        result=dict(schema=SCHEMA,status=paired['status'],report=report,comparison=paired,stream=stream_receipt,
            anchors_matched=anchors,source_receipts=source_receipts,config_sha256=config_sha,runtime_pins=pins,
            resource_sample=guard.last,resource_limits='Cooperative/sampled; final link may finish later; no hard peak or crash guarantee.',
            historical_scope='CONSUMED_CONDITIONAL_DEVELOPMENT',live_authorized=False,external_paper_authorized=False)
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
    require(args.execute,'REFERENCE_REPLAY_DISABLED')
    # Only these caller-designated control documents are read before authorization.
    config=q.decode(Path(args.config).read_bytes());authority=q.decode(Path(args.authority).read_bytes())
    result=run(config,authority,expected_authority_sha256=args.authority_sha256,enabled=True)
    print(json.dumps(dict(status=result['status'],events=result['stream']['events'],anchors=result['anchors_matched'])))


if __name__=='__main__':main()
