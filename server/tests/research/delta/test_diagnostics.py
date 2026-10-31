import copy
from decimal import Decimal as D
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
import numpy as np

from backend.delta.diagnostics.diagnostic_gate import evaluate,fee,normalized_cash,intent_class
from backend.delta.diagnostics.risk_math import covariance,envelope,digest
from backend.delta.diagnostics.synthetic_ledger import SyntheticLedger


def fixture():
    rows=[dict(session=str(i),end_ns=i+1,available_ns=i+2,returns=dict(A=a,B=b))
          for i,(a,b) in enumerate([(0.01,0),(-0.01,0),(0,0.02),(0,-0.02)])]
    cov=covariance(rows,['A','B'],10,minimum_history=4)
    spec=dict(rate='0.01',minimum='0.01',quantum='0.000001',aggregation='ONE_PARENT_AGGREGATE_SYNTHETIC')
    p=dict(profile_id='SYNTHETIC_V1_NOT_USER_POLICY',kind='SYNTHETIC_FIXTURE_ONLY',
           capability_status='ASSUMED_SCENARIO',cash_convention='GROSS_CASH_PLUS_PURCHASE_PAYABLE',
           feedback_mapping_id='SYNTHETIC_EVENT_LEDGER',source_rules_id='FIXTURE_SOURCE',calendar_id='FIXTURE_CALENDAR',
           corporate_action_rule='NO_ACTIONS_SYNTHETIC',minimum_history=4,history_window=4,
           calibration_cut=5,evaluation_cut=10,max_age_ns=5,valid_until_ns=100,
           clock_quality='SIMULATED_VERIFIED',covariance_tolerance=1e-14,max_states=1000,
           exposure_cap='1',name_cap='1',issuer_cap='1',sector_cap='1',volatility_cap='1',stress_cap='1',
           scenario_set_id='FIXTURE_SET',fee=spec,registry={s:dict(instrument_id=s,issuer=s,sector='COMMON') for s in ('A','B')},
           reconciliation_rule='FIXTURE_EXACT',exception_owner='FIXTURE_OWNER',rearm_rule='NEVER_AUTOMATIC',
           policy_authority='FIXTURE_ONLY',execution_scope='FIXED_LIMIT_SYNTHETIC_GRID')
    s=dict(at_ns=10,reconciled=True,cash='100',purchase_payable='0',receivables=[],holdings={},pending=[],
           marks={k:dict(price='50',event_ns=9,available_ns=9,instrument_id=k,publisher='FIXTURE',lineage_hash='f'*64) for k in ('A','B')})
    order=dict(id='buy:A',symbol='A',side='BUY',quantity='1',step='0.5',limit='50',status='OPEN',at_ns=10)
    scenarios=[dict(id='SHOCK_FIXTURE',horizon='ONE_SESSION',nodes=[dict(A='-0.2',B='-0.2')],liquidate=True)]
    return rows,cov,p,s,order,scenarios


class MathTests(unittest.TestCase):
    def test_covariance_rational_fixture(self):
        _,c,*_=fixture();self.assertAlmostEqual(c['shrinkage'],17/18)
        np.testing.assert_allclose(c['lw'],np.diag([29/240000,31/240000]),rtol=1e-12)
        np.testing.assert_allclose(c['sample'],np.diag([1/15000,1/3750]),rtol=1e-12)

    def test_installed_sklearn_comparison(self):
        from sklearn.covariance import LedoitWolf
        rows,c,*_=fixture();a=np.array([[r['returns'][s] for s in ('A','B')] for r in rows]);m=LedoitWolf().fit(a)
        np.testing.assert_allclose(m.covariance_,c['lw'],rtol=1e-12);self.assertAlmostEqual(m.shrinkage_,c['shrinkage'])

    def test_permutation_and_scaling(self):
        rows,c,*_=fixture();p=covariance(rows,['B','A'],10,minimum_history=4)
        np.testing.assert_allclose(np.array(p['lw'])[::-1,::-1],c['lw'])
        for r in rows:r['returns']={k:3*v for k,v in r['returns'].items()}
        scaled=covariance(rows,['A','B'],10,minimum_history=4)
        np.testing.assert_allclose(scaled['lw'],np.array(c['lw'])*9);self.assertAlmostEqual(scaled['shrinkage'],c['shrinkage'])

    def test_one_asset_and_isotropic(self):
        rows,_,*_=fixture();one=[{**r,'returns':{'A':r['returns']['A']}} for r in rows]
        self.assertEqual(covariance(one,['A'],10,minimum_history=4)['shrinkage'],0)
        for r in rows:r['returns']['B']/=2
        self.assertEqual(covariance(rows,['A','B'],10,minimum_history=4)['d2'],0)

    def test_history_time_missing_nonfinite(self):
        rows,*_=fixture()
        for mutation in ('future','duplicate','missing','nan','constant'):
            r=copy.deepcopy(rows)
            if mutation=='future':r[-1]['available_ns']=11
            if mutation=='duplicate':r[-1]['session']=r[0]['session']
            if mutation=='missing':del r[0]['returns']['B']
            if mutation=='nan':r[0]['returns']['B']=float('nan')
            if mutation=='constant':
                for x in r:x['returns']['A']=0
            with self.subTest(mutation=mutation),self.assertRaises(ValueError):covariance(r,['A','B'],10,minimum_history=4)
        for bad in (True,1,10**40,-1):
            with self.assertRaises(ValueError):covariance(rows,['A','B'],bad,minimum_history=4)

    def test_invalid_psd_horizon_coverage(self):
        _,c,*_=fixture();c['lw']=[[1,2],[2,1]]
        with self.assertRaises(ValueError):envelope(c,{'A':D(1)},D(100),1e-14)


class GateTests(unittest.TestCase):
    def run_gate(self,p=None,s=None,o='default',cov=None,scenarios=None,at=10):
        _,c,profile,state,order,sc=fixture()
        return evaluate(s if s is not None else state,order if o=='default' else o,
                        p if p is not None else profile,cov if cov is not None else c,
                        scenarios if scenarios is not None else sc,at)

    def test_synthetic_pass_and_determinism_immutability(self):
        _,c,p,s,o,sc=fixture();before=copy.deepcopy((s,o,p,c,sc));a=evaluate(s,o,p,c,sc,10)
        self.assertEqual(a['decision'],'PASS_RESEARCH');self.assertFalse(a['action_authorized'])
        self.assertEqual(a,evaluate(s,o,p,c,sc,10));self.assertEqual(before,(s,o,p,c,sc))
        self.assertEqual(a['metrics']['state_count'],3)

    def test_unconfigured_project_and_each_required_field(self):
        from backend.delta.diagnostics.diagnostic_gate import REQUIRED
        _,_,p,*_=fixture()
        for k in REQUIRED:
            q=copy.deepcopy(p);q[k]=None
            with self.subTest(k=k):self.assertEqual(self.run_gate(p=q)['decision'],'NOT_EVALUABLE')
        p['kind']='PROJECT';self.assertEqual(self.run_gate(p=p)['decision'],'NOT_EVALUABLE')

    def test_missing_and_breach_precedence(self):
        _,_,p,s,*_=fixture();s['purchase_payable']='101';p['name_cap']=None
        r=self.run_gate(p=p,s=s);self.assertEqual(r['decision'],'NOT_EVALUABLE');self.assertIn('DEFINITE_FUNDING_BREACH',r['reasons'])

    def test_no_policy_pruning_and_funding(self):
        _,_,p,s,o,_=fixture();p['name_cap']='0.1';o['quantity']='3';o['step']='1'
        r=self.run_gate(p=p,s=s,o=o);self.assertEqual(r['decision'],'REJECT')
        self.assertEqual(r['metrics']['state_count'],4);self.assertEqual(r['metrics']['excluded_for_policy'],0)
        self.assertIn('FULL_COMMITMENT_FUNDING_BREACH',r['reasons'])

    def test_no_partial_bound_on_bad_nav_or_domain_limit(self):
        _,_,p,s,o,_=fixture();o['quantity']='3';o['step']='1';o['limit']='100';s['marks']['A']['price']='1'
        r=self.run_gate(p=p,s=s,o=o);self.assertEqual(r['decision'],'NOT_EVALUABLE');self.assertEqual(r['metrics'],{})
        p['max_states']=2;self.assertEqual(self.run_gate(p=p)['decision'],'NOT_EVALUABLE')

    def test_missing_stale_identity_and_time(self):
        _,_,p,s,*_=fixture()
        for mutation in ('missing','stale','identity','future'):
            q=copy.deepcopy(s)
            if mutation=='missing':del q['marks']['A']
            if mutation=='stale':q['marks']['A']['event_ns']=0
            if mutation=='identity':q['marks']['A']['instrument_id']='OTHER'
            if mutation=='future':q['marks']['A']['available_ns']=11
            self.assertEqual(self.run_gate(s=q)['decision'],'NOT_EVALUABLE')
        self.assertEqual(self.run_gate(at=18)['decision'],'NOT_EVALUABLE')

    def test_duplicate_pending_cancel_and_sell_no_credit(self):
        _,_,p,s,o,_=fixture();s['pending']=[copy.deepcopy(o)]
        self.assertEqual(self.run_gate(s=s,o=o)['decision'],'NOT_EVALUABLE')
        s['pending'][0]['status']='CANCEL_PENDING';r=self.run_gate(s=s,o=None)
        self.assertEqual(r['metrics']['state_count'],3)
        s['holdings']={'A':'1'};s['cash']='0';s['pending']=[dict(o,id='sell',side='SELL')]
        r=self.run_gate(s=s,o=dict(o,id='new:B',symbol='B'))
        self.assertIn('FULL_COMMITMENT_FUNDING_BREACH',r['reasons'])

    def test_predecision_loss_identity_and_fee_floor(self):
        r=self.run_gate();state=r['metrics']['states'][-1];sc=state['scenarios'][0]
        self.assertEqual(D(state['nav']),D('99.5'));self.assertEqual(D(sc['total_loss']),D('.109'))
        self.assertAlmostEqual(float(sc['conditional_loss']),10.4/99.5)
        spec=fixture()[2]['fee'];self.assertEqual(fee(D(0),spec),0);self.assertEqual(fee(D('.1'),spec),D('.01'))

    def test_path_initial_loss_not_drawdown(self):
        _,_,p,s,_,_=fixture();s['cash']='0';s['holdings']={'A':'1'};s['marks']['A']['price']='100'
        values=['-.05',str(D(80)/95-1),'.875',str(D(110)/150-1),str(D(120)/110-1)]
        sc=[dict(id='PATH_FIXTURE',horizon='FIVE_SESSIONS',nodes=[dict(A=v) for v in values],liquidate=False)]
        r=self.run_gate(s=s,o=None,scenarios=sc);loss=r['metrics']['states'][0]['scenarios'][0]
        self.assertAlmostEqual(float(loss['path_loss']),.2);self.assertAlmostEqual(float(loss['total_loss']),-.2)

    def test_cash_normalization_intent_classes_and_dividend(self):
        self.assertEqual(normalized_cash('GROSS_CASH_PLUS_PURCHASE_PAYABLE','100','50.5'),D('49.5'))
        self.assertEqual(normalized_cash('NATIVE_BUYS_ALREADY_DEBITED','49.5','0'),D('49.5'))
        with self.assertRaises(ValueError):normalized_cash('NATIVE_BUYS_ALREADY_DEBITED','49.5','50.5')
        self.assertEqual(intent_class('1','-1'),'CLOSE')
        with self.assertRaises(ValueError):intent_class('0','-1')
        _,_,p,s,o,_=fixture();s['cash']='0';s['holdings']={'A':'.5'};s['marks']['A']['price']='98';s['receivables']=[dict(kind='DIVIDEND',event_id='div1',amount='1')]
        r=self.run_gate(s=s,o=None);self.assertEqual(D(r['metrics']['baseline_nav']),50)
        self.assertEqual(D(r['metrics']['available_cash']),0)


class LifecycleTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.path=Path(self.temp.name)/'fixture.sqlite'
        self.l=SyntheticLedger(self.path,starting_cash='100',scope='SYNTHETIC_FIXTURE_ONLY');self.at=0
    def tearDown(self):self.l.close();self.temp.cleanup()
    def ev(self,kind,**kw):
        self.at+=1;return dict(id='event:'+str(self.at),at_ns=self.at,kind=kind,**kw)
    def admit(self):
        s=self.l.snapshot();return self.ev('ADMIT',intent_id='buy:A',symbol='A',quantity='1',limit='50',fee=fixture()[2]['fee'],
                    expected_version=s['version'],context_hash=digest(s),quote_ns=0,max_age_ns=5,valid_until_ns=10,
                    clock_quality='SIMULATED_VERIFIED',profile_kind='SYNTHETIC_FIXTURE_ONLY',step='0.1',execution_quantum='0.1')
    def test_partial_cancel_race_settlement_duplicate(self):
        self.l.event(self.admit());self.assertEqual(self.l.available(),D('49.5'))
        self.l.event(self.ev('SUBMIT_UNKNOWN',intent_id='buy:A'))
        f=self.ev('FILL',intent_id='buy:A',quantity='.4',price='50');self.l.event(f);a=self.l.snapshot();self.l.event(f);self.assertEqual(a,self.l.snapshot())
        self.l.event(self.ev('CANCEL_REQUEST',intent_id='buy:A'));self.assertEqual(self.l.available(),D('49.5'))
        self.l.event(self.ev('FILL',intent_id='buy:A',quantity='.2',price='50'))
        self.l.event(self.ev('TERMINAL',intent_id='buy:A',authoritative=True,executions_reconciled=True))
        self.assertEqual(self.l.nav({'A':'50'}),D('99.7'));self.assertEqual(self.l.available(),D('69.7'))
        self.l.event(self.ev('SETTLE_BUY',fill_id=f['id']));self.assertEqual(self.l.nav({'A':'50'}),D('99.7'))
    def test_fenced_unsent_vs_unknown_and_restart(self):
        self.l.event(self.admit());self.l.event(self.ev('FENCE_UNSENT',intent_id='buy:A'));self.assertEqual(self.l.available(),100)
        self.l.close();self.l=SyntheticLedger(self.path,starting_cash='100',scope='SYNTHETIC_FIXTURE_ONLY');self.assertEqual(self.l.available(),100)
    def test_unknown_expiry_latches_retains_reserve(self):
        self.l.event(self.admit());self.l.event(self.ev('SUBMIT_UNKNOWN',intent_id='buy:A'))
        with self.assertRaises(ValueError):self.l.event(self.ev('FENCE_UNSENT',intent_id='buy:A'))
        self.assertTrue(self.l.snapshot()['latch']);self.assertEqual(self.l.available(),D('49.5'))
    def test_crash_before_after_and_redelivery(self):
        e=self.admit()
        with self.assertRaises(RuntimeError):self.l.event(e,crash='before_commit')
        self.assertEqual(self.l.available(),100)
        with self.assertRaises(RuntimeError):self.l.event(e,crash='after_commit')
        before=self.l.snapshot();self.l.close();self.l=SyntheticLedger(self.path,starting_cash='100',scope='SYNTHETIC_FIXTURE_ONLY')
        self.l.event(e);self.assertEqual(before,self.l.snapshot())
    def test_stale_version_duplicate_intent_and_stale_clock(self):
        e=self.admit();e['expected_version']=1
        with self.assertRaises(ValueError):self.l.event(e)
        self.assertTrue(self.l.snapshot()['latch'])
    def test_unchanged_hash_becomes_stale(self):
        e=self.admit();e['at_ns']=9
        with self.assertRaises(ValueError):self.l.event(e)
        self.assertEqual(self.l.available(),100)
    def test_huge_fractional_bool_negative_clock_failclosed(self):
        for at in [10**40,-1,.5,True]:
            e=self.admit();e['at_ns']=at
            with self.assertRaises(ValueError):self.l.event(e)
            self.assertTrue(self.l.snapshot()['latch']);self.assertEqual(self.l.available(),100)
    def test_referenced_bust_not_duplicate(self):
        self.l.event(self.admit());self.l.event(self.ev('SUBMIT_UNKNOWN',intent_id='buy:A'))
        f=self.ev('FILL',intent_id='buy:A',quantity='.4',price='50');self.l.event(f)
        self.l.event(self.ev('BUST',fill_id=f['id']));self.assertEqual(self.l.nav({'A':'50'}),100);self.assertEqual(self.l.available(),D('49.5'))
    def test_changed_duplicate_payload_and_rearm(self):
        e=self.admit();self.l.event(e);e['quantity']='.5'
        with self.assertRaises(ValueError):self.l.event(e)
        with self.assertRaises(ValueError):self.l.event(self.ev('REARM'))
        self.assertTrue(self.l.snapshot()['latch'])


if __name__=='__main__':unittest.main()
