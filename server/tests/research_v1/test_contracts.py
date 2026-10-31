import numpy as np
import pandas as pd
import pytest
from gamma_v1.contracts import *

def fixture(n=60):
    i=pd.bdate_range('2020-01-01',periods=n)
    return pd.DataFrame({'A':np.arange(n)/1000,'B':np.arange(n)/2000},index=i)

def test_deployment_target_exact_dates_and_legacy_difference():
    r=fixture();y,m=market_target(r,TargetContract(3,1));old,om=legacy_target(r,3)
    assert y.iloc[4]==pytest.approx(np.prod(1+r.mean(axis=1).iloc[6:9])-1)
    assert m.iloc[4]==r.index[8]
    assert old.iloc[4]==pytest.approx(np.prod(1+r.mean(axis=1).iloc[4:7])-1)
    assert om.iloc[4]==r.index[6]
    assert y.iloc[-4:].isna().all()

def test_maturity_boundary_and_future_mutation():
    r=fixture();y,m=market_target(r);cut=r.index[40];admit=admitted_labels(y,m,cut)
    assert admit.last_valid_index()==r.index[18]
    changed=r.copy();changed.loc[changed.index>cut]*=20
    y2,m2=market_target(changed)
    pd.testing.assert_series_equal(admit,admitted_labels(y2,m2,cut))
    yp,mp=market_target(r.loc[:cut])
    pd.testing.assert_series_equal(admit,admitted_labels(yp,mp,cut))

def test_no_missing_zero_fill():
    r=fixture();r.iloc[2,0]=np.nan
    with pytest.raises(ValueError,match='no imputation'):market_target(r)

def test_fz0_domain_and_support_diagnostic():
    m=tail_metrics([-.2,.1],[-.1,-.1],[-1.1,-.3]);assert m['es_support_violations']==1
    with pytest.raises(ValueError):fz0_numpy([0],[-.1],[0])

def test_admitted_maturity_identity_prefix_extension_invariance():
    r=fixture();cut=r.index[40]
    y,m=market_target(r);yp,mp=market_target(r.loc[:cut])
    a=admitted_labels(y,m,cut);b=admitted_labels(yp,mp,cut)
    pd.testing.assert_series_equal(admitted_maturities(a,m),admitted_maturities(b,mp))
    assert admitted_maturities(a,m).iloc[-22:].isna().all()
