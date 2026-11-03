"""Invented source bundles only; no historical values or portfolio consumer."""
import copy
from dataclasses import FrozenInstanceError
from datetime import datetime, timedelta, timezone
from decimal import localcontext, ROUND_DOWN
from fractions import Fraction
import hashlib
import json
import unittest

from backend.delta.research.reference_ledger import (
    derive_reference_ledger, canonical_hash, FULL_PRODUCER, FULL_VERSION, CONVENTION,
    TOLERANCE, number, stamp,
)


def fixture(count=2):
    days = []
    d = datetime(2025, 11, 3, tzinfo=timezone.utc)
    while len(days) < count + 8:
        if d.weekday() < 5: days.append(d)
        d += timedelta(days=1)
    labels = [d.date().isoformat() for d in days]
    ns = lambda d: int(d.timestamp()) * 10**9
    sessions = {day: dict(open_ns=ns(d.replace(hour=14, minute=30)),
                          close_ns=ns(d.replace(hour=21))) for day,d in zip(labels,days)}
    records, attempts, outcomes, errors = [], [], [], []
    symbols = tuple('ABCDEFG'); policy = 'a'*64; model = 'b'*64
    for j in range(1, count+1):
        origin, start, end = labels[j], labels[j+1], labels[j+5]
        rec = dict(created_at=origin+'T14:30:00+00:00', information_cutoff=labels[j-1]+'T21:00:00+00:00',
                   model_id=FULL_PRODUCER, model_version=FULL_VERSION, horizon_sessions=5,
                   units='period_return', values={s:.02 for s in symbols},
                   metadata=dict(config_fingerprint=policy, return_convention=CONVENTION,
                                 feature_lag_sessions=1, reference_mean=.01,
                                 training_information_cutoff=labels[j-1]+'T21:00:00+00:00',
                                 reference_training_cutoff=labels[j-1]+'T21:00:00+00:00',
                                 target_start=start, target_end=end,
                                 target_start_price_convention=
                                 'reported_adjusted_next_session_open_training_proxy_not_observed_fill'))
        records.append(rec); rh=canonical_hash(rec)
        for s in symbols:
            aid=canonical_hash(dict(record_sha256=rh,symbol=s,target_start=start,
                                    target_end=end,return_convention=CONVENTION))
            identity=dict(attempt_id=aid,symbol=s,origin=origin,target_start=start,due_session=end)
            attempts.append(dict(identity,horizon_sessions=5,forecast=.02,record_sha256=rh,
                                 model_source_sha256=model,model_id=FULL_PRODUCER,
                                 model_version=FULL_VERSION,config_fingerprint=policy))
            available=labels[j+6]+'T14:30:00+00:00'
            outcomes.append(dict(identity,publication_attested=False,target_is_public_open_proxy=True,
                                 source_adjusted_open=100,source_adjusted_close=103,
                                 realized_simple_return=.03,available_at_simulated=available))
            errors.append(dict(identity,forecast=.02,actual=.03,available_at_simulated=available,
                               error_actual_minus_forecast=.01,operational_risk_admitted=False))
    objects=dict(records=dict(records=records),attempts=dict(attempts=attempts),
                 outcomes=dict(outcomes=outcomes),errors=dict(errors=errors),calendar=dict(sessions=sessions))
    args=dict(origins=tuple(labels[1:count+1]),symbols=symbols,full_policy_id=policy,
              full_model_source_sha256=model,enabled=True)
    return objects,args


def run(objects,args,**overrides):
    raw={k:json.dumps(v,sort_keys=True,separators=(',',':'),allow_nan=False).encode()for k,v in objects.items()}
    pins={k:hashlib.sha256(v).hexdigest()for k,v in raw.items()}
    return derive_reference_ledger(raw,pins,**dict(args,**overrides))


class ReferenceLedgerTests(unittest.TestCase):
    def test_disabled_by_default_and_exact_pin_closure(self):
        objects,args=fixture();raw={k:json.dumps(v).encode()for k,v in objects.items()}
        pins={k:hashlib.sha256(v).hexdigest()for k,v in raw.items()}
        with self.assertRaisesRegex(ValueError,'disabled'):
            derive_reference_ledger(raw,pins,**dict(args,enabled=False))
        raw['records']+=b' '
        with self.assertRaisesRegex(ValueError,'hash mismatch'):
            derive_reference_ledger(raw,pins,**args)
        del raw['errors']
        with self.assertRaisesRegex(ValueError,'source closure'):
            derive_reference_ledger(raw,pins,**args)

    def test_exact_arithmetic_distinct_identity_immutable_deterministic(self):
        objects,args=fixture();before=copy.deepcopy(objects);a=run(objects,args)
        self.assertEqual(a,run(objects,args));self.assertEqual(objects,before)
        self.assertEqual(len(a.rows),14)
        self.assertTrue(all(r.identity!=r.original_attempt_id for r in a.rows))
        self.assertTrue(all(r.residual==Fraction(1,50)for r in a.rows))
        self.assertTrue(all(r.prediction==Fraction(1,100)for r in a.rows))
        self.assertFalse(a.operational_risk_admitted);self.assertFalse(a.historical_publication_attested)
        with self.assertRaises(FrozenInstanceError):a.rows[0].actual=Fraction(0)
        with localcontext()as ctx:
            ctx.prec=2;ctx.rounding=ROUND_DOWN
            for signal in ctx.traps:ctx.traps[signal]=True
            self.assertEqual(a,run(objects,args))

    def test_asof_due_unavailable_and_equality_boundaries(self):
        objects,args=fixture();a=run(objects,args);r=a.rows[0]
        self.assertEqual(a.asof('A',r.due_ns-1)['attempted'],0)
        at_due=a.asof('A',r.due_ns)
        self.assertEqual((at_due['attempted'],at_due['observed'],at_due['missing']),(1,0,1))
        self.assertEqual(a.asof('A',r.outcome_available_ns)['observed'],1)
        with self.assertRaises(ValueError):a.asof('A',True)
        with self.assertRaises(ValueError):a.asof('UNKNOWN',r.due_ns)

    def test_exact_nanosecond_label_maturity_no_early_cutoff(self):
        objects,args=fixture()
        old=objects['outcomes']['outcomes'][0]['available_at_simulated']
        exact=stamp(old)
        plus_one=old.replace('+00:00','.000000001+00:00')
        self.assertEqual(stamp(plus_one),exact+1)
        for source in ('outcomes','errors'):
            changed=copy.deepcopy(objects)
            changed[source][source][0]['available_at_simulated']=plus_one
            with self.subTest(source=source),self.assertRaisesRegex(ValueError,'modeled label maturity'):
                run(changed,args)
        # Explicit nine-digit equality is accepted and becomes observed at,
        # never before, its exact availability boundary.
        for source in ('outcomes','errors'):
            objects[source][source][0]['available_at_simulated']=old.replace('+00:00','.000000000Z')
        a=run(objects,args)
        self.assertEqual(a.asof('A',exact-1)['observed'],0)
        self.assertEqual(a.asof('A',exact)['observed'],1)

    def test_submicrosecond_expected_open_and_unsupported_precision(self):
        objects,args=fixture()
        for c in objects['calendar']['sessions'].values():
            c['open_ns']+=123; c['close_ns']+=123
        for source in ('outcomes','errors'):
            for row in objects[source][source]:
                row['available_at_simulated']=row['available_at_simulated'].replace('+00:00','.000000123+00:00')
        a=run(objects,args);r=a.rows[0]
        self.assertEqual(r.outcome_available_ns%1000,123)
        self.assertEqual(a.asof('A',r.outcome_available_ns-1)['observed'],0)
        self.assertEqual(a.asof('A',r.outcome_available_ns)['observed'],1)
        self.assertEqual(stamp('2025-01-01T01:00:00.000000001+01:00'),
                         stamp('2025-01-01T00:00:00Z')+1)
        for value in ('2025-01-01T00:00:00.0000000001Z',
                      '2025-01-01T00:00:00+00:00:00.000000001'):
            with self.subTest(value=value),self.assertRaisesRegex(ValueError,'supported exact'):
                stamp(value)

    def test_timezone_offsets_reject_normalization_and_preserve_boundaries(self):
        for offset in ('+00:60','-00:60','+01:99','-23:60','+24:00','-24:00'):
            value='2025-01-01T00:00:00'+offset
            with self.subTest(offset=offset),self.assertRaisesRegex(ValueError,'offset hour/minute range'):
                stamp(value)
            objects,args=fixture()
            for source in ('outcomes','errors'):
                changed=copy.deepcopy(objects)
                old=changed[source][source][0]['available_at_simulated']
                changed[source][source][0]['available_at_simulated']=old[:-6]+offset
                with self.subTest(source=source,offset=offset),self.assertRaisesRegex(ValueError,'offset hour/minute range'):
                    run(changed,args)
        zero=stamp('2025-01-02T00:00:00Z')
        for offset,minutes in (('+00:00',0),('-00:00',0),('+00:59',59),
                               ('-00:59',-59),('+23:59',1439),('-23:59',-1439)):
            with self.subTest(offset=offset):
                self.assertEqual(stamp('2025-01-02T00:00:00.000000001'+offset),
                                 zero-minutes*60*10**9+1)

    def test_85_origin_global_completeness_does_not_erase_asof_missing(self):
        objects,args=fixture(85);a=run(objects,args)
        self.assertEqual(len(a.rows),595)
        for symbol in args['symbols']:
            rows=[r for r in a.rows if r.symbol==symbol]
            for n in (84,85):
                at=a.asof(symbol,rows[n-1].due_ns)
                self.assertEqual((at['attempted'],at['observed'],at['missing']),(n,n-1,1))
                self.assertIsNone(at['residuals'][-1])

    def test_per_asset_missingness_is_preserved(self):
        objects,args=fixture();aid=objects['attempts']['attempts'][0]['attempt_id']
        for source in ('outcomes','errors'):
            objects[source][source]=[r for r in objects[source][source]if r['attempt_id']!=aid]
        a=run(objects,args);r=next(r for r in a.rows if r.original_attempt_id==aid)
        self.assertIsNone(r.actual);self.assertIsNone(r.residual);self.assertIsNone(r.outcome_available_ns)
        self.assertEqual(a.asof('A',a.rows[-1].outcome_available_ns)['missing'],1)
        self.assertEqual(a.asof('B',a.rows[-1].outcome_available_ns)['missing'],0)

    def test_duplicate_foreign_missing_attempts_and_error_rosters(self):
        for change in ('duplicate','foreign','missing','extra_error'):
            with self.subTest(change=change):
                objects,args=fixture()
                if change=='duplicate':objects['attempts']['attempts'].append(copy.deepcopy(objects['attempts']['attempts'][0]))
                elif change=='missing':objects['attempts']['attempts'].pop()
                elif change=='foreign':objects['attempts']['attempts'][0]['attempt_id']='foreign'
                else:objects['errors']['errors'].pop()
                with self.assertRaises(ValueError):run(objects,args)

    def test_wrong_record_and_attempt_provenance(self):
        for field,value in [('symbol','OTHER'),('origin','2025-01-01'),('target_start','2025-01-01'),
                            ('due_session','2025-01-01'),('record_sha256','c'*64),
                            ('model_source_sha256','c'*64),('horizon_sessions',True),
                            ('model_version','OTHER'),('forecast',.04)]:
            with self.subTest(field=field):
                objects,args=fixture();objects['attempts']['attempts'][0][field]=value
                with self.assertRaises(ValueError):run(objects,args)

    def test_reference_cutoff_basis_and_calendar_support(self):
        for field,value in [('reference_training_cutoff','2025-01-01'),
                            ('target_start_price_convention','raw_fill'),('return_convention','close_to_close'),
                            ('feature_lag_sessions',True),('reference_mean',-1)]:
            with self.subTest(field=field):
                objects,args=fixture();objects['records']['records'][0]['metadata'][field]=value
                with self.assertRaises(ValueError):run(objects,args)
        objects,args=fixture();objects['records']['records'].pop()
        with self.assertRaises(ValueError):run(objects,args)
        objects,args=fixture();day=next(iter(objects['calendar']['sessions']))
        objects['calendar']['sessions'][day]['close_ns']=0
        with self.assertRaises(ValueError):run(objects,args)

    def test_label_basis_identity_maturity_and_arithmetic(self):
        for field,value in [('symbol','OTHER'),('target_is_public_open_proxy',False),
                            ('publication_attested',True),('source_adjusted_open',0),
                            ('realized_simple_return',-.5),('available_at_simulated','2025-01-01T00:00:00'),
                            ('available_at_simulated','2025-01-01T00:00:00+00:00')]:
            with self.subTest(field=field,value=value):
                objects,args=fixture();objects['outcomes']['outcomes'][0][field]=value
                with self.assertRaises(ValueError):run(objects,args)
        objects,args=fixture();objects['errors']['errors'][0]['actual']=.04
        with self.assertRaises(ValueError):run(objects,args)
        objects,args=fixture();objects['errors']['errors'][0]['error_actual_minus_forecast']=.1
        with self.assertRaises(ValueError):run(objects,args)

    def test_tolerance_boundary_is_fixed_and_nonfinite_bool_reject(self):
        self.assertEqual(TOLERANCE,Fraction(1,10**12))
        for value in (True,float('nan'),float('inf')):
            with self.subTest(value=value),self.assertRaises(ValueError):number(value)
        # Exact decimal-rendered errors: tolerated legacy rounding vs failure.
        objects,args=fixture();objects['errors']['errors'][0]['error_actual_minus_forecast']=.010000000001
        self.assertEqual(len(run(objects,args).rows),14)
        objects['errors']['errors'][0]['error_actual_minus_forecast']=.010000000002
        with self.assertRaises(ValueError):run(objects,args)

    def test_malformed_structure_is_structured_not_evaluable(self):
        objects,args=fixture();objects['calendar']['sessions']['not-a-date'] = dict(open_ns=1,close_ns=2)
        with self.assertRaisesRegex(ValueError,'^NOT_EVALUABLE:'):
            run(objects,args)
        objects,args=fixture();raw={k:json.dumps(v).encode()for k,v in objects.items()}
        for payload in (b'{broken', b'{"errors":NaN}'):
            raw['errors']=payload
            pins={k:hashlib.sha256(v).hexdigest()for k,v in raw.items()}
            with self.assertRaisesRegex(ValueError,'^NOT_EVALUABLE:'):
                derive_reference_ledger(raw,pins,**args)

    def test_duplicate_json_keys_reject_even_with_matching_hash(self):
        objects,args=fixture();raw={k:json.dumps(v).encode()for k,v in objects.items()}
        raw['errors']=b'{"errors":[],"errors":[]}'
        pins={k:hashlib.sha256(v).hexdigest()for k,v in raw.items()}
        with self.assertRaisesRegex(ValueError,'duplicate JSON'):
            derive_reference_ledger(raw,pins,**args)


if __name__=='__main__':unittest.main()
