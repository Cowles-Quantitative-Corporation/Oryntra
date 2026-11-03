"""Synthetic regression evidence for pending-parent fee compatibility only."""
import copy
from decimal import Decimal, localcontext, ROUND_UP
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from test_diagnostics import fixture
from backend.delta.diagnostics import transaction_adapter as adapter
from backend.delta.diagnostics.synthetic_ledger import SyntheticLedger
from backend.delta.diagnostics.risk_math import digest


class PendingFeeBindingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / 'ledger.sqlite'
        self.ledger = SyntheticLedger(self.path, starting_cash='100', scope='SYNTHETIC_FIXTURE_ONLY')
        _, self.cov, self.profile, self.market, self.order, self.scenarios = fixture()
        self.profile['fee'] = dict(rate='0', minimum='0', quantum='.01',
                                   aggregation='ONE_PARENT_AGGREGATE_SYNTHETIC')
        self.profile['exposure_cap'] = '.58'
        self.order.update(id='buy:B', symbol='B', quantity='.1', step='.1')
        self.at = 0

    def tearDown(self):
        self.ledger.close()
        self.temp.cleanup()

    def event(self, kind, **fields):
        self.at += 1
        return self.ledger.event(dict(id='event:' + str(self.at), kind=kind, at_ns=self.at, **fields))

    def pending(self, minimum='0', ident='buy:A', spec=None, status='NEVER_SENT'):
        state = self.ledger.snapshot()
        spec = copy.deepcopy(spec if spec is not None else dict(self.profile['fee'], minimum=minimum))
        self.event('ADMIT', intent_id=ident, symbol='A', quantity='1', limit='50', step='.5',
                   execution_quantum='.5', fee=spec, expected_version=state['version'],
                   context_hash=digest(state), quote_ns=self.at + 1, max_age_ns=5,
                   valid_until_ns=100, clock_quality='SIMULATED_VERIFIED',
                   profile_kind='SYNTHETIC_FIXTURE_ONLY')
        if status != 'NEVER_SENT':
            self.event('SUBMIT_UNKNOWN', intent_id=ident)
        if status == 'CANCEL_PENDING':
            self.event('CANCEL_REQUEST', intent_id=ident)

    def call(self):
        return adapter.admit_synthetic(self.ledger, self.market, self.order, self.profile,
                                       self.cov, self.scenarios, evaluation_time=10, commit_time=10)

    def assert_pure_rejection(self, expected):
        before = self.ledger.snapshot()
        events = list(self.ledger.db.execute('SELECT * FROM events ORDER BY id'))
        with patch.object(self.ledger, 'event', side_effect=AssertionError('must not mutate')):
            result = self.call()
        self.assertEqual(result['decision'], 'NOT_EVALUABLE')
        self.assertEqual(result['missing'], [expected])
        self.assertEqual(result.get('metrics', {}), {})
        self.assertFalse(result['action_authorized'])
        self.assertEqual(self.ledger.snapshot(), before)
        self.assertEqual(list(self.ledger.db.execute('SELECT * FROM events ORDER BY id')), events)
        return result

    def test_original_false_pass_and_all_unresolved_statuses(self):
        for status in ('NEVER_SENT', 'SUBMIT_UNKNOWN', 'CANCEL_PENDING'):
            with self.subTest(status=status):
                self.pending(minimum='10', status=status)
                self.assert_pure_rejection('PENDING_FEE_CONTRACT_MISMATCH')
                self.ledger.close()
                self.path.unlink()
                self.ledger = SyntheticLedger(self.path, starting_cash='100', scope='SYNTHETIC_FIXTURE_ONLY')
                self.at = 0

    def test_higher_incoming_fee_does_not_expose_false_risk_reject(self):
        self.pending()
        self.profile['fee']['minimum'] = '10'
        self.profile['exposure_cap'] = '.65'
        # Correct all-fill equity55 / NAV90: old A fee0, new B fee10.
        self.assertLess(Decimal(55) / Decimal(90), Decimal('.65'))
        # The old bridge substitutes fee10 for BOTH orders: equity55 / NAV80.
        snapshot = copy.deepcopy(self.market)
        snapshot.update(cash='100', purchase_payable='0', receivables=[], holdings={},
                        pending=[dict(id='buy:A', symbol='A', side='BUY', quantity='1',
                                      step='.5', limit='50', at_ns=self.at, status='OPEN')])
        substituted = adapter.evaluate(snapshot, self.order, self.profile, self.cov, self.scenarios, 10)
        self.assertEqual(substituted['decision'], 'REJECT')
        self.assertIn('LIMIT:exposure', substituted['reasons'])
        self.assertEqual(max(Decimal(s['fractions']['exposure']) for s in substituted['metrics']['states']),
                         Decimal('.6875'))
        self.assert_pure_rejection('PENDING_FEE_CONTRACT_MISMATCH')

    def test_each_supported_numeric_field_compared_not_sampled_charge(self):
        # All three alternatives can produce the same fee at selected notionals;
        # they are distinct functions over the allowed fill domain.
        for field, value in (('rate', '.001'), ('minimum', '.01'), ('quantum', '.02')):
            with self.subTest(field=field):
                self.profile['fee'][field] = value
                self.pending(spec=dict(rate='0', minimum='0', quantum='.01',
                                       aggregation='ONE_PARENT_AGGREGATE_SYNTHETIC'))
                self.assert_pure_rejection('PENDING_FEE_CONTRACT_MISMATCH')
                self.ledger.close(); self.path.unlink()
                self.ledger = SyntheticLedger(self.path, starting_cash='100', scope='SYNTHETIC_FIXTURE_ONLY')
                self.at = 0
                self.profile['fee'] = dict(rate='0', minimum='0', quantum='.01',
                                          aggregation='ONE_PARENT_AGGREGATE_SYNTHETIC')

    def test_equivalent_spelling_signed_zero_metadata_and_profile_id(self):
        self.pending(spec=dict(rate='-0.00', minimum='0.000', quantum='0.0100',
                               aggregation='ONE_PARENT_AGGREGATE_SYNTHETIC', note='old'))
        self.profile['profile_id'] = 'NEW_SYNTHETIC_ID'
        self.profile['fee']['note'] = 'different irrelevant metadata'
        result = self.call()
        self.assertEqual(result['decision'], 'PASS_RESEARCH')
        self.assertTrue(result['mock_committed'])
        self.assertEqual(max(float(s['fractions']['exposure']) for s in result['metrics']['states']), .55)

    def test_partial_blocker_precedes_mismatch_in_either_order(self):
        self.ledger.close(); self.path.unlink()
        self.ledger = SyntheticLedger(self.path, starting_cash='200', scope='SYNTHETIC_FIXTURE_ONLY')
        for partial_first in (False, True):
            with self.subTest(partial_first=partial_first):
                sequence = ('partial', 'mismatch') if partial_first else ('mismatch', 'partial')
                for ident in sequence:
                    self.pending(minimum='0' if ident == 'partial' else '10', ident=ident,
                                 status='SUBMIT_UNKNOWN')
                    if ident == 'partial':
                        self.event('FILL', intent_id=ident, quantity='.5', price='50')
                self.assert_pure_rejection('PARTIAL_PARENT_FEE_MAPPING')
                self.ledger.close(); self.path.unlink()
                self.ledger = SyntheticLedger(self.path, starting_cash='200', scope='SYNTHETIC_FIXTURE_ONLY')
                self.at = 0

    def test_receivable_blocker_precedence(self):
        self.pending(minimum='10')
        state = self.ledger.snapshot(); state['receivable'] = '1'
        self.ledger.db.execute('UPDATE state SET body=? WHERE id=1', (json.dumps(state),))
        self.assert_pure_rejection('TYPED_RECEIVABLE_MAPPING')

    def test_invalid_incoming_fee_keeps_exact_pure_gate_diagnostic(self):
        self.pending()
        for field, value in (('rate', 'NaN'), ('rate', True), ('minimum', '-1'),
                             ('quantum', '0'), ('aggregation', 'OTHER')):
            with self.subTest(field=field, value=value):
                original = copy.deepcopy(self.profile['fee'])
                self.profile['fee'][field] = value
                before = self.ledger.snapshot()
                result = self.call()
                self.assertEqual(result['decision'], 'NOT_EVALUABLE')
                self.assertTrue(result['missing'][0].startswith('INVALID_OR_UNQUALIFIED_INPUT:'))
                self.assertEqual(result['metrics'], {})
                self.assertEqual(self.ledger.snapshot(), before)
                self.profile['fee'] = original
        # Return the first pure diagnostic verbatim, not a reconstructed error.
        marker = dict(decision='NOT_EVALUABLE', missing=['SENTINEL'], metrics={}, action_authorized=False)
        with patch.object(adapter, 'evaluate', return_value=marker):
            self.assertIs(self.call(), marker)

    def test_invalid_stored_contract_returns_empty_diagnostic(self):
        self.pending()
        original = self.ledger.snapshot()
        for field, value in (('rate', 'NaN'), ('minimum', None), ('quantum', '0'),
                             ('aggregation', 'OTHER'), ('fee', None)):
            with self.subTest(field=field):
                state = copy.deepcopy(original)
                if field == 'fee': state['orders']['buy:A']['fee'] = value
                else: state['orders']['buy:A']['fee'][field] = value
                self.ledger.db.execute('UPDATE state SET body=? WHERE id=1', (json.dumps(state),))
                result = self.call()
                self.assertEqual(result['decision'], 'NOT_EVALUABLE')
                self.assertTrue(result['missing'][0].startswith('INVALID_PENDING_FEE_CONTRACT:'))
                self.assertEqual(result['metrics'], {})
                self.assertEqual(self.ledger.snapshot(), state)

    def test_terminal_unfilled_fenced_and_full_fill_exclusions(self):
        for terminal in ('unfilled', 'fenced', 'filled'):
            with self.subTest(terminal=terminal):
                self.pending(minimum='10', status='NEVER_SENT' if terminal == 'fenced' else 'SUBMIT_UNKNOWN')
                if terminal == 'fenced': self.event('FENCE_UNSENT', intent_id='buy:A')
                elif terminal == 'filled': self.event('FILL', intent_id='buy:A', quantity='1', price='50')
                else: self.event('TERMINAL', intent_id='buy:A', authoritative=True, executions_reconciled=True)
                self.profile['exposure_cap'] = '1'
                result = self.call()
                self.assertEqual(result['decision'], 'PASS_RESEARCH')
                if terminal == 'filled':
                    self.assertEqual(result['metrics']['baseline_nav'], '90')
                    self.assertEqual(self.ledger.snapshot()['payable'], '60')
                self.ledger.close(); self.path.unlink()
                self.ledger = SyntheticLedger(self.path, starting_cash='100', scope='SYNTHETIC_FIXTURE_ONLY')
                self.at = 0

    def test_fully_busted_unresolved_parent_still_compared(self):
        self.pending(minimum='10', status='SUBMIT_UNKNOWN')
        self.event('FILL', intent_id='buy:A', quantity='.5', price='50')
        self.event('BUST', fill_id='event:3')
        self.assertEqual(self.ledger.snapshot()['orders']['buy:A']['notional'], '0.0')
        self.assert_pure_rejection('PENDING_FEE_CONTRACT_MISMATCH')

    def test_profile_mutation_reopen_and_duplicate_restore(self):
        self.pending(spec=self.profile['fee'])
        self.profile['fee']['minimum'] = '10'
        self.ledger.close()
        self.ledger = SyntheticLedger(self.path, starting_cash='100', scope='SYNTHETIC_FIXTURE_ONLY')
        saved = self.ledger.db.execute("SELECT body FROM events WHERE id='event:1'").fetchone()[0]
        before = self.ledger.snapshot()
        self.ledger.event(json.loads(saved))
        self.assertEqual(self.ledger.snapshot(), before)
        self.assert_pure_rejection('PENDING_FEE_CONTRACT_MISMATCH')

    def test_decimal_context_does_not_change_mismatch_receipt(self):
        self.pending(minimum='10')
        reference = self.call()
        with localcontext() as context:
            context.prec = 2; context.rounding = ROUND_UP
            for signal in context.traps: context.traps[signal] = True
            self.assertEqual(self.call(), reference)

    def test_concurrent_version_guard_still_rejects_at_commit(self):
        self.pending()
        original = adapter.evaluate
        calls = 0
        def evaluate_then_advance(*args, **kwargs):
            nonlocal calls
            result = original(*args, **kwargs); calls += 1
            if calls == 2:
                self.event('SUBMIT_UNKNOWN', intent_id='buy:A')
            return result
        with patch.object(adapter, 'evaluate', side_effect=evaluate_then_advance):
            with self.assertRaisesRegex(ValueError, 'stale or invalid version'):
                self.call()
        self.assertNotIn('buy:B', self.ledger.snapshot()['orders'])
        self.assertTrue(self.ledger.snapshot()['latch'])

    def test_cancellation_is_not_swallowed(self):
        self.pending()
        with patch.object(adapter, 'fee', side_effect=KeyboardInterrupt), self.assertRaises(KeyboardInterrupt):
            self.call()
        self.assertNotIn('buy:B', self.ledger.snapshot()['orders'])


if __name__ == '__main__': unittest.main()
