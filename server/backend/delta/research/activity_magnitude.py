"""Fixed cached-bar magnitude study. No raw reader or execution consumer.

Feature projection is label-independent. The runner must authenticate caches and
freeze all models before opening EVAL value receipts. Historical availability is
not today's model issuance. Negative magnitude predictions remain unchanged.
"""
import hashlib
import json
import math
from fractions import Fraction as F
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
import numpy as np

NANO = 10**9
NAMES = ('AAPL', 'AMZN', 'GOOGL', 'JPM', 'MSFT', 'WMT', 'XOM')
TRAIN = ('2026-04-01','2026-04-02','2026-04-06','2026-04-07','2026-04-08',
         '2026-04-09','2026-04-10','2026-04-13','2026-04-14','2026-04-15')
EVAL = ('2026-04-16','2026-04-17','2026-04-20','2026-04-21','2026-04-22',
        '2026-04-23','2026-04-24','2026-04-27','2026-04-28','2026-04-29','2026-04-30')
FEATURES = ('ACQUIRED_OFI_SHARES_DIV_100','RETURN_300S','RETURN_1800S',
            'LOG1P_SHARES_PER_SECOND_300S','LOG1P_PRINTS_PER_SECOND_300S',
            'LOG1P_SHARES_PER_SECOND_1800S','LOG1P_PRINTS_PER_SECOND_1800S')
CONFIG = dict(schema='delta.activity-magnitude.fixed.v1', names=NAMES, train=TRAIN,
              evaluation=EVAL, features=FEATURES, target='ABS_BINARY64_FROM_EXACT_60S_RETURN',
              ridge_mean_loss_lambda=1, std_ddof=0, std_floor=1e-12, z_clip=8,
              intercept_penalized=False, minimum_train_rows=100, fits=14,
              negative_predictions='PRESERVE_AND_REPORT', cohort_counts=[4560,4994],
              gain='1-balanced_MSE_challenger/balanced_MSE_baseline',
              zero_denominator='UNDEFINED_NO_SCREEN_PASS', screen=[.01,5,8],
              inference='REUSED_APRIL_DEVELOPMENT_ONLY_NO_CONFIRMATORY_CI_OR_SHARPE')

def canonical(v):
    return (json.dumps(v, sort_keys=True, separators=(',', ':'), allow_nan=False)+'\n').encode()

def digest(v):
    # Original context identity uses JSON without a trailing newline.
    return hashlib.sha256(canonical(v)[:-1]).hexdigest()

def integer(v):
    return type(v) is int

def require(ok, reason):
    if not ok:
        raise ValueError(reason)

def feature_row(n, s, m):
    """Project past activity only after exact original receipt/key validation."""
    require(n['symbol'] in NAMES and n['session'] in TRAIN+EVAL, 'fixed universe/date')
    for k in ('source','symbol','session','instrument_id','scheduled_ns','actual_decision_ns','source_index'):
        require(type(s[k]) is type(n[k]) and s[k] == n[k], 'sidecar identity')
    require(s['origin_id'] == n['origin_id'] and s['prospective_common_feature_status']=='DEFINED'
            and s['prospective_common_feature_reasons']==[]
            and all(x=='NATIVE_RECENT_OFI_UNAVAILABLE' for x in n['reasons']), 'unchanged feature support')
    clocks = ('scheduled_ns','actual_decision_ns','event_ns','source_index','epoch','instrument_id')
    require(all(integer(n[k]) for k in clocks), 'integer source/clock domains')
    d=n['actual_decision_ns']; q=s['recorded_statistic']
    require(n['scheduled_ns']<=d<=n['scheduled_ns']+2*NANO and 0<=d-n['event_ns']<=2*NANO,
            'historical decision/event clocks')
    require(n['source_index']>0 and n['instrument_id']>0 and n['epoch']>=0, 'source identity domains')
    require(q['status']=='AVAILABLE_RECORDED_SERIES' and q['statistic']=='ACQUIRED_GROUP_BBO_INCREMENT_30S_V1'
            and tuple(q['identity'])==(n['source'],n['symbol'],n['session'],n['instrument_id']), 'acquired source binding')
    qi=('sum_shares','support_epoch','latest_frontier','latest_available_ns','end_inclusive_ns',
        'start_exclusive_ns','observed_through_ns','anchor_frontier','anchor_available_ns')
    require(all(integer(q[k]) for k in qi) and q['support_epoch']>=0
            and q['latest_frontier']==n['source_index'] and q['latest_available_ns']==d
            and q['end_inclusive_ns']==d and q['start_exclusive_ns']==d-30*NANO
            and q['observed_through_ns']==d and q['anchor_available_ns']<=d-30*NANO
            and 0<q['anchor_frontier']<=n['source_index'], 'acquired clock/frontier')
    require([b['period'] for b in n['bars']]==[300,1800], 'fixed completed bars')
    opened=int(datetime.fromisoformat(n['session']+'T09:30:00').replace(tzinfo=ZoneInfo('America/New_York')).timestamp())*NANO
    require(n['scheduled_ns'] in range(opened+3600*NANO, opened+23100*NANO+1,300*NANO), 'fixed origin grid')
    exact=[F(q['sum_shares'],100)]; activity=[]; bars=[]
    for b in n['bars']:
        keys=('period','start_ns','end_ns','available_ns','frontier','epoch','O','H','L','C','volume','occurrences')
        require(all(integer(b[k]) for k in keys), 'integer bar domains')
        p=b['period']; end=opened+((d-opened)//(p*NANO))*(p*NANO)
        require(b['epoch']==n['epoch'] and b['end_ns']==end and b['start_ns']==end-p*NANO
                and end<=b['available_ns']<=d and 0<b['frontier']<=n['source_index'], 'bar causal closure')
        require(min(b['O'],b['H'],b['L'],b['C'],b['volume'],b['occurrences'])>0
                and b['L']<=min(b['O'],b['C'])<=max(b['O'],b['C'])<=b['H'], 'bar geometry/activity')
        require(max(b['volume'],b['occurrences']).bit_length()<=64, 'bounded activity integers')
        exact.append(F(b['C']-b['O'],b['O']))
        activity.extend((math.log1p(b['volume']/p), math.log1p(b['occurrences']/p)))
        bars.append({k:b[k] for k in ('period','start_ns','end_ns','available_ns','frontier','epoch','volume','occurrences','print_digest')})
    receipt=dict(schema='ACQUIRED_GROUP_PAIRED_ENDPOINT_CONTRACT_V1',native_origin_id=n['origin_id'],
        **{k:n[k] for k in ('source','symbol','session','instrument_id','scheduled_ns','actual_decision_ns','source_index')},
        event_ns=n['event_ns'],native_epoch=n['epoch'],recorded_support_epoch=q['support_epoch'],
        recorded_receipt_sha256=digest(q),bars=n['bars'],features_exact=[str(x) for x in exact])
    cid=digest(receipt)
    require(m['metadata_target_id']==digest({k:v for k,v in m.items() if k!='metadata_target_id'}), 'metadata digest')
    expected=dict(candidate_id=cid,context_sha256=cid,native_id=n['origin_id'],source=n['source'],symbol=n['symbol'],
                  session=n['session'],instrument_id=n['instrument_id'],scheduled_ns=n['scheduled_ns'],actual_decision_ns=d)
    require(all(type(m[k]) is type(v) and m[k]==v for k,v in expected.items()), 'exact candidate identity')
    require(m['origin']['local_source_row']==n['source_index'] and m['origin']['publisher_event_ns']==n['event_ns']
            and m['due_ns']==d+60*NANO and m['native_epoch_baseline_from_feature']==n['epoch'], 'origin/target clocks')
    require(m['status']=='AVAILABLE_METADATA_ONLY' and m['endpoint']['local_source_row']>n['source_index']
            and d+60*NANO<=m['endpoint']['available_ns']<=d+62*NANO
            and 0<=m['endpoint']['available_ns']-m['endpoint']['publisher_event_ns']<=2*NANO, 'fixed endpoint eligibility')
    xs=[float(x) for x in exact]+activity
    require(all(math.isfinite(x) for x in xs), 'finite inputs')
    return dict(**expected, metadata_target_id=m['metadata_target_id'], features=xs,
                features_exact=[str(x) for x in exact], bars=bars, due_ns=m['due_ns'],
                label_available_ns=m['endpoint']['available_ns'])

def target(v, row, stage):
    require(v['value_receipt_id']==digest({k:x for k,x in v.items() if k not in ('value_receipt_id','arms')}), 'value digest')
    require(v['stage']==stage and all(v[k]==row[k] for k in ('candidate_id','context_sha256','metadata_target_id')), 'target identity/stage')
    arm=dict(candidate_id=row['candidate_id'],metadata_target_id=row['metadata_target_id'],value_receipt_id=v['value_receipt_id'])
    require(v['arms']=={'single':arm,'joint':arm}, 'same shared target')
    y=F(v['target_exact'])
    require(str(y)==v['target_exact'] and max(abs(y.numerator).bit_length(),y.denominator.bit_length())<=128
            and y.numerator==v['numerator'] and y.denominator==v['denominator']
            and type(v['binary64_from_exact_diagnostic']) is float and float(y)==v['binary64_from_exact_diagnostic']
            and v['proposed_model_float_route']=='BINARY64_FROM_EXACT_FRACTION', 'exact fixed numerical route')
    require(math.isfinite(float(y)), 'finite magnitude')
    return abs(float(y))

def fit(rows, values, *, synthetic=False):
    require(set(values)=={r['candidate_id'] for r in rows}, 'complete TRAIN target set')
    require(len(rows)==(len(NAMES)*len(TRAIN)*2 if synthetic else 4560), 'fixed TRAIN count')
    require(len({r['candidate_id'] for r in rows})==len(rows), 'unique TRAIN ids')
    require({(r['symbol'],r['session']) for r in rows}=={(n,d) for n in NAMES for d in TRAIN}, 'all TRAIN cells')
    models=[]
    cutoff=int(datetime(2026,4,15,20,tzinfo=timezone.utc).timestamp())*NANO
    for name in NAMES:
        rr=sorted((r for r in rows if r['symbol']==name), key=lambda r:r['actual_decision_ns'])
        require(len(rr)>=(2 if synthetic else 100) and all(r['label_available_ns']<cutoff for r in rr), 'TRAIN maturity/support')
        require(all(b['actual_decision_ns']>a['actual_decision_ns'] for a,b in zip(rr,rr[1:])), 'ordered actual TRAIN clocks')
        y=np.asarray([values[r['candidate_id']] for r in rr],dtype=np.float64)
        require(np.all(np.isfinite(y)) and np.all(y>=0), 'nonnegative finite TRAIN magnitude')
        ref=float(sum((F.from_float(float(x)) for x in y),F(0))/len(y))
        for arm,p in (('baseline',3),('challenger',7)):
            x=np.asarray([r['features'][:p] for r in rr],dtype=np.float64)
            require(x.shape==(len(rr),p) and np.all(np.isfinite(x)), 'finite fixed feature matrix')
            center=x.mean(axis=0); scale=x.std(axis=0); scale=np.where(scale<=1e-12,1.,scale)
            z=np.clip((x-center)/scale,-8,8); a=np.column_stack((np.ones(len(rr)),z))
            penalty=np.eye(p+1)*len(rr); penalty[0,0]=0
            coef=np.linalg.solve(a.T@a+penalty,a.T@y)
            require(np.all(np.isfinite(coef)), 'finite fixed ridge')
            models.append(dict(symbol=name,arm=arm,features=FEATURES[:p],center=center.tolist(),scale=scale.tolist(),
                               coefficients=coef.tolist(),train_ids=[r['candidate_id'] for r in rr],train_mean=ref))
    return dict(configuration=CONFIG,models=models,actual_fit_completed_utc=datetime.now(timezone.utc).isoformat(),
                historical_live_issuance=False)

def validate_models(modelset):
    require(canonical(modelset['configuration'])==canonical(CONFIG) and len(modelset['models'])==14, 'fixed fourteen-model configuration')
    out={}
    for m in modelset['models']:
        n=m['symbol'];arm=m['arm'];p={'baseline':3,'challenger':7}.get(arm)
        require(n in NAMES and p is not None and (n,arm) not in out, 'model own-name/arm identity')
        require(list(m['features'])==list(FEATURES[:p]) and len(m['center'])==p and len(m['scale'])==p
                and len(m['coefficients'])==p+1 and all(math.isfinite(v) for k in ('center','scale','coefficients') for v in m[k])
                and all(v>0 for v in m['scale']) and math.isfinite(m['train_mean']), 'finite model dimensions')
        out[n,arm]=m
    for n in NAMES:
        require(out[n,'baseline']['train_ids']==out[n,'challenger']['train_ids']
                and out[n,'baseline']['train_mean']==out[n,'challenger']['train_mean'], 'paired TRAIN identity')
    return out

def predict(m, xs):
    p=len(m['center']); z=np.clip((np.asarray(xs[:p])-np.asarray(m['center']))/np.asarray(m['scale']),-8,8)
    value=float(m['coefficients'][0]+z@np.asarray(m['coefficients'][1:]))
    require(math.isfinite(value), 'finite prediction')
    return value  # Negative magnitude forecasts intentionally retained.

def balanced(cells):
    require(bool(cells), 'nonempty cells')
    return sum(cells.values(),F(0))/len(cells)

def score(rows, values, modelset):
    models=validate_models(modelset)
    require(len(rows)==4994 and len({r['candidate_id'] for r in rows})==4994
            and set(values)=={r['candidate_id'] for r in rows}, 'fixed shared EVAL identities')
    cells={(n,d):[] for n in NAMES for d in EVAL}
    for r in rows:
        require((r['symbol'],r['session']) in cells, 'EVAL phase/name')
        cells[r['symbol'],r['session']].append(r)
    require(all(cells.values()), 'all77 cells')
    losses={a:{} for a in ('baseline','challenger','baseline_TRAIN_mean','challenger_TRAIN_mean')}
    predictions=[];negative={a:0 for a in ('baseline','challenger')}
    for key,rr in sorted(cells.items()):
        sums={a:F(0) for a in losses}
        for r in rr:
            y=values[r['candidate_id']];require(math.isfinite(y) and y>=0, 'finite EVAL magnitude')
            pp={a:predict(models[key[0],a],r['features']) for a in negative}
            pp.update({a+'_TRAIN_mean':models[key[0],a]['train_mean'] for a in negative})
            sq={a:(F.from_float(v)-F.from_float(y))**2 for a,v in pp.items()}
            for a in sums:sums[a]+=sq[a]
            for a in negative:negative[a]+=int(pp[a]<0)
            predictions.append(dict(candidate_id=r['candidate_id'],metadata_target_id=r['metadata_target_id'],symbol=key[0],session=key[1],
                target_binary64=y,predictions=pp,squared_errors_exact={a:str(v) for a,v in sq.items()}))
        for a in losses:losses[a][key]=sums[a]/len(rr)
    b=balanced(losses['baseline']); c=balanced(losses['challenger']); gain=None if b==0 else 1-c/b
    def subset(keys):
        x=balanced({k:losses['baseline'][k] for k in keys});y=balanced({k:losses['challenger'][k] for k in keys})
        return dict(baseline_MSE_exact=str(x),challenger_MSE_exact=str(y),gain_exact=None if x==0 else str(1-y/x))
    names={n:subset([k for k in cells if k[0]==n]) for n in NAMES}
    dates={d:subset([k for k in cells if k[1]==d]) for d in EVAL}
    halves=[subset([k for k in cells if k[1] in ds]) for ds in (EVAL[:5],EVAL[5:])]
    good=lambda v:F(v['challenger_MSE_exact'])<F(v['baseline_MSE_exact'])
    passes=gain is not None and gain>=F(1,100) and sum(good(v) for v in names.values())>=5 and sum(good(v) for v in dates.values())>=8 and all(good(v) for v in halves)
    return dict(schema='delta.activity-magnitude.score.v1',status='SCREEN_PASS_DEVELOPMENT_ONLY' if passes else 'SCREEN_FAIL',
        balanced_MSE_exact={a:str(balanced(v)) for a,v in losses.items()},gain_exact=None if gain is None else str(gain),
        gain_percent=None if gain is None else float(gain*100),per_name=names,per_date=dates,fixed_halves=halves,
        leave_one_date_out={d:subset([k for k in cells if k[1]!=d]) for d in EVAL},
        negative_prediction_counts=negative,rows=len(rows),cells=[dict(symbol=n,session=d,rows=len(cells[n,d]),**{a+'_MSE_exact':str(losses[a][n,d]) for a in losses})for n,d in sorted(cells)],
        predictions=predictions,qualification=CONFIG['inference'],economics='NO_DIRECTION_SPREAD_FEES_FILLS_OR_SHARPE')
