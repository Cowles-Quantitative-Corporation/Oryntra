"""Invented means/labels only; optional actual preserved CODE bindings."""
from copy import deepcopy
from dataclasses import replace
from fractions import Fraction as F
from datetime import datetime,timezone
from pathlib import Path
import unittest
from unittest.mock import patch
import test_reference_consumer as fixtures
from backend.delta.research import mean_sensitivity as ms
from backend.delta.research import qualify_reference as q
from backend.delta.research import reference_consumer as own


class PortableSensitivityTests(unittest.TestCase):
    def test_disabled_before_bindings_or_bank(self):
        class Forbidden:
            def verify(self):raise AssertionError('must not verify')
        with self.assertRaisesRegex(ValueError,'disabled'):ms.FullResidualBank(Forbidden(),{}, {})
        with self.assertRaisesRegex(ValueError,'disabled'):ms.MeanOnlySensitivity(None)
        with self.assertRaisesRegex(ValueError,'disabled'):ms.make_sensitivity_scenario(None,{},[],source_manifest_sha256='a'*64)


class NativeSensitivityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixtures.PinnedReferenceConsumerTests.setUpClass()
        cls.fixture=fixtures.PinnedReferenceConsumerTests('test_real_fraction_math_and_strict_sequence')
        cls.bindings=ms.SensitivityBindings(cls.fixture.bindings,cls.fixture.full,cls.fixture.old_ledger)
        cls.bindings.verify()

    def bundle(self,*,mean=.05,full_mean=.05,actual=.11,objects=None,day='2026-04-08',days=None):
        f=self.fixture
        if objects is None:objects,_,_,_,_=f.bundle(mu=mean,full_mu=full_mean,actual=actual)
        else:objects=deepcopy(objects)
        cal=objects['calendar']['sessions'];historical=[r for r in objects['records']['records']if r['created_at'][:10]<'2026-03-31']
        full=f.old_ledger.build_own_ledger(historical,objects['attempts']['attempts'],objects['outcomes']['outcomes'],
            objects['errors']['errors'],cal,expected_policy_identity=ms.FULL_POLICY,expected_model_source_sha256=ms.MODEL_SOURCE_SHA)
        rows=full.native_observations(f.bindings.core);proof=[];evaluation_days=[day]if days is None else days
        for evaluation_day in evaluation_days:
            current=next(r for r in objects['records']['records']if r['metadata']['target_start']==evaluation_day)
            cutoff=cal[current['information_cutoff'][:10]]['close_ns']
            for symbol in f.bindings.family.UNIVERSE:
                c=f.bindings.core.daily_residual_cohort_asof(rows,symbol=symbol,producer=f.bindings.projection.PRODUCER,
                    model_version=f.bindings.projection.VERSION,policy_identity=ms.FULL_POLICY,horizon_sessions=5,cutoff_ns=cutoff)
                proof.append(dict(session=evaluation_day,symbol=symbol,cutoff_ns=cutoff,attempt_identities=list(c['attempt_identities']),
                    residuals_exact=[None if v is None else str(v)for v in c['residuals']],attempted=c['attempted'],observed=c['observed'],missing=c['missing']))
        raw={k:q.encode(v)for k,v in objects.items()};specs={k:dict(path='/invented/'+k+'.json',bytes=len(v),sha256=q.digest(v))for k,v in raw.items()}
        inputs={v['path']:v['sha256']for v in specs.values()};raw['r46_result']=q.encode(dict(inputs=inputs,rows=proof))
        specs['r46_result']=dict(path='/invented/r46_result.json',bytes=len(raw['r46_result']),sha256=q.digest(raw['r46_result']))
        raw['r46_audit']=q.encode(dict(status='INDEPENDENT_R46_OWN_OPEN_LEDGER_ASOF_AUDIT_PASS_NOT_RISK_ADMISSION',result_sha256=q.digest(raw['r46_result']),input_sha256=inputs))
        specs['r46_audit']=dict(path='/invented/r46_audit.json',bytes=len(raw['r46_audit']),sha256=q.digest(raw['r46_audit']))
        context=dict(scope='SYNTHETIC_FIXTURE_ONLY',source_specs=specs,evaluation_sessions=evaluation_days,symbols=list(f.bindings.family.UNIVERSE))
        bank=ms.FullResidualBank(self.bindings,raw,context,enabled=True)
        return ms.MeanOnlySensitivity(bank,enabled=True),bank,objects,raw,context

    def event(self,c,**kw):
        e=self.fixture.event(c,**kw);day=c.days[0]
        if day=='2026-04-08':return e
        delta=e.available_ns-c.calendar['2026-04-08']['open_ns'];at=c.calendar[day]['open_ns']+delta
        return replace(e,session=day,identity='xnas-itch-'+day.replace('-','')+'.mbp-10.csv.zst:'+str(e.source_index),
            available_ns=at,event_ns=at,source_records=((e.source_index,at,at,e.source_index,128,2,e.feed_instrument_id),))
    def clocks(self,c):return self.fixture.clocks(c)
    def call(self,c,event=None,*,forecast=None,record=None,rows=None):
        e=event or self.event(c);candidate=c.project(e);r,f,a,end,exit=candidate
        paper=c.bindings.core.CashPaperAccount(cash_nano=10000*10**9);paper.observe(e)
        return c.admit_daily_paper_entry(r if record is None else record,f if forecast is None else forecast,a,e,paper,
            c.risk_rows if rows is None else rows,policy_identity=ms.FULL_POLICY,session_calendar=self.clocks(c),
            entry_start_ns=c.calendar[e.session]['open_ns'],entry_end_ns=end,exit_start_ns=exit,
            corporate_action_contract='no_actions_assumed_for_sensitivity',allow_reported_close_sensitivity=True)

    def gapped_objects(self,*,due_missing=False,changed=False):
        objects,_,_,_,_=self.fixture.bundle();cal=objects['calendar']['sessions'];days=sorted(cal)
        oldhist=[r for r in objects['records']['records']if r['created_at'][:10]<'2026-03-31']
        original_current=deepcopy(objects['records']['records'][-1]);attempts={(r['origin'],r['symbol']):r for r in objects['attempts']['attempts']}
        outcomes={(r['origin'],r['symbol']):r for r in objects['outcomes']['outcomes']}
        current_index=46 if due_missing else 48;newrecords=[];newattempts=[];newoutcomes=[];newerrors=[]
        for j,old in enumerate(oldhist):
            origin_index=days.index(old['created_at'][:10]);idx=origin_index if j<40 else current_index if j==40 else origin_index+10
            origin,start,end,cut=days[idx],days[idx+1],days[idx+5],days[idx-1]
            record=deepcopy(old);record.update(created_at=origin+'T00:00:00',information_cutoff=cut+'T00:00:00')
            prediction=.05 if j==40 else .01;record['values']={s:prediction for s in self.fixture.bindings.family.UNIVERSE}
            record['metadata'].update(target_start=start,target_end=end,training_information_cutoff=cut+'T00:00:00',reference_training_cutoff=cut+'T00:00:00',
                reference_mean=.05 if j==40 else .01,target_type='absolute',beta_status='PIT_supplied',sec_quality_observed_symbols=list(self.fixture.bindings.family.UNIVERSE),residual_status='bounded_ridge_OOF_residual')
            record_sha=own.ref.canonical_hash(record);newrecords.append(record)
            for symbol in self.fixture.bindings.family.UNIVERSE:
                a=deepcopy(attempts[old['created_at'][:10],symbol]);a.update(origin=origin,target_start=start,due_session=end,forecast=prediction,record_sha256=record_sha)
                aid=own.ref.canonical_hash(dict(record_sha256=record_sha,symbol=symbol,target_start=start,target_end=end,return_convention=own.ref.CONVENTION));a['attempt_id']=aid
                actual=.91 if changed and j==(39 if due_missing else 40)else .11
                available=datetime.fromtimestamp(cal[days[idx+6]]['open_ns']//10**9,timezone.utc).isoformat()
                o=deepcopy(outcomes[old['created_at'][:10],symbol]);o.update(origin=origin,target_start=start,due_session=end,attempt_id=aid,
                    source_adjusted_close=100*(1+actual),realized_simple_return=actual,available_at_simulated=available)
                e=dict(attempt_id=aid,symbol=symbol,origin=origin,target_start=start,due_session=end,forecast=prediction,actual=actual,available_at_simulated=available,error_actual_minus_forecast=actual-prediction,operational_risk_admitted=False)
                newattempts.append(a);newoutcomes.append(o);newerrors.append(e)
        objects['records']['records']=newrecords+[original_current]
        objects['attempts']['attempts']=newattempts;objects['outcomes']['outcomes']=newoutcomes;objects['errors']['errors']=newerrors
        return objects,days[current_index+1]

    def test_future_labels_do_not_change_active_instructions(self):
        arms=[]
        for changed in (False,True):
            objects,day=self.gapped_objects(changed=changed);c,bank,_,_,_=self.bundle(objects=objects,day=day)
            e=self.event(c);forecast=c.project(e)[1];cohort=bank.asof('AAPL',forecast.forecast_information_cutoff_ns);result=self.call(c,e)
            self.assertEqual(cohort['observed'],40);self.assertEqual(cohort['missing'],0);self.assertIsNotNone(result['plan'])
            plan=result['plan'];arms.append((forecast.identity,cohort,result['broker_fee_sizing'],plan.maximum_buy_price_nano,plan.maximum_entry_shares,bank.archive_sha))
        self.assertEqual(arms[0][:-1],arms[1][:-1]);self.assertNotEqual(arms[0][-1],arms[1][-1])

    def test_due_unavailable_stays_missing_even_when_known_by_entry(self):
        tuples=[]
        for changed in (False,True):
            objects,day=self.gapped_objects(due_missing=True,changed=changed);c,bank,_,_,_=self.bundle(objects=objects,day=day)
            e=self.event(c);forecast=c.project(e)[1];cohort=bank.asof('AAPL',forecast.forecast_information_cutoff_ns)
            self.assertEqual(cohort['attempted'],40);self.assertEqual(cohort['missing'],1)
            self.assertIsNone(self.call(c,e)['plan']);self.assertIsNone(self.call(c,self.event(c,offset=100000000,index=2))['plan']);tuples.append(cohort)
        self.assertEqual(tuples[0],tuples[1])

    def test_equal_input_complete_account_trace_matches_full_loop(self):
        c,bank,_,_,_=self.bundle();b=c.bindings;settlement={'2026-04-14':'2026-04-15'};s=ms.make_sensitivity_scenario(c,settlement,c.days,source_manifest_sha256='a'*64,enabled=True)
        full=b.family.ConditionalScenario('C01',settlement,deepcopy(c.calendar),list(c.days),None)
        full.name='R48_'+self.fixture.r48.CASE
        full.account=b.account.MultiAssetCashAccount(source_manifest_sha256='a'*64,settlement_days=settlement,fee_policy=b.policy.BrokerFeePolicy('CURRENT_FIXED_SMART_US'))
        candidates={};idmap={}
        events=[self.event(c,symbol='MSFT'),self.event(c,symbol='MSFT',offset=100000000,index=2,size=20),self.event(c,offset=200000000,index=3),self.event(c,offset=60*10**9+1,index=4)]
        base=events[0]
        for day,at,index in [('2026-04-14',c.calendar['2026-04-14']['close_ns']-60*10**9,5),('2026-04-14',c.calendar['2026-04-14']['close_ns']-60*10**9+100000000,6),('2026-04-16',c.calendar['2026-04-16']['open_ns'],7)]:
            events.append(replace(base,session=day,identity='xnas-itch-'+day.replace('-','')+'.mbp-10.csv.zst:'+str(index),available_ns=at,event_ns=at,source_index=index,source_records=((index,at,at,index,128,2,base.feed_instrument_id),)))
        prior=None
        for e in events:
            if prior is not None and prior!=e.session:s.close_day(prior);full.close_day(prior)
            prior=e.session
            candidate=c.project(e)
            if candidate:
                record,f,a,end,exit=candidate;candidates[e.session,e.symbol]=(record,f.source_projection,a,end,exit)
                idmap[f.identity]=idmap[f.source_projection.identity]=e.session+':'+e.symbol
            s.step(c,e,sessions=self.clocks(c));full.step(bank._owner,e,candidates,bank.rows,self.clocks(c))
            self.assertEqual(s.account.cash_nano,full.account.cash_nano);self.assertEqual(s.account.positions,full.account.positions)
            self.assertEqual(s.account.reserved_cash_nano,full.account.reserved_cash_nano)
        def normalize(value):
            if type(value)is dict:
                return {k:('INTENT:'+v.rsplit(':',1)[-1]if k=='intent_id'else 'FILL:'+v.rsplit(':',1)[-1]if k=='fill_id'else idmap[v]if k=='forecast_id'else normalize(v))for k,v in value.items()}
            if type(value)in (list,tuple):return [normalize(v)for v in value]
            return value
        self.assertEqual(s.account.positions['MSFT'],0);self.assertEqual(s.account.unsettled,{})
        self.assertTrue(s.account.settlement_ledger)
        self.assertEqual(normalize(s.account.ledger),normalize(full.account.ledger))
        self.assertEqual(normalize(list(s.account.orders.values())),normalize(list(full.account.orders.values())))

    def test_complete147_synthetic_full_asof_scopes(self):
        import tempfile
        import test_reference_replay as replay_fixture
        with tempfile.TemporaryDirectory()as directory:
            fixture=replay_fixture.NativeReplayTests('test_authority_before_source_access');fixture.root=Path(directory);fixture.native=self.fixture
            config,_=fixture.build()
            objects={alias:q.decode(Path(config['sources'][alias if alias in ('records','calendar')else 'qualification_'+alias]['path']).read_bytes())for alias in ('records','calendar','attempts','outcomes','errors')}
            c,bank,_,raw,ctx=self.bundle(objects=objects,days=config['days'])
            self.assertEqual(len(bank._cohorts),147)
            for row in q.decode(raw['r46_result'])['rows']:
                cohort=bank.asof(row['symbol'],row['cutoff_ns'])
                self.assertEqual(list(cohort['attempt_identities']),row['attempt_identities'])
                self.assertEqual([None if v is None else str(v)for v in cohort['residuals']],row['residuals_exact'])

    def test_equal_mean_equal_bank_matches_native_full_economics(self):
        c,bank,_,_,_=self.bundle();e=self.event(c);r=self.call(c,e);record,f,a,end,exit=c.project(e)
        paper=c.bindings.core.CashPaperAccount(cash_nano=10000*10**9);paper.observe(e)
        original=bank._owner.admit_daily_paper_entry(record,f.source_projection,a,e,paper,bank.rows,
            policy_identity=ms.FULL_POLICY,session_calendar=self.clocks(c),entry_start_ns=c.calendar[e.session]['open_ns'],
            entry_end_ns=end,exit_start_ns=exit,corporate_action_contract='no_actions_assumed_for_sensitivity',allow_reported_close_sensitivity=True)
        self.assertEqual(r['broker_fee_sizing'],original['broker_fee_sizing'])
        self.assertEqual(r['receipt']['original_native_sizing'],original['receipt']['original_native_sizing'])
        self.assertEqual(r['plan'].maximum_buy_price_nano,original['plan'].maximum_buy_price_nano)
        self.assertIs(type(r['plan']),ms.SensitivityPlan);self.assertTrue(r['artificial_uncertainty_proxy']);self.assertFalse(r['calibrated_reference_risk'])
        self.assertNotEqual(f.identity,f.source_projection.identity)

    def test_reference_mean_reaches_native_strict_lower_and_fees(self):
        c,bank,objects,_,_=self.bundle(mean=.06,full_mean=.08);e=self.event(c)
        before=q.encode(objects);core=c.bindings.core;fees=c.bindings.fees
        with patch.object(core,'empirical_long_share_budget',wraps=core.empirical_long_share_budget)as native,patch.object(fees,'choose_quantity',wraps=fees.choose_quantity)as strict:
            result=self.call(c,e)
        self.assertIsNotNone(result['plan']);self.assertEqual(native.call_args.kwargs['expected_period_return'],F(3,50))
        cutoff=c.project(e)[1].forecast_information_cutoff_ns;cohort=bank.asof(e.symbol,cutoff)
        self.assertEqual(native.call_args.args[0],cohort['residuals'])
        bound=core.finite_cohort_tail_bound(cohort['residuals'])['lower_bound'];anchor=c.project(e)[2].price_nano
        def price(mu):
            value=anchor*(1+mu)*F(9997,10000);return (value.numerator//value.denominator)*9999//10000
        self.assertEqual(strict.call_args.kwargs['mean_exit'],price(F(3,50)))
        self.assertEqual(strict.call_args.kwargs['lower_exit'],price(max(F(-1),F(3,50)+bound)))
        self.assertEqual(result['receipt']['original_native_sizing']['observed_residual_second_moment'],F(1,100))
        self.assertNotEqual(strict.call_args.kwargs['mean_exit'],price(F(2,25)))
        self.assertEqual(before,q.encode(objects))

    def test_source_full_price_properties_do_not_leak(self):
        c,_,_,_,_=self.bundle(mean=.05,full_mean=.08);e=self.event(c);f=c.project(e)[1]
        self.assertEqual(f.terminal_mean_price_nano,f.anchor.price_nano*F(21,20))
        projection=c.bindings.projection.ConditionalOpenProjection
        with patch.object(projection,'terminal_mean_price_nano',new=property(lambda _:(_ for _ in ()).throw(AssertionError('full mean leaked')))):
            self.assertIsNotNone(self.call(c,e)['plan'])

    def test_lower_tail_mean_path_and_bank_only_perturbation(self):
        c,bank,_,_,_=self.bundle(actual=-.09);e=self.event(c);core=c.bindings.core
        with patch.object(core,'empirical_long_share_budget',wraps=core.empirical_long_share_budget)as native:
            self.call(c,e)
        self.assertEqual(core.finite_cohort_tail_bound(native.call_args.args[0])['lower_bound'],F(-1,10))
        a,ba,_,_,_=self.bundle(actual=.11);b,bb,_,_,_=self.bundle(actual=.21)
        ra=self.call(a);rb=self.call(b)
        self.assertEqual(a.project(self.event(a))[1].expected_period_return,b.project(self.event(b))[1].expected_period_return)
        self.assertNotEqual(ra['receipt']['original_native_sizing']['observed_residual_second_moment'],rb['receipt']['original_native_sizing']['observed_residual_second_moment'])
        self.assertNotEqual(ra['plan'].maximum_entry_shares,rb['plan'].maximum_entry_shares)

    def test_both_own_risk_guards_reject_cross(self):
        c,bank,_,_,_=self.bundle();e=self.event(c);record,f,a,end,exit=c.project(e)
        kwargs=dict(policy_identity=ms.FULL_POLICY,session_calendar=self.clocks(c),entry_start_ns=c.calendar[e.session]['open_ns'],entry_end_ns=end,exit_start_ns=exit,corporate_action_contract='no_actions_assumed_for_sensitivity',allow_reported_close_sensitivity=True)
        for owner,rows in [(bank._owner,bank.rows),(self.fixture.consumer()[0],None)]:
            paper=c.bindings.core.CashPaperAccount(cash_nano=10000*10**9);paper.observe(e)
            with self.assertRaises(ValueError):owner.admit_daily_paper_entry(record,f,a,e,paper,rows if rows is not None else owner.reference_rows,**kwargs)
        with self.assertRaises(ValueError):self.call(c,e,rows=tuple(list(bank.rows)))
        with self.assertRaises(ValueError):self.call(c,e,forecast=replace(f,expected_period_return=F(1,3)))

    def test_zero_missing_gate_even_if_native_tail_finite(self):
        c,bank,_,_,_=self.bundle();e=self.event(c);cut=c.project(e)[1].forecast_information_cutoff_ns;original=bank.asof('AAPL',cut)
        for count in (29,30):
            cohort=dict(original,residuals=(F(1,10),)*count,attempted=count,observed=count,missing=0)
            with patch.object(bank,'asof',return_value=cohort):result=self.call(c,e)
            self.assertEqual(result['plan']is not None,count==30)
        residuals=(None,)+(F(1,10),)*84
        self.assertIsNotNone(c.bindings.core.finite_cohort_tail_bound(residuals)['lower_bound'])
        cohort=dict(original,residuals=residuals,observed=84,missing=1)
        with patch.object(bank,'asof',return_value=cohort):
            r=self.call(c,e);self.assertIsNone(r['plan']);self.assertIn('FULL_PROXY',r['reasons'][0])

    def test_native_asof_future_label_and_nanosecond_boundaries(self):
        core=self.bindings.native.core
        def row(actual,available,due=200):
            return core.DailyResidualObservation('attempt','AAPL',self.bindings.native.projection.PRODUCER,self.bindings.native.projection.VERSION,ms.FULL_POLICY,5,10,100,due,.01,actual,available)
        def cohort(rows,cutoff=200):
            return core.daily_residual_cohort_asof(tuple(rows),symbol='AAPL',producer=self.bindings.native.projection.PRODUCER,model_version=self.bindings.native.projection.VERSION,policy_identity=ms.FULL_POLICY,horizon_sessions=5,cutoff_ns=cutoff)
        self.assertEqual(cohort([row(.11,300)]),cohort([row(.91,300)]))
        self.assertEqual(cohort([row(.11,300)])['residuals'],(None,))
        self.assertEqual(cohort([row(.11,200,due=199)])['residuals'],(F(1,10),))
        self.assertEqual(cohort([row(.11,201,due=199)])['residuals'],(None,))
        self.assertEqual(cohort([row(.11,500,due=400)])['attempted'],0)

    def test_zero_moment_negative_mean_and_price_domain(self):
        c,bank,_,_,_=self.bundle(actual=.01)
        r=self.call(c);self.assertIsNotNone(r['plan'])
        self.assertEqual(r['receipt']['original_native_sizing']['observed_residual_second_moment'],0)
        self.assertEqual(r['broker_fee_sizing']['shares'],r['broker_fee_sizing']['maximum_native_plan_shares'])
        negative,_,_,_,_=self.bundle(mean=-.01)
        with patch.object(negative.bindings.core,'empirical_long_share_budget',side_effect=AssertionError('precheck must reject')):
            r=self.call(negative);self.assertIsNone(r['plan']);self.assertEqual(r['status'],'NO_POSITIVE_NET_FORECAST')
        low,_,_,_,_=self.bundle()
        r=self.call(low,self.event(low,price=500000000));self.assertIsNone(r['plan']);self.assertEqual(r['status'],'SCENARIO_PRICE_OR_LOWER_PROXY_BELOW_USD1')

    def test_bank_prediction_is_full_and_scope_cannot_self_certify(self):
        c,bank,_,_,_=self.bundle(mean=.05,full_mean=.08)
        self.assertEqual(bank.rows[0].saved_prediction,.01)
        f=c.project(self.event(c))[1];self.assertEqual(f.expected_period_return,F(1,20))
        self.assertEqual(f.source_projection.expected_period_return,F(2,25))
        with self.assertRaises((AttributeError,TypeError)):bank.rows[0].saved_prediction=.05
        with self.assertRaises(ValueError):replace(f,calibrated_reference_risk=True)
        with self.assertRaises(ValueError):replace(f,expected_period_return=F(-1))
        with self.assertRaises(ValueError):bank.asof('AAPL',f.forecast_information_cutoff_ns+1)

    def test_exact_strict_quantity_tie_prefers_smaller_quantity(self):
        b=self.bindings.native;policy=b.policy.BrokerFeePolicy('CURRENT_FIXED_SMART_US');nav=10000*10**9;reference=100*10**9;entry=100*10**9;mean=105*10**9
        profit=mean-entry-policy.fee(1,entry,'B')-policy.fee(1,mean,'A');discount=F(3,10000)
        moment=F(profit*nav,15);residual_mse=moment/(reference**2*(1-discount)**2)
        result=b.fees.choose_quantity(policy=policy,maximum_shares=2,entry_limit=entry,mean_exit=mean,lower_exit=entry,
            free_cash=nav,nav=nav,marked_price=reference,loss_budget=nav//50,exposure_cap=F(1,2),reference_price=reference,
            residual_second_moment=residual_mse,discount=discount)
        utility=lambda qty:F(qty*profit,nav)-10*F(qty*qty,2*nav**2)*moment
        self.assertEqual(utility(1),utility(2));self.assertGreater(utility(1),0);self.assertEqual(result['shares'],1)

    def test_source_and_proof_tampering_fail_closed(self):
        _,_,_,raw,ctx=self.bundle()
        changed=dict(raw,records=raw['records']+b' ')
        with self.assertRaises(ValueError):ms.FullResidualBank(self.bindings,changed,ctx,enabled=True)
        proof=q.decode(raw['r46_result']);proof['rows'][0]['residuals_exact'][0]='999'
        raw=dict(raw,r46_result=q.encode(proof));ctx=deepcopy(ctx);ctx['source_specs']['r46_result'].update(bytes=len(raw['r46_result']),sha256=q.digest(raw['r46_result']))
        audit=q.decode(raw['r46_audit']);audit['result_sha256']=q.digest(raw['r46_result']);raw['r46_audit']=q.encode(audit);ctx['source_specs']['r46_audit'].update(bytes=len(raw['r46_audit']),sha256=q.digest(raw['r46_audit']))
        with self.assertRaisesRegex(ValueError,'full exact residual parity'):ms.FullResidualBank(self.bindings,raw,ctx,enabled=True)

    def test_raw_availability_plus_one_ns_cannot_be_truncated(self):
        _,_,objects,_,_=self.bundle()
        for alias in ('outcomes','errors'):
            row=objects[alias][alias][0]
            value=row['available_at_simulated']
            row['available_at_simulated']=value.replace('+00:00','.000000001+00:00')
        with self.assertRaisesRegex(ValueError,'exact full modeled availability'):self.bundle(objects=objects)

    def test_account_feedback_partial_expiry_and_report_labels(self):
        arms=[]
        for _ in range(2):
            c,_,_,_,_=self.bundle();s=ms.make_sensitivity_scenario(c,{},c.days,source_manifest_sha256='a'*64,enabled=True);arms.append((c,s))
        for index,(c,s)in enumerate(arms):
            e=self.event(c,symbol='MSFT');callback=c.admit_daily_paper_entry
            def decline(*a,**kw):return dict(callback(*a,**kw),plan=None,status='TEST_ONLY_DECLINE')
            if index:
                with patch.object(c,'admit_daily_paper_entry',side_effect=decline):s.step(c,e,sessions=self.clocks(c))
            else:s.step(c,e,sessions=self.clocks(c))
            s.step(c,self.event(c,symbol='MSFT',offset=100000000,index=2,size=20),sessions=self.clocks(c))
            free=s.account.cash_nano-s.account.reserved_cash_nano
            s.step(c,self.event(c,offset=200000000,index=3),sessions=self.clocks(c))
            self.assertEqual(c.broker_fee_admissions[-1]['shadow_nav_nano'],free)
            self.assertEqual(c.broker_fee_admissions[-1]['sizing']['loss_budget_nano'],free//50)
            s.step(c,self.event(c,offset=60*10**9+1,index=4),sessions=self.clocks(c))
            self.assertEqual(s.account.reserved_cash_nano,0)
            report=s.result({'2026-04-08':0});self.assertTrue(report['artificial_uncertainty_proxy']);self.assertFalse(report['calibrated_reference_risk'])
            self.assertFalse(report['full_model_attribution_included'])
            self.assertTrue(all(row['forecast_id']in {f.identity for _,f,_,_,_ in s.sensitivity_candidates.values()}for row in s.account.orders.values()))
        self.assertNotEqual(arms[0][0].broker_fee_admissions[-1]['shadow_nav_nano'],arms[1][0].broker_fee_admissions[-1]['shadow_nav_nano'])


if __name__=='__main__':unittest.main()
