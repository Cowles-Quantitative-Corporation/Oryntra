"""Lossless metadata-only replay audit shards; numerical state and core code unchanged."""
from pathlib import Path
import re,json
from backend.qoptima.replay_checkpoint import ReplayCheckpoint,_atomic_document,_read_document,_verify_document,content_digest
SCHEMA='matched_replay_audit_ref_v1';REF='_matched_audit_reference'
FIELDS=('covariance_provenance','resilience_calibration','replay','canonical_qoptima_replay','gamma_risk205','forecast_contract')

def _scalar_summary(value):
 if not isinstance(value,dict):return value if value is None else {'stored_in_audit_shard':True}
 result={k:v for k,v in value.items() if v is None or isinstance(v,(str,bool,int,float))}
 for key in ('qscan','qrm','final_qrm','component_risk_diagnostics'):
  sub=value.get(key)
  if not isinstance(sub,dict):continue
  summary={k:v for k,v in sub.items() if v is None or isinstance(v,(str,bool,int,float))}
  for k in ('selected_symbols','selected_indices','structural_gates'):
   if k in sub:summary[k]=sub[k]
  result[key]=summary
 return result

class AuditStore:
 def __init__(self,root):self.root=Path(root);self.directory=self.root/'decision_audit';self.directory.mkdir(parents=True,exist_ok=True);self.verified=set();self.references={}
 def path(self,ref):
  if not isinstance(ref,dict) or ref.get('schema')!=SCHEMA or not re.fullmatch('[0-9a-f]{64}',ref.get('sha256','')):raise ValueError('Invalid replay audit reference')
  expected='decision_audit/'+ref['sha256']+'.json.gz'
  if ref.get('relative_path')!=expected:raise ValueError('Noncanonical replay audit path')
  return self.root/expected
 def verify(self,ref):
  p=self.path(ref)
  if ref['sha256'] not in self.verified:_verify_document(p,ref['sha256']);self.verified.add(ref['sha256'])
  self.references[ref['sha256']]=ref
 def publish(self,payload):
  digest=content_digest(payload);ref={'schema':SCHEMA,'kind':payload['kind'],'sha256':digest,'relative_path':'decision_audit/'+digest+'.json.gz'};p=self.path(ref)
  if not p.exists():_atomic_document(p,payload)
  self.verify(ref);return ref
 def compact_event(self,event):
  if REF in event:self.verify(event[REF]);return
  original=dict(event);ref=self.publish({'kind':'rejection_event','value':original})
  summary=_scalar_summary(original);event.clear();event.update(summary);event[REF]=ref
 def compact_row(self,row):
  if REF in row:self.verify(row[REF]);return
  heavy={key:row[key] for key in FIELDS if key in row};ref=self.publish({'kind':'rebalance_fields','fields':heavy})
  for key in heavy:row[key]=_scalar_summary(row[key])
  row[REF]=ref
 def compact_state(self,state):
  for event in state['rejection_events']:self.compact_event(event)
  for row in state['rebalances']:self.compact_row(row)
  self.write_manifest()
 def hydrate(self,value):
  if isinstance(value,list):return [self.hydrate(x) for x in value]
  if isinstance(value,tuple):return tuple(self.hydrate(x) for x in value)
  if not isinstance(value,dict):return value
  if REF in value:
   ref=value[REF];self.verify(ref);payload=_read_document(self.path(ref))
   if payload['kind']=='rejection_event':return self.hydrate(payload['value'])
   if payload['kind']!='rebalance_fields':raise ValueError('Unknown audit payload kind')
   value={**{k:v for k,v in value.items() if k!=REF},**payload['fields']}
  return {k:self.hydrate(v) for k,v in value.items()}
 def write_manifest(self):
  value={'schema':SCHEMA,'metadata_only_fields':list(FIELDS),'references':list(self.references.values()),'numerical_state_policy':'No returns, targets, actual/decision-known weights, risk persistence, costs, dates, solver inputs or QRS summary scalars changed','core_source_changed':False,'reconstruction':'AuditStore(root).hydrate(record) restores every original field and nested rejection event'}
  temp=self.root/'audit_storage_manifest.tmp';temp.write_text(json.dumps(value,indent=2));temp.replace(self.root/'audit_storage_manifest.json')

def install(root):
 store=AuditStore(root);original_save=ReplayCheckpoint.save;original_load=ReplayCheckpoint.load
 def save(self,*,next_anchor_index,anchors,state,complete=False):
  store.compact_state(state)
  return original_save(self,next_anchor_index=next_anchor_index,anchors=anchors,state=state,complete=complete)
 def load(self,*,anchors):
  payload=original_load(self,anchors=anchors)
  if payload is not None:store.compact_state(payload['state'])
  return payload
 ReplayCheckpoint.save=save;ReplayCheckpoint.load=load
 def uninstall():
  if ReplayCheckpoint.save is save:ReplayCheckpoint.save=original_save
  if ReplayCheckpoint.load is load:ReplayCheckpoint.load=original_load
 store.uninstall=uninstall
 return store
