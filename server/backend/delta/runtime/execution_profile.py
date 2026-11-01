"""Fixed inactive engineering constants matching the published native worker."""
from ..volume.profile import LIFETIME_CAPS
PROFILE_SHA='2c4b1ccbbe796e60115122a90184faa47de39e8804290a6d02e51707251d51fe'
LANES=tuple((i,n,v)for i,(n,v)in enumerate(zip(('AAPL','AMZN','GOOGL','JPM','MSFT','WMT','XOM'),(38,853,7152,9155,10888,17448,17709))))
PROFILE={'math_namespace':'synthetic-source:recorded:3f6c7dbb85f25964842f8957d675ce35c59c0375c19b845df632b92b4e9beecb','input':{'session':'2026-04-01','raw_rows_exact':14868179},'group_cap':13945264,'operation_cap':13945280,'input_wire_cap':3134071984,'output_wire_cap':4183809614,'slice_open_ns':1775050200000000000,'slice_close_ns':1775050260000000000,'snapshot_clocks':(1775050230000000000,1775050260000000000),'per_lane_group_caps':{n:LIFETIME_CAPS[v]for _,n,v in LANES},'limits':{'wall_seconds_proposed':600}}
