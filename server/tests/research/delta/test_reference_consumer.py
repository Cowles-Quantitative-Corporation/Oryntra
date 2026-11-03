"""Synthetic reference dispatch. Pinned-code integration needs explicit local root.

Set DELTA_FROZEN_CODE_ROOT to a separately preserved code-only root. No dataset
is read; fixtures below invent every forecast, outcome and quote.
"""
import copy
from datetime import datetime, timedelta, timezone
from fractions import Fraction as F
import hashlib
import importlib
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

from test_reference_ledger import fixture
from backend.delta.research import reference_ledger as ref
from backend.delta.research.qualify_reference import encode,digest,SCHEMA
from backend.delta.research.reference_consumer import (
    ReferenceConsumer,ReferenceForecast,ReferencePlan,FrozenBindings,PINS,PRODUCER,make_reference_scenario,
)


class PortableConsumerGuards(unittest.TestCase):
    def test_disabled_before_binding_access(self):
        class Forbidden:
            def verify(self):raise AssertionError('must stay disabled')
        with self.assertRaisesRegex(ValueError,'disabled'):
            ReferenceConsumer(Forbidden(),b'',b'',b'',{})
        with self.assertRaisesRegex(ValueError,'disabled'):
            make_reference_scenario(None,{},[],source_manifest_sha256='a'*64)

    def test_plan_scope_and_clock_domains(self):
        p=ReferencePlan('plan','forecast','A',1,2,3,4,100,1)
        self.assertFalse(p.live_authorized)
        for kw in ({'live_authorized':True},{'entry_end_ns':4},{'maximum_entry_shares':True}):
            from dataclasses import replace
            with self.assertRaises(ValueError):replace(p,**kw)


class PinnedReferenceConsumerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        raw=os.environ.get('DELTA_FROZEN_CODE_ROOT')
        if not raw:raise unittest.SkipTest('explicit preserved code-only bindings not supplied')
        root=Path(raw)
        names=dict(projection='delta_open_entry_anchor_projection_v1',fees='delta_broker_fee_admission_robust_mean_v1',
                   policy='delta_broker_fee_policy_v1',family='run_new7_conditional_April_family_v1',
                   account='delta_multi_asset_account_broker_v1')
        for alias,name in names.items():
            assert digest((root/(name+'.py')).read_bytes())==PINS[alias]
        before=list(sys.path);sys.path.insert(0,str(root))
        real_open=Path.open
        def code_only(path,*args,**kwargs):
            if path.is_relative_to(root) and path.suffix in ('.json','.csv','.sqlite'):
                raise AssertionError('historical payload access during code binding')
            return real_open(path,*args,**kwargs)
        try:
            with patch.object(Path,'open',code_only):
                modules={alias:importlib.import_module(name)for alias,name in names.items()}
                cache=importlib.import_module('delta_development_cache');core,_=cache.load_core()
                cls.calendar_adapter=importlib.import_module('daily_gamma_inputs')
                cls.full=importlib.import_module('delta_conditional_open_proposal_v1')
                cls.r48=importlib.import_module('run_delta_open_target_causal_April_replay_v1')
                cls.old_ledger=importlib.import_module('delta_open_error_ledger_v1')
            cls.bindings=FrozenBindings(core=core,**modules);cls.bindings.verify()
        finally:sys.path[:]=before

    def bundle(self,count=85,mu=.05,full_mu=.08,actual=.11):
        objects,args=fixture(count);policy=self.full.FULL_POLICY;model=self.full.MODEL_SOURCE_SHA
        names=('AAPL','MSFT','AMZN','GOOGL','JPM','XOM','WMT')
        mapping=dict(zip(args['symbols'],names));args['symbols']=names
        for r in objects['records']['records']:
            r['values']={mapping[k]:v for k,v in r['values'].items()}
        for key in ('attempts','outcomes','errors'):
            for row in objects[key][key]:row['symbol']=mapping[row['symbol']]
        # Extend the invented weekday calendar, retaining its explicit clocks.
        cal=objects['calendar']['sessions'];d=datetime.fromisoformat(min(cal)).replace(tzinfo=timezone.utc)
        while d.date().isoformat()<='2026-04-30':
            if d.weekday()<5:
                ns=lambda hour,minute=0:int(d.replace(hour=hour,minute=minute).timestamp())*10**9
                cal[d.date().isoformat()]=dict(open_ns=ns(14,30),close_ns=ns(20 if d.month==4 else 21))
            d+=timedelta(days=1)
        old_attempts={a['attempt_id']:a for a in objects['attempts']['attempts']}
        old_by={(a['origin'],a['symbol']):a for a in old_attempts.values()}
        outcomes={(o['origin'],o['symbol']):o for o in objects['outcomes']['outcomes']}
        for r in objects['records']['records']:
            r['metadata']['config_fingerprint']=policy;r['values']={s:.01 for s in args['symbols']}
        new_attempts=[];new_outcomes=[];new_errors=[]
        for r in objects['records']['records']:
            origin=r['created_at'][:10];rh=ref.canonical_hash(r)
            for symbol in args['symbols']:
                a=copy.deepcopy(old_by[origin,symbol]);a.update(record_sha256=rh,forecast=.01,
                    model_source_sha256=model,config_fingerprint=policy)
                aid=ref.canonical_hash(dict(record_sha256=rh,symbol=symbol,target_start=a['target_start'],
                    target_end=a['due_session'],return_convention=ref.CONVENTION));a['attempt_id']=aid
                o=copy.deepcopy(outcomes[origin,symbol]);o.update(attempt_id=aid,source_adjusted_close=100*(1+actual),realized_simple_return=actual)
                e=dict(attempt_id=aid,symbol=symbol,origin=origin,target_start=a['target_start'],due_session=a['due_session'],
                    forecast=.01,actual=actual,available_at_simulated=o['available_at_simulated'],
                    error_actual_minus_forecast=actual-.01,operational_risk_admitted=False)
                new_attempts.append(a);new_outcomes.append(o);new_errors.append(e)
        objects['attempts']['attempts']=new_attempts;objects['outcomes']['outcomes']=new_outcomes;objects['errors']['errors']=new_errors
        current=copy.deepcopy(objects['records']['records'][0]);current.update(created_at='2026-04-07T00:00:00',information_cutoff='2026-04-06T00:00:00')
        current['values']={s:full_mu for s in args['symbols']}
        current['metadata'].update(target_start='2026-04-08',target_end='2026-04-14',reference_mean=mu,
            training_information_cutoff='2026-04-06T00:00:00',reference_training_cutoff='2026-04-06T00:00:00',
            target_type='absolute',beta_status='PIT_supplied',sec_quality_observed_symbols=list(args['symbols']),
            residual_status='bounded_ridge_OOF_residual')
        objects['records']['records'].append(current)
        raw={k:encode(v)for k,v in objects.items()};pins={k:digest(v)for k,v in raw.items()}
        bank=ref.derive_reference_ledger(raw,pins,origins=args['origins'],symbols=args['symbols'],
            full_policy_id=policy,full_model_source_sha256=model,enabled=True)
        from dataclasses import asdict
        members=[];cut=cal['2026-04-06']['close_ns'];lookup={r.identity:r.original_attempt_id for r in bank.rows}
        for symbol in args['symbols']:
            at=bank.asof(symbol,cut);members.append(dict(session='2026-04-08',symbol=symbol,cutoff_ns=cut,
                original_ids=[lookup[i]for i in at['attempts']],missing_original_ids=[]))
        data=dict(ledger=[asdict(r)for r in bank.rows],memberships=members,reference_policy_identity=bank.policy_identity)
        codepins=dict(runner_sha256='c'*64,component_sha256='d'*64)
        summary=dict(schema=SCHEMA,status='COMPLETE_SOURCE_QUALIFICATION_ONLY',code_pins=codepins,
            config_sha256='e'*64,source_pins=pins,row_count=len(bank.rows),scope_count=7,
            semantic_sha256=digest(encode(data)),operational_risk_admitted=False,
            historical_publication_attested=False,portfolio_replay=False)
        qualified=encode(dict(data,summary=summary))
        acceptance=dict(qualification_sha256=digest(qualified),config_sha256='e'*64,code_pins=codepins,
            source_pins=pins,full_policy_id=policy,symbols=list(args['symbols']),evaluation_sessions=['2026-04-08'])
        return objects,raw,qualified,acceptance,bank

    def consumer(self,**kwargs):
        objects,raw,qualified,acceptance,bank=self.bundle(**kwargs)
        c=ReferenceConsumer(self.bindings,qualified,raw['records'],raw['calendar'],acceptance,enabled=True)
        return c,objects,bank

    def event(self,consumer,symbol='AAPL',offset=0,index=1,price=100*10**9,size=10000,**kw):
        core=self.bindings.core;day='2026-04-08';at=consumer.calendar[day]['open_ns']+offset
        return core.DepthEvent('xnas-itch-20260408.mbp-10.csv.zst:'+str(index),symbol,at,at,day,
            (core.DepthLevel(price-10**7,size),),(core.DepthLevel(price+10**7,size),),
            source_index=index,feed_instrument_id=consumer.symbols.index(symbol)+1,
            source_records=((index,at,at,index,128,2,consumer.symbols.index(symbol)+1),),**kw)

    def clocks(self,c):return self.calendar_adapter.typed_session_calendar(self.bindings.core,c.calendar)
    def callback(self,c,event):
        candidate=c.project(event);r,f,anchor,end,exit=candidate
        paper=self.bindings.core.CashPaperAccount(cash_nano=10000*10**9);paper.observe(event)
        return c.admit_daily_paper_entry(r,f,anchor,event,paper,c.reference_rows,
            policy_identity=c.full_policy,session_calendar=self.clocks(c),entry_start_ns=c.calendar[event.session]['open_ns'],
            entry_end_ns=end,exit_start_ns=exit,corporate_action_contract='no_actions_assumed_for_sensitivity',allow_reported_close_sensitivity=True)

    def test_real_fraction_math_and_strict_sequence(self):
        c,_,_=self.consumer();e=self.event(c);r=self.callback(c,e)
        self.assertIsNotNone(r['plan']);self.assertTrue(r['native_proportional_precheck_preserved'])
        self.assertEqual(r['receipt']['forecast_stream'],PRODUCER)
        self.assertEqual(r['receipt']['shadow_nav_nano'],10000*10**9)
        self.assertEqual(r['receipt']['loss_budget_nano'],200*10**9)
        self.assertTrue(all(type(x)is F for x in r['receipt']['cohort']['residuals']))
        self.assertEqual(r['receipt']['original_native_sizing']['observed_residual_second_moment'],F(1,100))

    def test_actual_full_stage_matching_economics_and_reference_type_rejection(self):
        c,objects,_=self.consumer(full_mu=.05);e=self.event(c);reference=self.callback(c,e)
        historical=[r for r in objects['records']['records']if r['created_at'][:10]<'2026-03-31']
        ledger=self.old_ledger.build_own_ledger(historical,objects['attempts']['attempts'],objects['outcomes']['outcomes'],
            objects['errors']['errors'],c.calendar,expected_policy_identity=self.full.FULL_POLICY,
            expected_model_source_sha256=self.full.MODEL_SOURCE_SHA)
        full=self.full.ConditionalOpenSimulationCore(self.bindings.core,ledger,conditional_normalizer_transfer=self.bindings.projection.ASSUMPTION)
        record,wrapped,anchor,end,exit=c.project(e)
        def call(forecast):
            paper=self.bindings.core.CashPaperAccount(cash_nano=10000*10**9);paper.observe(e)
            return full.admit_daily_paper_entry(record,forecast,anchor,e,paper,full.native_residual_rows,
                policy_identity=self.full.FULL_POLICY,session_calendar=self.clocks(c),entry_start_ns=c.calendar[e.session]['open_ns'],
                entry_end_ns=end,exit_start_ns=exit,corporate_action_contract='no_actions_assumed_for_sensitivity',allow_reported_close_sensitivity=True)
        original=call(wrapped.source_projection)
        self.assertEqual(reference['broker_fee_sizing'],original['broker_fee_sizing'])
        self.assertEqual(reference['plan'].maximum_buy_price_nano,original['plan'].maximum_buy_price_nano)
        with self.assertRaises(ValueError):call(wrapped)

    def test_reference_candidates_dispatch_report_and_divergent_fresh_cash(self):
        arms=[]
        for _ in range(2):
            c,_,_=self.consumer();s=make_reference_scenario(c,{},c.days,source_manifest_sha256='a'*64,enabled=True)
            arms.append((c,s))
        for n,(c,s) in enumerate(arms):
            e=self.event(c,'MSFT');original=c.admit_daily_paper_entry
            def once(*args,**kwargs):
                result=original(*args,**kwargs)
                self.assertIs(type(args[1]),ReferenceForecast)
                return dict(result,plan=None,status='TEST_ONLY_FORCED_DECLINE')
            with patch.object(self.r48.CausalOpenScenario,'step',side_effect=AssertionError('full dispatch')):
                if n==1:
                    with patch.object(c,'admit_daily_paper_entry',side_effect=once):s.step(c,e,sessions=self.clocks(c))
                else:s.step(c,e,sessions=self.clocks(c))
            s.step(c,self.event(c,'MSFT',100_000_000,2),sessions=self.clocks(c))
            free=s.account.cash_nano-s.account.reserved_cash_nano
            s.step(c,self.event(c,'AAPL',200_000_000,3),sessions=self.clocks(c))
            row=c.broker_fee_admissions[-1]
            self.assertEqual(row['shadow_nav_nano'],free)
            self.assertEqual(row['sizing']['loss_budget_nano'],free//50)
            self.assertEqual(row['forecast_id'],s.reference_candidates['2026-04-08','AAPL'][1].identity)
            for order in s.account.orders.values():
                self.assertIn(order['forecast_id'],{f.identity for _,f,_,_,_ in s.reference_candidates.values()})
            with patch.object(self.r48.CausalOpenScenario,'result',side_effect=AssertionError('full report')):
                report=s.result({'2026-04-08':0})
            self.assertEqual(report['forecast_stream'],PRODUCER);self.assertFalse(report['full_model_attribution_included'])
            self.assertNotIn('forecast_component_attribution',report)
            self.assertTrue(all(r['producer']==PRODUCER for r in report['reference_candidate_manifest']))
        self.assertNotEqual(arms[0][0].broker_fee_admissions[-1]['sizing']['shares'],arms[1][0].broker_fee_admissions[-1]['sizing']['shares'])
        self.assertLess(arms[0][1].account.cash_nano,arms[1][1].account.cash_nano)

    def test_support_and_input_perturbations(self):
        low,_,_=self.consumer(count=29);self.assertIsNone(self.callback(low,self.event(low))['plan'])
        ready,_,_=self.consumer(count=30);self.assertIsNotNone(self.callback(ready,self.event(ready))['plan'])
        a,_,_=self.consumer(full_mu=.08);b,_,_=self.consumer(full_mu=.20)
        ra=self.callback(a,self.event(a));rb=self.callback(b,self.event(b))
        self.assertEqual(ra['broker_fee_sizing'],rb['broker_fee_sizing'])
        c,_,_=self.consumer(mu=.06);rc=self.callback(c,self.event(c))
        self.assertNotEqual(ra['broker_fee_sizing']['shares'],rc['broker_fee_sizing']['shares'])
        d,_,_=self.consumer(actual=.21);rd=self.callback(d,self.event(d))
        self.assertNotEqual(ra['broker_fee_sizing']['shares'],rd['broker_fee_sizing']['shares'])

    def test_native_partial_retry_expiry_and_required_exit(self):
        from dataclasses import replace
        c,_,_=self.consumer()
        scenario=make_reference_scenario(c,{'2026-04-14':'2026-04-15'},c.days,
            source_manifest_sha256='a'*64,enabled=True)
        def step(offset,index,size=20):
            event=self.event(c,'MSFT',offset,index,size=size)
            scenario.step(c,event,sessions=self.clocks(c));return event
        step(0,1);self.assertIn('MSFT',scenario.account.pending)
        step(100_000_000,2)
        self.assertEqual(scenario.account.positions['MSFT'],1)
        self.assertTrue(any(o['filled_shares']==1 and o['cancelled_shares']>0 for o in scenario.account.orders.values()))
        self.assertIn('MSFT',scenario.account.pending)
        self.assertGreater(scenario.account.reserved_cash_nano,0)
        step(60*10**9+1,3)
        self.assertNotIn('MSFT',scenario.account.pending)
        self.assertEqual(scenario.account.reserved_cash_nano,0)
        self.assertEqual(scenario.account.positions['MSFT'],1)
        cash=scenario.account.cash_nano
        base=self.event(c,'MSFT',index=4)
        at=c.calendar['2026-04-14']['close_ns']-60*10**9
        def exit_event(at,index):
            return replace(base,identity='xnas-itch-20260414.mbp-10.csv.zst:'+str(index),
                available_ns=at,event_ns=at,session='2026-04-14',source_index=index,
                source_records=((index,at,at,index,128,2,base.feed_instrument_id),))
        scenario.step(c,exit_event(at,4),sessions=self.clocks(c))
        self.assertEqual(scenario.account.pending['MSFT'].side,'A')
        scenario.step(c,exit_event(at+100_000_000,5),sessions=self.clocks(c))
        self.assertEqual(scenario.account.positions['MSFT'],0)
        self.assertEqual(scenario.account.cash_nano,cash)
        self.assertTrue(scenario.account.unsettled)
        scenario.account.assert_invariants()

    def test_membership_identity_and_null_mask_fail_closed(self):
        import json
        _,raw,qualified,acceptance,_=self.bundle()
        for field in ('original_ids','missing_original_ids'):
            data=json.loads(qualified)
            data['memberships'][0][field]=['f'*64]
            data['summary']['semantic_sha256']=digest(encode({k:v for k,v in data.items()if k!='summary'}))
            altered=encode(data);context=dict(acceptance,qualification_sha256=digest(altered))
            with self.subTest(field=field),self.assertRaisesRegex(ValueError,'membership'):
                ReferenceConsumer(self.bindings,altered,raw['records'],raw['calendar'],context,enabled=True)

    def test_clock_book_and_foreign_row_boundaries(self):
        from dataclasses import replace
        c,_,_=self.consumer()
        self.assertIsNone(c.project(self.event(c,offset=-1)))
        self.assertIsNone(c.project(self.event(c,offset=60*10**9+1)))
        for fields in ({'reset':True},{'valid':False}):
            fresh,_,_=self.consumer()
            self.assertIsNone(fresh.project(self.event(fresh,**fields)))
        c,_,_=self.consumer();event=self.event(c);candidate=c.project(event)
        record,forecast,anchor,end,exit=candidate
        def call(e,rows=None,calendar=None):
            paper=self.bindings.core.CashPaperAccount(cash_nano=10000*10**9);paper.observe(e)
            return c.admit_daily_paper_entry(record,forecast,anchor,e,paper,
                c.reference_rows if rows is None else rows,policy_identity=c.full_policy,
                session_calendar=self.clocks(c)if calendar is None else calendar,
                entry_start_ns=c.calendar[e.session]['open_ns'],entry_end_ns=end,exit_start_ns=exit,
                corporate_action_contract='no_actions_assumed_for_sensitivity',allow_reported_close_sensitivity=True)
        self.assertIsNotNone(call(self.event(c,offset=60*10**9,index=2))['plan'])
        self.assertIsNone(call(self.event(c,offset=60*10**9+1,index=3))['plan'])
        with self.assertRaisesRegex(ValueError,'own reference row stream'):call(event,rows=tuple(list(c.reference_rows)))
        with self.assertRaisesRegex(ValueError,'calendar binding'):call(event,calendar=self.clocks(c)[:-1])
        with self.assertRaises(ValueError):call(replace(event,event_ns=event.available_ns+1))

    def test_no_positive_proportional_economics_preserves_abstention(self):
        c,_,_=self.consumer(mu=-.01)
        result=self.callback(c,self.event(c))
        self.assertIsNone(result['plan'])
        self.assertEqual(result['status'],'NO_POSITIVE_NET_FORECAST')
        self.assertEqual(c.broker_fee_admissions,[])

    def test_source_tampering_disabled_bindings_and_anchor_freeze(self):
        objects,raw,qualified,acceptance,_=self.bundle()
        with self.assertRaises(ValueError):ReferenceConsumer(self.bindings,qualified+b' ',raw['records'],raw['calendar'],acceptance,enabled=True)
        with self.assertRaises(ValueError):ReferenceConsumer(self.bindings,qualified,raw['records']+b' ',raw['calendar'],acceptance,enabled=True)
        c=ReferenceConsumer(self.bindings,qualified,raw['records'],raw['calendar'],acceptance,enabled=True)
        first=c.project(self.event(c));later=c.project(self.event(c,offset=1_000_000,index=2,price=101*10**9))
        self.assertEqual(first[1].anchor,later[1].anchor)
        self.assertEqual(first[1].expected_period_return,F(1,20))
        self.assertNotEqual(first[1].identity,first[1].source_projection.identity)


if __name__=='__main__':unittest.main()
