"""Invented seven-asset fits only; no source file or outcome is opened."""
import copy
import math
import unittest
from unittest.mock import patch
import numpy as np
from backend.delta.research import beta_clock_sensitivity as m


def fixture(mode='interaction'):
    scores=[-1.,-2/3,-1/3,0.,1/3,2/3,1.]
    old=[.25]*4;new=[.1,.2,.3,.4];x=[]
    for score in scores:x.append([score]+[0.]*8+[1.]*9+old+[1.]+[score*p for p in old])
    coef=[0.]*28
    if mode=='interaction':coef[24]=1.
    elif mode=='probability':coef[1]=.7;coef[19]=1.
    else:raise ValueError(mode)
    t=dict(origin='2026-04-07',information_cutoff='2026-04-06',target_end='2026-04-14',
        symbols=list(m.SYMBOLS),economic_feature_names=list(m.FEATURES),design_column_names=m.design_names(),
        current_score=scores,centered_score=scores,alpha_prior_means=[.0002*s for s in scores],
        Beta_probabilities=old,Beta_observed=1.,current_design=x,ridge_coefficients=coef,
        ridge_center=[0.]*27,ridge_scale=[1.]*27,ridge_penalty=1.,OOF_base_scale=.01,
        residual_kappa=.25,reference_mean=.001,return_units='simple_period_return',not_causal_profit_attribution=True)
    # Independent scalar math oracle: neither production reconstruct nor analyze is used.
    pre=[math.fsum([coef[0]]+[max(-8.,min(8.,v))*c for v,c in zip(row,coef[1:])])for row in x]
    raw=[.0025*math.tanh(v)for v in pre];mean=math.fsum(raw)/7
    correction=[v-mean for v in raw];relative=[a+c for a,c in zip(t['alpha_prior_means'],correction)]
    rm=math.fsum(relative)/7;relative=[v-rm for v in relative];absolute=[v+.001 for v in relative]
    t.update(clipped_design_with_intercept=[[1.]+r for r in x],pre_tanh=pre,
        actual_centered_correction=correction,actual_relative=relative,actual_absolute=absolute)
    provenance=dict(artifact_id='invented-artifact',as_of='2026-04-02T13:30:00Z',
        available_at='2026-04-02T13:30:00Z',information_cutoff='2026-04-01T20:00:00Z')
    state=dict(provenance,probabilities=old,native_beta_provenance_sha256=m.digest(provenance))
    o=dict(origin=t['origin'],cutoff=t['information_cutoff'],cohort='April17_matured',prefix='later',
        observed=True,probabilities=old,state=state,prior_close_diagnostic=dict(cutoff_ns=1775505600000000000,
        state_as_of='2026-04-06T13:30:00Z',artifact_id='invented-artifact',probabilities=new,
        L1_difference=sum(abs(a-b)for a,b in zip(new,old)),policy_adopted=False))
    metadata=dict(return_convention='next_session_open_to_horizon_close',target_origin='2026-04-07T00:00:00',target_start='2026-04-08T00:00:00',
        target_end='2026-04-14T00:00:00',alpha_feature_names=list(m.FEATURES),residual_status='bounded_ridge_OOF_residual',
        relative_means=dict(zip(m.SYMBOLS,relative)),alpha_prior_means=dict(zip(m.SYMBOLS,t['alpha_prior_means'])),
        reference_mean=.001,OOF_baseline_error_scale=.01,beta_provenance=provenance)
    r=dict(model_id='gamma_entry_open_target_research_v1',model_version=m.VERSION,created_at='2026-04-07T00:00:00',
        information_cutoff='2026-04-06T00:00:00',horizon_sessions=5,units='period_return',
        values=dict(zip(m.SYMBOLS,absolute)),metadata=metadata)
    b=dict(native_sha256=m.NATIVE_SHA,writer_sha256=m.WRITER_SHA,origin=t['origin'],cutoff=t['information_cutoff'],
        target_end=t['target_end'],entry='2026-04-08',cohort=o['cohort'],prefix=o['prefix'],frontier_ns=1775505600000000000,
        old_asof=state['as_of'],new_asof=o['prior_close_diagnostic']['state_as_of'],record_sha256=m.digest(r,ascii=False))
    return t,o,r,b


class MechanismTests(unittest.TestCase):
    def test_scalar_interaction_oracle_and_all_outputs(self):
        t,o,r,b=fixture();out=m.analyze(t,o,r,b)
        self.assertEqual([x['symbol']for x in out['outputs']],list(m.SYMBOLS))
        expected=[.001+.0002*s+.0025*math.tanh(s*.1)for s in t['centered_score']]
        np.testing.assert_allclose([x['alternative_absolute']for x in out['outputs']],expected,rtol=0,atol=1e-15)
        self.assertFalse(out['invariant_within_1e_15']);self.assertEqual(len(out['replaced_columns']),8)
        self.assertAlmostEqual(out['msft_delta'],expected[1]-r['values']['MSFT'],places=15)

    def test_probability_block_is_replaced_with_nonlinear_centering(self):
        t,o,r,b=fixture('probability');out=m.analyze(t,o,r,b)
        raw=[.0025*math.tanh(.7*s+.1)for s in t['centered_score']];avg=sum(raw)/7
        expected=[.001+a+v-avg for a,v in zip(t['alpha_prior_means'],raw)]
        np.testing.assert_allclose([x['alternative_absolute']for x in out['outputs']],expected,rtol=0,atol=1e-15)
        self.assertGreater(out['maximum_absolute_mean_change'],0)

    def test_same_state_invariant(self):
        t,o,r,b=fixture();o['prior_close_diagnostic']['probabilities']=list(t['Beta_probabilities'])
        o['prior_close_diagnostic']['L1_difference']=0.
        out=m.analyze(t,o,r,b);self.assertTrue(out['exactly_invariant'])
        self.assertEqual(out['msft_delta'],0.)

    def test_inputs_are_not_mutated_and_parameters_not_refitted(self):
        v=fixture();old=copy.deepcopy(v)
        with patch('numpy.linalg.solve',side_effect=AssertionError('no fit')):out=m.analyze(*v)
        self.assertEqual(v,old);self.assertFalse(out['model_fit']);self.assertFalse(out['targets_used'])
        self.assertIn('no predictive improvement',out['qualification'])
        self.assertFalse(out['live_authorized']);self.assertNotIn('values',out)

    def test_record_mapping_key_order_is_not_trace_asset_order(self):
        import json
        t,o,r,b=fixture();r=json.loads(json.dumps(r,sort_keys=True))
        self.assertEqual(len(m.analyze(t,o,r,b)['outputs']),7)

    def test_reference_mean_preserved(self):
        out=m.analyze(*fixture())
        self.assertAlmostEqual(sum(x['alternative_absolute']for x in out['outputs'])/7,.001,places=15)
        self.assertAlmostEqual(sum(x['alternative_relative']for x in out['outputs']),0,places=15)

    def test_baseline_mismatch_stops_before_second_reconstruction(self):
        t,o,r,b=fixture();t['actual_absolute'][0]+=.0001
        with patch.object(m,'reconstruct',wraps=m.reconstruct)as call:
            with self.assertRaisesRegex(ValueError,'SAVED_BASELINE_absolute'):m.analyze(t,o,r,b)
        self.assertEqual(call.call_count,1)

    def test_invalid_order_and_identity(self):
        mutations=[lambda t,o,r,b:t['symbols'].reverse(),lambda t,o,r,b:t['economic_feature_names'].reverse(),
            lambda t,o,r,b:t['design_column_names'].reverse(),lambda t,o,r,b:t['symbols'].__setitem__(0,'MSFT'),
            lambda t,o,r,b:b.__setitem__('native_sha256','0'*64),lambda t,o,r,b:b.__setitem__('writer_sha256','0'*64),
            lambda t,o,r,b:o.__setitem__('cohort','later85'),lambda t,o,r,b:o.__setitem__('prefix','earlier'),
            lambda t,o,r,b:o.__setitem__('origin','2026-04-08'),lambda t,o,r,b:b.__setitem__('record_sha256','0'*64),
            lambda t,o,r,b:r.__setitem__('model_version','0.4.3')]
        for change in mutations:
            with self.subTest(change=change):
                v=fixture();change(*v)
                with self.assertRaises(ValueError):m.analyze(*v)

    def test_bad_numeric_and_observed_domains(self):
        for value in (None,True,'1',float('nan'),float('inf')):
            with self.subTest(value=value):
                v=fixture();v[0]['ridge_coefficients'][0]=value
                with self.assertRaises(ValueError):m.analyze(*v)
        for value in (0.,-1.):
            v=fixture();v[0]['ridge_scale'][0]=value
            with self.assertRaisesRegex(ValueError,'POSITIVE_NORMALIZATION'):m.analyze(*v)
        for field,value in [('ridge_scale',[1.]),('Beta_observed',True),('residual_kappa',0.),('OOF_base_scale',-.01)]:
            v=fixture();v[0][field]=value
            with self.assertRaises(ValueError):m.analyze(*v)

    def test_both_old_blocks_and_simplex_are_validated(self):
        for change in (lambda t,o,r,b:t['current_design'][0].__setitem__(18,.2),
                       lambda t,o,r,b:t['current_design'][0].__setitem__(23,.2),
                       lambda t,o,r,b:o['prior_close_diagnostic'].__setitem__('probabilities',[0.,0.,0.,0.]),
                       lambda t,o,r,b:o.__setitem__('observed',False)):
            v=fixture();change(*v)
            with self.assertRaises(ValueError):m.analyze(*v)

    def test_clock_and_provenance_boundaries(self):
        for change in (lambda t,o,r,b:o['prior_close_diagnostic'].__setitem__('cutoff_ns',True),
            lambda t,o,r,b:o['prior_close_diagnostic'].__setitem__('state_as_of','2026-04-06T20:00:00.000000001Z'),
            lambda t,o,r,b:o['state'].__setitem__('available_at','2026-04-06T00:00:00.000000001Z'),
            lambda t,o,r,b:o['state'].__setitem__('native_beta_provenance_sha256','0'*64),
            lambda t,o,r,b:o['prior_close_diagnostic'].__setitem__('policy_adopted',True)):
            v=fixture();change(*v)
            with self.assertRaises(ValueError):m.analyze(*v)
        v=fixture();v[1]['prior_close_diagnostic']['state_as_of']='2026-04-06T20:00:00Z';v[3]['new_asof']='2026-04-06T20:00:00Z'
        self.assertEqual(len(m.analyze(*v)['outputs']),7)

    def test_native_session_label_is_not_an_availability_clock(self):
        for changed in ('2026-04-06T00:00:00Z','2026-04-06T00:00:00.000000001','2026-04-06T16:00:00'):
            v=fixture();v[2]['information_cutoff']=changed;v[3]['record_sha256']=m.digest(v[2],ascii=False)
            with self.assertRaisesRegex(ValueError,'FORECAST_IDENTITY'):m.analyze(*v)

    def test_extra_target_field_rejected(self):
        v=fixture();v[0]['training_targets']=[999.]
        with self.assertRaisesRegex(ValueError,'SELECTIVE_TRACE_FIELDS'):m.analyze(*v)

    def test_zero_gap_explicitly_undefined(self):
        v=fixture();v[2]['values']['MSFT']=v[0]['reference_mean']
        # Use a constant, zero-correction fit so this is an internally valid baseline.
        t,o,r,b=v;t['ridge_coefficients']=[0.]*28;t['alpha_prior_means']=[0.]*7
        t['pre_tanh']=[0.]*7;t['actual_centered_correction']=[0.]*7;t['actual_relative']=[0.]*7;t['actual_absolute']=[.001]*7
        r['values']=dict.fromkeys(m.SYMBOLS,.001);r['metadata']['relative_means']=dict.fromkeys(m.SYMBOLS,0.)
        r['metadata']['alpha_prior_means']=dict.fromkeys(m.SYMBOLS,0.);b['record_sha256']=m.digest(r,ascii=False)
        self.assertIsNone(m.analyze(*v)['msft_delta_divided_by_established_gap'])

    def test_clipping_matches_scalar_oracle(self):
        t,o,r,b=fixture();t['ridge_scale'][0]=.01;t['ridge_coefficients']=[0.]*28;t['ridge_coefficients'][1]=.1
        x=np.array(t['current_design']);z=np.column_stack((np.ones(7),np.clip(x/np.array(t['ridge_scale']),-8,8)))
        pre=[max(-8,min(8,s/.01))*.1 for s in t['centered_score']]
        raw=[.0025*math.tanh(v)for v in pre];avg=sum(raw)/7
        c=[v-avg for v in raw];rel=[a+v for a,v in zip(t['alpha_prior_means'],c)];rm=sum(rel)/7;rel=[v-rm for v in rel]
        t.update(clipped_design_with_intercept=z.tolist(),pre_tanh=pre,actual_centered_correction=c,actual_relative=rel,actual_absolute=[v+.001 for v in rel])
        r['values']=dict(zip(m.SYMBOLS,t['actual_absolute']));r['metadata']['relative_means']=dict(zip(m.SYMBOLS,rel));b['record_sha256']=m.digest(r,ascii=False)
        self.assertTrue(m.analyze(t,o,r,b)['exactly_invariant'])

if __name__=='__main__':unittest.main()
