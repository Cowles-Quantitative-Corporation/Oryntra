"""Explicit close-to-close target clocks, support constraints and tail diagnostics."""
from dataclasses import dataclass, asdict
import numpy as np
import pandas as pd

@dataclass(frozen=True)
class TargetContract:
    horizon: int = 21
    entry_lag: int = 1
    name: str = 'deployment_close_c_plus_1'
    def __post_init__(self):
        if self.horizon < 1 or self.entry_lag < 0:
            raise ValueError('Positive horizon and nonnegative entry lag required')

def market_target(returns, contract=TargetContract()):
    """Features observe close c; entry at c+L; returns c+L+1 .. c+L+H."""
    if returns.empty or not returns.index.is_unique or not returns.index.is_monotonic_increasing:
        raise ValueError('Unique ascending nonempty sessions required')
    if not np.isfinite(returns.to_numpy()).all() or (returns.to_numpy() < -1).any():
        raise ValueError('Complete real returns >= -1 required; no imputation')
    market=returns.mean(axis=1)
    h,lag=contract.horizon,contract.entry_lag
    target=(1+market).rolling(h,min_periods=h).apply(np.prod,raw=True).shift(-(h+lag))-1
    idx=returns.index
    maturity=pd.Series([idx[i+h+lag] if i+h+lag<len(idx) else pd.NaT for i in range(len(idx))],index=idx)
    return target,maturity

def legacy_target(returns,horizon=21):
    """Frozen original target, including the already-known return at c."""
    market=returns.mean(axis=1)
    target=(1+market).rolling(horizon,min_periods=horizon).apply(np.prod,raw=True).shift(-(horizon-1))-1
    idx=returns.index
    maturity=pd.Series([idx[i+horizon-1] if i+horizon-1<len(idx) else pd.NaT for i in range(len(idx))],index=idx)
    return target,maturity

def admitted_labels(target,maturity,cutoff):
    cutoff=pd.Timestamp(cutoff)
    return target.loc[:cutoff].where(maturity.loc[:cutoff].notna() & (maturity.loc[:cutoff]<=cutoff))

def fz0_numpy(y,v,e,alpha=.05):
    y,v,e=np.broadcast_arrays(y,v,e)
    if not np.isfinite(y).all() or not np.isfinite(v).all() or not np.isfinite(e).all() or np.any(e>=0) or np.any(e>v):
        raise ValueError('FZ0 requires finite e <= v and e < 0')
    return -((y<=v)*(v-y))/(alpha*e)+v/e+np.log(-e)-1

def tail_metrics(y,v,e,alpha=.05):
    y,v,e=np.broadcast_arrays(np.asarray(y,float),np.asarray(v,float),np.asarray(e,float))
    hit=y<=v
    return {'observations':int(y.size),'fz0_mean':float(fz0_numpy(y,v,e,alpha).mean()),
            'pinball_mean':float(np.mean((alpha-(y<v))*(y-v))),
            'var_coverage':float(hit.mean()),'coverage_error':float(hit.mean()-alpha),
            'es_identification_mean':float(np.mean(e-v+(v-y)*hit/alpha)),
            'var_support_violations':int(np.sum(v < -1)),
            'es_support_violations':int(np.sum(e < -1)),
            'var_min':float(v.min()),'var_max':float(v.max()),'es_min':float(e.min()),'es_max':float(e.max())}


def admitted_maturities(labels,maturities):
    """Unknown labels carry no future-calendar identity, even if source extends."""
    return maturities.reindex(labels.index).where(labels.notna())
