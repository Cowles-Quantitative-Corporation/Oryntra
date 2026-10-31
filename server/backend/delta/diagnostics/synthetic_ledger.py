"""SQLite transactional SYNTHETIC lifecycle harness, never an execution adapter.

No network, dispatch, actual account restoration, automatic liquidation or rearm.
Cash uses gross-minus-purchase-payable. All events are explicit simulated inputs.
"""
from copy import deepcopy
from decimal import Decimal as D
import json
import sqlite3
from backend.delta.diagnostics.diagnostic_gate import decimal, fee
from backend.delta.diagnostics.risk_math import digest, ns
from backend.delta.diagnostics.arithmetic_policy import controlled,SPEC,validate_external


def encode(v):
    return json.dumps(v,sort_keys=True,separators=(',',':'),allow_nan=False)


class SyntheticLedger:
    @controlled
    def __init__(self,path,*,starting_cash,scope):
        if scope != 'SYNTHETIC_FIXTURE_ONLY':
            raise ValueError('synthetic harness only')
        cash = validate_external(starting_cash)
        if cash<0:raise ValueError('negative initial cash')
        self.db=sqlite3.connect(path,isolation_level=None)
        self.db.execute('CREATE TABLE IF NOT EXISTS state (id INTEGER PRIMARY KEY, body TEXT NOT NULL)')
        self.db.execute('CREATE TABLE IF NOT EXISTS events (id TEXT PRIMARY KEY, body TEXT NOT NULL, resulting_hash TEXT NOT NULL)')
        row=self.db.execute('SELECT body FROM state WHERE id=1').fetchone()
        if row is None:
            body=dict(cash=str(cash),payable='0',receivable='0',holdings={},orders={},
                      fills={},clock=0,version=0,latch=False,incidents=[],initial_cash=str(cash),arithmetic_policy=deepcopy(SPEC))
            self.db.execute('INSERT INTO state VALUES(1,?)',(encode(body),))
        elif json.loads(row[0])['initial_cash'] != str(cash):
            raise ValueError('restart seed mismatch')
        elif json.loads(row[0]).get('arithmetic_policy') != SPEC:
            raise ValueError('restart arithmetic policy mismatch; no silent v1 migration')

    def close(self): self.db.close()

    def snapshot(self):
        return json.loads(self.db.execute('SELECT body FROM state WHERE id=1').fetchone()[0])

    def _latch(self,reason,event):
        self.db.execute('BEGIN IMMEDIATE')
        try:
            state=self.snapshot();state['latch']=True
            try:evidence=encode(event)
            except (ValueError,TypeError,OverflowError):evidence='UNENCODABLE_EVENT:'+repr(event)[:4096]
            incident=dict(reason=reason,event_evidence=evidence[:8192],evidence_truncated=len(evidence)>8192)
            identity=digest(incident)
            if all(x['identity']!=identity for x in state.setdefault('incidents',[])):
                state['incidents'].append(dict(identity=identity,**incident))
            self.db.execute('UPDATE state SET body=? WHERE id=1',(encode(state),));self.db.execute('COMMIT')
        except BaseException:
            self.db.execute('ROLLBACK');raise

    @controlled
    def event(self,event,*,crash=None):
        """before_commit/after_commit are test-only crash injection points."""
        try:
            if type(event) is not dict or type(event.get('id')) is not str or not 0<len(event['id'])<=256:
                raise ValueError('bounded nonempty event identity required')
            raw=encode(event)
        except (ValueError,TypeError,OverflowError):
            self._latch('MALFORMED_EVENT',event)
            raise ValueError('malformed synthetic event; latched')
        self.db.execute('BEGIN IMMEDIATE')
        try:
            prior=self.db.execute('SELECT body FROM events WHERE id=?',(event['id'],)).fetchone()
            if prior:
                if prior[0]!=raw: raise ValueError('same identity changed payload')
                self.db.execute('COMMIT');return self.snapshot()
            state=self.snapshot();at=ns(event['at_ns'])
            if at < state['clock']: raise ValueError('backdated event')
            kind=event['kind'];orders=state['orders']
            if kind=='ADMIT':
                if state['latch']: raise ValueError('blocked rearm')
                if type(event['expected_version']) is not int or event['expected_version'] != state['version']: raise ValueError('stale or invalid version')
                if event['context_hash'] != digest(state): raise ValueError('stale state hash')
                ns(event['quote_ns']);ns(event['max_age_ns']);ns(event['valid_until_ns'])
                if not event['quote_ns']<=at<=event['valid_until_ns'] or at-event['quote_ns']>event['max_age_ns']:
                    raise ValueError('expired or stale unchanged quote')
                if event['clock_quality']!='SIMULATED_VERIFIED' or event['profile_kind']!='SYNTHETIC_FIXTURE_ONLY':
                    raise ValueError('unqualified admission context')
                ident=event['intent_id']
                if ident in orders: raise ValueError('duplicate intent')
                q,limit,step,quantum=(validate_external(event[k]) for k in ('quantity','limit','step','execution_quantum'))
                if min(q,limit,step,quantum)<=0 or q%step or q%quantum or step%quantum:
                    raise ValueError('positive aligned order-entry/execution grid required')
                if event.get('arithmetic_policy',SPEC)!=SPEC:raise ValueError('admission arithmetic mismatch')
                reserve=q*limit+fee(q*limit,event['fee'])
                existing=sum(decimal(o['reserve']) for o in orders.values())
                if reserve+existing+decimal(state['payable'])>decimal(state['cash']):
                    raise ValueError('insufficient settled cash')
                orders[ident]=dict(symbol=event['symbol'],quantity=str(q),remaining=str(q),limit=str(limit),
                                  reserve=str(reserve),notional='0',paid_fee='0',fee=deepcopy(event['fee']),
                                  status='NEVER_SENT',dispatch_fenced=False,step=str(step),execution_quantum=str(quantum))
            elif kind in ('SUBMIT_UNKNOWN','CANCEL_REQUEST','FENCE_UNSENT','TERMINAL','FILL'):
                o=orders[event['intent_id']]
                if kind=='SUBMIT_UNKNOWN':
                    if o['status']!='NEVER_SENT' or o['dispatch_fenced']:raise ValueError('invalid dispatch branch')
                    o['status']='SUBMIT_UNKNOWN'
                elif kind=='CANCEL_REQUEST':
                    if o['status']=='TERMINAL':raise ValueError('terminal intent')
                    o['status']='CANCEL_PENDING'
                elif kind=='FENCE_UNSENT':
                    if o['status']!='NEVER_SENT':raise ValueError('cannot infer unsent from timeout')
                    o.update(status='TERMINAL',reserve='0',dispatch_fenced=True)
                elif kind=='TERMINAL':
                    if event.get('authoritative') is not True or event.get('executions_reconciled') is not True:
                        raise ValueError('terminal evidence required')
                    o.update(status='TERMINAL',reserve='0')
                else:
                    if o['status'] not in ('SUBMIT_UNKNOWN','CANCEL_PENDING'):raise ValueError('fill outside possible working order')
                    q,price=validate_external(event['quantity']),validate_external(event['price'])
                    quantum=decimal(o['execution_quantum'])
                    if min(q,price)<=0 or q>decimal(o['remaining']) or price>decimal(o['limit']) or q%quantum or (decimal(o['remaining'])-q)%quantum:
                        raise ValueError('fill quantity/limit')
                    total=decimal(o['notional'])+q*price
                    increment=fee(total,o['fee'])-decimal(o['paid_fee'])
                    cost=q*price+increment
                    state['payable']=str(decimal(state['payable'])+cost)
                    state['holdings'][o['symbol']]=str(decimal(state['holdings'].get(o['symbol'],'0'))+q)
                    remaining=decimal(o['remaining'])-q
                    o.update(remaining=str(remaining),notional=str(total),paid_fee=str(fee(total,o['fee'])))
                    o['reserve']=str(remaining*decimal(o['limit'])+fee(total+remaining*decimal(o['limit']),o['fee'])-decimal(o['paid_fee']))
                    state['fills'][event['id']]=dict(intent_id=event['intent_id'],quantity=str(q),notional=str(q*price),
                                                   cost=str(cost),settled=False,corrected=False)
                    if remaining==0:o.update(status='TERMINAL',reserve='0')
            elif kind=='SETTLE_BUY':
                f=state['fills'][event['fill_id']]
                if f['settled'] or f['corrected']:raise ValueError('already settled/corrected')
                cost=decimal(f['cost']);state['cash']=str(decimal(state['cash'])-cost)
                state['payable']=str(decimal(state['payable'])-cost);f['settled']=True
            elif kind=='BUST':
                f=state['fills'][event['fill_id']];o=orders[f['intent_id']]
                # Explicitly narrow: only last, unsettled fill of a still unresolved parent.
                if f['settled'] or f['corrected'] or o['status']=='TERMINAL':
                    raise ValueError('unsupported post-terminal/settlement correction; reconcile')
                active=[k for k,v in state['fills'].items() if v['intent_id']==f['intent_id'] and not v['corrected']]
                if not active or active[-1]!=event['fill_id']:raise ValueError('only last unsettled fill correction supported')
                q=decimal(f['quantity']);state['holdings'][o['symbol']]=str(decimal(state['holdings'][o['symbol']])-q)
                state['payable']=str(decimal(state['payable'])-decimal(f['cost']))
                total=decimal(o['notional'])-decimal(f['notional']);remaining=decimal(o['remaining'])+q
                o.update(notional=str(total),remaining=str(remaining),paid_fee=str(fee(total,o['fee'])))
                o['reserve']=str(remaining*decimal(o['limit'])+fee(total+remaining*decimal(o['limit']),o['fee'])-decimal(o['paid_fee']))
                f['corrected']=True
            else:
                raise ValueError('unsupported event; no automatic rearm, sell or liquidation')
            for o in orders.values():
                quantum=decimal(o['execution_quantum'])
                if quantum<=0 or decimal(o['remaining'])%quantum:
                    raise ValueError('remaining execution-grid invariant')
            if decimal(state['cash'])<decimal(state['payable'])+sum(decimal(o['reserve']) for o in orders.values()):
                raise ValueError('funding invariant')
            state['clock']=at;state['version']+=1
            self.db.execute('UPDATE state SET body=? WHERE id=1',(encode(state),))
            self.db.execute('INSERT INTO events VALUES(?,?,?)',(event['id'],raw,digest(state)))
            if crash=='before_commit':raise RuntimeError('simulated precommit crash')
            self.db.execute('COMMIT')
        except BaseException as exc:
            if self.db.in_transaction:self.db.execute('ROLLBACK')
            if not isinstance(exc,RuntimeError):self._latch(str(exc),event)
            raise
        if crash=='after_commit':raise RuntimeError('simulated postcommit acknowledgment loss')
        return state

    @controlled
    def available(self):
        s=self.snapshot()
        return decimal(s['cash'])-decimal(s['payable'])-sum(decimal(o['reserve']) for o in s['orders'].values())

    @controlled
    def nav(self,marks):
        s=self.snapshot()
        return decimal(s['cash'])+decimal(s['receivable'])-decimal(s['payable'])+sum(decimal(q)*decimal(marks[k]) for k,q in s['holdings'].items())
