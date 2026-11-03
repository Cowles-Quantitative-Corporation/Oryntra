"""Invented archives only. Optional native tests bind preserved CODE, not data."""
import copy
from dataclasses import asdict,replace
from datetime import datetime,timedelta,timezone
from fractions import Fraction
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from backend.delta.research import reference_replay as rr
from backend.delta.research import reference_ledger as ledger
from backend.delta.research import reference_consumer as rc
from backend.delta.research import qualify_reference as q
import test_reference_consumer as fixtures


class ReplayUnitTests(unittest.TestCase):
    def test_disabled_and_import_contract(self):
        with patch.object(rr,'authorize',side_effect=AssertionError('disabled before authority')):
            with self.assertRaisesRegex(q.QualificationFailure,'DISABLED'):rr.run({})

    def test_sqlite_delete_wal_and_sidecar_rejection(self):
        with tempfile.TemporaryDirectory()as t:
            p=Path(t)/'q.sqlite';db=sqlite3.connect(p);db.execute('create table quotes(x)');db.commit();db.close()
            connection=rr.open_database(p);self.assertEqual(connection.execute('pragma query_only').fetchone()[0],1);connection.close()
            for suffix in ('-wal','-shm','-journal'):
                extra=Path(str(p)+suffix);extra.write_bytes(b'')
                with self.subTest(suffix=suffix),self.assertRaisesRegex(q.QualificationFailure,'SIDECAR'):rr.open_database(p)
                extra.unlink()
            db=sqlite3.connect(p);db.execute('pragma journal_mode=wal');db.close()
            with self.assertRaisesRegex(q.QualificationFailure,'JOURNAL_MODE'):rr.open_database(p)

    def test_wal_committed_rows_not_authenticated_by_main_bytes(self):
        with tempfile.TemporaryDirectory()as t:
            path=Path(t)/'wal.sqlite';writer=sqlite3.connect(path)
            writer.execute('create table quotes(x)');writer.commit()
            writer.execute('pragma journal_mode=wal');writer.execute('pragma wal_autocheckpoint=0')
            before=path.read_bytes();writer.execute('insert into quotes values(42)');writer.commit()
            self.assertEqual(before,path.read_bytes())
            reader=sqlite3.connect(path);self.assertEqual(reader.execute('select x from quotes').fetchone()[0],42);reader.close()
            with self.assertRaisesRegex(q.QualificationFailure,'SIDECAR'):rr.open_database(path)
            writer.close()

    def test_missing_marks_have_no_aggregate(self):
        report=dict(daily=[dict(session='d')],missing_recorded_days=['d'],metrics={},mark_failures=[dict(reason='FRESH_HELD_MARK_UNAVAILABLE')])
        result=rr.comparison(report,{},['d'])
        self.assertEqual(result['status'],'COMPARISON_NOT_EVALUABLE_MISSING_MARKS');self.assertIsNone(result['aggregate_performance'])
        report['metrics']={'mid':{'net_compounded_return':.1}}
        with self.assertRaisesRegex(q.QualificationFailure,'PARTIAL_PERIOD'):rr.comparison(report,{},['d'])

    def test_publication_logical_hardlink_and_failure_headroom(self):
        with tempfile.TemporaryDirectory()as t:
            out=Path(t);limits=dict(rr.LIMITS);result={'schema':rr.SCHEMA,'status':'SYNTHETIC'}
            needed=2*len(q.encode(result))+q.RESERVE;limits['output_bytes']=needed
            guard=q.Guard(out,limits);q.publish(out,result,guard)
            self.assertEqual((out/'result.pending').stat().st_ino,(out/'result.json').stat().st_ino)
            guard.check('both_names',additional=q.RESERVE)
            limits['output_bytes']=needed-1
            with self.assertRaisesRegex(q.QualificationFailure,'OUTPUT_LIMIT'):guard.check('one_byte_over',additional=q.RESERVE)
        with tempfile.TemporaryDirectory()as t:
            out=Path(t);guard=q.Guard(out,dict(rr.LIMITS));err=KeyboardInterrupt();err._publication_committed=True
            rr._failure(out,err,guard);self.assertEqual(list(out.iterdir()),[])
            err=SystemExit();err._publication_uncertain=True;rr._failure(out,err,guard)
            self.assertEqual(json.loads((out/'failure.json').read_bytes())['status'],'PUBLICATION_UNCERTAIN')


class NativeReplayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixtures.PinnedReferenceConsumerTests.setUpClass()
        cls.native=fixtures.PinnedReferenceConsumerTests('test_real_fraction_math_and_strict_sequence')

    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
        self.config,self.runtime=self.build()
        self.authorize()

    def spec(self,name,value,raw=False):
        path=self.root/name;payload=value if raw else q.encode(value);path.write_bytes(payload)
        return dict(path=str(path),bytes=len(payload),sha256=q.digest(payload))

    def authorize(self):
        self.authority=dict(schema=rr.SCHEMA,operation=rr.OPERATION,scope=self.config['scope'],
            config_sha256=rr.check_config(self.config),runtime_pins=rr.runtime_pins(),one_attempt=True)
        self.authority_pin=q.digest(q.encode(self.authority))

    def build(self):
        f=self.native;objects,_,_,_,_=f.bundle(mu=-.01)
        cal=objects['calendar']['sessions'];d=datetime(2026,3,31,tzinfo=timezone.utc)
        while d.date().isoformat()<='2026-05-15':
            if d.weekday()<5 and d.date().isoformat()!='2026-04-03':
                cal[d.date().isoformat()]=dict(open_ns=int(d.replace(hour=13,minute=30).timestamp())*10**9,
                    close_ns=int(d.replace(hour=20).timestamp())*10**9)
            d+=timedelta(days=1)
        cal.pop('2026-04-03',None)
        days=sorted(d for d in cal if d.startswith('2026-04-'));all_days=sorted(cal)
        base=copy.deepcopy(objects['records']['records'][-1])
        historical=[r for r in objects['records']['records']if r['created_at'][:10]<'2026-03-31']
        records=[]
        for day in days:
            i=all_days.index(day);origin=all_days[i-1];cutoff=all_days[i-2];end=all_days[i+4]
            r=copy.deepcopy(base);r.update(created_at=origin+'T00:00:00',information_cutoff=cutoff+'T00:00:00')
            r['metadata'].update(target_start=day,target_end=end,training_information_cutoff=cutoff+'T00:00:00',
                reference_training_cutoff=cutoff+'T00:00:00');records.append(r)
        objects['records']['records']=historical+records
        sources={k:self.spec(k+'.json',v)for k,v in objects.items()}
        for alias in ('r46_result','r46_audit'):sources[alias]=self.spec(alias+'.json',{'synthetic':True})
        raw={k:Path(v['path']).read_bytes()for k,v in sources.items()};pins={k:v['sha256']for k,v in sources.items()}
        origins=tuple(r['created_at'][:10]for r in historical)
        bank=ledger.derive_reference_ledger({k:raw[k]for k in ledger.SOURCE_KEYS},{k:pins[k]for k in ledger.SOURCE_KEYS},
            origins=origins,symbols=f.bindings.family.UNIVERSE,full_policy_id=f.full.FULL_POLICY,
            full_model_source_sha256=f.full.MODEL_SOURCE_SHA,enabled=True)
        qc=dict(sources=sources,synthetic=True);qc_raw=q.encode(qc);qc_spec=self.spec('qualification-config.json',qc_raw,raw=True)
        lookup={r.identity:r.original_attempt_id for r in bank.rows};members=[]
        for day,r in zip(days,records):
            cut=cal[r['information_cutoff'][:10]]['close_ns']
            for symbol in f.bindings.family.UNIVERSE:
                at=bank.asof(symbol,cut)
                members.append(dict(session=day,symbol=symbol,cutoff_ns=cut,original_ids=[lookup[i]for i in at['attempts']],
                    missing_original_ids=[lookup[i]for i,v in zip(at['attempts'],at['residuals'])if v is None]))
        data=dict(ledger=[asdict(r)for r in bank.rows],memberships=members,reference_policy_identity=bank.policy_identity)
        codepins=dict(runner_sha256='c'*64,component_sha256='d'*64)
        summary=dict(schema=q.SCHEMA,status='COMPLETE_SOURCE_QUALIFICATION_ONLY',code_pins=codepins,
            config_sha256=qc_spec['sha256'],source_pins=pins,row_count=len(bank.rows),scope_count=len(members),
            semantic_sha256=q.digest(q.encode(data)),operational_risk_admitted=False,historical_publication_attested=False,portfolio_replay=False)
        qualified=q.encode(dict(data,summary=summary));qual_spec=self.spec('qualification.json',qualified,raw=True)
        acceptance=dict(qualification_sha256=qual_spec['sha256'],config_sha256=qc_spec['sha256'],code_pins=codepins,
            source_pins=pins,full_policy_id=f.full.FULL_POLICY,symbols=list(f.bindings.family.UNIVERSE),evaluation_sessions=days)
        collector=rc.ReferenceConsumer(f.bindings,qualified,raw['records'],raw['calendar'],acceptance,enabled=True)
        rows=[];index=0
        for day in days:
            for symbol in f.bindings.family.UNIVERSE:
                index+=1;at=cal[day]['open_ns'];instrument=f.bindings.family.UNIVERSE.index(symbol)+1
                row=dict(identity='xnas-itch-'+day.replace('-','')+'.mbp-10.csv.zst:'+str(index),symbol=symbol,
                    at_ns=at,event_ns=at,session=day,valid=1,reset=0,source_index=index,instrument_id=instrument,
                    bid_nano=99990000000,ask_nano=100010000000,bid_size=10000,ask_size=10000,
                    source_records_json=json.dumps([[index,at,at,index,128,2,instrument]]))
                rows.append(row);collector.project(f.r48.OLD.row_event(f.bindings.core,row))
            index+=1;close=copy.deepcopy(rows[-7]);at=cal[day]['close_ns']
            close.update(identity='xnas-itch-'+day.replace('-','')+'.mbp-10.csv.zst:'+str(index),source_index=index,
                at_ns=at,event_ns=at,source_records_json=json.dumps([[index,at,at,index,128,2,1]]));rows.append(close)
        anchors=[]
        for (_,symbol),(_,forecast,_,_,_)in collector.candidates.items():
            a=forecast.anchor
            anchors.append(dict(session=forecast.target_start,symbol=symbol,base_record_sha256=forecast.base_record_sha256,
                source_index=a.source_index,source_quote_identity=a.source_quote_identity,anchor_price_nano=a.price_nano,
                anchor_available_ns=a.observed_at_ns,forecast_information_cutoff_ns=forecast.forecast_information_cutoff_ns,
                modeled_forecast_available_ns=forecast.modeled_forecast_available_ns,target_exit_ns=forecast.target_exit_ns,
                projection_identity=forecast.source_projection.identity))
        marks=lambda:dict(mid={'value_nano':10000*10**9},liquidation={'value_nano':10000*10**9})
        control=dict(anchor_manifest=anchors,daily=[dict(session=d,opening=marks(),closing=marks())for d in days],
            missing_recorded_days=[],metrics={'mid':{},'liquidation':{}})
        economic=self.spec('control-economic.json',control)
        control_receipt=self.spec('control-receipt.json',dict(economic_sha256=economic['sha256'],
            preserved_inputs_equal_before_after=True,new_model_or_risk_policy=False))
        path=self.root/'quotes.sqlite';db=sqlite3.connect(path)
        types={k:'INTEGER'if type(v)is int else 'TEXT'for k,v in rows[0].items()}
        db.execute('create table quotes('+','.join(k+' '+v for k,v in types.items())+')')
        db.executemany('insert into quotes values('+','.join('?'for _ in types)+')',[tuple(r.values())for r in rows]);db.commit();db.close()
        cache=dict(path=str(path),bytes=path.stat().st_size,sha256=q.digest(path.read_bytes()))
        all_sources={k:sources[k]for k in ('records','calendar')}
        all_sources.update({'qualification_'+k:sources[k]for k in rr.QUAL_ALIASES})
        all_sources.update(qualification=qual_spec,qualification_config=qc_spec,control_economic=economic,control_receipt=control_receipt,
            cache=cache,cache_receipt=self.spec('cache-receipt.json',dict(cache_sha256=cache['sha256'],quotes=len(rows))))
        for alias in ('benchmark_prices','benchmark_receipt','benchmark_returns'):all_sources[alias]=self.spec(alias+'.json',{'synthetic':True})
        code_path=Path(f.bindings.family.old.__file__)
        codes=[dict(path=str(code_path),bytes=code_path.stat().st_size,sha256=q.digest(code_path.read_bytes()))]
        config=dict(schema=rr.SCHEMA,operation=rr.OPERATION,scope='SYNTHETIC_FIXTURE_ONLY',sources=all_sources,
            code_sources=codes,code_root=str(code_path.parent),acceptance=acceptance,days=days,symbols=list(f.bindings.family.UNIVERSE),
            expected_events=len(rows),expected_anchors=147,resources=dict(rr.LIMITS),output_directory=str(self.root/'output'))
        benchmark=lambda roster:({d:0 for d in roster},{d:0 for d in roster if d>='2026-04-08'})
        runtime=(f.bindings,f.r48.OLD.row_event,f.calendar_adapter.typed_session_calendar,benchmark,'a'*64)
        self.rows=rows;self.collector=collector;self.control=control
        return config,runtime

    def call(self):
        with patch.object(rr,'_load_runtime',return_value=self.runtime):
            return rr.run(self.config,self.authority,expected_authority_sha256=self.authority_pin,enabled=True)

    def test_full_synthetic_native_zero_order_complete_and_no_overwrite(self):
        trace=[];scenarios=[];factory=rc.make_reference_scenario
        def tracked(*args,**kwargs):
            scenario=factory(*args,**kwargs);scenarios.append(scenario)
            original_step=scenario.step;original_close=scenario.close_day
            def step(core,event,**kw):
                trace.append(('step',event.session,event.source_index));return original_step(core,event,**kw)
            def close(day):trace.append(('close',day));return original_close(day)
            scenario.step=step;scenario.close_day=close;return scenario
        decoder=self.runtime[1];calls=[]
        def decode(core,row):calls.append(row['source_index']);return decoder(core,row)
        self.runtime=(self.runtime[0],decode,*self.runtime[2:])
        with patch.object(rc,'make_reference_scenario',side_effect=tracked),patch.object(self.native.r48.CausalOpenScenario,'step',side_effect=AssertionError('full dispatch')):
            result=self.call()
        self.assertEqual(calls,[r['source_index']for r in self.rows])
        self.assertEqual(set(scenarios[0].close_attempted),set(self.config['days']))
        for prior,day in zip(self.config['days'],self.config['days'][1:]):
            first=next(i for i,item in enumerate(trace)if item[:2]==('step',day))
            self.assertEqual(trace[first-1],('close',prior))
        self.assertEqual(trace[-1],('close',self.config['days'][-1]))
        self.assertEqual(result['status'],'COMPLETE_DESCRIPTIVE_POLICY_COMPARISON')
        self.assertEqual(result['anchors_matched'],147);self.assertEqual(result['stream']['events'],168)
        self.assertEqual(result['report']['orders'],{});self.assertEqual(result['report']['fees_nano'],0)
        self.assertEqual(len(result['report']['daily']),21)
        out=Path(self.config['output_directory']);before={p.name:q.digest(p.read_bytes())for p in out.iterdir()}
        with self.assertRaisesRegex(q.QualificationFailure,'FRESH_EXCLUSIVE'):self.call()
        self.assertEqual(before,{p.name:q.digest(p.read_bytes())for p in out.iterdir()})

    def test_authority_before_source_access(self):
        with patch.object(rr,'_authenticate',side_effect=AssertionError('source touched')):
            with self.assertRaises(q.QualificationFailure):rr.run(self.config,None,enabled=True)
        self.assertFalse(Path(self.config['output_directory']).exists())

    def test_stream_order_truncation_gaps_and_digest(self):
        config=dict(self.config,expected_events=2,days=[self.rows[0]['session']])
        rows=self.rows[:2];decode=self.runtime[1];core=self.runtime[0].core
        def consume(rows,config=config):
            stream=rr.Stream(config,list(rows[0]),'a'*64)
            for row in rows:stream.accept(row,decode(core,row))
            return stream.finish()
        first=consume(rows);self.assertEqual(first,consume(copy.deepcopy(rows)))
        changed=copy.deepcopy(rows);changed[1]['bid_size']+=1
        self.assertNotEqual(first['raw_row_sha256'],consume(changed)['raw_row_sha256'])
        self.assertNotEqual(first['decoded_event_sha256'],consume(changed)['decoded_event_sha256'])
        wrong_symbol=copy.deepcopy(rows);wrong_symbol[1]['symbol']='OTHER'
        wrong_session=copy.deepcopy(rows);wrong_session[1]['session']='2026-05-01'
        future=copy.deepcopy(rows);future[1]['event_ns']=future[1]['at_ns']+1
        for bad in (rows[:1],[rows[0],rows[0]],list(reversed(rows)),wrong_symbol,wrong_session,future):
            with self.subTest(bad=len(bad)),self.assertRaises((q.QualificationFailure,ValueError)):consume(bad)
        gap=copy.deepcopy(rows);gap[1]['source_index']=34;gap[1]['identity']=gap[1]['identity'].rsplit(':',1)[0]+':34'
        gap[1]['source_records_json']=json.dumps([[34,gap[1]['at_ns'],gap[1]['event_ns'],34,128,2,gap[1]['instrument_id']]])
        self.assertEqual(consume(gap)['events'],2)

    def test_all_anchor_fields_not_counts_only(self):
        self.assertEqual(rr.verify_anchors(self.collector,self.control,147),147)
        for field in ('base_record_sha256','source_index','source_quote_identity','anchor_price_nano','anchor_available_ns',
                      'forecast_information_cutoff_ns','modeled_forecast_available_ns','target_exit_ns'):
            changed=copy.deepcopy(self.control);v=changed['anchor_manifest'][0][field]
            changed['anchor_manifest'][0][field]=v+1 if type(v)is int else 'tampered'
            with self.subTest(field=field),self.assertRaises(q.QualificationFailure):rr.verify_anchors(self.collector,changed,147)
        changed=copy.deepcopy(self.control);changed['anchor_manifest'][0]=changed['anchor_manifest'][1]
        with self.assertRaises(q.QualificationFailure):rr.verify_anchors(self.collector,changed,147)

    def test_pin_failure_has_no_complete_and_keeps_failure(self):
        spec=self.config['sources']['control_receipt'];Path(spec['path']).write_bytes(b'changed')
        with self.assertRaises(Exception):self.call()
        out=Path(self.config['output_directory']);self.assertFalse((out/'result.json').exists())
        self.assertEqual(json.loads((out/'failure.json').read_bytes())['status'],'FAILED_EXECUTION_NO_COMPLETE')

    def test_final_source_identity_replacement_rejected(self):
        original=rr._authenticate;replaced=[]
        def substitute(spec,guard,alias,collect=False):
            if alias=='final:cache':
                path=Path(spec['path']);temp=path.with_suffix('.replacement')
                temp.write_bytes(path.read_bytes());temp.replace(path);replaced.append(True)
            return original(spec,guard,alias,collect)
        with patch.object(rr,'_authenticate',side_effect=substitute):
            with self.assertRaisesRegex(q.QualificationFailure,'SOURCE_IDENTITY_CHANGED'):self.call()
        self.assertTrue(replaced)
        out=Path(self.config['output_directory']);self.assertFalse((out/'result.json').exists())
        self.assertEqual(json.loads((out/'failure.json').read_bytes())['status'],'FAILED_EXECUTION_NO_COMPLETE')

    def test_precommit_resource_failure_keeps_no_complete(self):
        original=q.Guard.check
        def fault(guard,phase,additional=0):
            if phase=='precommit':raise q.QualificationFailure('WALL_LIMIT')
            return original(guard,phase,additional)
        with patch.object(q.Guard,'check',fault):
            with self.assertRaisesRegex(q.QualificationFailure,'WALL_LIMIT'):self.call()
        out=Path(self.config['output_directory']);self.assertFalse((out/'result.json').exists())
        self.assertTrue((out/'result.pending').exists());self.assertTrue((out/'failure.json').exists())

    def test_postlink_cancellation_preserved_without_false_failure(self):
        original=q.os.link
        def linked_then_cancelled(*args,**kwargs):
            original(*args,**kwargs);raise KeyboardInterrupt()
        with patch.object(q.os,'link',side_effect=linked_then_cancelled):
            with self.assertRaises(KeyboardInterrupt):self.call()
        out=Path(self.config['output_directory']);self.assertTrue((out/'result.json').exists())
        self.assertFalse((out/'failure.json').exists())
        self.assertEqual(json.loads((out/'result.json').read_bytes())['status'],'COMPLETE_DESCRIPTIVE_POLICY_COMPARISON')

    def test_uncertain_publication_is_not_authoritative_failure(self):
        original=q.os.link;original_stat=Path.lstat
        def linked_then_error(*args,**kwargs):
            original(*args,**kwargs);raise OSError('synthetic lost result')
        def uncertain(path,*args,**kwargs):
            if path.name=='result.json':raise PermissionError('synthetic unknown identity')
            return original_stat(path,*args,**kwargs)
        with patch.object(q.os,'link',side_effect=linked_then_error),patch.object(Path,'lstat',uncertain):
            with self.assertRaises(q.PublicationUncertain):self.call()
        out=Path(self.config['output_directory'])
        self.assertEqual(json.loads((out/'failure.json').read_bytes())['status'],'PUBLICATION_UNCERTAIN')
        self.assertTrue((out/'result.json').exists())

    def test_zero_staged_write_never_publishes(self):
        original=q.write_all
        def stopped(stream,payload,guard=None):
            if str(stream.name).endswith('result.pending'):
                raise q.QualificationFailure('WRITE_PROGRESS')
            return original(stream,payload,guard)
        with patch.object(q,'write_all',side_effect=stopped):
            with self.assertRaisesRegex(q.QualificationFailure,'WRITE_PROGRESS'):self.call()
        out=Path(self.config['output_directory']);self.assertFalse((out/'result.json').exists());self.assertTrue((out/'failure.json').exists())

    def test_real_runtime_loader_code_only_bindings(self):
        f=self.native;root=Path(f.bindings.family.__file__).parent
        import sys
        specs=[]
        for module in tuple(sys.modules.values()):
            name=getattr(module,'__file__',None)
            if name and Path(name).suffix=='.py'and Path(name).is_relative_to(root):
                path=Path(name);specs.append(dict(path=str(path),bytes=path.stat().st_size,sha256=q.digest(path.read_bytes())))
        core=Path(f.bindings.core.__file__);specs.append(dict(path=str(core),bytes=core.stat().st_size,sha256=q.digest(core.read_bytes())))
        old=Path(f.bindings.family.old.__file__)
        if not any(s['path']==str(old)for s in specs):specs.append(dict(path=str(old),bytes=old.stat().st_size,sha256=q.digest(old.read_bytes())))
        config=copy.deepcopy(self.config);config['code_sources']=specs
        for alias,name in [('benchmark_prices','SPY-adjusted-April.csv'),('benchmark_receipt','receipt.json'),('benchmark_returns','matched-returns.csv')]:
            config['sources'][alias]['path']=str(root/'spy-April-adjusted-benchmark-v1'/name)
        original=Path.open
        def code_only(path,*args,**kwargs):
            if path.is_relative_to(root)and path.suffix in ('.json','.csv','.sqlite'):raise AssertionError('historical source access')
            return original(path,*args,**kwargs)
        with patch.object(Path,'open',code_only):runtime=rr._load_runtime(config)
        self.assertIs(runtime[0].core,f.bindings.core);self.assertIs(runtime[1],f.r48.OLD.row_event)
        fake_root=self.root/'other-code-root';fake_root.mkdir();fake_specs=[]
        for name in [n+'.py'for n in rr.MODULES.values()]+['delta_development_cache.py','run_april_daily_comparison.py','daily_gamma_inputs.py','run_delta_open_target_causal_April_replay_v1.py']:
            target=fake_root/name;target.write_bytes((root/name).read_bytes())
            fake_specs.append(dict(path=str(target),bytes=target.stat().st_size,sha256=q.digest(target.read_bytes())))
        other=copy.deepcopy(config);other['code_root']=str(fake_root);other['code_sources']=fake_specs
        with self.assertRaisesRegex(q.QualificationFailure,'LOADED_CODE_ROOT'):rr._load_runtime(other)

        bad=copy.deepcopy(config);bad['code_sources'][0]['sha256']='f'*64
        # Every required binding pin is checked before module import.
        bad['code_sources']=[s for s in bad['code_sources']if Path(s['path']).stem!='delta_broker_fee_policy_v1']
        with patch.object(rr.importlib,'import_module',side_effect=AssertionError('must reject before import')):
            with self.assertRaises(q.QualificationFailure):rr._load_runtime(bad)

    def test_native_missing_held_mark_retained(self):
        f=self.native;c,_,_=f.consumer();s=rc.make_reference_scenario(c,{},c.days,source_manifest_sha256='a'*64,enabled=True)
        s.step(c,f.event(c),sessions=f.clocks(c));s.step(c,f.event(c,offset=100_000_000,index=2),sessions=f.clocks(c))
        self.assertGreater(s.account.positions['AAPL'],0)
        s.close_day('2026-04-08');report=s.result({'2026-04-08':0})
        self.assertTrue(report['missing_recorded_days']);self.assertFalse(report['metrics'])
        self.assertEqual(rr.comparison(report,{},list(c.days))['status'],'COMPARISON_NOT_EVALUABLE_MISSING_MARKS')


if __name__=='__main__':unittest.main()
