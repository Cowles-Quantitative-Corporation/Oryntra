"""Version2 durable SYNTHETIC-only evidence journal; no native/broker integration.

A write transaction owns refresh, checks and append. Unique semantic review keys
and replay validation prevent duplicate economic effects across handles/retries.
V1 files require a separately reviewed recovery; no implicit migration is offered.
"""
from copy import deepcopy
from fractions import Fraction as F
import json,sqlite3,hashlib


def canonical(x):return json.dumps(x,sort_keys=True,separators=(',',':'),allow_nan=False)
def signature(x):return hashlib.sha256(canonical(x).encode()).hexdigest()


class SurpriseJournal:
    VERSION='2'
    def __init__(self,path):
        self.db=sqlite3.connect(path,isolation_level=None,timeout=5)
        try:
            self.db.execute('pragma synchronous=FULL')
            self.db.execute('BEGIN IMMEDIATE')
            tables={r[0] for r in self.db.execute("select name from sqlite_master where type='table'")}
            if 'events' in tables and 'journal_metadata' not in tables:
                raise ValueError('legacy/unversioned journal blocked; independent recovery required')
            self.db.execute('create table if not exists journal_metadata (key text primary key, value text not null)')
            self.db.execute('create table if not exists events (n integer primary key, kind text not null, semantic_key text not null, payload text not null, sha text not null, unique(kind,semantic_key))')
            if 'journal_metadata' not in tables:
                self.db.execute('insert into journal_metadata values(?,?)',('version',self.VERSION))
            self._check_version()
            self.db.execute('COMMIT')
            self._refresh_read()
        except BaseException:
            if self.db.in_transaction:self.db.execute('ROLLBACK')
            self.db.close()
            raise
    def close(self):self.db.close()
    def _check_version(self):
        if list(self.db.execute('select key,value from journal_metadata'))!=[('version',self.VERSION)]:
            raise ValueError('journal version/metadata mismatch')
    @staticmethod
    def _key(kind,value):
        if kind=='SYNTHETIC_REVIEW':
            ident=value.get('execution_id')
            if type(ident) is not str or not ident:raise ValueError('review execution identity')
            return ident
        if kind in ('RECEIVE','REVIEW_DISPUTE'):return signature(value)
        raise ValueError('unmapped journal kind')
    def _load_state(self):
        """Called only within one explicit database snapshot transaction."""
        self._check_version()
        self.incidents={};self.conflicts=[];self.applied={};self.raw=[]
        self.book=dict(cash=F(100),owned=F(0),reserve=F('50.50'));self.cumulative=F(0)
        seen=set()
        for n,kind,key,payload,sha in self.db.execute('select n,kind,semantic_key,payload,sha from events order by n'):
            if n!=len(self.raw)+1 or hashlib.sha256(payload.encode()).hexdigest()!=sha:raise ValueError('durable journal integrity')
            value=json.loads(payload)
            if type(value) is not dict or canonical(value)!=payload or key!=self._key(kind,value) or (kind,key) in seen:
                raise ValueError('durable semantic uniqueness/schema mismatch')
            seen.add((kind,key));self._apply(kind,value);self.raw.append((kind,value))
    def _rebuild(self):
        self._load_state()
    def _refresh_read(self):
        self.db.execute('BEGIN')
        try:self._rebuild();self.db.execute('COMMIT')
        except BaseException:
            if self.db.in_transaction:self.db.execute('ROLLBACK')
            raise
    def _validate_review(self,v):
        ident=v['execution_id']
        if self.conflicts:raise ValueError('disputed/malformed evidence unresolved')
        if v.get('scope')!='SYNTHETIC_REVIEW_EVIDENCE' or type(v.get('review_id')) is not str or not v['review_id'] or not all(v.get(k) is True for k in ('identity_verified','cash_fees_verified','order_remainder_verified')):
            raise ValueError('synthetic independent evidence required; no actual authentication provided')
        if ident in self.applied:raise ValueError('duplicate economic review in durable replay')
        e=self.incidents[ident]
        if e.get('source')!='SYNTHETIC_ONLY' or e.get('kind','FILL')!='FILL':raise ValueError('actual/correction source accounting blocked')
        q,p,fee,remain,reserve=map(F,(e['quantity'],e['price'],e['fee'],v['remaining_quantity'],v['remaining_reserve']))
        if q<=0 or p<=0 or min(fee,remain,reserve)<0 or self.cumulative+q+remain!=1:raise ValueError('cumulative/remainder domain')
        if reserve!=remain*F('50.50') or self.book['cash']-q*p-fee<reserve:raise ValueError('fixture reserve/funding reconciliation')
        return q,p,fee,reserve
    def _apply(self,kind,v):
        if kind=='RECEIVE':
            ident=v.get('provider_execution_id')
            if type(ident) is not str or not ident:self.conflicts.append(deepcopy(v));return
            if ident in self.incidents:
                if self.incidents[ident]!=v:self.conflicts.append(deepcopy(v))
            else:self.incidents[ident]=deepcopy(v)
        elif kind=='REVIEW_DISPUTE':self.conflicts.append(deepcopy(v))
        elif kind=='SYNTHETIC_REVIEW':
            q,p,fee,reserve=self._validate_review(v)
            self.book=dict(cash=self.book['cash']-q*p-fee,owned=self.book['owned']+q,reserve=reserve)
            self.cumulative+=q;self.applied[v['execution_id']]=deepcopy(v)
        else:raise ValueError('unmapped journal event')
    def _insert(self,kind,value):
        payload=canonical(value)
        self.db.execute('insert into events(kind,semantic_key,payload,sha) values(?,?,?,?)',
            (kind,self._key(kind,value),payload,hashlib.sha256(payload.encode()).hexdigest()))
    def _write(self,kind,value):
        """Refresh/validate/idempotency/append are serialized by SQLite, not cache."""
        error=None
        self.db.execute('BEGIN IMMEDIATE')
        try:
            self._load_state()
            key=self._key(kind,value)
            if kind=='RECEIVE':
                if not self.db.execute('select 1 from events where kind=? and semantic_key=?',(kind,key)).fetchone():self._insert(kind,value)
            elif kind=='SYNTHETIC_REVIEW':
                if key in self.applied:
                    if self.applied[key]!=value:
                        dispute=dict(execution_id=key,attempted_review=value,status='REVIEW_REVISION_REQUIRES_UNSUPPORTED_CORRECTION_WORKFLOW')
                        dk=self._key('REVIEW_DISPUTE',dispute)
                        if not self.db.execute('select 1 from events where kind=? and semantic_key=?',('REVIEW_DISPUTE',dk)).fetchone():self._insert('REVIEW_DISPUTE',dispute)
                        error=ValueError('conflicting review retained; correction workflow blocked')
                else:self._validate_review(value);self._insert(kind,value)
            else:raise ValueError('unsupported write')
            self.db.execute('COMMIT')
        except BaseException:
            if self.db.in_transaction:self.db.execute('ROLLBACK')
            raise
        # If this post-commit refresh fails, a retry still refreshes under the write
        # transaction before checking uniqueness. Never trust the stale local cache.
        self._refresh_read()
        if error:raise error
    def receive(self,event):
        if type(event) is not dict:raise ValueError('JSON object source evidence required')
        self._write('RECEIVE',deepcopy(event))
    def reconcile_synthetic(self,execution_id,review):
        if type(review) is not dict or type(execution_id) is not str or not execution_id:raise ValueError('review identity/schema')
        self._write('SYNTHETIC_REVIEW',dict(deepcopy(review),execution_id=execution_id))
    def status(self):
        self._refresh_read()
        return dict(admission='NOT_EVALUABLE',model_qualified=False,rearm_authorized=False,
            accounted=sorted(self.applied),unresolved=sorted(set(self.incidents)-set(self.applied)),
            disputes=len(self.conflicts),raw_events=len(self.raw),
            synthetic_view={k:str(v) for k,v in self.book.items()},
            available_synthetic=str(self.book['cash']-self.book['reserve']),
            actual_source_reconciliation='BLOCKED_AUTHENTICATION_CORRECTION_SETTLEMENT_CONTRACTS')
