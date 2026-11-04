"""Invented archives only; optional preserved code interfaces, no empirical replay."""
from copy import deepcopy
from pathlib import Path
import tempfile
import sqlite3
import json
import unittest
from unittest.mock import patch
import test_reference_replay as rf
import test_mean_sensitivity as sf
from backend.delta.research import mean_sensitivity_replay as runner
from backend.delta.research import mean_sensitivity as ms
from backend.delta.research import reference_replay as rr
from backend.delta.research import qualify_reference as q

class PortableTests(unittest.TestCase):
    def test_disabled_before_authorization(self):
        with patch.object(runner,'authorize',side_effect=AssertionError('early')):
            with self.assertRaisesRegex(q.QualificationFailure,'DISABLED'):runner.run({},enabled=False)
    def test_comparison_exact_and_missing(self):
        marks=lambda n:dict(mid={'value_nano':n},liquidation={'value_nano':n-1})
        control=dict(daily=[dict(session='x',opening=marks(10),closing=marks(12))],metrics={},missing_recorded_days=[])
        report=dict(daily=[dict(session='x',opening=marks(10),closing=marks(15))],metrics={},missing_recorded_days=[],status='COMPLETE_CONDITIONAL_DEVELOPMENT_REPLAY')
        result=runner.comparison(report,control,['x']);self.assertEqual(result['primary_terminal_liquidation_difference_nano'],3)
        self.assertTrue(result['artificial_uncertainty_proxy']);self.assertFalse(result['calibrated_reference_risk'])
        report.update(missing_recorded_days=['x'],metrics={})
        self.assertIsNone(runner.comparison(report,control,['x'])['primary_terminal_liquidation_difference_nano'])

    def test_failure_schema_and_publication_uncertainty(self):
        with tempfile.TemporaryDirectory()as directory:
            out=Path(directory);guard=q.Guard(out,dict(runner.LIMITS))
            original=q.QualificationFailure('ORIGINAL_REASON')
            runner._failure(out,original,guard)
            body=q.decode((out/'failure.json').read_bytes())
            self.assertEqual(body['schema'],runner.SCHEMA)
            self.assertEqual(body['status'],'FAILED_EXECUTION_NO_COMPLETE')
            self.assertEqual(body['reason'],'ORIGINAL_REASON')
            self.assertLessEqual((out/'failure.json').stat().st_size,8192)
        with tempfile.TemporaryDirectory()as directory:
            out=Path(directory);guard=q.Guard(out,dict(runner.LIMITS))
            original=q.PublicationUncertain('UNCERTAIN')
            runner._failure(out,original,guard)
            body=q.decode((out/'failure.json').read_bytes())
            self.assertEqual(body['schema'],runner.SCHEMA)
            self.assertEqual(body['status'],'PUBLICATION_UNCERTAIN')
        with tempfile.TemporaryDirectory()as directory:
            out=Path(directory);guard=q.Guard(out,dict(runner.LIMITS));original=KeyboardInterrupt()
            original._publication_committed=True;runner._failure(out,original,guard)
            self.assertEqual(list(out.iterdir()),[])

class NativeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        sf.NativeSensitivityTests.setUpClass();cls.helper=sf.NativeSensitivityTests('test_complete147_synthetic_full_asof_scopes')
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
        self.fixture=rf.NativeReplayTests('test_authority_before_source_access');self.fixture.root=self.root;self.fixture.native=self.helper.fixture
        old,self.runtime=self.fixture.build();objects={a:q.decode(Path(old['sources'][a if a in ('records','calendar')else 'qualification_'+a]['path']).read_bytes())for a in ('records','calendar','attempts','errors','outcomes')}
        self.objects=objects
        _,_,_,raw,ctx=self.helper.bundle(objects=objects,days=old['days'])
        specs={a:self.fixture.spec('full-'+a+'.json',raw[a],raw=True)for a in ms.ALIASES}
        proof=q.decode(raw['r46_result']);proof['inputs']={specs[a]['path']:specs[a]['sha256']for a in ms.ref.SOURCE_KEYS}
        specs['r46_result']=self.fixture.spec('full-proof.json',proof)
        audit=q.decode(raw['r46_audit']);audit.update(result_sha256=specs['r46_result']['sha256'],input_sha256=proof['inputs'])
        specs['r46_audit']=self.fixture.spec('full-audit.json',audit)
        ctx['source_specs']=specs
        sources={k:v for k,v in old['sources'].items()if k in runner.ALIASES};sources.update(specs)
        self.config={k:v for k,v in old.items()if k!='acceptance'};self.config.update(schema=runner.SCHEMA,operation=runner.OPERATION,sources=sources,bank_context=ctx)
        self.authorize()
    def authorize(self):
        self.authority=dict(schema=runner.SCHEMA,operation=runner.OPERATION,scope=self.config['scope'],config_sha256=runner.check_config(self.config),runtime_pins=runner.runtime_pins(),one_attempt=True)
        self.pin=q.digest(q.encode(self.authority))
    def call(self):
        runtime=(self.helper.bindings,*self.runtime[1:])
        with patch.object(runner,'_load_runtime',return_value=runtime):return runner.run(self.config,self.authority,expected_authority_sha256=self.pin,enabled=True)
    def test_actual_native21sessions147anchors_wrong_dispatch_and_private_result(self):
        with patch.object(rr.rc,'make_reference_scenario',side_effect=AssertionError('own reference dispatch')),patch.object(self.helper.fixture.r48.CausalOpenScenario,'step',side_effect=AssertionError('full dispatch')):
            result=self.call()
        self.assertEqual(result['anchors_matched'],147);self.assertEqual(result['stream']['events'],168)
        self.assertEqual(len(result['report']['daily']),21);self.assertTrue(result['artificial_uncertainty_proxy'])
        self.assertEqual(result['comparison']['primary_terminal_liquidation_difference_nano'],0)
        self.assertIn('not continuous custody',result['source_guarantee'])
        with self.assertRaisesRegex(q.QualificationFailure,'FRESH_EXCLUSIVE'):self.call()
    def test_authority_before_any_source_access(self):
        self.authority['one_attempt']=False
        with patch.object(rr,'_authenticate',side_effect=AssertionError('source touched')):
            with self.assertRaisesRegex(q.QualificationFailure,'AUTHORITY'):self.call()
    def test_bank_context_mismatch_before_read(self):
        self.config['bank_context']['evaluation_sessions']=[]
        with patch.object(rr,'_authenticate',side_effect=AssertionError('source touched')):
            with self.assertRaisesRegex(q.QualificationFailure,'BANK_CONTEXT'):self.call()
    def test_bad_source_preserves_failure_without_complete(self):
        Path(self.config['sources']['records']['path']).write_bytes(b'changed')
        with self.assertRaises(Exception):self.call()
        self.assertFalse((Path(self.config['output_directory'])/'result.json').exists())
        self.assertTrue((Path(self.config['output_directory'])/'failure.json').exists())
        self.assertEqual(q.decode((Path(self.config['output_directory'])/'failure.json').read_bytes())['schema'],runner.SCHEMA)
    def test_late_sidecar_failure(self):
        original=rr.sidecars;calls=[]
        def fault(path):
            calls.append(path)
            if len(calls)>2:raise q.QualificationFailure('SQLITE_SIDECAR_PRESENT')
            return original(path)
        with patch.object(rr,'sidecars',side_effect=fault):
            with self.assertRaisesRegex(q.QualificationFailure,'SIDECAR'):self.call()
        self.assertFalse((Path(self.config['output_directory'])/'result.json').exists())
    def test_final_resource_failure(self):
        original=q.Guard.check
        def fault(g,stage):
            if stage=='before_publish':raise q.QualificationFailure('FINAL_RESOURCE')
            return original(g,stage)
        with patch.object(q.Guard,'check',fault):
            with self.assertRaisesRegex(q.QualificationFailure,'FINAL_RESOURCE'):self.call()
        self.assertFalse((Path(self.config['output_directory'])/'result.json').exists())
    def test_anchor_price_mismatch(self):
        p=Path(self.config['sources']['control_economic']['path']);body=q.decode(p.read_bytes());body['anchor_manifest'][0]['anchor_price_nano']+=1
        self.config['sources']['control_economic']=self.fixture.spec('changed-control.json',body)
        old=q.decode(Path(self.config['sources']['control_receipt']['path']).read_bytes());old['economic_sha256']=self.config['sources']['control_economic']['sha256']
        self.config['sources']['control_receipt']=self.fixture.spec('changed-receipt.json',old);self.authorize()
        with self.assertRaisesRegex(q.QualificationFailure,'ANCHOR_SOURCE'):self.call()

    def test_wrapper_uncertain_schema_and_original_exception(self):
        original=q.PublicationUncertain('SYNTHETIC_UNCERTAIN')
        with patch.object(q,'publish',side_effect=original):
            with self.assertRaises(q.PublicationUncertain)as caught:self.call()
        self.assertIs(caught.exception,original)
        body=q.decode((Path(self.config['output_directory'])/'failure.json').read_bytes())
        self.assertEqual(body['schema'],runner.SCHEMA)
        self.assertEqual(body['status'],'PUBLICATION_UNCERTAIN')
    def test_failure_logging_error_cannot_replace_original(self):
        original=KeyboardInterrupt()
        with patch.object(q,'publish',side_effect=original),patch.object(q,'write_all',side_effect=OSError('logging failure')):
            with self.assertRaises(KeyboardInterrupt)as caught:self.call()
        self.assertIs(caught.exception,original)

    def test_missing_marks_wrapper_has_no_partial_primary(self):
        factory=ms.make_sensitivity_scenario
        def changed(*a,**kw):
            scenario=factory(*a,**kw);original=scenario.result
            def missing(*args):
                result=original(*args);result.update(missing_recorded_days=[self.config['days'][0]],metrics={})
                result.pop('evaluation_metrics',None);return result
            scenario.result=missing;return scenario
        with patch.object(ms,'make_sensitivity_scenario',side_effect=changed):result=self.call()
        self.assertEqual(result['status'],'COMPARISON_NOT_EVALUABLE_MISSING_MARKS')
        self.assertIsNone(result['comparison']['primary_terminal_liquidation_difference_nano'])
        self.assertIsNone(result['comparison']['aggregate_performance'])
    def test_final_source_replacement_rejected(self):
        original=rr._authenticate
        def changed(spec,guard,alias,**kw):
            if alias=='final:records':
                p=Path(spec['path']);raw=p.read_bytes();p.unlink();p.write_bytes(raw)
            return original(spec,guard,alias,**kw)
        with patch.object(rr,'_authenticate',side_effect=changed):
            with self.assertRaisesRegex(q.QualificationFailure,'SOURCE_IDENTITY'):self.call()
        self.assertFalse((Path(self.config['output_directory'])/'result.json').exists())
    def test_postlink_cancellation_propagates_without_false_failure(self):
        original=q.os.link
        def changed(*a,**kw):original(*a,**kw);raise KeyboardInterrupt()
        with patch.object(q.os,'link',side_effect=changed):
            with self.assertRaises(KeyboardInterrupt):self.call()
        out=Path(self.config['output_directory']);self.assertTrue((out/'result.json').exists())
        self.assertFalse((out/'failure.json').exists())
    def test_truncated_and_reordered_stream_helpers(self):
        columns=tuple(self.fixture.rows[0]);stream=rr.Stream(self.config,columns,'a'*64)
        row=self.fixture.rows[0];event=self.runtime[1](self.helper.bindings.native.core,row);stream.accept(row,event)
        with self.assertRaisesRegex(q.QualificationFailure,'TRUNCATED'):stream.finish()
        with self.assertRaisesRegex(q.QualificationFailure,'INDEX_ORDER'):stream.accept(row,event)
    def test_real_runtime_loader_uses_pinned_full_code_only(self):
        root=Path(self.helper.fixture.full.__file__).parent
        # Native helper already checks its own transitive closure. Verify the added
        # full-bank import/root/pin path without opening any historical source.
        config=deepcopy(self.config);config['code_root']=str(root)
        paths=[root/'delta_conditional_open_proposal_v1.py',root/'delta_open_error_ledger_v1.py']
        config['code_sources']=[dict(path=str(p),bytes=p.stat().st_size,sha256=q.digest(p.read_bytes()))for p in paths]
        # Bind all already-loaded frozen native modules to their actual code bytes.
        import sys
        for module in tuple(sys.modules.values()):
            name=getattr(module,'__file__',None)
            if name and Path(name).suffix=='.py'and Path(name).is_relative_to(root)and Path(name)not in paths:
                p=Path(name);config['code_sources'].append(dict(path=str(p),bytes=p.stat().st_size,sha256=q.digest(p.read_bytes())))
        with patch.object(rr,'_load_runtime',return_value=self.runtime):loaded=runner._load_runtime(config)
        self.assertEqual(loaded[0],self.helper.bindings)
        config['code_sources'][0]['sha256']='0'*64
        with patch.object(rr,'_load_runtime',return_value=self.runtime):
            with self.assertRaisesRegex(q.QualificationFailure,'FULL_CODE_CLOSURE'):runner._load_runtime(config)

    def test_positive_order_wrapper_equal_input_partial_exit_settlement(self):
        objects=deepcopy(self.objects)
        for r in objects['records']['records']:
            if r['created_at'][:10]>='2026-03-31':
                r['values']={symbol:.05 for symbol in self.config['symbols']};r['metadata']['reference_mean']=.05
        consumer,bank,_,raw,ctx=self.helper.bundle(objects=objects,days=self.config['days'])
        specs={a:self.fixture.spec('positive-'+a+'.json',raw[a],raw=True)for a in ms.ALIASES}
        proof=q.decode(raw['r46_result']);proof['inputs']={specs[a]['path']:specs[a]['sha256']for a in ms.ref.SOURCE_KEYS}
        specs['r46_result']=self.fixture.spec('positive-proof.json',proof)
        audit=q.decode(raw['r46_audit']);audit.update(result_sha256=specs['r46_result']['sha256'],input_sha256=proof['inputs'])
        specs['r46_audit']=self.fixture.spec('positive-audit.json',audit);ctx['source_specs']=specs
        self.config['sources'].update(specs);self.config['bank_context']=ctx
        # Invent opening anchors, constrained partial liquidity, expiry, required exits,
        # closing marks for every held name and later settlement. No market payload.
        rows=[];index=0;cal=consumer.calendar
        for day in self.config['days']:
            base=[r for r in self.fixture.rows if r['session']==day][:7]
            times=[(cal[day]['open_ns'],10000),(cal[day]['open_ns']+100000000,20),
                   (cal[day]['open_ns']+1200000000,20),(cal[day]['open_ns']+1300000000,20),
                   (cal[day]['open_ns']+60*10**9+1,10000),(cal[day]['close_ns']-60*10**9,10000),
                   (cal[day]['close_ns']-60*10**9+100000000,10000),(cal[day]['close_ns'],10000)]
            for at,size in times:
                for row in base:
                    index+=1;r=deepcopy(row);instrument=r['instrument_id']
                    r.update(identity='xnas-itch-'+day.replace('-','')+'.mbp-10.csv.zst:'+str(index),source_index=index,
                        at_ns=at,event_ns=at,bid_size=size,ask_size=size,
                        source_records_json=json.dumps([[index,at,at,index,128,2,instrument]]));rows.append(r)
        path=self.root/'positive.sqlite';db=sqlite3.connect(path)
        types={k:'INTEGER'if type(v)is int else 'TEXT'for k,v in rows[0].items()}
        db.execute('create table quotes('+','.join(k+' '+v for k,v in types.items())+')')
        db.executemany('insert into quotes values('+','.join('?'for _ in types)+')',[tuple(r.values())for r in rows]);db.commit();db.close()
        cache=dict(path=str(path),bytes=path.stat().st_size,sha256=q.digest(path.read_bytes()))
        self.config['sources']['cache']=cache;self.config['expected_events']=len(rows)
        self.config['sources']['cache_receipt']=self.fixture.spec('positive-cache.json',dict(cache_sha256=cache['sha256'],quotes=len(rows)))
        control=deepcopy(self.fixture.control);anchors=[]
        for row in rows:consumer.project(self.runtime[1](consumer.bindings.core,row))
        for key,(_,f,a,_,_)in consumer.candidates.items():
            anchors.append(dict(session=key[0],symbol=key[1],base_record_sha256=f.base_record_sha256,source_index=a.source_index,
                source_quote_identity=a.source_quote_identity,anchor_price_nano=a.price_nano,anchor_available_ns=a.observed_at_ns,
                forecast_information_cutoff_ns=f.forecast_information_cutoff_ns,modeled_forecast_available_ns=f.modeled_forecast_available_ns,
                target_exit_ns=f.target_exit_ns,projection_identity=f.source_projection.identity))
        control['anchor_manifest']=anchors;self.config['sources']['control_economic']=self.fixture.spec('positive-control.json',control)
        self.config['sources']['control_receipt']=self.fixture.spec('positive-control-receipt.json',dict(economic_sha256=self.config['sources']['control_economic']['sha256'],preserved_inputs_equal_before_after=True,new_model_or_risk_policy=False))
        self.authorize();b=consumer.bindings;settlement={d:sorted(cal)[sorted(cal).index(d)+1]for d in self.config['days']}
        full=b.family.ConditionalScenario('C01',settlement,deepcopy(cal),self.config['days'],None)
        full.name='R48_'+self.helper.fixture.r48.CASE
        full.account=b.account.MultiAssetCashAccount(source_manifest_sha256='a'*64,settlement_days=settlement,fee_policy=b.policy.BrokerFeePolicy('CURRENT_FIXED_SMART_US'))
        factory=ms.make_sensitivity_scenario;seen=[];idmap={};candidates={};fullowner=bank._owner
        def tracked(c,*args,**kwargs):
            scenario=factory(c,*args,**kwargs);seen.append(scenario);step=scenario.step;close=scenario.close_day
            def both(core,event,**kw):
                candidate=c.project(event)
                if candidate:
                    record,f,a,end,exit=candidate;candidates[event.session,event.symbol]=(record,f.source_projection,a,end,exit)
                    idmap[f.identity]=idmap[f.source_projection.identity]=event.session+':'+event.symbol
                step(core,event,**kw);full.step(fullowner,event,candidates,bank.rows,kw['sessions'])
                self.assertEqual(scenario.account.cash_nano,full.account.cash_nano)
                self.assertEqual(scenario.account.positions,full.account.positions)
                self.assertEqual(scenario.account.reserved_cash_nano,full.account.reserved_cash_nano)
            def closing(day):close(day);full.close_day(day)
            scenario.step=both;scenario.close_day=closing;return scenario
        with patch.object(ms,'make_sensitivity_scenario',side_effect=tracked):result=self.call()
        account=seen[0].account
        def normalize(value):
            if type(value)is dict:return {k:('INTENT:'+v.rsplit(':',1)[-1]if k=='intent_id'else 'FILL:'+v.rsplit(':',1)[-1]if k=='fill_id'else idmap[v]if k=='forecast_id'else normalize(v))for k,v in value.items()}
            if type(value)in (list,tuple):return [normalize(v)for v in value]
            return value
        self.assertTrue(account.ledger);self.assertTrue(account.settlement_ledger)
        self.assertEqual(normalize(account.ledger),normalize(full.account.ledger))
        self.assertEqual(normalize(list(account.orders.values())),normalize(list(full.account.orders.values())))
        self.assertEqual(result['anchors_matched'],147)
        self.assertTrue(any(o['side']=='A'for o in account.orders.values()))
        buy_ids=[o['forecast_id']for o in account.orders.values()if o['side']=='B']
        self.assertGreater(len(buy_ids),len(set(buy_ids)), 'same-candidate entry retries exercised')
        self.assertTrue(any(o['filled_shares']>0 and o['filled_shares']<o['quantity']for o in account.orders.values()))

if __name__=='__main__':unittest.main()
