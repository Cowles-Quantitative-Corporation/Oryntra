"""Typed completed decoder-group→existing volume group; no sign imputation."""
from .types import Group as DecodedGroup,Trade as DecodedTrade,CompactBook
from ..volume.reference import Group,Trade

def project(g,*,enabled=False):
 if enabled is not True:raise ValueError('DISABLED_GROUP_ADAPTER')
 if type(g)is not DecodedGroup or type(g.book)is not CompactBook or type(g.trades)is not tuple:raise ValueError('TYPED_DECODER_GROUP_REQUIRED')
 b=g.book
 if not b.source.startswith('synthetic-source:'):raise ValueError('EXPLICIT_ENGINEERING_NAMESPACE')
 if any(type(v)is not int for v in(g.count,g.first_index,b.source_index))or not 0<g.first_index<=b.source_index or not 0<g.count<=32 or(g.count==1)!=(g.first_index==b.source_index)or g.count>b.source_index-g.first_index+1 or len(g.trades)>g.count:raise ValueError('ACTUAL_GROUP_COUNT_DOMAIN')
 if any(type(t)is not DecodedTrade for t in g.trades):raise ValueError('TYPED_T_PRINT_REQUIRED')
 return Group(b.source,b.symbol,b.session,b.feed_instrument_id,g.first_index,b.source_index,g.count,g.first_recv,b.available_ns,b.event_ns,g.flags,b.valid,b.reset,False,tuple(Trade(t.index,t.recv,t.price,t.shares,t.side,'T')for t in g.trades))
