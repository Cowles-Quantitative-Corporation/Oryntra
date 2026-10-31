import copy
import numpy as np
import pandas as pd
from qpo_gamma.config import defaults
from gamma_v1.shrinkage import native_shrinkage

def test_zero_strength_identity_bounds_and_future_mutation():
    rng=np.random.default_rng(73);idx=pd.bdate_range('2010-01-01',periods=800)
    native=pd.DataFrame(rng.normal(.1,.4,(800,8)),index=idx);truth=pd.DataFrame(rng.normal(.01,.1,(800,8)),index=idx);cfg=defaults();cfg['ranking'].update(enabled=True,model='native_shrinkage')
    d,comp,diag=native_shrinkage(native,truth,cfg)
    assert np.nanmax(abs(d.to_numpy()))<=.05+1e-15
    assert np.nanmax(abs(d.sum(axis=1)))<1e-15
    mutated=truth.copy();mutated.iloc[500:]*=100
    d2,_,_=native_shrinkage(native,mutated,cfg)
    pd.testing.assert_frame_equal(d.iloc[:522],d2.iloc[:522])
    cfg['ranking']['strength']=0;z,_,_=native_shrinkage(native,truth,cfg)
    assert np.nanmax(abs(z.to_numpy()))==0
