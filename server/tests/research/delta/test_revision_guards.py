import copy
from decimal import getcontext,localcontext,ROUND_DOWN,ROUND_UP,Inexact,Rounded
from pathlib import Path
import tempfile
import unittest
from test_diagnostics import fixture
from backend.delta.diagnostics.diagnostic_gate import evaluate,fee,normalized_cash
from backend.delta.diagnostics.synthetic_ledger import SyntheticLedger
from backend.delta.diagnostics.transaction_adapter import admit_synthetic
from backend.delta.diagnostics.arithmetic_policy import SPEC


def context_identity(c):
    return (c.prec,c.rounding,c.Emin,c.Emax,c.clamp,dict(c.traps),dict(c.flags))


class ArithmeticRepairTests(unittest.TestCase):
    def test_identical_receipt_under_adverse_precision_rounding_traps(self):
        _,cov,p,s,o,z=fixture();p['name_cap']='0.5025128'
        reference=evaluate(s,o,p,cov,z,10)
        self.assertEqual(reference['decision'],'PASS_RESEARCH')
        for precision in (2,6,28,70):
            for rounding in (ROUND_DOWN,ROUND_UP):
                with self.subTest(precision=precision,rounding=rounding),localcontext() as ambient:
                    ambient.prec=precision;ambient.rounding=rounding;ambient.Emin=-3;ambient.Emax=3
                    for signal in ambient.traps:ambient.traps[signal]=True
                    ambient.flags[Inexact]=True;ambient.flags[Rounded]=True
                    before=context_identity(ambient)
                    self.assertEqual(evaluate(s,o,p,cov,z,10),reference)
                    self.assertEqual(context_identity(ambient),before)

    def test_fee_and_native_normalization_context_independent(self):
        from decimal import Decimal as D
        spec=fixture()[2]['fee']
        ref=(fee(D('1.23456789'),spec),normalized_cash('GROSS_CASH_PLUS_PURCHASE_PAYABLE','100','50.5'))
        with localcontext() as c:
            c.prec=2;c.rounding=ROUND_DOWN
            for signal in c.traps:c.traps[signal]=True
            self.assertEqual((fee(D('1.23456789'),spec),normalized_cash('GROSS_CASH_PLUS_PURCHASE_PAYABLE','100','50.5')),ref)

    def test_unsupported_external_numeric_domain_fails_closed(self):
        _,cov,p,s,o,z=fixture()
        for value in ('1e999','1e-999','0.12345678901234567890123456789','NaN','Infinity'):
            bad=copy.deepcopy(s);bad['cash']=value
            self.assertEqual(evaluate(bad,o,p,cov,z,10)['decision'],'NOT_EVALUABLE')

    def test_ledger_states_and_events_identical_across_contexts(self):
        _,cov,p,s,o,z=fixture();states=[]
        with tempfile.TemporaryDirectory() as t:
            for precision in (2,6,28):
                with localcontext() as ambient:
                    ambient.prec=precision;ambient.rounding=ROUND_DOWN
                    for signal in ambient.traps:ambient.traps[signal]=True
                    a=SyntheticLedger(Path(t)/str(precision),starting_cash='100',scope='SYNTHETIC_FIXTURE_ONLY')
                    admit_synthetic(a,s,o,p,cov,z,evaluation_time=10,commit_time=10)
                    a.event(dict(id='submit',kind='SUBMIT_UNKNOWN',intent_id=o['id'],at_ns=11))
                    a.event(dict(id='fill',kind='FILL',intent_id=o['id'],at_ns=12,quantity='0.5',price='49.99'))
                    a.event(dict(id='settle',kind='SETTLE_BUY',at_ns=13,fill_id='fill'))
                    states.append((a.snapshot(),a.available(),a.nav({'A':'50'}),a.db.execute('SELECT * FROM events ORDER BY rowid').fetchall()))
                    a.close()
            self.assertTrue(all(x==states[0] for x in states[1:]))


class GridRepairTests(unittest.TestCase):
    def setUp(self):
        self.t=tempfile.TemporaryDirectory();self.path=Path(self.t.name)/'fixture';self.a=SyntheticLedger(self.path,starting_cash='100',scope='SYNTHETIC_FIXTURE_ONLY')
        _,cov,p,s,self.order,z=fixture();self.receipt=admit_synthetic(self.a,s,self.order,p,cov,z,evaluation_time=10,commit_time=10)
        self.a.event(dict(id='submit',kind='SUBMIT_UNKNOWN',intent_id=self.order['id'],at_ns=11))
    def tearDown(self):self.a.close();self.t.cleanup()
    def fill(self,q,ident='fill',at=12):return dict(id=ident,kind='FILL',intent_id=self.order['id'],at_ns=at,quantity=q,price='50')

    def test_original_offgrid_reproduction_preserves_economics_latches_and_recovers(self):
        before=self.a.snapshot();count=self.a.db.execute('SELECT COUNT(*) FROM events').fetchone()[0]
        self.assertEqual(self.receipt['metrics']['state_count'],3)
        with self.assertRaises(ValueError):self.a.event(self.fill('0.1'))
        after=self.a.snapshot()
        for k in ('cash','payable','receivable','holdings','orders','fills','clock','version'):
            self.assertEqual(before[k],after[k])
        self.assertTrue(after['latch']);self.assertEqual(len(after['incidents']),1)
        self.assertEqual(self.a.db.execute('SELECT COUNT(*) FROM events').fetchone()[0],count)
        self.a.close();self.a=SyntheticLedger(self.path,starting_cash='100',scope='SYNTHETIC_FIXTURE_ONLY')
        self.assertEqual(self.a.snapshot(),after)

    def test_ongrid_partial_cancel_bust_settlement_and_duplicate(self):
        f=self.fill('0.5');self.a.event(f);before=self.a.snapshot();self.a.event(f);self.assertEqual(before,self.a.snapshot())
        self.a.event(dict(id='cancel',kind='CANCEL_REQUEST',intent_id=self.order['id'],at_ns=13))
        self.a.event(dict(id='bust',kind='BUST',fill_id='fill',at_ns=14))
        self.assertEqual(self.a.snapshot()['orders'][self.order['id']]['remaining'],'1.0')
        self.a.event(self.fill('0.5','fill2',15))
        self.a.event(dict(id='terminal',kind='TERMINAL',intent_id=self.order['id'],at_ns=16,authoritative=True,executions_reconciled=True))
        nav=self.a.nav({'A':'50'});self.a.event(dict(id='settle',kind='SETTLE_BUY',at_ns=17,fill_id='fill2'))
        self.assertEqual(self.a.nav({'A':'50'}),nav);self.assertFalse(self.a.snapshot()['latch'])

    def test_zero_negative_nan_fill_and_offgrid_remainder(self):
        for q in ('0','-0.5','NaN','0.75'):
            with self.subTest(q=q),self.assertRaises(ValueError):self.a.event(self.fill(q,ident='bad:'+q))
        self.assertTrue(self.a.snapshot()['latch']);self.assertEqual(self.a.snapshot()['holdings'],{})

    def test_admission_quantum_must_be_explicit_positive_and_aligned(self):
        original=self.receipt['reservation_event']
        for q in ('0','-0.5','NaN','0.3',None):
            with tempfile.TemporaryDirectory() as t:
                a=SyntheticLedger(Path(t)/'x',starting_cash='100',scope='SYNTHETIC_FIXTURE_ONLY')
                from backend.delta.diagnostics.risk_math import digest
                e=dict(original,execution_quantum=q,context_hash=digest(a.snapshot()))
                with self.assertRaises(ValueError):a.event(e)
                self.assertTrue(a.snapshot()['latch']);self.assertEqual(a.available(),100);a.close()


if __name__=='__main__':unittest.main()
