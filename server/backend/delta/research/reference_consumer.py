"""Disabled, isolated recorded-reference consumer for synthetic qualification.

No market-file reader or replay launcher. Explicit pinned code bindings reuse the
original math/account loop; the full-model consumer and its guards are untouched.
Archive identities are retrospective provenance, not historical publication proof.
"""
from copy import deepcopy
from dataclasses import dataclass, fields
from fractions import Fraction
import hashlib
import json
from pathlib import Path

from backend.delta.research import reference_ledger as ref
from backend.delta.research.qualify_reference import encode, digest, decode, SCHEMA as QUAL_SCHEMA

PRODUCER = 'delta_recorded_common_reference_v1'
VERSION = '1'
PINS = {
    'core':'5863fec8781bf7a12ff48548b8a42a5a4f82e8f87e75d0313cfadeba4e169e38',
    'projection':'2a3593ab59eaf81b56911070976955198302b30c793d461fd8f261d7b6ae2e5d',
    'fees':'bd724de773782ccb95210874dbc2b53a9cc757399f35fcc626d8b60ec7bbfcc6',
    'policy':'104277b47c14609bf777ef269488512c425a3a8a1f5ab13cc23d3cb514cd99da',
    'family':'c2f1614dde1432fe20d2771db6a4a101f8f5e5770efa0ceb0a69ee6ca6a3f2a4',
    'account':'4ce7627e16cb3d85db58cec853c007606c59ef70c4eda586997e3354756ce94a',
}
NANO=10**9
require=ref.require


@dataclass(frozen=True)
class FrozenBindings:
    core: object
    projection: object
    fees: object
    policy: object
    family: object
    account: object

    def verify(self):
        for name,pin in PINS.items():
            require(digest(Path(getattr(self,name).__file__).read_bytes())==pin,'pinned code:'+name)


def _fraction(value):
    require(type(value) is str and 0<len(value)<=4096,'exact rational encoding')
    out=Fraction(value);require(str(out)==value,'canonical rational encoding');return out


@dataclass(frozen=True)
class ReferenceForecast:
    source_projection: object
    expected_period_return: Fraction
    policy_identity: str
    producer: str=PRODUCER
    version: str=VERSION
    operational_risk_admitted: bool=False
    historical_issuance_attested: bool=False

    @property
    def anchor(self): return self.source_projection.anchor
    @property
    def symbol(self): return self.source_projection.symbol
    @property
    def origin(self): return self.source_projection.origin
    @property
    def target_start(self): return self.source_projection.target_start
    @property
    def target_end(self): return self.source_projection.target_end
    @property
    def forecast_information_cutoff_ns(self): return self.source_projection.forecast_information_cutoff_ns
    @property
    def modeled_forecast_available_ns(self): return self.source_projection.modeled_forecast_available_ns
    @property
    def target_exit_ns(self): return self.source_projection.target_exit_ns
    @property
    def base_record_sha256(self): return self.source_projection.base_record_sha256
    @property
    def identity(self):
        return ref.canonical_hash(dict(producer=PRODUCER,version=VERSION,policy=self.policy_identity,
            source_record=self.base_record_sha256,symbol=self.symbol,origin=self.origin,
            start=self.target_start,end=self.target_end,cutoff=self.forecast_information_cutoff_ns,
            anchor=self.anchor.identity,mean=str(self.expected_period_return)))


@dataclass(frozen=True)
class ReferencePlan:
    identity: str
    forecast_identity: str
    symbol: str
    entry_start_ns: int
    entry_end_ns: int
    exit_start_ns: int
    original_target_exit_ns: int
    maximum_buy_price_nano: int
    maximum_entry_shares: int
    simulation_only: bool=True
    external_paper_authorized: bool=False
    live_authorized: bool=False
    operational_risk_admitted: bool=False

    def __post_init__(self):
        require(all(type(v) is str and v for v in (self.identity,self.forecast_identity,self.symbol)), 'plan identities')
        require(all(type(v) is int and v>=0 for v in (self.entry_start_ns,self.entry_end_ns,
                self.exit_start_ns,self.original_target_exit_ns)) and
                self.entry_start_ns<=self.entry_end_ns<self.exit_start_ns<=self.original_target_exit_ns,'plan clocks')
        require(type(self.maximum_buy_price_nano) is int and self.maximum_buy_price_nano>0
                and type(self.maximum_entry_shares) is int and self.maximum_entry_shares>0,'plan price/quantity')
        require(self.simulation_only is True and self.external_paper_authorized is False
                and self.live_authorized is False and self.operational_risk_admitted is False,'plan scope')


class _ReferenceStage:
    def __init__(self,bindings,qualified,records,calendar,acceptance):
        self.b=bindings;self._core=bindings.core;self.acceptance=deepcopy(acceptance)
        require(digest(qualified)==acceptance['qualification_sha256'],'accepted qualification bytes')
        data=decode(qualified);summary=data['summary']
        require(summary['schema']==QUAL_SCHEMA and summary['status']=='COMPLETE_SOURCE_QUALIFICATION_ONLY'
                and summary['code_pins']==acceptance['code_pins']
                and summary['config_sha256']==acceptance['config_sha256']
                and summary['source_pins']==acceptance['source_pins']
                and summary['operational_risk_admitted'] is False
                and summary['historical_publication_attested'] is False
                and summary['portfolio_replay'] is False,'accepted qualification context')
        require(digest(encode({k:v for k,v in data.items() if k!='summary'}))==summary['semantic_sha256'], 'qualification semantic digest')
        require(digest(records)==acceptance['source_pins']['records']
                and digest(calendar)==acceptance['source_pins']['calendar'],'source records/calendar bytes')
        self.calendar=decode(calendar)['sessions'];self.records={};self.hashes={}
        for r in decode(records)['records']:
            day=r['metadata']['target_start'][:10]
            require(day not in self.records,'unique source target session')
            self.records[day]=r;self.hashes[day]=ref.canonical_hash(r)
        self.days=tuple(acceptance['evaluation_sessions']);self.symbols=tuple(acceptance['symbols'])
        require(len(set(self.days))==len(self.days) and all(d in self.records for d in self.days),'fixed evaluation roster')
        require(len(set(self.symbols))==len(self.symbols)==7,'seven symbols')
        self.full_policy=acceptance['full_policy_id']
        by_origin={r['created_at'][:10]:r for r in self.records.values()}
        require(len(by_origin)==len(self.records),'unique source origins')
        restored=[]
        for row in data['ledger']:
            require(set(row)=={f.name for f in fields(ref.ReferenceRow)},'reference row schema')
            r=dict(row)
            for key in ('prediction','actual','residual'):
                if r[key] is not None:r[key]=_fraction(r[key])
            r=ref.ReferenceRow(**r)
            require(r.prediction>-1 and (r.actual is None)==(r.residual is None)==(r.outcome_available_ns is None), 'row domain/missingness')
            require(r.actual is None or r.actual>=-1 and r.residual==r.actual-r.prediction,'reference residual identity')
            require(all(type(v) is int and v>=0 for v in (r.forecast_available_ns,r.target_start_ns,r.due_ns))
                    and r.forecast_available_ns<=r.target_start_ns<r.due_ns,'row clocks')
            require(r.outcome_available_ns is None or type(r.outcome_available_ns) is int
                    and r.outcome_available_ns>r.due_ns,'outcome maturity')
            source=by_origin[r.origin];record_sha=ref.canonical_hash(source)
            require(r.source_record_sha256==record_sha and r.symbol in source['values']
                    and r.target_start==source['metadata']['target_start'][:10]
                    and r.target_end==source['metadata']['target_end'][:10]
                    and r.prediction==ref.number(source['metadata']['reference_mean']), 'row source/reference binding')
            original=ref.canonical_hash(dict(record_sha256=record_sha,symbol=r.symbol,
                target_start=r.target_start,target_end=r.target_end,return_convention=ref.CONVENTION))
            ident=ref.canonical_hash(dict(schema=ref.SCHEMA,policy=data['reference_policy_identity'],
                original_attempt=original,record_sha256=record_sha,reference=str(r.prediction),
                symbol=r.symbol,origin=r.origin,target_start=r.target_start,target_end=r.target_end))
            require(r.original_attempt_id==original and r.identity==ident,'reference row identities')
            require(r.forecast_available_ns==self.calendar[r.origin]['open_ns']
                    and r.target_start_ns==self.calendar[r.target_start]['open_ns']
                    and r.due_ns==self.calendar[r.target_end]['close_ns'],'row/calendar binding')
            restored.append(r)
        require(len(restored)==summary['row_count'] and len({r.identity for r in restored})==len(restored),'complete reference rows')
        self.bank=ref.ReferenceLedger(tuple(restored),tuple(sorted((k,v) for k,v in acceptance['source_pins'].items() if k in ref.SOURCE_KEYS)),data['reference_policy_identity'])
        require(type(data['memberships']) is list and len(data['memberships'])==summary['scope_count'],
                'qualified membership roster')
        scopes={(r['session'],r['symbol']):r for r in data['memberships']}
        require(len(scopes)==len(data['memberships']),'unique qualified scopes')
        for day in self.days:
            cutoff=self.calendar[self.records[day]['information_cutoff'][:10]]['close_ns']
            for symbol in self.symbols:
                require((day,symbol) in scopes and scopes[day,symbol]['cutoff_ns']==cutoff,'qualified evaluation cutoff')
                scope=scopes[day,symbol];at=self.bank.asof(symbol,cutoff)
                originals={r.identity:r.original_attempt_id for r in self.bank.rows}
                require(scope['original_ids']==[originals[i] for i in at['attempts']]
                        and scope['missing_original_ids']==[originals[i] for i,v in zip(at['attempts'],at['residuals']) if v is None],
                        'qualified original-ID/null-mask membership')
        self.archive_sha=digest(qualified)
        self.policy_id=ref.canonical_hash(dict(producer=PRODUCER,version=VERSION,source_policy=self.full_policy,
            code=PINS,cost='CURRENT_FIXED_SMART_US',budget='FREE_SETTLED_CASH_SHADOW',eligibility='FULL_FORMED_ROSTER'))
        self.store=bindings.projection.FirstAnchorProjectionStore(max_anchors=7*len(self.days))
        self.candidates={};self.manifest=[]

    def __getattr__(self,name): return getattr(self._core,name)
    def number(self,v): return v if type(v) is Fraction else self._core.number(v)

    def project(self,event):
        require(type(event) is self._core.DepthEvent,'pinned event type')
        if event.session not in self.days or event.symbol not in self.symbols:return None
        key=(event.session,event.symbol)
        if key in self.candidates:return self.candidates[key]
        clock=self.calendar[event.session];end=min(clock['close_ns'],clock['open_ns']+60*NANO)
        if not clock['open_ns']<=event.available_ns<=end:return None
        record=self.records[event.session]
        bid=event.bids[0] if event.bids else None;ask=event.asks[0] if event.asks else None
        quote=self.b.projection.ObservedBBO(event.symbol,event.session,event.available_ns,event.event_ns,
            event.source_index,event.identity,bid.price if bid else 0,ask.price if ask else 0,
            bid.size if bid else 0,ask.size if ask else 0,event.valid,event.reset)
        source=self.store.observe(record,event.symbol,self.calendar,quote,
            expected_record_sha256=self.hashes[event.session],expected_policy_identity=self.full_policy)
        if source is None:return None
        forecast=ReferenceForecast(source,self.number(record['metadata']['reference_mean']),self.policy_id)
        require(forecast.expected_period_return>-1,'reference simple return')
        candidate=(deepcopy(record),forecast,forecast.anchor,end,forecast.target_exit_ns-60*NANO)
        self.candidates[key]=candidate
        self.manifest.append(dict(session=event.session,symbol=event.symbol,forecast_identity=forecast.identity,
            producer=PRODUCER,version=VERSION,source_record_sha256=forecast.base_record_sha256,
            source_quote_identity=forecast.anchor.source_quote_identity,source_index=forecast.anchor.source_index,
            anchor_available_ns=forecast.anchor.observed_at_ns,anchor_price_nano=forecast.anchor.price_nano,
            cutoff_ns=forecast.forecast_information_cutoff_ns,target_exit_ns=forecast.target_exit_ns,
            prediction=str(forecast.expected_period_return),reference_bank_archive_sha256=self.archive_sha))
        return candidate

    def admit_daily_paper_entry(self,record,forecast,reference,event,paper,residual_rows,*,policy_identity,
            session_calendar,entry_start_ns,entry_end_ns,exit_start_ns,corporate_action_contract,**kwargs):
        core=self._core
        require(not kwargs or kwargs=={'allow_reported_close_sensitivity':True},'no parameter overrides')
        require(type(forecast) is ReferenceForecast and forecast.policy_identity==self.policy_id
                and type(forecast.source_projection) is self.b.projection.ConditionalOpenProjection,'reference forecast identity')
        key=(forecast.target_start,forecast.symbol)
        require(key in self.candidates and forecast==self.candidates[key][1]
                and reference==forecast.anchor,'owned source projection/anchor')
        require(ref.canonical_hash(record)==self.hashes[forecast.target_start]==forecast.base_record_sha256
                and forecast.expected_period_return==self.number(record['metadata']['reference_mean'])
                and policy_identity==self.full_policy,'source/reference binding')
        require(residual_rows is self.bank.rows,'own reference row stream')
        require(type(event) is core.DepthEvent and type(paper) is core.CashPaperAccount and paper.last==event
                and event.symbol==forecast.symbol,'current event/shadow')
        require(paper.max_weight==Fraction(1,2) and paper.fee_bps==Fraction(1,2) and paper.slippage_bps==1
                and paper.latency_ns==100000000 and paper.expiry_ns==2*NANO
                and paper.participation==Fraction(1,20) and paper.max_clip==100,'frozen shadow settings')
        require(corporate_action_contract=='no_actions_assumed_for_sensitivity','conditional actions')
        require(type(session_calendar) is tuple and all(type(x) is core.ForecastSessionClock for x in session_calendar)
                and tuple((x.session,x.open_ns,x.close_ns) for x in session_calendar)==tuple(
            (d,self.calendar[d]['open_ns'],self.calendar[d]['close_ns']) for d in sorted(self.calendar)), 'calendar binding')
        start=self.calendar[forecast.target_start];terminal=self.calendar[forecast.target_end]
        require(entry_start_ns==start['open_ns'] and entry_end_ns==min(start['close_ns'],start['open_ns']+60*NANO)
                and exit_start_ns==terminal['close_ns']-60*NANO,'fixed entry/exit windows')
        flags=dict(simulation_only=True,external_paper_authorized=False,live_authorized=False,
                   operational_risk_admitted=False,future_coverage_certified=False,sensitivity_only=True)
        def unavailable(status,reason,**extra):return dict(status=status,reasons=(reason,),plan=None,**flags,**extra)
        if paper.shares or paper.pending is not None:return unavailable('UNAVAILABLE','ACCOUNT_NOT_FLAT')
        if event.session!=forecast.target_start or not entry_start_ns<=event.available_ns<=entry_end_ns:
            return unavailable('UNAVAILABLE','OUTSIDE_FIXED_ENTRY_WINDOW')
        if forecast.modeled_forecast_available_ns>event.available_ns or reference.observed_at_ns>event.available_ns:
            return unavailable('UNAVAILABLE','INPUT_NOT_YET_AVAILABLE')
        if not core.quote_is_fresh(event,2*NANO):return unavailable('UNAVAILABLE','INVALID_OR_STALE_BOOK')
        at=self.bank.asof(event.symbol,forecast.forecast_information_cutoff_ns)
        lookup={r.identity:r.original_attempt_id for r in self.bank.rows}
        originals=tuple(lookup[i] for i in at['attempts'])
        cohort=dict(residuals=at['residuals'],attempt_identities=at['attempts'],attempted=at['attempted'],
            observed=at['observed'],missing=at['missing'],cutoff_ns=at['cutoff_ns'],
            original_attempt_identities=originals,future_coverage_certified=False,cohort_completeness_verified=False,
            causal_asof_hash=ref.canonical_hash(dict(symbol=event.symbol,cutoff=at['cutoff_ns'],ids=originals,
                values=[str(x) if x is not None else None for x in at['residuals']])))
        if cohort['observed']<30 or cohort['missing']!=0:
            return unavailable('UNAVAILABLE','OWN_PROXY_MANIFEST_INCOMPLETE_OR_INSUFFICIENT',cohort=cohort)
        mu=forecast.expected_period_return;discount=Fraction(3,10000)
        terminal_mean=reference.price_nano*(1+mu);sale=terminal_mean*(1-discount)
        sale_price=sale.numerator//sale.denominator
        mean_receipt=sale_price-core.CashPaperAccount.ceil(sale_price*paper.fee_bps/10000)
        quote_limit=Fraction(event.asks[0].price)*Fraction(10005,10000)
        economic_limit=int(Fraction(mean_receipt)/(1+paper.fee_bps/10000))
        while economic_limit>0 and economic_limit+core.CashPaperAccount.ceil(economic_limit*paper.fee_bps/10000)>=mean_receipt:economic_limit-=1
        limit=min(economic_limit,quote_limit.numerator//quote_limit.denominator)
        anticipated=core.CashPaperAccount.ceil(event.asks[0].price*(1+paper.slippage_bps/10000))
        if limit<anticipated:return unavailable('NO_POSITIVE_NET_FORECAST','ENTRY_EXCEEDS_PROPORTIONAL_ECONOMIC_LIMIT')
        nav=paper.nav2(event)//2
        require(nav==paper.cash,'fresh free-settled-cash shadow denominator')
        sizing=core.empirical_long_share_budget(cohort['residuals'],reference_price_nano=reference.price_nano,
            expected_period_return=mu,maximum_entry_price_nano=limit,entry_fee_rate=paper.fee_bps/10000,
            exit_fee_rate=paper.fee_bps/10000,exit_price_discount=discount,settled_cash_nano=paper.cash,
            marked_nav_nano=nav,marked_share_price_nano=(event.mid2+1)//2,
            loss_budget_nano=int(Fraction(1,50)*nav),exposure_cap=paper.max_weight,minimum_observed=30)
        if sizing['status']!='SIZED_RESEARCH':return unavailable(sizing['status'],'NATIVE_EMPIRICAL_SHARE_BUDGET',sizing=sizing)
        receipt=dict(admission_source_sha256=forecast.base_record_sha256,prediction_identity=forecast.identity,
            symbol=event.symbol,policy_identity=self.policy_id,source_policy_identity=self.full_policy,
            forecast_stream=PRODUCER,reference_bank_archive_sha256=self.archive_sha,cohort=cohort,sizing=sizing,
            quote_identity=event.identity,reference_price_nano=reference.price_nano,exit_price_discount=discount,
            calendar_content_sha256=core.calendar_content_digest(session_calendar),horizon_sessions=5,
            shadow_free_settled_cash_nano=paper.cash,shadow_nav_nano=nav,loss_budget_nano=nav//50,
            sizing_denominator='FREE_SETTLED_CASH_SHADOW',modeled_issuance_not_attested=True,**flags)
        ident=hashlib.sha256(repr(receipt).encode()).hexdigest()
        plan=ReferencePlan(ident,forecast.identity,event.symbol,entry_start_ns,entry_end_ns,exit_start_ns,
                           forecast.target_exit_ns,limit,sizing['shares'])
        return dict(status='CONDITIONAL_REFERENCE_NATIVE_PROPOSAL',plan=plan,receipt=receipt,**flags)


class ReferenceConsumer:
    def __init__(self,bindings,qualification_bytes,records_bytes,calendar_bytes,acceptance,*,enabled=False):
        require(enabled is True,'reference consumer disabled')
        bindings.verify();self.bindings=bindings
        self._stage=_ReferenceStage(bindings,qualification_bytes,records_bytes,calendar_bytes,acceptance)
        self._guard=bindings.fees.BrokerAdmissionCore(self._stage,bindings.policy.BrokerFeePolicy('CURRENT_FIXED_SMART_US'))
    def __getattr__(self,name):return getattr(self._stage,name)
    @property
    def reference_rows(self):return self._stage.bank.rows
    @property
    def broker_fee_admissions(self):return self._guard.records
    def project(self,event):return self._stage.project(event)
    def admit_daily_paper_entry(self,*args,**kwargs):
        result=self._guard.admit_daily_paper_entry(*args,**kwargs)
        return dict(result,status='CONDITIONAL_REFERENCE_FEE_CHECKED_PROPOSAL') if result.get('plan') is not None else result


def make_reference_scenario(consumer,settlement,days,*,source_manifest_sha256,initial_cash_nano=10000*NANO,
                            evaluation_benchmark=None,enabled=False):
    require(enabled is True and type(consumer) is ReferenceConsumer,'reference scenario disabled/type')
    b=consumer.bindings;require(tuple(days)==consumer.days,'fixed reference scenario dates')
    class ReferenceScenario(b.family.ConditionalScenario):
        def __init__(self):
            super().__init__('C01',settlement,deepcopy(consumer.calendar),list(days),evaluation_benchmark)
            self.name='RECORDED_REFERENCE_OWN_RISK_V1'
            self.account=b.account.MultiAssetCashAccount(source_manifest_sha256=source_manifest_sha256,
                settlement_days=settlement,initial_cash_nano=initial_cash_nano,
                fee_policy=b.policy.BrokerFeePolicy('CURRENT_FIXED_SMART_US'))
            self.reference_candidates={}
        def step(self,core,event,candidates=None,residuals=None,sessions=None):
            require(core is consumer and sessions is not None,'own reference callback/clock context')
            candidate=consumer.project(event)
            if candidate is not None:self.reference_candidates[event.session,event.symbol]=candidate
            # Deliberately bypass CausalOpenScenario: it constructs full candidates.
            return b.family.ConditionalScenario.step(self,consumer,event,self.reference_candidates,
                                                     consumer.reference_rows,sessions)
        def result(self,benchmark,candidates=None):
            result=b.family.ConditionalScenario.result(self,benchmark,self.reference_candidates)
            result['two_legacy_numeric_goals_only']=result.pop('conditional_qualification',{})
            result.update(forecast_stream=PRODUCER,forecast_version=VERSION,
                reference_candidate_manifest=deepcopy(consumer.manifest),
                reference_bank_archive_sha256=consumer.archive_sha,
                full_model_attribution_included=False,full_acceptance=False,annual_alpha=None,
                historical_scope='CONSUMED_CONDITIONAL_DEVELOPMENT_NOT_CONFIRMATORY',
                broker_fee_admissions=deepcopy(consumer.broker_fee_admissions),
                sizing_denominator='FREE_SETTLED_CASH_SHADOW')
            return result
    return ReferenceScenario()
