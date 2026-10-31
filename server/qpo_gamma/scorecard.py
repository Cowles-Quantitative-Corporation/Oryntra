"""Historical forecast diagnostics kept separate from portfolio attribution."""
from pathlib import Path
import numpy as np
import pandas as pd
from .identity import atomic_json

def rank_ic(x,y):
    a=pd.Series(x).rank(method='average').to_numpy(float);b=pd.Series(y).rank(method='average').to_numpy(float)
    a-=a.mean();b-=b.mean();den=np.sqrt((a@a)*(b@b));return float(a@b/den) if den>1e-15 else None

def make_scorecard(panel,cutoffs,directory):
    h=panel.h;rows=[];loss_deltas=[];feature_rows=[]
    for cutoff in cutoffs:
        cutoff=pd.Timestamp(cutoff);y=panel.realized.loc[cutoff].to_numpy(float);native=panel.native.loc[cutoff].to_numpy(float)*h/252;candidate=panel.issued.loc[cutoff].to_numpy(float)*h/252
        if not np.isfinite(np.r_[y,native,candidate]).all():raise ValueError('Missing completed evaluation forecast outcome')
        common=float(y.mean());e0=y-native;e1=y-candidate;c0=e0-e0.mean();c1=e1-e1.mean();d=candidate-native
        rows.append({'cutoff':str(cutoff),'assets':len(y),'native_MSE':float(np.mean(e0**2)),'candidate_MSE':float(np.mean(e1**2)),'paired_MSE_delta':float(np.mean(e1**2-e0**2)),'native_centered_MSE':float(np.mean(c0**2)),'candidate_centered_MSE':float(np.mean(c1**2)),'paired_centered_MSE_delta':float(np.mean(c1**2-c0**2)),'native_mean_error':float(e0.mean()),'candidate_mean_error':float(e1.mean()),'native_rank_IC':rank_ic(native,y),'candidate_rank_IC':rank_ic(candidate,y),'native_increment_rank_overlap':rank_ic(native,d),'maximum_abs_increment_H':float(abs(d).max()),'nonzero_increment_stocks':int((abs(d)>1e-14).sum())})
        loss_deltas.append(e0**2-e1**2)
        for name,feature in panel.features.items():
            f=feature.loc[cutoff].to_numpy(float)
            if not np.isfinite(f).all():raise ValueError('Missing feature in evaluation scorecard')
            rank=pd.Series(f).rank(method='average',pct=True).to_numpy();top=y[rank>=.8];bottom=y[rank<=.2]
            feature_rows.append({'cutoff':str(cutoff),'feature':name,'rank_IC':rank_ic(f,y),'top_minus_bottom_H':float(top.mean()-bottom.mean()) if len(top) and len(bottom) else None})
    out=Path(directory);out.mkdir(parents=True,exist_ok=True);frame=pd.DataFrame(rows);frame.to_csv(out/'forecast_origin_scorecard.csv',index=False)
    stock=pd.Series(np.mean(loss_deltas,axis=0),index=panel.columns,name='mean_squared_loss_improvement');stock.to_csv(out/'forecast_stock_loss_improvement.csv')
    pd.DataFrame(feature_rows).to_csv(out/'feature_rank_IC_and_spreads.csv',index=False)
    summary={'completed_origins':len(rows),'assets':len(panel.columns),'forecast_loss_supporting_stocks':int((stock>1e-14).sum()),'forecast_loss_hurting_stocks':int((stock<-1e-14).sum()),'mean_paired_MSE_delta':float(frame.paired_MSE_delta.mean()),'mean_paired_centered_MSE_delta':float(frame.paired_centered_MSE_delta.mean()),'mean_native_rank_IC':None if frame.native_rank_IC.isna().all() else float(frame.native_rank_IC.mean()),'mean_candidate_rank_IC':None if frame.candidate_rank_IC.isna().all() else float(frame.candidate_rank_IC.mean()),'nonzero_increment_origins':int((frame.nonzero_increment_stocks>0).sum()),'centering_reference':'equal-weight declared upstream universe; no post-selection recentering','limits':'Historical explored sample; stocks are dependent. Forecast-loss support differs from portfolio-contribution support. No automatic promotion.'}
    atomic_json(out/'FORECAST_SCORECARD.json',summary);return summary
