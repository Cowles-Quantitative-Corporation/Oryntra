import unittest
from copy import deepcopy
from fractions import Fraction as F
from unittest.mock import patch
from backend.delta.research import activity_magnitude as a
from backend.delta.research import activity_runner as runner

class ActivityTests(unittest.TestCase):
    def fixture(self):
        from datetime import datetime
        from zoneinfo import ZoneInfo
        opened=int(datetime.fromisoformat(a.TRAIN[0]+'T09:30:00').replace(tzinfo=ZoneInfo('America/New_York')).timestamp())*a.NANO
        d=opened+3600*a.NANO
        bars=[dict(period=p,start_ns=d-p*a.NANO,end_ns=d,available_ns=d,frontier=90,epoch=0,
                   O=100,H=110,L=90,C=105,volume=3000,occurrences=30,print_digest='synthetic') for p in (300,1800)]
        n=dict(source='synthetic',symbol='AAPL',session=a.TRAIN[0],instrument_id=1,scheduled_ns=d,
               actual_decision_ns=d,event_ns=d,source_index=100,epoch=0,origin_id='native',bars=bars,reasons=[])
        q=dict(status='AVAILABLE_RECORDED_SERIES',statistic='ACQUIRED_GROUP_BBO_INCREMENT_30S_V1',
               identity=['synthetic','AAPL',a.TRAIN[0],1],sum_shares=50,support_epoch=0,latest_frontier=100,
               latest_available_ns=d,end_inclusive_ns=d,start_exclusive_ns=d-30*a.NANO,observed_through_ns=d,
               anchor_frontier=1,anchor_available_ns=d-30*a.NANO)
        s={k:n[k] for k in ('source','symbol','session','instrument_id','scheduled_ns','actual_decision_ns','source_index','origin_id')}
        s.update(prospective_common_feature_status='DEFINED',prospective_common_feature_reasons=[],recorded_statistic=q)
        receipt=dict(schema='ACQUIRED_GROUP_PAIRED_ENDPOINT_CONTRACT_V1',native_origin_id='native',
                     **{k:n[k] for k in ('source','symbol','session','instrument_id','scheduled_ns','actual_decision_ns','source_index')},
                     event_ns=d,native_epoch=0,recorded_support_epoch=0,recorded_receipt_sha256=a.digest(q),
                     bars=bars,features_exact=['1/2','1/20','1/20'])
        cid=a.digest(receipt)
        m=dict(candidate_id=cid,context_sha256=cid,native_id='native',**{k:n[k] for k in ('source','symbol','session','instrument_id','scheduled_ns','actual_decision_ns')},
               origin=dict(local_source_row=100,publisher_event_ns=d),due_ns=d+60*a.NANO,native_epoch_baseline_from_feature=0,
               status='AVAILABLE_METADATA_ONLY',endpoint=dict(local_source_row=101,available_ns=d+60*a.NANO,publisher_event_ns=d+60*a.NANO))
        m['metadata_target_id']=a.digest(m)
        return n,s,m

    def test_feature_projection_clock_boundary_and_unsigned_counts(self):
        import math
        n,s,m=self.fixture();r=a.feature_row(n,s,m)
        self.assertEqual(r['features'][:3],[.5,.05,.05])
        self.assertEqual(r['features'][3],math.log1p(10))
        self.assertNotIn('O',r['bars'][0])
        self.assertNotIn('target',r)

    def test_feature_adversarial_identity_and_past_closure(self):
        for key,value in [('available_ns',10**20),('frontier',101),('epoch',1),
                          ('volume',0),('occurrences',True),('end_ns',1),('start_ns',1)]:
            n,s,m=self.fixture();n['bars'][0][key]=value
            with self.subTest(key=key),self.assertRaises(ValueError):a.feature_row(n,s,m)
        n,s,m=self.fixture();s['symbol']='MSFT'
        with self.assertRaises(ValueError):a.feature_row(n,s,m)
        n,s,m=self.fixture();m['candidate_id']='wrong';m['metadata_target_id']=a.digest({k:v for k,v in m.items() if k!='metadata_target_id'})
        with self.assertRaises(ValueError):a.feature_row(n,s,m)

    def test_zero_loss_undefined_and_reference_report_for_both_arms(self):
        models=[]
        for n in a.NAMES:
            for arm,p in [('baseline',3),('challenger',7)]:
                models.append(dict(symbol=n,arm=arm,features=a.FEATURES[:p],center=[0.]*p,scale=[1.]*p,
                                   coefficients=[0.]*(p+1),train_ids=['synthetic'],train_mean=.25))
        rr=[];ys={};i=0
        for cell,(n,d) in enumerate((n,d) for n in a.NAMES for d in a.EVAL):
            for j in range(65 if cell<66 else 64):
                cid=str(i);i+=1;ys[cid]=0.
                rr.append(dict(symbol=n,session=d,candidate_id=cid,metadata_target_id=cid,features=[0.]*7))
        result=a.score(rr,ys,dict(configuration=a.CONFIG,models=models))
        self.assertEqual(result['rows'],4994)
        self.assertIsNone(result['gain_exact']);self.assertEqual(result['status'],'SCREEN_FAIL')
        self.assertEqual(result['balanced_MSE_exact']['baseline_TRAIN_mean'],'1/16')
        self.assertEqual(result['balanced_MSE_exact']['baseline_TRAIN_mean'],result['balanced_MSE_exact']['challenger_TRAIN_mean'])

    def test_output_discloses_full_value_parse_and_history_limit(self):
        original={'action':'full JSON parsed before freeze'}
        result=runner.qualify_deviation({'status':'SCREEN_FAIL'}, {'access_deviation':original}, {'sha256':'receipt'})
        self.assertEqual(result['qualification'],'CONTAMINATED_DESCRIPTIVE_REUSED_APRIL_DEVELOPMENT_ONLY')
        q=result['access_order_limitation']
        self.assertEqual(q['original_author_record'],original)
        self.assertFalse(q['clean_prospective_or_untouched_evidence'])
        self.assertIn('NOT_INDEPENDENTLY_PROVEN',q['history_evidence_limit'])
        self.assertEqual(result['status'],'SCREEN_FAIL')

    def test_balanced_ratio_not_mean_percentage(self):
        b={1:F(1),2:F(100)};c={1:F(0),2:F(100)}
        self.assertEqual(1-a.balanced(c)/a.balanced(b),F(1,101))
        self.assertNotEqual(F(1,101),F(1,2))

    def test_negative_prediction_not_clipped(self):
        m=dict(center=[0,0,0],scale=[1,1,1],coefficients=[-1,0,0,0])
        self.assertEqual(a.predict(m,[0]*7),-1)

    def test_nonfinite_prediction_rejected(self):
        with self.assertRaises(ValueError):a.predict(dict(center=[0],scale=[1],coefficients=[float('inf'),0]),[0])

    def test_exact_target_abs_binary64(self):
        r=dict(candidate_id='c',context_sha256='c',metadata_target_id='m')
        v=dict(**r,stage='TRAIN',target_exact='-1/3',numerator=-1,denominator=3,
               binary64_from_exact_diagnostic=float(F(-1,3)),proposed_model_float_route='BINARY64_FROM_EXACT_FRACTION')
        v['value_receipt_id']=a.digest(v); arm={k:v[k] for k in ('candidate_id','metadata_target_id','value_receipt_id')}
        v['arms']={'single':arm,'joint':arm}
        self.assertEqual(a.target(v,r,'TRAIN'),abs(float(F(-1,3))))
        for k,x in [('candidate_id','other'),('stage','EVAL'),('numerator',1)]:
            bad=deepcopy(v);bad[k]=x
            with self.subTest(k=k),self.assertRaises(ValueError):a.target(bad,r,'TRAIN')

    def test_train_reader_never_resolves_eval_values(self):
        manifest={'days':[{'session':a.EVAL[0]}]}
        with patch.object(runner,'read',side_effect=AssertionError('must not open')):
            self.assertEqual(runner.values(manifest,[],'TRAIN',lambda:None),{})

    def test_eval_deviation_blocks_before_values(self):
        freeze=dict(status='COMPLETE_REOPENED14',manifest_sha256=a.digest({}),configuration_sha256=a.digest(a.CONFIG))
        with patch.object(runner,'read',return_value=freeze),patch.object(runner,'values',side_effect=AssertionError('no values')):
            with self.assertRaisesRegex(ValueError,'deviation'):runner.evaluate({},None,{},lambda:None)

    def test_fit_paired_models_and_train_only_normalization(self):
        rows=[];ys={}
        for ni,n in enumerate(a.NAMES):
            for di,d in enumerate(a.TRAIN):
                for i in range(2):
                    cid=f'{n}-{d}-{i}';t=(di*1000+i+1)*a.NANO
                    rows.append(dict(symbol=n,session=d,candidate_id=cid,actual_decision_ns=t,
                        label_available_ns=t+60*a.NANO,features=[float(i),1.,0.,2.,3.,4.,5.]))
                    ys[cid]=float(i+1)/100
        model=a.fit(rows,ys,synthetic=True);ms=a.validate_models(model)
        self.assertEqual(len(ms),14)
        for m in ms.values():self.assertEqual(m['scale'][1],1.)
        broken=deepcopy(rows);broken[0]['symbol']='JPM'
        with self.assertRaises(ValueError):a.fit(broken,ys,synthetic=True)
        broken=deepcopy(rows);broken[0]['label_available_ns']=10**20
        with self.assertRaises(ValueError):a.fit(broken,ys,synthetic=True)
        model['models'][0]['symbol']='MSFT'
        with self.assertRaises(ValueError):a.validate_models(model)

if __name__=='__main__':unittest.main()
