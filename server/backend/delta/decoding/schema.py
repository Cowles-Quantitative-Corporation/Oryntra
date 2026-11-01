"""Fixed accepted April MBP10 in-memory schema; no CSV/Zstandard reader."""
import pyarrow as pa
import pyarrow.compute as pc
EXPECTED_COLUMNS=['ts_recv','ts_event','rtype','publisher_id','instrument_id','action','side','depth','price','size','flags','ts_in_delta','sequence']+[f'{side}_{field}_{level:02}'for level in range(10)for field in('px','sz','ct')for side in('bid','ask')]+['symbol']
def times(a):
 if not pc.all(pc.match_substring_regex(a,r'^2026-04-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}\.[0-9]{9}Z$')).as_py():raise ValueError('dated UTC ns timestamp syntax')
 return pc.cast(pc.cast(a,pa.timestamp('ns',tz='UTC')),pa.int64(),safe=True).to_numpy(zero_copy_only=False)
