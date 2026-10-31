"""Final supported forecast publication: identity calibration is exact identity."""
import numpy as np

def supported_tail_publication(var,es,calibration=1.0):
    if not np.isfinite(calibration) or calibration!=1.0:raise ValueError('Supported head requires identity calibration')
    v=np.asarray(var,dtype=float);e=np.asarray(es,dtype=float)
    if not np.isfinite(v).all() or not np.isfinite(e).all() or np.any(v<=-1) or np.any(e < -1) or np.any(e>=v) or np.any(e>=0):
        raise ValueError('Published supported tail violates finite simple-return/FZ0 domain')
    # Do not compute v - (v - e): cancellation can destroy a valid small ES.
    return float(e) if e.ndim==0 else e.copy()
