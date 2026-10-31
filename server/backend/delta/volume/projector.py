"""Disabled bounded native-artifact→v33 projection. No I/O, fit, receiver or order path."""
from pathlib import Path
import sys,json,hashlib
from .features import Context,Scale,digest,snapshot_to_feature,integer,sha,require
RESULT_SHA='89fb96ed8f8dacd501cf34b4f784a9ed96a100b1b377b4bfd9ca9cd3414745d0'
PROFILE_SHA='2c4b1ccbbe796e60115122a90184faa47de39e8804290a6d02e51707251d51fe'
MAX_BYTES=2*1024*1024

def decode(b):
 require(type(b)is bytes and 0<len(b)<=MAX_BYTES,'ARTIFACT_BYTE_BOUND')
 def unique(items):
  d={}
  for k,v in items:
   require(k not in d,'DUPLICATE_JSON_KEY');d[k]=v
  return d
 def invalid(x):raise ValueError('NONFINITE_JSON')
 return json.loads(b,object_pairs_hook=unique,parse_constant=invalid)

def project(result_bytes,profile_bytes,*,symbol,decision_ns,enabled=False,synthetic_fixture=False):
 require(enabled is True,'DISABLED_PROJECTOR')
 require(type(synthetic_fixture)is bool,'EXPLICIT_FIXTURE_SCOPE')
 require(type(result_bytes)is bytes and type(profile_bytes)is bytes,'ARTIFACT_BYTES_REQUIRED')
 require(len(result_bytes)<=MAX_BYTES and len(profile_bytes)<=MAX_BYTES,'ARTIFACT_BYTE_BOUND')
 rh=hashlib.sha256(result_bytes).hexdigest();ph=hashlib.sha256(profile_bytes).hexdigest()
 if not synthetic_fixture:require(rh==RESULT_SHA and ph==PROFILE_SHA,'UNACCEPTED_ARTIFACT_BYTES')
 r=decode(result_bytes);p=decode(profile_bytes)
 require(type(r)is dict and type(p)is dict,'ARTIFACT_SCHEMA')
 if synthetic_fixture:require(r.get('schema')=='INVENTED_PROJECTOR_FIXTURE_V1' and p.get('schema')=='INVENTED_PROFILE_V1','FIXTURE_NAMESPACE_REQUIRED')
 else:require(r.get('schema')=='delta.source-volume.result.v31'and p.get('schema')=='delta.source-volume.profile.v31','RECORDED_SCHEMA')
 require(r.get('profile_sha256')==ph,'RESULT_PROFILE_BINDING')
 require(r.get('status')=='COMPLETE_WINDOW_ENGINEERING_WITH_INHERITED_ARCHIVE_EVIDENCE_REVIEW_REQUIRED','INCOMPLETE_RESULT')
 require(r.get('forecast_or_account_consumer')is False and r.get('source_fullsession_native_qualification')is False,'SCOPE_ESCALATION')
 require(p.get('active')is False and p.get('full_source_23460second_allowance_active')is False,'PROFILE_ACTIVATION')
 require(r.get('window_14S_finalF_checked')is True and r.get('raw_compressed_hash_before_after')is True,'COMPLETION_EVIDENCE')
 native=r['native'];require(native.get('finished')is True and native.get('failed')is False and native.get('failure')is None and type(native.get('owned_child_returncode'))is int and native['owned_child_returncode']==0 and native['last_attempt']['operation']=='F','NATIVE_NOT_FINISHED')
 mem=native['live_memory_supervision'];require(mem.get('watchdog_stopped')is True and mem.get('watchdog_alive')is False and mem.get('failure')is None,'MONITOR_NOT_FINISHED')
 raw=sha(p['input']['raw_sha256']);source=p['math_namespace'];session=p['input']['session'];mapping=p['input']['mapping']
 if synthetic_fixture:require(source=='synthetic-source:projector-fixture','FIXTURE_SOURCE_REQUIRED')
 else:require(source=='synthetic-source:recorded:'+raw,'RECORDED_SOURCE')
 require(r['actual_source']==p['input'] and r['reversible_math_namespace']==source,'SOURCE_PROFILE_BINDING')
 require(type(mapping)is dict and len(mapping)==7 and all(type(n)is str for n in mapping),'SEVEN_LANES_REQUIRED')
 for v in mapping.values():integer(v,1)
 require(len(set(mapping.values()))==7,'DUPLICATE_INSTRUMENT')
 clocks=p['snapshot_clocks'];require(type(clocks)is list and len(clocks)==2,'TWO_FIXED_CLOCKS')
 for v in clocks:integer(v,0,2**63-1)
 require(clocks[0]<clocks[1],'CLOCK_ORDER')
 scale=Scale(p['bucket_shares'],p['keep'],p['gap_ns'],30_000_000_000);scale.validate() # fixed pinned native LEGACY_BUCKET_MAX_AGE_NS
 require(symbol in mapping and type(decision_ns)is int and decision_ns in clocks,'EXACT_SNAPSHOT_SELECTION')
 snaps=r['coverage']['snapshots'];require(type(snaps)is list and len(snaps)==14,'EXACT_SNAPSHOT_POPULATION')
 observed=integer(r['coverage']['prefix_accounting']['rows']);selected=None;seen=set()
 # Validate every contained snapshot, including unavailable diagnostics, before returning any feature.
 for s in snaps:
  require(type(s)is dict,'SNAPSHOT_SCHEMA');n=s.get('symbol');t=s.get('decision_ns');require(n in mapping and type(t)is int and t in clocks,'SNAPSHOT_ROSTER')
  key=(n,t);require(key not in seen,'DUPLICATE_SNAPSHOT');seen.add(key)
  require(s.get('source')==source and s.get('session')==session and s.get('instrument')==mapping[n],'SNAPSHOT_SOURCE_IDENTITY')
  ctx=Context(rh,digest(s),'INVENTED_FIXTURE'if synthetic_fixture else'RECORDED_NORMALIZED_OCCURRENCES',source,None if synthetic_fixture else raw,n,session,mapping[n],t,s['frontier'],scale,'SYNTHETIC_ONLY'if synthetic_fixture else'OPENING_MINUTE_ENGINEERING_ONLY')
  feature=snapshot_to_feature(s,ctx,decision_ns=t,observed_source_frontier=observed,enabled=True)
  if key==(symbol,decision_ns):selected=feature
 require(len(seen)==14 and selected is not None,'MISSING_SNAPSHOT')
 return selected
