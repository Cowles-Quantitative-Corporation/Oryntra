"""Exact typed full-depth validation and per-symbol completed-event grouping."""
import numpy as np
import pyarrow as pa
import pyarrow.compute as pc
MAXPX=9223372036854775806
# Book bits; source/clock/group bits added by scanner.
MISSING=1;CROSSED=2;HOLE=4;UNORDERED=8;MISMATCH=16

def price_nanos(strings):
 absent=pc.or_(pc.equal(strings,''),pc.equal(strings,'9223372036854775807'))
 syntax=pc.match_substring_regex(strings,r'^[0-9]+(\.[0-9]{1,9})?$')
 if not pc.all(pc.or_(absent,syntax)).as_py():raise ValueError('invalid exact decimal price syntax')
 normal=pc.if_else(absent,pa.scalar(None,pa.string()),strings)
 dec=pc.cast(normal,pa.decimal128(20,9),safe=True)
 raw=np.frombuffer(dec.buffers()[1],dtype='<i8',count=2*len(dec),offset=16*dec.offset).reshape(-1,2)
 present=pc.is_valid(dec).to_numpy(zero_copy_only=False)
 if np.any(present & ((raw[:,1]!=0)|(raw[:,0]<0)|(raw[:,0]>MAXPX))):raise ValueError('nanodollar price outside exact source domain')
 return raw[:,0].copy(),present

def unsigned(strings,bits=32):
 if not pc.all(pc.match_substring_regex(strings,r'^[0-9]+$')).as_py():raise ValueError('unsigned source integer syntax')
 return pc.cast(strings,pa.uint8() if bits==8 else pa.uint32(),safe=True).to_numpy(zero_copy_only=False)

def book_quality(batch):
 n=batch.num_rows;mask=np.zeros(n,dtype=np.uint16);tops={};counts={}
 for side in ('bid','ask'):
  empty=np.zeros(n,dtype=bool);previous=np.zeros(n,dtype=np.int64);prev_present=np.zeros(n,dtype=bool);count=np.zeros(n,dtype=np.uint8)
  for level in range(10):
   p,present=price_nanos(batch.column(batch.schema.get_field_index(f'{side}_px_{level:02}')));size=unsigned(batch.column(batch.schema.get_field_index(f'{side}_sz_{level:02}')))
   # Count fields are source-schema validated although not mark valuation inputs.
   unsigned(batch.column(batch.schema.get_field_index(f'{side}_ct_{level:02}')))
   populated=present & (size>0)
   mask[(present!=(size>0))| (populated&(p<=0))]|=MISMATCH
   mask[empty&populated]|=HOLE
   if level:mask[prev_present&populated&((p>=previous) if side=='bid' else (p<=previous))]|=UNORDERED
   empty|=~populated;previous=p;prev_present=populated;count+=populated.astype(np.uint8)
   if level==0:tops[side]=(p,present,size);mask[~populated]|=MISSING
  counts[side]=count
 bid= tops['bid'];ask=tops['ask'];mask[bid[1]&ask[1]&(bid[0]>=ask[0])]|=CROSSED
 return mask,tops,counts

class Groups:
 """Every same-symbol raw row since preceding F_LAST belongs to one group.
 Raw rows may interleave symbols and span Arrow batches. No partial group emits.
 """
 def __init__(self,names):self.names=names;self.carry={i:dict(count=0,flags=0,reset=False,first=0,recv=0,event=0) for i in range(len(names))}
 def consume(self,code,flags,resets,index,recv,event):
  out=[]
  for s in range(len(self.names)):
   lane=np.flatnonzero(code==s);c=self.carry[s]
   if not len(lane):continue
   lf=flags[lane];lr=resets[lane];ends=np.flatnonzero((lf&128)!=0)
   if not len(ends):
    if c['count']==0:c.update(first=int(index[lane[0]]),recv=int(recv[lane[0]]),event=int(event[lane[0]]))
    c.update(count=c['count']+len(lane),flags=c['flags']|int(np.bitwise_or.reduce(lf)),reset=c['reset']|bool(np.any(lr)))
    if c['count']>10000:raise ValueError('uncompleted group row budget')
    continue
   starts=np.r_[0,ends[:-1]+1];allstarts=np.r_[starts,ends[-1]+1] if ends[-1]+1<len(lane) else starts
   ors=np.bitwise_or.reduceat(lf,allstarts)[:len(ends)];r=np.logical_or.reduceat(lr,allstarts)[:len(ends)];sizes=ends-starts+1
   firsts=index[lane[starts]].copy();fr=recv[lane[starts]].copy();fe=event[lane[starts]].copy()
   if c['count']:
    sizes[0]+=c['count'];ors[0]|=c['flags'];r[0]|=c['reset'];firsts[0]=c['first'];fr[0]=c['recv'];fe[0]=c['event']
   if np.any(sizes>10000):raise ValueError('completed group row budget')
   out.append((lane[ends],firsts,sizes,ors,r,fr,fe))
   tail=lane[ends[-1]+1:]
   self.carry[s]=dict(count=len(tail),flags=int(np.bitwise_or.reduce(flags[tail])) if len(tail) else 0,reset=bool(np.any(resets[tail])) if len(tail) else False,first=int(index[tail[0]]) if len(tail) else 0,recv=int(recv[tail[0]]) if len(tail) else 0,event=int(event[tail[0]]) if len(tail) else 0)
  if not out:return None
  joined=[np.concatenate([x[i] for x in out]) for i in range(7)];order=np.argsort(joined[0],kind='stable');return [x[order] for x in joined]
