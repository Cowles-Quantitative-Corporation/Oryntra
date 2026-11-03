"""Small invented bundles only; no historical execution receipt or data access."""
import copy
from contextlib import redirect_stdout
import importlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from test_reference_ledger import fixture
from backend.delta.research import qualify_reference as q
from backend.delta.research.reference_ledger import UNIT


class QualifierTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        objects,args=fixture();self.objects=objects
        self.sources={}
        for alias,value in objects.items():
            path=self.root/(alias+'.json');raw=q.encode(value);path.write_bytes(raw)
            self.sources[alias]=dict(path=str(path),bytes=len(raw),sha256=q.digest(raw))
        attempts=objects['attempts']['attempts'];calendar=objects['calendar']['sessions']
        cuts=[]
        for origin in args['origins']:
            due=next(a['due_session']for a in attempts if a['origin']==origin)
            cuts.append(dict(session=due,cutoff_ns=calendar[due]['close_ns']))
        rows=[]
        for c in cuts:
            for symbol in args['symbols']:
                selected=sorted([a for a in attempts if a['symbol']==symbol
                                 and calendar[a['due_session']]['close_ns']<=c['cutoff_ns']],
                                key=lambda a:(calendar[a['due_session']]['close_ns'],a['attempt_id']))
                values=[]
                for a in selected:
                    o=next(o for o in objects['outcomes']['outcomes']if o['attempt_id']==a['attempt_id'])
                    at=calendar[o['available_at_simulated'][:10]]['open_ns']
                    values.append('UNPARSED_FULL_RESIDUAL' if at<=c['cutoff_ns'] else None)
                rows.append(dict(session=c['session'],symbol=symbol,cutoff_ns=c['cutoff_ns'],
                                 attempt_identities=[a['attempt_id']for a in selected],residuals_exact=values,
                                 attempted=len(selected),observed=sum(v is not None for v in values),
                                 missing=sum(v is None for v in values),unit=UNIT))
        inputs={v['path']:v['sha256']for v in self.sources.values()}
        self.r46=dict(own_attempts=14,own_origins=2,inputs=inputs,rows=rows)
        self.write_proof('r46_result',self.r46)
        self.audit=dict(status='INDEPENDENT_R46_OWN_OPEN_LEDGER_ASOF_AUDIT_PASS_NOT_RISK_ADMISSION',
                        result_sha256=self.sources['r46_result']['sha256'],input_sha256=inputs)
        self.write_proof('r46_audit',self.audit)
        self.config=dict(schema=q.SCHEMA,operation=q.OPERATION,scope='SYNTHETIC_FIXTURE_ONLY',
                         sources=self.sources,origins=list(args['origins']),symbols=list(args['symbols']),
                         full_policy_id=args['full_policy_id'],full_model_source_sha256=args['full_model_source_sha256'],
                         cutoffs=cuts,resources=dict(q.LIMITS),output_directory=str(self.root/'output'))
        self.authorize()
        self.disk=patch.object(q.shutil,'disk_usage',return_value=type('Disk',(),{'free':9*1024**3})())
        self.disk.start();self.addCleanup(self.disk.stop);self.addCleanup(self.temp.cleanup)

    def write_proof(self,alias,obj):
        path=self.root/(alias+'.json');raw=q.encode(obj);path.write_bytes(raw)
        self.sources[alias]=dict(path=str(path),bytes=len(raw),sha256=q.digest(raw))

    def authorize(self):
        self.authority=dict(schema=q.SCHEMA,operation=q.OPERATION,scope='SYNTHETIC_FIXTURE_ONLY',
                            config_sha256=q.check_config(self.config),one_attempt=True,**q.runtime_pins())
        self.authority_pin=q.digest(q.encode(self.authority))

    def call(self):
        return q.qualify(self.config,self.authority,expected_authority_sha256=self.authority_pin,enabled=True)

    def failure(self,status='FAILED_NO_COMPLETE'):
        out=Path(self.config['output_directory'])
        self.assertFalse((out/'result.json').exists())
        body=json.loads((out/'failure.json').read_bytes());self.assertEqual(body['status'],status)
        return body

    def test_success_private_rows_and_all_original_id_null_masks(self):
        out=self.call();summary=out['summary']
        self.assertEqual(summary['row_count'],14);self.assertEqual(summary['scope_count'],14)
        self.assertFalse(summary['economic_metrics']);self.assertFalse(summary['portfolio_replay'])
        self.assertNotIn('ledger',summary);self.assertNotIn('memberships',summary)
        self.assertFalse(any(k in summary for k in ('actual','residual','price','target')))
        self.assertEqual(len(out['ledger']),14)
        self.assertEqual(summary['semantic_sha256'],q.digest(q.encode({k:v for k,v in out.items()if k!='summary'})))
        for row in out['memberships']:
            expected=next(r for r in self.r46['rows']if (r['session'],r['symbol'])==(row['session'],row['symbol']))
            self.assertEqual(row['original_ids'],expected['attempt_identities'])
            self.assertEqual(row['missing_original_ids'],[i for i,v in zip(expected['attempt_identities'],expected['residuals_exact'])if v is None])
        path=Path(self.config['output_directory'])
        self.assertEqual((path/'result.json').stat().st_ino,(path/'result.pending').stat().st_ino)
        self.assertFalse((path/'failure.json').exists())
        with self.assertRaises(FileExistsError):self.call()

    def test_authority_barrier_missing_changed_code_config_and_disabled(self):
        for mutation in ('none','code','component','config','receiptpin'):
            authority=copy.deepcopy(self.authority);pin=self.authority_pin
            if mutation=='none':authority=None
            elif mutation=='config':self.config['full_policy_id']='c'*64
            elif mutation=='receiptpin':pin='0'*64
            else:
                authority[mutation+'_sha256' if mutation=='component' else 'runner_sha256']='0'*64
                pin=q.digest(q.encode(authority))
            with self.subTest(mutation=mutation),patch.object(q,'read_source',side_effect=AssertionError('no source access')),patch.object(Path,'mkdir',side_effect=AssertionError('no output')):
                with self.assertRaises(q.QualificationFailure):
                    q.qualify(self.config,authority,expected_authority_sha256=pin,enabled=True)
            self.config['full_policy_id']='a'*64
        with patch.object(q,'authorize',side_effect=AssertionError('disabled')):
            with self.assertRaisesRegex(q.QualificationFailure,'DISABLED'):q.qualify(self.config)

    def test_import_help_config_only_do_not_touch_historical_paths(self):
        historical={v['path'] for v in self.sources.values()}
        real_open=Path.open;real_stat=Path.stat;real_lstat=Path.lstat
        def guard(method):
            def check(path,*args,**kwargs):
                if str(path) in historical:raise AssertionError('historical I/O')
                return method(path,*args,**kwargs)
            return check
        cfg=self.root/'config.json';raw=q.encode(self.config);cfg.write_bytes(raw)
        with patch.object(Path,'open',guard(real_open)),patch.object(Path,'stat',guard(real_stat)),patch.object(Path,'lstat',guard(real_lstat)):
            importlib.reload(q)
            with redirect_stdout(io.StringIO()),self.assertRaises(SystemExit)as e:q.main(['--help'])
            self.assertEqual(e.exception.code,0)
            self.assertEqual(q.main(['--check-config','--config',str(cfg),'--config-sha256',q.digest(raw)]),0)

    def test_source_hash_type_and_after_derivation_changes_reject(self):
        Path(self.sources['attempts']['path']).write_bytes(b'changed')
        with self.assertRaises(q.QualificationFailure),patch.object(q.component,'derive_reference_ledger',side_effect=AssertionError('no derive')):self.call()
        self.failure()

    def test_equal_size_hash_mismatch_symlink_and_wrong_audit_inputs(self):
        source=Path(self.sources['attempts']['path']);raw=source.read_bytes()
        source.write_bytes(raw.replace(b'forecast',b'forecasX',1))
        with self.assertRaisesRegex(q.QualificationFailure,'SOURCE_HASH'):
            self.call()
        self.failure()
        source.write_bytes(raw)
        self.config['output_directory']=str(self.root/'symlink-output');self.authorize()
        target=self.root/'other';source.rename(target);source.symlink_to(target)
        with self.assertRaisesRegex(q.QualificationFailure,'SOURCE_TYPE_SIZE'):
            self.call()
        self.failure()
        source.unlink();target.rename(source)
        self.audit['input_sha256'][self.sources['attempts']['path']]='0'*64
        self.write_proof('r46_audit',self.audit)
        self.config['output_directory']=str(self.root/'audit-input-output');self.authorize()
        with self.assertRaisesRegex(q.QualificationFailure,'R46_INPUT_BINDING'):
            self.call()
        self.failure()

    def test_fsync_failure_and_failure_reserve_stay_bounded(self):
        with patch.object(q.os,'fsync',side_effect=OSError('synthetic')),self.assertRaises(OSError):
            self.call()
        self.failure()
        size=sum(p.stat().st_size for p in Path(self.config['output_directory']).iterdir())
        self.assertLessEqual(size,q.LIMITS['output_bytes'])

    def test_rehash_detects_mutation_after_derivation(self):
        original=q.component.derive_reference_ledger
        def mutate(*args,**kwargs):
            result=original(*args,**kwargs);Path(self.sources['attempts']['path']).write_bytes(b'changed');return result
        with patch.object(q.component,'derive_reference_ledger',side_effect=mutate),self.assertRaises(q.QualificationFailure):self.call()
        self.failure()

    def test_wrong_ids_reordered_ids_and_null_masks_not_just_counts(self):
        for case in ('wrong','reordered','missingid','scope'):
            config=copy.deepcopy(self.config);proof=copy.deepcopy(self.r46)
            row=next(r for r in proof['rows']if len(r['attempt_identities'])==2)
            if case=='wrong':row['attempt_identities'][0]='f'*64
            elif case=='reordered':row['attempt_identities'].reverse()
            elif case=='missingid':row['residuals_exact'].reverse()  # Counts identical, missing identity differs.
            else:proof['rows'][-1]=proof['rows'][0]
            self.write_proof('r46_result',proof);self.audit['result_sha256']=self.sources['r46_result']['sha256']
            self.write_proof('r46_audit',self.audit);self.config['sources']=self.sources
            self.config['output_directory']=str(self.root/('out-'+case));self.authorize()
            with self.subTest(case=case),self.assertRaises(q.QualificationFailure):self.call()
            self.failure()
            self.config=config

    def test_audit_binding_and_input_provenance(self):
        self.audit['result_sha256']='0'*64;self.write_proof('r46_audit',self.audit);self.authorize()
        with self.assertRaisesRegex(q.QualificationFailure,'AUDIT_BINDING'):self.call()
        self.failure()

    def test_resource_failure_after_each_major_phase_never_completes(self):
        real=q.Guard.check
        for phase in ('authorized','before_derivation','after_derivation','after_memberships','before_semantic_hash','after_semantic_hash','before_serialization','serialize','write','verify_staged','precommit'):
            self.config['output_directory']=str(self.root/phase);self.authorize()
            def fail(guard,current,additional=0):
                real(guard,current,additional)
                if current==phase:raise q.QualificationFailure('INJECTED_RESOURCE_FAILURE')
            with self.subTest(phase=phase),patch.object(q.Guard,'check',fail),self.assertRaises(q.QualificationFailure):self.call()
            self.failure()

    def test_guard_wall_rss_disk_and_exact_output_boundaries(self):
        out=self.root/'guard';out.mkdir();g=q.Guard(out,dict(q.LIMITS))
        with patch.object(q.time,'monotonic',return_value=g.start+61),self.assertRaisesRegex(q.QualificationFailure,'WALL'):g.check('x')
        with patch.object(q.resource,'getrusage',return_value=type('R',(),{'ru_maxrss':q.LIMITS['rss_bytes']*1024})()),self.assertRaisesRegex(q.QualificationFailure,'RSS'):g.check('x')
        with patch.object(q.shutil,'disk_usage',return_value=type('D',(),{'free':0})()),self.assertRaisesRegex(q.QualificationFailure,'DISK'):g.check('x')
        result={'summary':{'status':'COMPLETE'}};size=len(q.encode(result))
        g.limits['output_bytes']=2*size+q.RESERVE
        q.publish(out,result,g)
        other=self.root/'guard2';other.mkdir();h=q.Guard(other,dict(q.LIMITS));h.limits['output_bytes']=2*size+q.RESERVE-1
        with self.assertRaisesRegex(q.QualificationFailure,'OUTPUT'):q.publish(other,result,h)
        self.assertFalse((other/'result.json').exists())

    def test_partial_writes_complete_exact_payload(self):
        real_open=Path.open
        class Partial:
            def __init__(self,stream):self.stream=stream
            def __enter__(self):return self
            def __exit__(self,*args):return self.stream.__exit__(*args)
            def __getattr__(self,name):return getattr(self.stream,name)
            def write(self,data):return self.stream.write(data[:max(1,len(data)//2)])
        def open_pending(path,*args,**kwargs):
            stream=real_open(path,*args,**kwargs)
            return Partial(stream) if path.name=='result.pending' else stream
        with patch.object(Path,'open',open_pending):result=self.call()
        payload=(Path(self.config['output_directory'])/'result.json').read_bytes()
        self.assertEqual(payload,q.encode(result))
        self.assertEqual(json.loads(payload)['summary']['semantic_sha256'],result['summary']['semantic_sha256'])

    def test_zero_none_negative_bool_and_overstated_write_progress_reject(self):
        real_open=Path.open
        class Invalid:
            def __init__(self,stream,kind):self.stream=stream;self.kind=kind
            def __enter__(self):return self
            def __exit__(self,*args):return self.stream.__exit__(*args)
            def __getattr__(self,name):return getattr(self.stream,name)
            def write(self,data):return len(data)+1 if self.kind=='too_large' else self.kind
        for kind in (0,None,-1,True,'too_large'):
            self.config['output_directory']=str(self.root/('invalid-'+str(kind)));self.authorize()
            def open_pending(path,*args,**kwargs):
                stream=real_open(path,*args,**kwargs)
                return Invalid(stream,kind) if path.name=='result.pending' else stream
            with self.subTest(kind=kind),patch.object(Path,'open',open_pending),self.assertRaisesRegex(q.QualificationFailure,'WRITE_PROGRESS'):
                self.call()
            self.failure()

    def test_staged_size_and_content_verified_not_writer_claims(self):
        real_open=Path.open
        class Liar:
            def __init__(self,stream,kind):self.stream=stream;self.kind=kind;self.first=True
            def __enter__(self):return self
            def __exit__(self,*args):return self.stream.__exit__(*args)
            def __getattr__(self,name):return getattr(self.stream,name)
            def write(self,data):
                if self.kind=='short':self.stream.write(data[:max(1,len(data)//2)])
                else:
                    raw=bytes(data)
                    if self.first:raw=b'!'+raw[1:];self.first=False
                    self.stream.write(raw)
                return len(data)
        for kind,reason in (('short','PENDING_SIZE_MISMATCH'),('corrupt','PENDING_CONTENT_MISMATCH')):
            self.config['output_directory']=str(self.root/kind);self.authorize()
            def open_pending(path,*args,**kwargs):
                stream=real_open(path,*args,**kwargs)
                return Liar(stream,kind) if path.name=='result.pending' else stream
            with self.subTest(kind=kind),patch.object(Path,'open',open_pending),self.assertRaisesRegex(q.QualificationFailure,reason):
                self.call()
            self.failure()

    def test_link_failure_and_confirmed_postlink_error(self):
        real=q.os.link
        for after in (False,True):
            self.config['output_directory']=str(self.root/('link-'+str(after)));self.authorize()
            def link(a,b):
                if after:real(a,b)
                raise OSError('injected')
            with patch.object(q.os,'link',side_effect=link):
                if after:self.call()
                else:
                    with self.assertRaises(OSError):self.call()
            if after:self.assertFalse((Path(self.config['output_directory'])/'failure.json').exists())
            else:self.failure()

    def test_delayed_commit_after_last_sample_stays_complete(self):
        real=q.os.link
        def delayed(a,b):
            # Simulate an unobserved late OS completion. Any postcommit guard
            # would reject, but no such guard may contradict the committed marker.
            real(a,b)
            q.time.monotonic=lambda:10**30
        with patch.object(q.time,'monotonic',side_effect=q.time.monotonic),patch.object(q.os,'link',side_effect=delayed):
            result=self.call()
        self.assertEqual(result['summary']['status'],'COMPLETE_SOURCE_QUALIFICATION_ONLY')
        self.assertFalse((Path(self.config['output_directory'])/'failure.json').exists())

    def test_uncertain_link_identity_is_not_authoritative_failure(self):
        real_link=q.os.link;real_lstat=Path.lstat
        def uncertain(path,*args,**kwargs):
            if path.name=='result.json':raise PermissionError('unknown')
            return real_lstat(path,*args,**kwargs)
        def link(a,b):real_link(a,b);raise OSError('afterlink')
        with patch.object(q.os,'link',side_effect=link),patch.object(Path,'lstat',uncertain),self.assertRaises(q.PublicationUncertain):self.call()
        body=json.loads((Path(self.config['output_directory'])/'failure.json').read_bytes())
        self.assertEqual(body['status'],'PUBLICATION_UNCERTAIN_NO_AUTHORITATIVE_FAILURE')

    def test_cancellation_propagates_and_does_not_contradict_commit(self):
        real=q.os.link
        for after in (False,True):
            self.config['output_directory']=str(self.root/('cancel-'+str(after)));self.authorize()
            def link(a,b):
                if after:real(a,b)
                raise KeyboardInterrupt
            with patch.object(q.os,'link',side_effect=link),self.assertRaises(KeyboardInterrupt):self.call()
            if after:self.assertFalse((Path(self.config['output_directory'])/'failure.json').exists())
            else:self.failure()


if __name__=='__main__':unittest.main()
