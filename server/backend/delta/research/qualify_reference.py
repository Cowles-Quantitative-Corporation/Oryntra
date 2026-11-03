"""Disabled one-shot source qualifier. No import-time I/O or portfolio consumer.

Only synthetic invocations are tested. Actual source access requires a separately
reviewed exact authority receipt. Receipts bind a scope; they are not signatures.
"""
import argparse
from copy import deepcopy
from dataclasses import asdict
from fractions import Fraction
import hashlib
import json
import os
from pathlib import Path
import resource
import shutil
import stat
import sys
import time

from backend.delta.research import reference_ledger as component

SCHEMA = 'DELTA_REFERENCE_QUALIFIER_V1'
OPERATION = 'REFERENCE_OWN_RISK_SOURCE_QUALIFICATION'
LIMITS = dict(wall_seconds=60, rss_bytes=256*1024**2, output_bytes=5*1024**2,
              disk_reserve_bytes=2*1024**3)
ALIASES = component.SOURCE_KEYS | {'r46_result', 'r46_audit'}
RESERVE = 12288  # bounded failure/uncertainty receipt + console metadata


class QualificationFailure(RuntimeError): pass
class PublicationUncertain(QualificationFailure): pass


def require(ok, code):
    if not ok: raise QualificationFailure(code)


def encode(value):
    def exact(x):
        if isinstance(x, Fraction): return str(x)
        raise TypeError('unsupported serialization type')
    return (json.dumps(value, sort_keys=True, separators=(',', ':'),
                       allow_nan=False, default=exact)+'\n').encode()


def digest(raw): return hashlib.sha256(raw).hexdigest()


def decode(raw):
    return json.loads(raw, object_pairs_hook=component._pairs,
                      parse_constant=lambda _: component.require(False, 'nonfinite JSON'))


def runtime_pins():
    """Public code bytes only; never follows configured source paths."""
    return dict(runner_sha256=digest(Path(__file__).read_bytes()),
                component_sha256=digest(Path(component.__file__).read_bytes()))


def check_config(config):
    """Structure only: no source stat/open/hash, output creation or existence check."""
    require(type(config) is dict and set(config) == {
        'schema','operation','scope','sources','origins','symbols','full_policy_id',
        'full_model_source_sha256','cutoffs','resources','output_directory'}, 'CONFIG_SCHEMA')
    require(config['schema']==SCHEMA and config['operation']==OPERATION, 'CONFIG_OPERATION')
    require(config['scope'] in ('SYNTHETIC_FIXTURE_ONLY','PINNED_PREAPRIL_REFERENCE_QUALIFICATION'),
            'CONFIG_SCOPE')
    require(config['resources']==LIMITS and all(type(v) is int for v in config['resources'].values()),
            'RESOURCE_CONFIG')
    require(type(config['sources']) is dict and set(config['sources'])==ALIASES, 'SOURCE_CLOSURE')
    for value in config['sources'].values():
        require(type(value) is dict and set(value)=={'path','bytes','sha256'}, 'SOURCE_SPEC')
        require(type(value['path']) is str and Path(value['path']).is_absolute(), 'ABSOLUTE_SOURCE')
        require(type(value['bytes']) is int and value['bytes']>=0, 'SOURCE_SIZE')
        require(type(value['sha256']) is str and len(value['sha256'])==64
                and all(c in '0123456789abcdef' for c in value['sha256']), 'SOURCE_PIN')
    require(type(config['output_directory']) is str and Path(config['output_directory']).is_absolute(),
            'OUTPUT_PATH')
    require(type(config['origins']) is list and config['origins']
            and config['origins']==sorted(set(config['origins'])), 'ORIGIN_ROSTER')
    require(type(config['symbols']) is list and len(config['symbols'])==len(set(config['symbols']))==7,
            'SYMBOL_ROSTER')
    require(type(config['cutoffs']) is list and config['cutoffs'], 'CUTOFF_ROSTER')
    seen=set()
    for c in config['cutoffs']:
        require(type(c) is dict and set(c)=={'session','cutoff_ns'} and type(c['session']) is str
                and type(c['cutoff_ns']) is int and c['cutoff_ns']>=0, 'CUTOFF_SCHEMA')
        require(c['session'] not in seen, 'DUPLICATE_CUTOFF');seen.add(c['session'])
    if config['scope']=='PINNED_PREAPRIL_REFERENCE_QUALIFICATION':
        require(len(config['origins'])==85 and len(config['cutoffs'])==21
                and all(c['session'].startswith('2026-04-') for c in config['cutoffs']), 'FIXED_SCOPE')
    return digest(encode(config))


def authorize(config, authority, expected_authority_sha256):
    config_sha=check_config(config)
    require(type(authority) is dict and type(expected_authority_sha256) is str
            and digest(encode(authority))==expected_authority_sha256, 'AUTHORITY_REQUIRED')
    require(set(authority)=={'schema','operation','scope','config_sha256','runner_sha256',
                             'component_sha256','one_attempt'}, 'AUTHORITY_SCHEMA')
    pins=runtime_pins()
    require(authority['schema']==SCHEMA and authority['operation']==OPERATION
            and authority['scope']==config['scope'] and authority['config_sha256']==config_sha
            and authority['one_attempt'] is True
            and all(authority.get(k)==v for k,v in pins.items()), 'AUTHORITY_BINDING')
    return config_sha,pins


class Guard:
    def __init__(self, out, limits):
        self.out=out;self.limits=limits;self.start=time.monotonic();self.last=None;self.maximum=0;self.failure_room_reserved=False

    def check(self, phase, additional=0):
        elapsed=time.monotonic()-self.start
        rss=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss*(1 if sys.platform=='darwin' else 1024)
        self.maximum=max(self.maximum,rss)
        self.last=dict(phase=phase,elapsed_seconds=elapsed,observed_process_peak_rss_bytes=rss,
                       maximum_observed_peak_rss_bytes=self.maximum,
                       observation='cooperative sample; process-lifetime peak reading, not preemption')
        require(elapsed<=self.limits['wall_seconds'],'WALL_LIMIT')
        require(rss<=self.limits['rss_bytes'],'RSS_LIMIT')
        require(shutil.disk_usage(self.out).free>=self.limits['disk_reserve_bytes'],'DISK_RESERVE')
        size=0
        for p in self.out.iterdir():
            st=p.lstat();require(stat.S_ISREG(st.st_mode),'UNEXPECTED_OUTPUT_TYPE');size+=st.st_size
        require(size+additional<=self.limits['output_bytes'],'OUTPUT_LIMIT')


def read_source(spec, guard, alias, *, collect=True):
    """Called only AFTER authorize. Authenticate exactly the bytes used."""
    path=Path(spec['path']);before=path.lstat()
    require(stat.S_ISREG(before.st_mode) and before.st_size==spec['bytes'],'SOURCE_TYPE_SIZE')
    h=hashlib.sha256();parts=[];size=0
    with path.open('rb') as f:
        owned=os.fstat(f.fileno())
        require((owned.st_dev,owned.st_ino)==(before.st_dev,before.st_ino),'SOURCE_IDENTITY')
        while True:
            guard.check('read:'+alias)
            chunk=f.read(512*1024)
            if not chunk:break
            size+=len(chunk);require(size<=spec['bytes'],'SOURCE_GREW');h.update(chunk)
            if collect:parts.append(chunk)
        after=os.fstat(f.fileno())
    final=path.lstat()
    identity=lambda s:(s.st_dev,s.st_ino,s.st_size,s.st_mtime_ns,s.st_ctime_ns)
    require(identity(before)==identity(after)==identity(final),'SOURCE_CHANGED')
    require(size==spec['bytes'] and h.hexdigest()==spec['sha256'],'SOURCE_HASH')
    raw=b''.join(parts) if collect else None
    guard.check('authenticated:'+alias)
    return raw


def compare_memberships(ledger, reference, config):
    """Compare original IDs AND null masks, never counts alone or residual values."""
    require(type(reference['own_attempts']) is int and type(reference['own_origins']) is int
            and reference['own_attempts']==len(ledger.rows)
            and reference['own_origins']==len(config['origins']), 'R46_ROSTER')
    rows=reference['rows'];require(type(rows) is list,'R46_SCOPES')
    expected={(c['session'],s):c['cutoff_ns'] for c in config['cutoffs'] for s in config['symbols']}
    require(len(rows)==len(expected),'R46_SCOPE_COUNT')
    lookup={r.identity:r.original_attempt_id for r in ledger.rows}
    require(len(lookup)==len(ledger.rows) and len(set(lookup.values()))==len(lookup),'ID_BIJECTION')
    seen=set();out=[]
    for row in rows:
        key=(row['session'],row['symbol']);require(key in expected and key not in seen,'R46_SCOPE_ID')
        seen.add(key);require(row['cutoff_ns']==expected[key] and type(row['cutoff_ns']) is int,'R46_CUTOFF')
        ids=row['attempt_identities'];values=row['residuals_exact']
        require(type(ids) is list and type(values) is list and len(ids)==len(values)
                and all(type(i) is str and len(i)==64 for i in ids)
                and all(v is None or type(v) is str for v in values)
                and len(set(ids))==len(ids),'R46_ID_MASK')
        old_missing=[i for i,v in zip(ids,values) if v is None]
        require(all(type(row[k]) is int for k in ('attempted','observed','missing'))
                and row['attempted']==len(ids) and row['missing']==len(old_missing)
                and row['observed']==len(ids)-len(old_missing) and row['unit']==component.UNIT,'R46_COUNTS_UNIT')
        at=ledger.asof(row['symbol'],row['cutoff_ns'])
        original=[lookup[i] for i in at['attempts']]
        missing=[lookup[i] for i,v in zip(at['attempts'],at['residuals']) if v is None]
        require(original==ids and missing==old_missing,'ORIGINAL_ID_NULL_MASK_PARITY')
        out.append(dict(session=key[0],symbol=key[1],cutoff_ns=row['cutoff_ns'],
                        reference_ids=list(at['attempts']),original_ids=original,missing_original_ids=missing,
                        attempted=at['attempted'],observed=at['observed'],missing=at['missing']))
    require(seen==set(expected),'SCOPE_COMPLETENESS')
    return out


def write_all(stream, payload, guard=None):
    """Raw writes may be short; only positive valid byte progress is accepted."""
    view=memoryview(payload);offset=0
    while offset<len(view):
        chunk=view[offset:offset+65536]
        if guard is not None:guard.check('write',additional=len(chunk)+RESERVE)
        written=stream.write(chunk)
        require(type(written) is int and 0<written<=len(chunk),'WRITE_PROGRESS')
        offset+=written


def publish(out, result, guard):
    """Exclusive hardlink is terminal authority; pending evidence is retained."""
    payload=encode(result)
    require(2*len(payload)+RESERVE<=guard.limits['output_bytes'],'OUTPUT_LIMIT')
    guard.check('serialize',additional=len(payload)+RESERVE)
    pending=out/'result.pending';marker=out/'result.json'
    with pending.open('x+b',buffering=0) as f:
        write_all(f,payload,guard)
        os.fsync(f.fileno())
        require(os.fstat(f.fileno()).st_size==len(payload),'PENDING_SIZE_MISMATCH')
        f.seek(0);observed=hashlib.sha256();size=0
        while True:
            guard.check('verify_staged',additional=RESERVE)
            chunk=f.read(65536)
            require(type(chunk) is bytes,'PENDING_READ_PROGRESS')
            if not chunk:break
            size+=len(chunk);require(size<=len(payload),'PENDING_SIZE_MISMATCH');observed.update(chunk)
        require(size==len(payload) and observed.hexdigest()==digest(payload),'PENDING_CONTENT_MISMATCH')
        owned=os.fstat(f.fileno())
        require(owned.st_size==len(payload),'PENDING_SIZE_MISMATCH')
    staged=pending.lstat()
    require(stat.S_ISREG(staged.st_mode) and staged.st_size==len(payload)
            and (staged.st_dev,staged.st_ino)==(owned.st_dev,owned.st_ino),
            'PENDING_IDENTITY_CHANGED')
    guard.check('precommit',additional=len(payload)+RESERVE)
    guard.failure_room_reserved=True
    try:
        os.link(pending,marker)
    except BaseException as exc:
        try:
            published=marker.lstat()
            committed=stat.S_ISREG(published.st_mode) and (published.st_dev,published.st_ino)==(owned.st_dev,owned.st_ino)
        except FileNotFoundError:committed=False
        except BaseException as unknown:
            if isinstance(exc,(KeyboardInterrupt,SystemExit)):
                exc._publication_uncertain=True
                raise exc from unknown
            raise PublicationUncertain('PUBLICATION_UNCERTAIN') from unknown
        if not committed:
            if marker.exists():
                if isinstance(exc,(KeyboardInterrupt,SystemExit)):
                    exc._publication_uncertain=True
                    raise
                raise PublicationUncertain('MARKER_IDENTITY_CONFLICT') from exc
            raise
        if isinstance(exc,(KeyboardInterrupt,SystemExit)):
            exc._publication_committed=True
            raise
    # No cleanup, sampling, fsync or failure-producing work follows commit.
    return result


def failure_receipt(out, exc, guard):
    """Best effort; never contradict a confirmed/possibly committed marker."""
    try:
        if getattr(exc,'_publication_committed',False):return
        status='PUBLICATION_UNCERTAIN_NO_AUTHORITATIVE_FAILURE' if (
            isinstance(exc,PublicationUncertain) or getattr(exc,'_publication_uncertain',False)) else 'FAILED_NO_COMPLETE'
        code=str(exc) if isinstance(exc,QualificationFailure) else type(exc).__name__
        # No source exception repr, actuals, residuals or prices in diagnostics.
        body=encode(dict(schema=SCHEMA,status=status,reason=code[:128],sample=guard.last,
                         no_retry=True,receipt_budget='precommit reserved headroom' if guard.failure_room_reserved else 'current footprint',
                         authority='result.json only; uncertainty is not authoritative failure'))
        require(len(body)<=8192,'FAILURE_RECEIPT_LIMIT')
        try:
            size=sum(p.lstat().st_size for p in out.iterdir())
        except OSError:
            # The final guard reserved this bounded receipt before linking. An
            # unreadable marker cannot be reclassified as a confirmed failure.
            require(guard.failure_room_reserved,'FAILURE_BUDGET_UNOBSERVABLE')
        else:
            require(size+len(body)<=guard.limits['output_bytes'],'FAILURE_OUTPUT_LIMIT')
        with (out/'failure.json').open('xb',buffering=0) as f:write_all(f,body)
    except BaseException:
        pass  # Best effort only; original error/cancellation must propagate.


def qualify(config, authority=None, *, expected_authority_sha256=None, enabled=False):
    require(enabled is True,'QUALIFIER_DISABLED')
    config,authority=deepcopy((config,authority))
    # No historical path operation (including hashing/stat) before this barrier.
    config_sha,pins=authorize(config,authority,expected_authority_sha256)
    out=Path(config['output_directory'])
    if config['scope']!='SYNTHETIC_FIXTURE_ONLY':
        allowed=Path(__file__).resolve().parents[4]/'local-artifacts'
        require(out.resolve().is_relative_to(allowed.resolve()),'PRIVATE_OUTPUT_REQUIRED')
    out.mkdir(exist_ok=False)
    guard=Guard(out,config['resources'])
    try:
        guard.check('authorized')
        raw={alias:read_source(spec,guard,alias) for alias,spec in config['sources'].items()}
        result=decode(raw['r46_result']);audit=decode(raw['r46_audit'])
        require(audit['status']=='INDEPENDENT_R46_OWN_OPEN_LEDGER_ASOF_AUDIT_PASS_NOT_RISK_ADMISSION'
                and audit['result_sha256']==digest(raw['r46_result']),'R46_AUDIT_BINDING')
        for alias in component.SOURCE_KEYS:
            spec=config['sources'][alias]
            require(audit['input_sha256'][spec['path']]==spec['sha256']
                    and result['inputs'][spec['path']]==spec['sha256'],'R46_INPUT_BINDING')
        guard.check('before_derivation')
        ledger=component.derive_reference_ledger(
            {k:raw[k] for k in component.SOURCE_KEYS},
            {k:config['sources'][k]['sha256'] for k in component.SOURCE_KEYS},
            origins=tuple(config['origins']),symbols=tuple(config['symbols']),
            full_policy_id=config['full_policy_id'],
            full_model_source_sha256=config['full_model_source_sha256'],enabled=True)
        guard.check('after_derivation')
        memberships=compare_memberships(ledger,result,config)
        guard.check('after_memberships')
        for alias,spec in config['sources'].items():read_source(spec,guard,alias,collect=False)
        require(runtime_pins()==pins and check_config(config)==config_sha,'CODE_CONFIG_CHANGED')
        data=dict(ledger=[asdict(r)for r in ledger.rows],memberships=memberships,
                  reference_policy_identity=ledger.policy_identity)
        guard.check('before_semantic_hash')
        semantic_sha=digest(encode(data))
        guard.check('after_semantic_hash')
        summary=dict(schema=SCHEMA,status='COMPLETE_SOURCE_QUALIFICATION_ONLY',
                     semantic_sha256=semantic_sha,
                     row_count=len(ledger.rows),scope_count=len(memberships),config_sha256=config_sha,
                     code_pins=pins,source_pins={k:v['sha256'] for k,v in config['sources'].items()},
                     resource_sample=guard.last,
                     resource_limitations='Sampled/cooperative; final link can finish later; no hard peak or completed-runtime guarantee.',
                     historical_publication_attested=False,operational_risk_admitted=False,
                     portfolio_replay=False,economic_metrics=False)
        private=dict(summary=summary,**data)
        guard.check('before_serialization')
        return publish(out,private,guard)
    except BaseException as exc:
        failure_receipt(out,exc,guard)
        raise


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config');parser.add_argument('--config-sha256')
    parser.add_argument('--authority');parser.add_argument('--authority-sha256')
    parser.add_argument('--enable',action='store_true');parser.add_argument('--check-config',action='store_true')
    args=parser.parse_args(argv)
    require(args.enable or args.check_config,'QUALIFIER_DISABLED')
    raw=Path(args.config).read_bytes()
    require(digest(raw)==args.config_sha256,'CONFIG_FILE_PIN')
    config=decode(raw)
    if args.check_config:
        check_config(config);return 0
    receipt_raw=Path(args.authority).read_bytes()
    require(digest(receipt_raw)==args.authority_sha256,'AUTHORITY_FILE_PIN')
    authority=decode(receipt_raw)
    # Receipt file bytes have already been authenticated; runtime API binds its
    # deterministic semantic encoding as well, without historical I/O.
    qualify(config,authority,expected_authority_sha256=digest(encode(authority)),enabled=True)
    return 0


if __name__=='__main__':
    try:raise SystemExit(main())
    except (QualificationFailure,ValueError,KeyError,TypeError,OSError) as exc:
        print(json.dumps(dict(status='NOT_EVALUABLE',reason=type(exc).__name__)))
        raise SystemExit(1)
