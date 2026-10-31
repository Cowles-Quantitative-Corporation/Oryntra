from __future__ import annotations

from typing import Optional
import numpy as np
import pandas as pd

from .conditioned import align_returns_to_beta_decision, _max_drawdown_full_timeline, strategy_return_alignment_contract
from .labels import REGIME_NAMES


def _metrics(r: pd.Series, rf: pd.Series) -> dict:
    r=r.dropna().astype(float); rf=rf.reindex(r.index).fillna(0.0)
    if r.empty: return {"cagr":np.nan,"sharpe":np.nan,"max_drawdown":np.nan}
    total=float((1+r.clip(lower=-.999999)).prod())
    cagr=total**(252.0/len(r))-1.0 if total>0 else -1.0
    ex=(r-rf); vol=float(ex.std(ddof=0))
    sharpe=float(ex.mean()/vol*np.sqrt(252)) if vol>0 else np.nan
    return {"cagr":float(cagr),"sharpe":sharpe,"max_drawdown":_max_drawdown_full_timeline(r)}


def _regime_means(train_returns: pd.Series, states: pd.DataFrame) -> np.ndarray:
    vals=[]
    for regime in REGIME_NAMES:
        p=states[f"p_{regime}"].reindex(train_returns.index).fillna(0.0)
        valid=train_returns.notna()&p.notna()
        w=p.loc[valid].to_numpy(float); r=train_returns.loc[valid].to_numpy(float)
        vals.append(float(np.sum(w*r)/np.sum(w)) if np.sum(w)>0 else 0.0)
    return np.asarray(vals,dtype=float)


def _delta_metrics(base: pd.Series, overlay: pd.Series, rf: pd.Series) -> dict:
    b,o=_metrics(base,rf),_metrics(overlay,rf)
    return {
        "sharpe": o["sharpe"]-b["sharpe"] if np.isfinite(o["sharpe"]) and np.isfinite(b["sharpe"]) else np.nan,
        "max_drawdown_improvement": b["max_drawdown"]-o["max_drawdown"] if np.isfinite(o["max_drawdown"]) and np.isfinite(b["max_drawdown"]) else np.nan,
        "cagr": o["cagr"]-b["cagr"] if np.isfinite(o["cagr"]) and np.isfinite(b["cagr"]) else np.nan,
    }


def _paired_block_bootstrap(base: pd.Series, overlay: pd.Series, rf: pd.Series, *, reps: int=500, seed: int=17) -> dict:
    idx=base.index.intersection(overlay.index).intersection(rf.index)
    frame=pd.concat([base.reindex(idx).rename("b"),overlay.reindex(idx).rename("o"),rf.reindex(idx).rename("rf")],axis=1).dropna()
    n=len(frame)
    if n<60 or reps<=0:
        return {"reps":0,"block_length":0,"delta_ci95":{},"probabilities":{}}
    block=max(5,min(n,int(round(np.sqrt(n)))))
    starts=np.arange(0,max(1,n-block+1)); rng=np.random.default_rng(seed); rows=[]
    arr=frame.to_numpy(float)
    for _ in range(int(reps)):
        pieces=[]
        while sum(len(x) for x in pieces)<n:
            s=int(rng.choice(starts)); pieces.append(arr[s:s+block])
        sample=np.concatenate(pieces,axis=0)[:n]
        ix=pd.RangeIndex(n)
        d=_delta_metrics(pd.Series(sample[:,0],index=ix),pd.Series(sample[:,1],index=ix),pd.Series(sample[:,2],index=ix))
        if all(np.isfinite(list(d.values()))): rows.append(d)
    if not rows:
        return {"reps":0,"block_length":block,"delta_ci95":{},"probabilities":{}}
    out={}
    for k in ("sharpe","max_drawdown_improvement","cagr"):
        vals=np.asarray([r[k] for r in rows],dtype=float)
        lo,hi=np.quantile(vals,[.025,.975]); out[k]={"low":float(lo),"high":float(hi),"median":float(np.median(vals))}
    return {
        "reps":len(rows),"block_length":block,"delta_ci95":out,
        "probabilities":{
            "sharpe_positive":float(np.mean([r["sharpe"]>=0 for r in rows])),
            "drawdown_nonworse":float(np.mean([r["max_drawdown_improvement"]>=0 for r in rows])),
        },
        "_raw_cagr_deltas":[float(r["cagr"]) for r in rows],
    }


def incremental_portfolio_evidence(
    strategy_returns: pd.DataFrame,
    beta_states: pd.DataFrame,
    *,
    holdout_start: pd.Timestamp,
    return_convention: str,
    risk_free_daily: Optional[pd.Series]=None,
    policy: Optional[dict]=None,
    seed: int=17,
) -> dict:
    """Pre-registered holdout-only test of whether Beta adds incremental value.

    Candidate selection never sees strategy returns. Regime return means and
    overlay rules are fixed using pre-holdout evidence; holdout is evaluated once.
    """
    alignment=strategy_return_alignment_contract(return_convention)
    if not alignment.get("declared"):
        return {"available":False,"blocker":"strategy return alignment does not match the causal Beta-at-open contract","alignment":alignment,"paths":{}}
    r=align_returns_to_beta_decision(strategy_returns.astype(float),return_convention)
    idx=r.index.intersection(beta_states.index); r=r.loc[idx]; s=beta_states.loc[idx]
    rf=(align_returns_to_beta_decision(risk_free_daily.astype(float),return_convention).reindex(idx).fillna(0.0)
        if risk_free_daily is not None else pd.Series(0.0,index=idx))
    a_col=next((c for c in ("tba8","model_a","TBA8") if c in r.columns),None)
    b_col=next((c for c in ("alpha_v12_alt","alpha_v11","alpha","model_b","Alpha") if c in r.columns),None)
    if a_col is None:
        return {"available":False,"blocker":"Model A/TBA8 return series not supplied","paths":{}}
    dev=idx[idx<pd.Timestamp(holdout_start)]; hold=idx[idx>=pd.Timestamp(holdout_start)]
    if len(dev)<252 or len(hold)<60:
        return {"available":False,"blocker":"insufficient pre-holdout or holdout strategy history","paths":{}}
    pcols=[f"p_{x}" for x in REGIME_NAMES]
    means_a=_regime_means(r.loc[dev,a_col],s.loc[dev]); scale=max(float(np.std(means_a)),1e-8)
    expected_a=s.loc[hold,pcols].to_numpy()@means_a
    exposure_a=0.5+0.5/(1.0+np.exp(-expected_a/scale))
    base_a=r.loc[hold,a_col]
    overlay_a=pd.Series(exposure_a,index=hold)*base_a
    paths={"A_plus_C":{"baseline":_metrics(base_a,rf),"overlay":_metrics(overlay_a,rf),
                       "formula":"A exposure = 0.5 + 0.5*sigmoid(E[A return|Beta]/development regime-mean std); pre-holdout only"}}

    if b_col is not None:
        means_b=_regime_means(r.loc[dev,b_col],s.loc[dev]); exp_b=s.loc[hold,pcols].to_numpy()@means_b
        sa=expected_a/max(float(np.std(means_a)),1e-8); sb=exp_b/max(float(np.std(means_b)),1e-8)
        ea=np.exp(np.clip(sa,-10,10)); eb=np.exp(np.clip(sb,-10,10))
        wa=np.clip(ea/(ea+eb),0.2,0.8); wb=1-wa
        weights=(policy or {}).get("a_plus_b_baseline_weights",[0.5,0.5])
        aw,bw=float(weights[0]),float(weights[1]); norm=aw+bw
        if norm<=0: aw,bw=.5,.5
        else: aw,bw=aw/norm,bw/norm
        baseline_ab=aw*r.loc[hold,a_col]+bw*r.loc[hold,b_col]
        overlay_ab=pd.Series(wa,index=hold)*r.loc[hold,a_col]+pd.Series(wb,index=hold)*r.loc[hold,b_col]
        paths["A_plus_B_plus_C"]={"baseline":_metrics(baseline_ab,rf),"overlay":_metrics(overlay_ab,rf),
                                  "formula":"A/B softmax of development-only regime expected returns; 20/80 bounds; no holdout tuning",
                                  "baseline_weights":{a_col:aw,b_col:bw}}

    thresholds=(policy or {}).get("incremental_portfolio",policy or {})
    sh=float(thresholds.get("sharpe_improvement_min",.05))
    dd=float(thresholds.get("max_drawdown_improvement_min",.02))
    cagr_tol=float(thresholds.get("cagr_sacrifice_max",.01))
    reps=int(thresholds.get("bootstrap_reps",500))
    p_sh=float(thresholds.get("bootstrap_min_probability_positive_sharpe",.75))
    p_dd=float(thresholds.get("bootstrap_min_probability_nonworse_drawdown",.60))
    p_cagr=float(thresholds.get("bootstrap_min_probability_cagr_within_tolerance",.75))

    for name,block in paths.items():
        # Recreate aligned holdout series from formula outputs kept above.
        if name=="A_plus_C":
            base_series=base_a; overlay_series=overlay_a
        else:
            base_series=baseline_ab; overlay_series=overlay_ab
        deltas=_delta_metrics(base_series,overlay_series,rf.reindex(hold).fillna(0.0))
        boot=_paired_block_bootstrap(base_series,overlay_series,rf.reindex(hold).fillna(0.0),reps=reps,seed=seed)
        raw_cagr=np.asarray(boot.pop("_raw_cagr_deltas",[]),dtype=float)
        if len(raw_cagr):
            boot["probabilities"]["cagr_within_tolerance"]=float(np.mean(raw_cagr>=-cagr_tol))
        else:
            boot["probabilities"]["cagr_within_tolerance"]=np.nan
        point_pass=bool(deltas["sharpe"]>=sh and deltas["max_drawdown_improvement"]>=dd and deltas["cagr"]>=-cagr_tol)
        probs=boot.get("probabilities",{})
        evidence_pass=bool(
            boot.get("reps",0)>0
            and probs.get("sharpe_positive",0)>=p_sh
            and probs.get("drawdown_nonworse",0)>=p_dd
            and probs.get("cagr_within_tolerance",0)>=p_cagr
        )
        block["deltas"]=deltas; block["bootstrap"]=boot
        block["point_threshold_pass"]=point_pass; block["bootstrap_evidence_pass"]=evidence_pass
        block["pass"]=bool(point_pass and evidence_pass)

    return {
        "available":True,"holdout_start":str(pd.Timestamp(holdout_start).date()),"holdout_sessions":int(len(hold)),
        "return_convention":return_convention,
        "alignment":alignment,
        "thresholds":{
            "sharpe_improvement_min":sh,"max_drawdown_improvement_min":dd,"cagr_sacrifice_max":cagr_tol,
            "bootstrap_reps":reps,"bootstrap_min_probability_positive_sharpe":p_sh,
            "bootstrap_min_probability_nonworse_drawdown":p_dd,
            "bootstrap_min_probability_cagr_within_tolerance":p_cagr,
        },
        "paths":paths,"pass_any":any(v.get("pass",False) for v in paths.values())
    }
