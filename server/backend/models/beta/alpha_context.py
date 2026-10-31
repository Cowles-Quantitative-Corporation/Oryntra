"""Pure Alpha-V1.2-Alt raw feature context for Beta / Model C.

Beta may reuse the same raw economic feature families that Alpha V1.2 Alt uses,
but it never consumes Alpha scores, expected returns, uncertainty outputs, TBA8
forecasts, or portfolio weights. Cross-sectional feature panels are reduced to
market-level state context and shifted one completed session so Beta's open
decision cannot see the current close.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Optional

import numpy as np
import pandas as pd

from ...alpha_v1 import AlphaV1Config, _build_features

ALPHA_ALT_CONTEXT_FEATURE_FAMILIES = (
    "momentum_21", "momentum_63", "momentum_126",
    "residual_momentum_21", "residual_momentum_63",
    "short_reversion", "low_volatility", "sector_relative", "value", "quality",
)

@dataclass(frozen=True)
class AlphaAltContextConfig:
    beta_window_sessions: int = 126
    volatility_window_sessions: int = 21
    include_momentum_21: bool = True
    include_momentum_63: bool = True
    include_momentum_126: bool = True
    include_residual_momentum_21: bool = True
    include_residual_momentum_63: bool = True
    include_short_reversion: bool = True
    include_low_volatility: bool = True
    include_sector_relative: bool = True
    include_value: bool = True
    include_quality: bool = True

    def __post_init__(self) -> None:
        if not (63 <= self.beta_window_sessions <= 504):
            raise ValueError("beta_window_sessions must be within [63, 504]")
        if not (10 <= self.volatility_window_sessions <= 126):
            raise ValueError("volatility_window_sessions must be within [10, 126]")
        switches=[v for k,v in asdict(self).items() if k.startswith("include_")]
        if not all(isinstance(v,bool) for v in switches):
            raise ValueError("feature switches must be boolean")

    def alpha_config(self) -> AlphaV1Config:
        return AlphaV1Config(
            enabled=True,
            beta_window_sessions=self.beta_window_sessions,
            volatility_window_sessions=self.volatility_window_sessions,
            include_momentum_21=self.include_momentum_21,
            include_momentum_63=self.include_momentum_63,
            include_momentum_126=self.include_momentum_126,
            include_residual_momentum_21=self.include_residual_momentum_21,
            include_residual_momentum_63=self.include_residual_momentum_63,
            include_short_reversion=self.include_short_reversion,
            include_low_volatility=self.include_low_volatility,
            include_sector_relative=self.include_sector_relative,
            include_value=self.include_value,
            include_quality=self.include_quality,
        )

def _validate_close_panel(closes: pd.DataFrame) -> pd.DataFrame:
    if not isinstance(closes,pd.DataFrame) or closes.empty:
        raise ValueError("Beta Alpha-context requires a non-empty stock close panel")
    out=closes.astype(float).copy()
    out.index=pd.DatetimeIndex(pd.to_datetime(out.index)).tz_localize(None).normalize()
    out=out[~out.index.duplicated(keep="last")].sort_index()
    if out.columns.duplicated().any():
        raise ValueError("stock columns must be unique")
    vals=out.to_numpy(dtype=float); finite=np.isfinite(vals)
    if finite.any() and (vals[finite] <= 0).any():
        raise ValueError("observed stock closes must be positive")
    return out

def _aggregate(panel: pd.DataFrame, feature_name: str) -> pd.DataFrame:
    x=panel.astype(float)
    valid=x.notna().sum(axis=1).replace(0,np.nan)
    result=pd.DataFrame({
        f"alpha_alt_{feature_name}_median":x.median(axis=1,skipna=True),
        f"alpha_alt_{feature_name}_mean":x.mean(axis=1,skipna=True),
        f"alpha_alt_{feature_name}_breadth":x.gt(0).sum(axis=1).div(valid),
        f"alpha_alt_{feature_name}_dispersion":x.std(axis=1,ddof=0,skipna=True),
        f"alpha_alt_{feature_name}_iqr":x.quantile(.75,axis=1)-x.quantile(.25,axis=1),
        f"alpha_alt_{feature_name}_coverage":valid/float(max(1,x.shape[1])),
    },index=x.index)
    return result.shift(1)

def build_alpha_alt_market_context(
    closes: pd.DataFrame, *, benchmark_returns: Optional[pd.Series]=None,
    sector_labels: Optional[pd.DataFrame]=None, value_scores: Optional[pd.DataFrame]=None,
    quality_scores: Optional[pd.DataFrame]=None,
    config: AlphaAltContextConfig=AlphaAltContextConfig(),
) -> pd.DataFrame:
    clean=_validate_close_panel(closes)
    bench=None
    if benchmark_returns is not None:
        bench=pd.Series(benchmark_returns,dtype=float).copy()
        bench.index=pd.DatetimeIndex(pd.to_datetime(bench.index)).tz_localize(None).normalize()
        bench=bench[~bench.index.duplicated(keep="last")].sort_index().reindex(clean.index)
    features=_build_features(clean,bench,config.alpha_config(),sector_labels=sector_labels,
                             value_scores=value_scores,quality_scores=quality_scores)
    blocks=[_aggregate(panel,name) for name,panel in features.items()]
    out=pd.concat(blocks,axis=1).sort_index() if blocks else pd.DataFrame(index=clean.index)
    out=out.replace([np.inf,-np.inf],np.nan)
    out.attrs.update({
        "source_model_family":"alpha_v12_alt_raw_features",
        "feature_families":list(features),
        "tba_anchor_consumed":False,
        "alpha_forecast_consumed":False,
        "alpha_score_consumed":False,
        "decision_timing":"completed close -> following session open",
        "stock_count":int(clean.shape[1]),
    })
    return out
