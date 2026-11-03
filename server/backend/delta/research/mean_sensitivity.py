"""Disabled reference-mean/full-residual sensitivity; NOT reference risk calibration.

No historical reader or replay. Frozen full/reference own-risk guards are untouched.
The full residual law remains owned by the full model and is reused artificially.
"""
from copy import deepcopy
from dataclasses import dataclass
from fractions import Fraction
import hashlib
from pathlib import Path
from backend.delta.research import reference_consumer as own
from backend.delta.research import reference_ledger as ref
from backend.delta.research.qualify_reference import encode,digest,decode

PRODUCER='delta_reference_mean_full_risk_sensitivity'
VERSION='1'
MEAN_SOURCE='RECORDED_REFERENCE_MEAN'
FULL_POLICY='d1c251cf1f6139c6578494f53ab72f7ba836c34573ffa75d4ffcdaac15416722'
MODEL_SOURCE_SHA='63cf2c4d2d38c1f37ef7d51a4a40c12561ce3d5e2747158d8902711393699d9b'
EXTRA_PINS=dict(full='a522faeb790a45ac497a39a7bc091909b468c602b08b5e675ba67776f81c96f2',ledger='dfaa1255ed3e23577a6721ed36f8dd806402df728008737502545283bc92341f')
ALIASES=ref.SOURCE_KEYS|{'r46_result','r46_audit'}
NANO=10**9
require=ref.require

@dataclass(frozen=True)
class SensitivityBindings:
    native: object
    full: object
    ledger: object
    def verify(self):
        require(type(self.native)is own.FrozenBindings,'typed native bindings')
        self.native.verify()
        for name,pin in EXTRA_PINS.items():require(digest(Path(getattr(self,name).__file__).read_bytes())==pin,'pinned sensitivity code:'+name)

class FullResidualBank:
    """Source-bound full native stream; never relabelled as reference errors."""
    def __init__(self,bindings,sources,context,*,enabled=False):
        require(enabled is True,'full bank view disabled')
        require(type(bindings)is SensitivityBindings,'typed sensitivity bindings')
        bindings.verify();self.bindings=bindings;context=deepcopy(context)
        require(set(sources)==set(context['source_specs'])==ALIASES,'full source closure')
        require(context['scope']in ('SYNTHETIC_FIXTURE_ONLY','PINNED_FULL_R46_BANK'),'full bank scope')
        for alias in ALIASES:
            spec=context['source_specs'][alias]
            require(type(sources[alias])is bytes and len(sources[alias])==spec['bytes']and digest(sources[alias])==spec['sha256'],'full source bytes:'+alias)
        if context['scope']=='PINNED_FULL_R46_BANK':
            require(context['source_specs']['r46_result']['sha256']=='5142852a602da20aa912fb0534225e8251cb57dd3473ed98e8952b24a4310be6'
                and context['source_specs']['r46_audit']['sha256']=='8d07ab35632ee2eab5c26208bf4bcd4ab20e68c98bfb180074eadc49014c4a4c','accepted full R46 identity')
        data={alias:decode(raw)for alias,raw in sources.items()};proof=data['r46_result'];audit=data['r46_audit']
        require(audit['status']=='INDEPENDENT_R46_OWN_OPEN_LEDGER_ASOF_AUDIT_PASS_NOT_RISK_ADMISSION'
                and audit['result_sha256']==digest(sources['r46_result']),'full R46 audit binding')
        for alias in ref.SOURCE_KEYS:
            spec=context['source_specs'][alias]
            require(proof['inputs'][spec['path']]==audit['input_sha256'][spec['path']]==spec['sha256'],'full R46 input binding')
        self.days=tuple(context['evaluation_sessions']);self.symbols=tuple(context['symbols'])
        require(self.days and self.days==tuple(sorted(set(self.days)))and self.symbols==tuple(bindings.native.family.UNIVERSE),'fixed sensitivity roster')
        self.calendar=data['calendar']['sessions'];self.records={};self.hashes={}
        for record in data['records']['records']:
            day=record['metadata']['target_start'][:10];require(day not in self.records,'unique target session')
            self.records[day]=record;self.hashes[day]=ref.canonical_hash(record)
        require(all(day in self.records for day in self.days),'fixed source sessions')
        historical=[r for r in data['records']['records']if r['created_at'][:10]<'2026-03-31']
        require(len(historical)==85,'fixed85 full history')
        # The old builder checks modeled next-open clocks at microsecond precision.
        # Preserve its exact declared model contract before typed reconstruction.
        calendar_days=sorted(self.calendar)
        for alias in ('outcomes','errors'):
            for row in data[alias][alias]:
                due=row['due_session'];i=calendar_days.index(due)
                require(i+1<len(calendar_days)and ref.stamp(row['available_at_simulated'])==self.calendar[calendar_days[i+1]]['open_ns'],
                        'exact full modeled availability')
        ledger=bindings.ledger.build_own_ledger(historical,data['attempts']['attempts'],data['outcomes']['outcomes'],data['errors']['errors'],
            self.calendar,expected_policy_identity=FULL_POLICY,expected_model_source_sha256=MODEL_SOURCE_SHA)
        self._owner=bindings.full.ConditionalOpenSimulationCore(bindings.native.core,ledger,
            conditional_normalizer_transfer=bindings.native.projection.ASSUMPTION)
        self.rows=self._owner.native_residual_rows
        self.policy_identity=FULL_POLICY
        self.archive_sha=ref.canonical_hash({k:digest(sources[k])for k in sorted(sources)})
        scopes={(r['session'],r['symbol']):r for r in proof['rows']}
        require(len(scopes)==len(proof['rows'])==len(self.days)*7,'complete full R46 scopes')
        self._cohorts={}
        for day in self.days:
            cutoff=self.calendar[self.records[day]['information_cutoff'][:10]]['close_ns']
            for symbol in self.symbols:
                saved=scopes[day,symbol];cohort=bindings.native.core.daily_residual_cohort_asof(self.rows,symbol=symbol,
                    producer=bindings.native.projection.PRODUCER,model_version=bindings.native.projection.VERSION,
                    policy_identity=FULL_POLICY,horizon_sessions=5,cutoff_ns=cutoff)
                require(type(saved['cutoff_ns'])is int and saved['cutoff_ns']==cutoff,'full cutoff')
                require(saved['attempt_identities']==list(cohort['attempt_identities']),'full original attempt IDs')
                exact=tuple(None if v is None else own._fraction(v)for v in saved['residuals_exact'])
                require(exact==cohort['residuals'],'full exact residual parity')
                for name in ('attempted','observed','missing'):
                    require(type(saved[name])is int and saved[name]==cohort[name],'full membership counts')
                self._cohorts[symbol,cutoff]=cohort
    def asof(self,symbol,cutoff):
        require((symbol,cutoff)in self._cohorts,'fixed full forecast cutoff')
        return dict(self._cohorts[symbol,cutoff])

@dataclass(frozen=True)
class SensitivityForecast:
    source_projection: object
    expected_period_return: Fraction
    policy_identity: str
    mean_source: str=MEAN_SOURCE
    residual_bank_source: str=FULL_POLICY
    producer: str=PRODUCER
    version: str=VERSION
    artificial_uncertainty_proxy: bool=True
    calibrated_reference_risk: bool=False
    operational_risk_admitted: bool=False
    historical_issuance_attested: bool=False
    def __post_init__(self):
        require(type(self.expected_period_return)is Fraction and self.expected_period_return>-1,'sensitivity mean domain')
        require(self.mean_source==MEAN_SOURCE and self.residual_bank_source==FULL_POLICY and self.producer==PRODUCER and self.version==VERSION,'sensitivity identities')
        require(self.artificial_uncertainty_proxy is True and self.calibrated_reference_risk is False
            and self.operational_risk_admitted is False and self.historical_issuance_attested is False,'artificial unqualified scope')
    def __getattr__(self,name):
        if name in ('anchor','symbol','origin','target_start','target_end','forecast_information_cutoff_ns',
                    'modeled_forecast_available_ns','target_exit_ns','base_record_sha256'):
            return getattr(self.source_projection,name)
        raise AttributeError(name)
    @property
    def terminal_mean_price_nano(self):return self.anchor.price_nano*(1+self.expected_period_return)
    @property
    def identity(self):
        return ref.canonical_hash(dict(producer=PRODUCER,version=VERSION,consumer_policy=self.policy_identity,
            mean_source=MEAN_SOURCE,residual_bank_source=FULL_POLICY,record=self.base_record_sha256,
            symbol=self.symbol,origin=self.origin,target_start=self.target_start,target_end=self.target_end,
            cutoff=self.forecast_information_cutoff_ns,anchor=self.anchor.identity,mean=str(self.expected_period_return)))

@dataclass(frozen=True)
class SensitivityPlan(own.ReferencePlan):
    artificial_uncertainty_proxy: bool=True
    calibrated_reference_risk: bool=False
    def __post_init__(self):
        super().__post_init__()
        require(self.artificial_uncertainty_proxy is True and self.calibrated_reference_risk is False,'sensitivity plan scope')

class _SensitivityStage:
    def __init__(self,bank):
        self.bank=bank;self.b=bank.bindings.native;self._core=self.b.core
        self.records=bank.records;self.hashes=bank.hashes;self.calendar=bank.calendar;self.days=bank.days;self.symbols=bank.symbols
        self.full_policy=FULL_POLICY;self.archive_sha=bank.archive_sha
        self.policy_id=ref.canonical_hash(dict(producer=PRODUCER,version=VERSION,mean_source=MEAN_SOURCE,
            residual_source=FULL_POLICY,code=dict(own.PINS,**EXTRA_PINS),budget='FREE_SETTLED_CASH_SHADOW',cost='CURRENT_FIXED_SMART_US'))
        self.store=self.b.projection.FirstAnchorProjectionStore(max_anchors=7*len(self.days));self.candidates={};self.manifest=[]
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
        forecast=SensitivityForecast(source,self.number(record['metadata']['reference_mean']),self.policy_id)
        require(forecast.expected_period_return>-1,'reference simple return')
        candidate=(deepcopy(record),forecast,forecast.anchor,end,forecast.target_exit_ns-60*NANO)
        self.candidates[key]=candidate
        self.manifest.append(dict(session=event.session,symbol=event.symbol,forecast_identity=forecast.identity,
            producer=PRODUCER,version=VERSION,source_record_sha256=forecast.base_record_sha256,
            source_quote_identity=forecast.anchor.source_quote_identity,source_index=forecast.anchor.source_index,
            anchor_available_ns=forecast.anchor.observed_at_ns,anchor_price_nano=forecast.anchor.price_nano,
            cutoff_ns=forecast.forecast_information_cutoff_ns,target_exit_ns=forecast.target_exit_ns,
            prediction=str(forecast.expected_period_return),full_bank_archive_sha256=self.archive_sha,
            mean_source=MEAN_SOURCE,residual_bank_source=FULL_POLICY,artificial_uncertainty_proxy=True,calibrated_reference_risk=False))
        return candidate

    def admit_daily_paper_entry(self,record,forecast,reference,event,paper,residual_rows,*,policy_identity,
            session_calendar,entry_start_ns,entry_end_ns,exit_start_ns,corporate_action_contract,**kwargs):
        core=self._core
        require(not kwargs or set(kwargs)=={'allow_reported_close_sensitivity'}and kwargs['allow_reported_close_sensitivity']is True,'no parameter overrides')
        require(type(forecast) is SensitivityForecast and forecast.policy_identity==self.policy_id
                and type(forecast.source_projection) is self.b.projection.ConditionalOpenProjection,'sensitivity forecast identity')
        key=(forecast.target_start,forecast.symbol)
        require(key in self.candidates and forecast==self.candidates[key][1]
                and reference==forecast.anchor,'owned source projection/anchor')
        require(ref.canonical_hash(record)==self.hashes[forecast.target_start]==forecast.base_record_sha256
                and forecast.expected_period_return==self.number(record['metadata']['reference_mean'])
                and policy_identity==self.full_policy,'source/reference binding')
        require(residual_rows is self.bank.rows,'exact full risk row stream')
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
                   operational_risk_admitted=False,future_coverage_certified=False,sensitivity_only=True,
                   mean_source=MEAN_SOURCE,residual_bank_source=FULL_POLICY,artificial_uncertainty_proxy=True,calibrated_reference_risk=False)
        def unavailable(status,reason,**extra):return dict(status=status,reasons=(reason,),plan=None,**flags,**extra)
        if paper.shares or paper.pending is not None:return unavailable('UNAVAILABLE','ACCOUNT_NOT_FLAT')
        if event.session!=forecast.target_start or not entry_start_ns<=event.available_ns<=entry_end_ns:
            return unavailable('UNAVAILABLE','OUTSIDE_FIXED_ENTRY_WINDOW')
        if forecast.modeled_forecast_available_ns>event.available_ns or reference.observed_at_ns>event.available_ns:
            return unavailable('UNAVAILABLE','INPUT_NOT_YET_AVAILABLE')
        if not core.quote_is_fresh(event,2*NANO):return unavailable('UNAVAILABLE','INVALID_OR_STALE_BOOK')
        cohort=self.bank.asof(event.symbol,forecast.forecast_information_cutoff_ns)
        if cohort['observed']<30 or cohort['missing']!=0:
            return unavailable('UNAVAILABLE','FULL_PROXY_MANIFEST_INCOMPLETE_OR_INSUFFICIENT',cohort=cohort)
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
            forecast_stream=PRODUCER,full_bank_archive_sha256=self.archive_sha,cohort=cohort,sizing=sizing,
            quote_identity=event.identity,reference_price_nano=reference.price_nano,exit_price_discount=discount,
            calendar_content_sha256=core.calendar_content_digest(session_calendar),horizon_sessions=5,
            shadow_free_settled_cash_nano=paper.cash,shadow_nav_nano=nav,loss_budget_nano=nav//50,
            sizing_denominator='FREE_SETTLED_CASH_SHADOW',modeled_issuance_not_attested=True,**flags)
        ident=hashlib.sha256(repr(receipt).encode()).hexdigest()
        plan=SensitivityPlan(ident,forecast.identity,event.symbol,entry_start_ns,entry_end_ns,exit_start_ns,
                           forecast.target_exit_ns,limit,sizing['shares'])
        return dict(status='ARTIFICIAL_REFERENCE_MEAN_FULL_RISK_NATIVE_PROPOSAL',plan=plan,receipt=receipt,**flags)


class MeanOnlySensitivity:
    def __init__(self,bank,*,enabled=False):
        require(enabled is True,'mean sensitivity disabled')
        require(type(bank)is FullResidualBank,'source-bound full bank required')
        bank.bindings.verify();self.bindings=bank.bindings.native;self._stage=_SensitivityStage(bank)
        self._guard=self.bindings.fees.BrokerAdmissionCore(self._stage,self.bindings.policy.BrokerFeePolicy('CURRENT_FIXED_SMART_US'))
    def __getattr__(self,name):return getattr(self._stage,name)
    @property
    def risk_rows(self):return self._stage.bank.rows
    @property
    def broker_fee_admissions(self):return self._guard.records
    def project(self,event):return self._stage.project(event)
    def admit_daily_paper_entry(self,*args,**kwargs):
        out=self._guard.admit_daily_paper_entry(*args,**kwargs)
        return dict(out,status='ARTIFICIAL_REFERENCE_MEAN_FULL_RISK_FEE_CHECKED')if out.get('plan')is not None else out


def make_sensitivity_scenario(consumer,settlement,days,*,source_manifest_sha256,initial_cash_nano=10000*NANO,evaluation_benchmark=None,enabled=False):
    require(enabled is True and type(consumer)is MeanOnlySensitivity,'sensitivity scenario disabled/type')
    b=consumer.bindings;require(tuple(days)==consumer.days,'fixed sensitivity dates')
    class SensitivityScenario(b.family.ConditionalScenario):
        def __init__(self):
            super().__init__('C01',settlement,deepcopy(consumer.calendar),list(days),evaluation_benchmark)
            self.name='ARTIFICIAL_REFERENCE_MEAN_FULL_RISK_V1'
            self.account=b.account.MultiAssetCashAccount(source_manifest_sha256=source_manifest_sha256,settlement_days=settlement,
                initial_cash_nano=initial_cash_nano,fee_policy=b.policy.BrokerFeePolicy('CURRENT_FIXED_SMART_US'))
            self.sensitivity_candidates={}
        def step(self,core,event,candidates=None,residuals=None,sessions=None):
            require(core is consumer and sessions is not None,'own sensitivity callback/clock')
            candidate=consumer.project(event)
            if candidate is not None:self.sensitivity_candidates[event.session,event.symbol]=candidate
            return b.family.ConditionalScenario.step(self,consumer,event,self.sensitivity_candidates,consumer.risk_rows,sessions)
        def result(self,benchmark,candidates=None):
            report=b.family.ConditionalScenario.result(self,benchmark,self.sensitivity_candidates)
            report['two_legacy_numeric_goals_only']=report.pop('conditional_qualification',{})
            report.update(forecast_stream=PRODUCER,mean_source=MEAN_SOURCE,residual_bank_source=FULL_POLICY,
                artificial_uncertainty_proxy=True,calibrated_reference_risk=False,full_model_attribution_included=False,
                sensitivity_candidate_manifest=deepcopy(consumer.manifest),full_bank_archive_sha256=consumer.archive_sha,
                full_acceptance=False,annual_alpha=None,live_authorized=False,external_paper_authorized=False,
                broker_fee_admissions=deepcopy(consumer.broker_fee_admissions),sizing_denominator='FREE_SETTLED_CASH_SHADOW',
                qualification='ARTIFICIAL_SHARED_FULL_RISK_SENSITIVITY_NOT_REFERENCE_RISK_CALIBRATION')
            return report
    return SensitivityScenario()
