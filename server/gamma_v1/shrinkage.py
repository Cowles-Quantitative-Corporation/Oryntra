"""Causal native-mean shrinkage as a simple residual forecast model."""
import numpy as np
import pandas as pd

def native_shrinkage(native_annual,realized,config):
    h=config['portfolio']['rebalance'];native=native_annual*h/252
    nc=native.sub(native.mean(axis=1),axis=0);yc=realized.sub(realized.mean(axis=1),axis=0);good=nc.notna()&yc.notna()
    numerator=(nc.where(good)*yc.where(good)).sum(axis=1);denominator=nc.where(good).pow(2).sum(axis=1)+config['training']['rank_ridge']
    slope=(numerator/denominator).where(good.sum(axis=1)>=6)
    matured=slope.shift(h+1);raw=matured.rolling(config['training']['rank_window'],min_periods=config['training']['rank_min_days']).median();coefficient=raw.clip(0,1)
    d=nc.mul(config['ranking']['strength']*(coefficient-1),axis=0)
    limit=config['ranking']['maximum_horizon_delta'];d=d.clip(-limit,limit);d=d.sub(d.mean(axis=1),axis=0);maximum=d.abs().max(axis=1)
    scale=pd.Series(np.minimum(1,np.divide(limit,maximum.to_numpy(),out=np.ones(len(maximum)),where=maximum.to_numpy()>0)),index=d.index);d=d.mul(scale,axis=0)
    if not config['ranking']['enabled']:d=d.where(d.isna(),0.)
    diagnostics=pd.DataFrame({'raw_signed_slope':raw-1,'applied_slope':coefficient-1,'matured_calibration_days':matured.rolling(config['training']['rank_window']).count(),'upstream_assets':len(native.columns),'model':'native_mean_shrinkage','coefficient':coefficient},index=d.index)
    return d,nc,diagnostics
