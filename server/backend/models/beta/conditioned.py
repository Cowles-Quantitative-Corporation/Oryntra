from __future__ import annotations

from typing import Dict, Optional

import numpy as np
import pandas as pd

from .labels import REGIME_NAMES
from .config import BETA_V1_CONFIG

RETURN_CONVENTIONS = {
    "forward_from_open",
    "open_to_close_same_session",
    "open_to_open_next_session",
    "close_to_close_end_labeled",
}


def strategy_return_alignment_contract(convention: str) -> dict:
    cfg = dict(BETA_V1_CONFIG.get("strategy_return_alignment") or {})
    required = str(cfg.get("return_convention") or "open_to_close_same_session")
    causal = bool(convention == required and cfg.get("causal", False))
    return {
        "declared": causal,
        "causal": causal,
        "return_convention": convention,
        "required_return_convention": required,
        "beta_decision_time": cfg.get("beta_decision_time", "NYSE regular-session open"),
        "strategy_return_definition": cfg.get("strategy_return_definition"),
        "strategy_execution_timing": cfg.get("strategy_execution_timing"),
        "research_backtest_without_consensus_allowed": bool(cfg.get("research_backtest_without_consensus_allowed", True)),
    }


def align_returns_to_beta_decision(returns: pd.DataFrame | pd.Series, convention: str):
    """Align returns to a Beta state known at the session open.

    `close_to_close_end_labeled`: a return indexed by its ending close is shifted
    backward one session so the t->t+1 close return is associated with Beta at open t.
    Forward/open-labeled conventions are already associated with the decision session.
    """
    if convention not in RETURN_CONVENTIONS:
        raise ValueError(f"Unknown return convention {convention!r}; choose one of {sorted(RETURN_CONVENTIONS)}")
    x = returns.sort_index().copy()
    if convention == "close_to_close_end_labeled":
        x = x.shift(-1)
    return x


def _max_drawdown_full_timeline(returns: pd.Series) -> float:
    r = returns.fillna(0.0).astype(float).clip(lower=-0.999999)
    if r.empty:
        return np.nan
    wealth = (1.0 + r).cumprod()
    peak = wealth.cummax()
    return float((wealth / peak - 1.0).min())


def _geometric_annualized(returns: np.ndarray, periods_per_year: float = 252.0) -> float:
    r=np.asarray(returns,dtype=float); r=r[np.isfinite(r)]
    if len(r)==0 or np.any(r<=-1.0): return np.nan
    total=float(np.prod(1.0+r)); return total**(periods_per_year/len(r))-1.0


def _newey_west_mean_stats(x: np.ndarray, max_lag: Optional[int]=None) -> dict:
    x=np.asarray(x,dtype=float); x=x[np.isfinite(x)]; n=len(x)
    if n<3: return {"mean":np.nan if n==0 else float(np.mean(x)),"se":np.nan,"t":np.nan,"ci95_low":np.nan,"ci95_high":np.nan,"lag":0}
    mean=float(np.mean(x)); u=x-mean
    if max_lag is None: max_lag=int(np.floor(4.0*(n/100.0)**(2.0/9.0)))
    L=max(0,min(int(max_lag),n-1)); lrv=float(np.dot(u,u)/n)
    for lag in range(1,L+1):
        autocov=float(np.dot(u[lag:],u[:-lag])/n); lrv+=2.0*(1.0-lag/(L+1.0))*autocov
    se=float(np.sqrt(max(lrv/n,0.0))); t=mean/se if se>0 else np.nan
    return {"mean":mean,"se":se,"t":float(t) if np.isfinite(t) else np.nan,"ci95_low":mean-1.96*se if np.isfinite(se) else np.nan,"ci95_high":mean+1.96*se if np.isfinite(se) else np.nan,"lag":L}


def _moving_block_bootstrap_ann_return(returns: np.ndarray, *, reps=500, block_length=None, seed=17) -> dict:
    r=np.asarray(returns,dtype=float); r=r[np.isfinite(r)]; n=len(r)
    if n<20: return {"low":np.nan,"high":np.nan,"reps":0,"block_length":0}
    b=int(block_length or max(5,min(n,round(np.sqrt(n))))); rng=np.random.default_rng(seed); starts=np.arange(0,max(1,n-b+1)); stats=[]
    for _ in range(int(reps)):
        sample=np.concatenate([r[int(rng.choice(starts)):][:b] for __ in range(int(np.ceil(n/b)))])[:n]
        stat=_geometric_annualized(sample)
        if np.isfinite(stat): stats.append(stat)
    if not stats: return {"low":np.nan,"high":np.nan,"reps":0,"block_length":b}
    low,high=np.quantile(stats,[.025,.975]); return {"low":float(low),"high":float(high),"reps":len(stats),"block_length":b}


def _effective_sample_size(weights: np.ndarray) -> float:
    w=np.asarray(weights,dtype=float); w=w[np.isfinite(w)&(w>0)]
    if not len(w): return 0.0
    d=float(np.sum(w**2)); return float((w.sum()**2)/d) if d>0 else 0.0


def regime_conditioned_research(
    strategy_returns: pd.DataFrame,
    beta_states: pd.DataFrame,
    *,
    return_convention: str,
    risk_free_daily: Optional[pd.Series]=None,
    turnover: Optional[pd.DataFrame]=None,
    cost_bps_per_turnover: float=0.0,
    bootstrap_reps: int=500,
    seed: int=17,
) -> Dict[str, Dict[str, dict]]:
    if strategy_returns.empty or beta_states.empty: return {}
    aligned=align_returns_to_beta_decision(strategy_returns.astype(float),return_convention)
    idx=aligned.index.intersection(beta_states.index)
    if not len(idx): return {}
    gross=aligned.loc[idx].copy(); states=beta_states.loc[idx].copy()
    rf=(align_returns_to_beta_decision(risk_free_daily.astype(float),return_convention).reindex(idx).fillna(0.0) if risk_free_daily is not None else pd.Series(0.0,index=idx))
    if turnover is not None:
        t=align_returns_to_beta_decision(turnover.astype(float),return_convention).reindex(idx).reindex(columns=gross.columns).fillna(0.0)
        net=gross-t*(float(cost_bps_per_turnover)/10000.0)
    else:
        t=pd.DataFrame(0.0,index=idx,columns=gross.columns); net=gross.copy()
    pcols=[f"p_{r}" for r in REGIME_NAMES]
    if any(c not in states for c in pcols): raise ValueError("beta_states missing regime probabilities")
    if "dominant_regime" not in states: states["dominant_regime"]=[REGIME_NAMES[i] for i in states[pcols].to_numpy().argmax(axis=1)]
    out={}
    for strategy in net.columns:
        out[strategy]={}; sr=net[strategy].replace([np.inf,-np.inf],np.nan); ex=sr-rf; gross_sr=gross[strategy]
        for regime in REGIME_NAMES:
            p=states[f"p_{regime}"].clip(0,1); hard=(states["dominant_regime"]==regime)&sr.notna()
            hard_r=sr.loc[hard].to_numpy(float); hard_ex=ex.loc[hard].to_numpy(float); hac=_newey_west_mean_stats(hard_ex)
            # Chronology-aware sleeve: non-regime dates remain in their actual place with zero exposure.
            hard_sleeve=sr.where(hard,0.0)
            soft_sleeve=(p*sr.fillna(0.0)).where(sr.notna(),0.0)
            valid=sr.notna()&p.notna(); w=p.loc[valid].to_numpy(float); x=ex.loc[valid].to_numpy(float)
            wm=float(np.sum(w*x)/np.sum(w)) if np.sum(w)>0 else np.nan
            wv=float(np.sum(w*(x-wm)**2)/np.sum(w)) if np.sum(w)>0 and np.isfinite(wm) else np.nan
            out[strategy][regime]={
                "hard":{"observations":int(hard.sum()),"annualized_return_on_active_days":_geometric_annualized(hard_r),"chronological_sleeve_max_drawdown":_max_drawdown_full_timeline(hard_sleeve),"chronological_sleeve_total_return":float((1+hard_sleeve.fillna(0)).prod()-1),"hit_rate":float(np.mean(hard_r>0)) if len(hard_r) else np.nan,"hac_t":hac["t"],"hac_daily_mean_ci95":[hac["ci95_low"],hac["ci95_high"]],"bootstrap_annualized_return_ci95":_moving_block_bootstrap_ann_return(hard_r,reps=bootstrap_reps,seed=seed)},
                "probability_weighted":{"probability_mass":float(np.nansum(w)),"effective_sample_size":_effective_sample_size(w),"weighted_mean_daily_excess":wm,"annualized_weighted_excess_mean":wm*252 if np.isfinite(wm) else np.nan,"annualized_weighted_volatility":float(np.sqrt(wv)*np.sqrt(252)) if np.isfinite(wv) else np.nan,"weighted_sharpe":float(wm/np.sqrt(wv)*np.sqrt(252)) if np.isfinite(wv) and wv>0 else np.nan,"chronological_sleeve_max_drawdown":_max_drawdown_full_timeline(soft_sleeve)},
                "costs":{"cost_bps_per_turnover":float(cost_bps_per_turnover),"mean_turnover":float(t[strategy].loc[hard].mean()) if hard.any() else 0.0,"gross_to_net_mean_daily_drag":float((gross_sr.loc[hard]-sr.loc[hard]).mean()) if hard.any() else 0.0},
            }
        alignment = strategy_return_alignment_contract(return_convention)
        out[strategy]["_metadata"]={**alignment,"drawdown_method":"full chronology; zero exposure outside sleeve"}
    return out
