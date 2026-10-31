"""Causal Alpha-feature increment with explicitly matured native-residual labels.

An origin c observes close c; enters close c+1; its H outcomes are returns
c+2..c+H+1 inclusive. Training uses only origin outcomes matured by c.
Equal-weight centering over the declared upstream universe is not neutrality
for a concentrated portfolio. Never re-center after selecting a subset.
"""
from pathlib import Path
import json
from hashlib import sha256
import numpy as np
import pandas as pd
from .config import fingerprint

def validate_prices(prices):
    if not isinstance(prices.index,pd.DatetimeIndex) or not prices.index.is_unique or not prices.index.is_monotonic_increasing:raise ValueError('Unique ascending source sessions required')
    if not prices.columns.is_unique or prices.empty:raise ValueError('Unique symbols required')
    v=prices.to_numpy(float)
    if not np.isfinite(v).all() or np.any(v<=0):raise ValueError('Complete positive prices required; no imputation')

class ForecastPanel:
    def __init__(self,prices,spy,config):
        validate_prices(prices)
        self.prices=prices;self.config=config;self.h=config['portfolio']['rebalance'];self.index=prices.index
        self.columns=prices.columns;self.returns=prices.pct_change(fill_method=None)
        self.native=self.returns.rolling(config['portfolio']['lookback'],min_periods=config['portfolio']['lookback']).mean()*126.0
        self.realized=prices.shift(-(self.h+1))/prices.shift(-1)-1
        self.delta=pd.DataFrame(0.,index=self.index,columns=self.columns)
        self.composite=pd.DataFrame(np.nan,index=self.index,columns=self.columns)
        self.diagnostics=pd.DataFrame(index=self.index)
        self.features={}
        self.rank_support_required=config['ranking']['enabled'] or config['errors']['policy']=='matched_as_issued'
        if self.rank_support_required:
            self._build()
        self.issued=self.native+self.delta*(252.0/self.h)
        self.error=(self.realized-self.issued*self.h/252.0)*(252.0/self.h)
        self.identity=fingerprint({'config':config,'clock':'cutoff_c_entry_c+1_exit_c+H+1','units':'linear_annual_equivalent','symbols':list(self.columns)})

    def _build(self):
        from backend.alpha_v1 import AlphaV1Config,_build_features,_PRIORS
        cfg=self.config;switches={k:v for k,v in cfg['ranking'].items() if k.startswith('include_')}
        a=AlphaV1Config(enabled=True,horizon_sessions=self.h,include_value=False,include_quality=False,include_sector_relative=False,**switches)
        self.features=_build_features(self.prices,self._spy_returns,a,sector_labels=None,value_scores=None,quality_scores=None)

    @property
    def _spy_returns(self):
        # Assigned by build_panel before _build. Property exists only to keep
        # the benchmark input explicit and auditable.
        return self.spy_returns

    def finish_features(self):
        from backend.alpha_v1 import _PRIORS
        cfg=self.config;weights=np.asarray([_PRIORS.get(n,0.05) for n in self.features],float)
        stack=np.stack([x.to_numpy(float) for x in self.features.values()]);valid=np.isfinite(stack)
        numerator=np.nansum(stack*weights[:,None,None],axis=0);denominator=np.sum(valid*weights[:,None,None],axis=0)
        comp=np.divide(numerator,denominator,out=np.full_like(numerator,np.nan),where=denominator>0)
        # Require all requested sleeves for every stock, never silently change
        # feature mixes on individual names.
        comp[~valid.all(axis=0)]=np.nan
        comp_mean=np.divide(np.nansum(comp,axis=1),np.isfinite(comp).sum(axis=1),out=np.full(len(comp),np.nan),where=np.isfinite(comp).sum(axis=1)>0)
        comp-=comp_mean[:,None]
        self.composite=pd.DataFrame(comp,index=self.index,columns=self.columns)
        y=self.realized.to_numpy(float).copy()
        if cfg['training']['target']=='native_residual':y-=self.native.to_numpy(float)*self.h/252.0
        y_mean=np.divide(np.nansum(y,axis=1),np.isfinite(y).sum(axis=1),out=np.full(len(y),np.nan),where=np.isfinite(y).sum(axis=1)>0)
        y-=y_mean[:,None]
        ok=np.isfinite(comp)&np.isfinite(y);count=ok.sum(axis=1)
        slopes=np.divide(np.nansum(np.where(ok,comp*y,0),axis=1),np.nansum(np.where(ok,comp*comp,0),axis=1)+cfg['training']['rank_ridge'])
        slopes[count<6]=np.nan
        # A label at origin j matures j+H+1; at c this shifted value is known.
        matured=pd.Series(slopes,index=self.index).shift(self.h+1)
        window=cfg['training']['rank_window'];minimum=cfg['training']['rank_min_days']
        raw=matured.rolling(window,min_periods=minimum).median()
        calibrated=raw.clip(lower=0) if cfg['ranking']['nonnegative_slope'] else raw
        delta=comp*calibrated.to_numpy()[:,None]*cfg['ranking']['strength']
        limit=cfg['ranking']['maximum_horizon_delta'];delta=np.clip(delta,-limit,limit)
        delta_mean=np.divide(np.nansum(delta,axis=1),np.isfinite(delta).sum(axis=1),out=np.full(len(delta),np.nan),where=np.isfinite(delta).sum(axis=1)>0)
        delta-=delta_mean[:,None]
        # Centering could enlarge a clipped value; one common shrink preserves
        # centering and the hard bound without reranking or subset changes.
        max_abs=np.max(np.where(np.isfinite(delta),abs(delta),0),axis=1);scale=np.minimum(1,np.divide(limit,max_abs,out=np.ones_like(max_abs),where=max_abs>0));delta*=scale[:,None]
        if not cfg['ranking']['enabled']:delta=np.where(np.isfinite(delta),0.,np.nan)
        self.delta=pd.DataFrame(delta,index=self.index,columns=self.columns)
        self.issued=self.native+self.delta*252/self.h
        self.error=(self.realized-self.issued*self.h/252)*(252/self.h)
        self.diagnostics=pd.DataFrame({'raw_signed_slope':raw,'applied_slope':calibrated,'matured_calibration_days':matured.rolling(window).count(),'upstream_assets':len(self.columns)},index=self.index)
        if cfg['ranking']['model']=='native_shrinkage':
            from gamma_v1.shrinkage import native_shrinkage
            self.delta,self.composite,self.diagnostics=native_shrinkage(self.native,self.realized,cfg)
            self.issued=self.native+self.delta*252/self.h
            self.error=(self.realized-self.issued*self.h/252)*(252/self.h)

    def at(self,cutoff,symbols):
        cutoff=pd.Timestamp(cutoff);i=self.index.get_loc(cutoff)
        if self.config['ranking']['enabled'] and not np.isfinite(self.delta.loc[cutoff,symbols]).all():raise ValueError('Insufficient matured ranking calibration')
        result={'mu_annual':self.issued.loc[cutoff,symbols].to_numpy(float),'native_mu_annual':self.native.loc[cutoff,symbols].to_numpy(float),'delta_horizon':self.delta.loc[cutoff,symbols].to_numpy(float),'upstream_universe':list(self.columns),'information_cutoff':str(cutoff),'entry_close':str(self.index[i+1]) if i+1<len(self.index) else None,'exit_close':str(self.index[i+self.h+1]) if i+self.h+1<len(self.index) else None,'training_last_matured_origin':str(self.index[i-self.h-1]) if i>=self.h+1 else None,'units':'linear_annual_equivalent'}
        if not np.isfinite(result['mu_annual']).all():raise ValueError('Insufficient native forecast history')
        return result

    def errors_at(self,cutoff,symbols):
        i=self.index.get_loc(pd.Timestamp(cutoff));cfg=self.config['errors']
        # All sampled origins share the evaluation-origin phase. No overlapping
        # outcome intervals; the just-prior origin still has one late outcome.
        origins=list(range(i-self.h, -1,-self.h))[::-1]
        origins=[j for j in origins if j+self.h+1<=i and np.isfinite(self.error.iloc[j][symbols]).all()]
        origins=origins[-cfg['window_origins']:]
        if len(origins)<cfg['minimum_origins']:raise ValueError(f'Insufficient matured as-issued errors: {len(origins)} < {cfg["minimum_origins"]}')
        return {'samples':self.error.iloc[origins][symbols].to_numpy(float),'information_cutoffs':tuple(str(self.index[j]) for j in origins),'realized_at':tuple(str(self.index[j+self.h+1]) for j in origins),'origins':origins,'as_issued':True,'overlap_sessions':0,'units':'linear_annual_equivalent'}

    def save(self,directory):
        p=Path(directory);p.mkdir(parents=True,exist_ok=True)
        for name in ['native','delta','issued','realized','error','composite','diagnostics']:getattr(self,name).to_csv(p/(name+'.csv.gz'),compression='gzip')
        (p/'identity.json').write_text(json.dumps({'fingerprint':self.identity,'config':self.config,'clock':'c observes close; entry c+1; last realized return c+H+1','data_usage':'realized/error panels are retrospective storage; admission strictly checks maturity','not_original_gamma_ledger':True},indent=2))

def build_panel(prices,spy,config,native_overrides=None):
    # Keep feature construction causal and vectorized; storing future labels
    # for auditing never permits them into at()/errors_at() before maturity.
    panel=ForecastPanel.__new__(ForecastPanel)
    benchmark=spy.reindex(prices.index)
    if not np.isfinite(benchmark.to_numpy(float)).all() or (benchmark<=0).any():raise ValueError('Complete positive benchmark on identical sessions required')
    panel.spy_returns=benchmark.pct_change(fill_method=None)
    ForecastPanel.__init__(panel,prices,spy,config)
    for cutoff,mu in (native_overrides or {}).items():panel.native.loc[pd.Timestamp(cutoff)]=mu
    if panel.rank_support_required:panel.finish_features()
    else:
        panel.issued=panel.native.copy()
        panel.error=(panel.realized-panel.issued*panel.h/252)*(252/panel.h)
    panel.identity=fingerprint({'config':config,'clock':'cutoff_c_entry_c+1_exit_c+H+1','units':'linear_annual_equivalent','producer_source_sha256':sha256(Path(__file__).read_bytes()).hexdigest(),'prices_sha256':sha256(pd.util.hash_pandas_object(prices,index=True).values.tobytes()).hexdigest(),'benchmark_sha256':sha256(pd.util.hash_pandas_object(benchmark,index=True).values.tobytes()).hexdigest(),'native_issued_sha256':sha256(pd.util.hash_pandas_object(panel.native,index=True).values.tobytes()).hexdigest(),'symbols':list(prices.columns)})
    return panel
