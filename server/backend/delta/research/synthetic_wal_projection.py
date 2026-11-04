"""Disabled invented-fixture WAL projection; never real-source authority.

BEGIN does not anchor a snapshot: the first schema SELECT does. All schema,
keys and values use that same connection/transaction. A read-only WAL reader
may affect coordination files/checkpoint progress. No immutable URI, journal
mode change, checkpoint, backup, main-file copy or manual sidecar cleanup.
"""
from copy import deepcopy
from datetime import date,datetime
import hashlib
import math
from pathlib import Path
import sqlite3
import stat
import tempfile

from backend.delta.research import qualify_reference as q

SCHEMA='DELTA_SYNTHETIC_WAL_PROJECTION_V1'
LIMITS=dict(wall_seconds=120,rss_bytes=512*1024**2,output_bytes=32*1024**2,disk_reserve_bytes=2*1024**3)
SYMBOLS=('AAPL','MSFT','AMZN','GOOGL','JPM','XOM','WMT','SPY','IWM','RSP','IWF','IWD','XLY','XLP','UUP','GLD','USO','IEF','TLT')
COLUMNS=('ticker','timestamp','open','high','low','close','volume','provider','adjusted','fetched_at')
TYPES=dict(ticker='TEXT',timeframe='TEXT',timestamp='TEXT',open='REAL',high='REAL',low='REAL',close='REAL',volume='REAL',provider='TEXT',adjusted='INTEGER',fetched_at='TEXT')
START='2006-01-03';END='2026-04-28';MAX_ROWS=10000
WHERE="timeframe='1d' AND adjusted=1 AND ticker IN ("+','.join('?'for _ in SYMBOLS)+") AND substr(timestamp,1,10)>=? AND substr(timestamp,1,10)<=?"
PARAMETERS=(*SYMBOLS,START,END)
KEY_SQL='SELECT ticker,substr(timestamp,1,10) AS session FROM ohlcv_bars WHERE '+WHERE+' ORDER BY session,ticker'
VALUE_SQL='SELECT '+','.join(COLUMNS)+' FROM ohlcv_bars WHERE '+WHERE+' ORDER BY substr(timestamp,1,10),ticker'
ANCHOR_SQL="SELECT sql FROM sqlite_schema WHERE type='table' AND name='ohlcv_bars'"
require=q.require


def check_config(config,pin):
    require(type(config)is dict and set(config)=={'schema','scope','fixture_root','fixture_nonce','source_identity','expected_keys','resources'},'CONFIG_SCHEMA')
    require(config['schema']==SCHEMA and config['scope']=='INVENTED_TEMP_FIXTURE_ONLY','FIXTURE_SCOPE')
    require(type(pin)is str and q.digest(q.encode(config))==pin,'FIXTURE_CONFIG_BINDING')
    require(config['resources']==LIMITS and all(type(v)is int for v in config['resources'].values()),'FIXTURE_LIMITS')
    require(type(config['fixture_root'])is str and Path(config['fixture_root']).is_absolute(),'FIXTURE_ROOT')
    require(type(config['fixture_nonce'])is str and 1<=len(config['fixture_nonce'])<=128,'FIXTURE_NONCE')
    identity=config['source_identity'];require(type(identity)is list and len(identity)==2 and all(type(v)is int and v>=0 for v in identity),'FIXTURE_IDENTITY')
    keys=config['expected_keys'];require(type(keys)is list and len(keys)<=MAX_ROWS,'FIXTURE_KEYS')
    for row in keys:
        require(type(row)is list and len(row)==2 and row[0]in SYMBOLS and type(row[1])is str and START<=row[1]<=END and len(row[1])==10,'FIXTURE_KEY')
        try:date.fromisoformat(row[1])
        except ValueError:raise q.QualificationFailure('FIXTURE_KEY_DATE')from None
    require(keys==[list(k)for k in sorted({tuple(x)for x in keys},key=lambda x:(x[1],x[0]))],'FIXTURE_KEY_ORDER')


def _identity(path):
    st=path.lstat();require(stat.S_ISREG(st.st_mode),'FIXTURE_SOURCE_REGULAR')
    return [st.st_dev,st.st_ino]


def _paths(config):
    root=Path(config['fixture_root']);require(not root.is_symlink(),'FIXTURE_ROOT_SYMLINK')
    root=root.resolve();temporary=Path(tempfile.gettempdir()).resolve()
    require(temporary in root.parents and root!=temporary,'TEMP_FIXTURE_ROOT_REQUIRED')
    require(root.is_dir(),'FIXTURE_ROOT_DIRECTORY')
    source=root/'source.sqlite';out=root/'projection'
    require(not out.exists()and not out.is_symlink(),'FRESH_EXCLUSIVE_OUTPUT')
    require(_identity(source)==config['source_identity'],'FIXTURE_SOURCE_IDENTITY')
    return root,source,out


def _release(db):
    """Best effort both rollback and close; return errors, never mask primary."""
    errors=[]
    for name,call in [('progress_clear',lambda:db.set_progress_handler(None,0)),
                      ('rollback',db.rollback),('close',db.close)]:
        try:call()
        except BaseException as exc:errors.append((name,exc))
    return errors


def _failure(out,exc,guard):
    if getattr(exc,'_publication_committed',False):return
    try:
        uncertain=isinstance(exc,q.PublicationUncertain)or getattr(exc,'_publication_uncertain',False)
        body=q.encode(dict(schema=SCHEMA,status='PUBLICATION_UNCERTAIN'if uncertain else 'FAILED_NO_COMPLETE',
            reason=(str(exc)if isinstance(exc,q.QualificationFailure)else type(exc).__name__)[:128],sample=guard.last,
            no_retry=True,actual_source_authorized=False))
        require(len(body)<=8192,'FAILURE_SIZE')
        size=sum(p.lstat().st_size for p in out.iterdir());require(size+len(body)<=guard.limits['output_bytes'],'FAILURE_OUTPUT_LIMIT')
        with(out/'failure.json').open('xb',buffering=0)as f:q.write_all(f,body)
    except BaseException:pass


def run_fixture(config,*,expected_config_sha256=None,enabled=False):
    require(enabled is True,'SYNTHETIC_WAL_PROJECTION_DISABLED')
    config=deepcopy(config);check_config(config,expected_config_sha256)
    root,source,out=_paths(config);out.mkdir(exist_ok=False);guard=q.Guard(out,config['resources'])
    db=None;primary=None;rows=[];keys=[];byte_count=0;callback_error=[]
    try:
        guard.check('before_connect')
        db=sqlite3.connect(source.as_uri()+'?mode=ro',uri=True,timeout=0,isolation_level=None)
        try:
            def progress():
                try:guard.check('sqlite_progress');return 0
                except BaseException as exc:
                    if not callback_error:callback_error.append(exc)
                    return 1
            def execute(sql,parameters=()):
                try:return db.execute(sql,parameters)
                except BaseException:
                    if callback_error:raise callback_error[0]
                    raise
            db.set_progress_handler(progress,100)
            execute('PRAGMA query_only=ON');execute('PRAGMA read_uncommitted=OFF')
            execute('BEGIN DEFERRED');guard.check('after_begin_before_first_read')
            # First actual database read, not BEGIN, anchors this snapshot.
            schema=execute(ANCHOR_SQL).fetchall();guard.check('after_anchor')
            require(len(schema)==1 and type(schema[0][0])is str and schema[0][0].lstrip().upper().startswith('CREATE TABLE '),'FIXTURE_TABLE')
            columns={r[1]:r[2].upper()for r in execute('PRAGMA table_info(ohlcv_bars)')}
            require(all(columns.get(k)==v for k,v in TYPES.items()),'FIXTURE_COLUMN_TYPES')
            require(execute('SELECT nonce FROM synthetic_fixture_metadata').fetchall()==[(config['fixture_nonce'],)],'FIXTURE_MARKER')
            require(db.in_transaction,'SNAPSHOT_TRANSACTION')
            cursor=execute(KEY_SQL,PARAMETERS)
            for row in cursor:
                guard.check('key_row');keys.append(list(row));require(len(keys)<=MAX_ROWS,'ROW_LIMIT')
            require(keys==config['expected_keys'],'FIXTURE_KEY_ROSTER')
            guard.check('after_keys_before_values');require(db.in_transaction,'SNAPSHOT_TRANSACTION')
            cursor=execute(VALUE_SQL,PARAMETERS)
            for values in cursor:
                guard.check('value_row');row=dict(zip(COLUMNS,values))
                for name in ('open','high','low','close','volume'):
                    value=row[name];require(value is None or type(value)in(int,float)and math.isfinite(value),'FINITE_OR_NULL_NUMBER')
                for name in ('ticker','timestamp','provider','fetched_at'):
                    require(type(row[name])is str and len(row[name])<=128,'BOUNDED_TEXT')
                try:datetime.fromisoformat(row['timestamp'])
                except ValueError:raise q.QualificationFailure('FIXTURE_TIMESTAMP')from None
                require(len(rows)<len(keys)and [row['ticker'],row['timestamp'][:10]]==keys[len(rows)],'SNAPSHOT_KEY_VALUE_PARITY')
                require(type(row['adjusted'])is int and row['adjusted']==1,'ADJUSTED_FLAG')
                byte_count+=len(q.encode(row));require(byte_count<=LIMITS['output_bytes']//4,'ROW_BUFFER_LIMIT');rows.append(row)
            require(len(rows)==len(keys)and db.in_transaction,'SNAPSHOT_ROW_COUNT')
            guard.check('after_values')
        except BaseException as exc:
            primary=callback_error[0]if callback_error else exc
        errors=_release(db);db=None
        if primary is not None:
            for name,exc in errors:primary.add_note('READER_CLEANUP_UNCERTAIN:'+name+':'+type(exc).__name__)
            raise primary
        if errors:
            # A cancellation first observed during cleanup remains cancellation,
            # not a generic qualification error. Keep its object and attach all
            # secondary failures without replacing an existing body primary.
            cancellation=next((exc for _,exc in errors if not isinstance(exc,Exception)),None)
            if cancellation is not None:
                for name,exc in errors:
                    cancellation.add_note('READER_CLEANUP_UNCERTAIN:'+name+':'+type(exc).__name__)
                raise cancellation
            raise q.QualificationFailure('READER_CLEANUP_UNCERTAIN')
        guard.check('reader_closed');require(_identity(source)==config['source_identity'],'SOURCE_PATH_IDENTITY_CHANGED')
        payload=dict(schema=SCHEMA,status='COMPLETE_SYNTHETIC_TRANSACTION_PROJECTION',scope=config['scope'],
            config_sha256=expected_config_sha256,source_identity_boundary=config['source_identity'],keys=keys,rows=rows,
            key_sha256=q.digest(q.encode(keys)),row_sha256=q.digest(q.encode(rows)),query=dict(keys=KEY_SQL,values=VALUE_SQL,parameters=PARAMETERS),
            snapshot='First schema SELECT anchors; same connection/read transaction for schema, keys and values; reader closed before publication.',
            qualifications='Invented fixture only; no continuous custody, sidecar-invariance, hard resource, crash or PIT guarantee. No real-source authority.',
            resource_sample=guard.last,actual_source_authorized=False,live_authorized=False)
        return q.publish(out,payload,guard)
    except BaseException as exc:
        if db is not None:
            for name,cleanup in _release(db):exc.add_note('READER_CLEANUP_UNCERTAIN:'+name+':'+type(cleanup).__name__)
        _failure(out,exc,guard);raise
