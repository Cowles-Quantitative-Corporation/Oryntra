from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from .config import BETA_V1_CONFIG
from .consensus import SurpriseBuilder
from .repository import MacroRepository


@dataclass
class FeatureBundle:
    frame: pd.DataFrame
    groups: Dict[str, List[str]]
    data_quality: pd.Series
    target_frame: pd.DataFrame = field(default_factory=pd.DataFrame)
    target_event_mask: pd.Series = field(default_factory=lambda: pd.Series(dtype=bool))
    provenance: dict = field(default_factory=dict)


def _rolling_z(s: pd.Series, window: int = 252, min_periods: int = 60) -> pd.Series:
    x = s.astype(float)
    mean = x.rolling(window, min_periods=min_periods).mean()
    std = x.rolling(window, min_periods=min_periods).std(ddof=0).replace(0, np.nan)
    return (x - mean) / std


def _scale_preserve_sign(s: pd.Series, window: int = 252, min_periods: int = 40) -> pd.Series:
    """Scale by trailing volatility without subtracting a mean, preserving direction."""
    x = s.astype(float)
    std = x.shift(1).rolling(window, min_periods=min_periods).std(ddof=0).replace(0, np.nan)
    return x / std


def _annualize_pct_change(pct_change: pd.Series, periods_per_year_ratio: float) -> pd.Series:
    base = 1.0 + pct_change.astype(float)
    return (np.power(base.where(base > 0), periods_per_year_ratio) - 1.0) * 100.0


def _ratio_return(a: pd.Series, b: pd.Series, lookback: int) -> pd.Series:
    ratio = a.astype(float) / b.astype(float).replace(0, np.nan)
    return ratio.pct_change(lookback)


class BetaFeatureEngine:
    """Build causal candidate features plus independent economic-direction targets.

    Candidate features never contain the dedicated `target_*` columns used to define
    growth rising/falling and inflation rising/falling. Market inputs are assumed to
    be adjusted/total-return series and are shifted one session because Beta is a
    state available at the next NYSE open.
    """

    MARKET_TICKERS = (
        "SPY", "IWM", "RSP", "IWF", "IWD", "XLY", "XLP",
        "UUP", "GLD", "USO", "IEF", "TLT",
    )

    def __init__(self, *, z_window: int = 252, z_min_periods: int = 60) -> None:
        self.z_window = int(z_window)
        self.z_min_periods = int(z_min_periods)

    def _z(self, s: pd.Series) -> pd.Series:
        return _rolling_z(s, self.z_window, self.z_min_periods)

    @staticmethod
    def _add(groups: Dict[str, List[str]], group: str, *names: str) -> None:
        groups.setdefault(group, [])
        for name in names:
            if name not in groups[group]:
                groups[group].append(name)

    def build_macro(self, repo: MacroRepository, sessions: pd.DatetimeIndex) -> Tuple[pd.DataFrame, Dict[str, List[str]], pd.DataFrame]:
        raw = repo.pit_transform_panel(sessions)
        idx = pd.DatetimeIndex(pd.to_datetime(sessions)).tz_localize(None).normalize().sort_values().unique()
        f = pd.DataFrame(index=idx, dtype=float)
        targets = pd.DataFrame(index=idx, dtype=float)
        groups: Dict[str, List[str]] = {"growth": [], "inflation": [], "rates": [], "credit": [], "volatility": []}

        rate_names = {
            "DGS3MO": "rate_3m", "DGS2": "rate_2y", "DGS5": "rate_5y",
            "DGS10": "rate_10y", "DGS30": "rate_30y", "DFII5": "real_5y", "DFII10": "real_10y",
        }
        for sid, name in rate_names.items():
            p = raw.get(sid)
            if p is None or p.empty:
                continue
            f[name] = p["level"]
            f[f"{name}_chg21"] = p.get("diff_21")
            f[f"{name}_z"] = self._z(f[name])
            f[f"{name}_chg21_z"] = self._z(f[f"{name}_chg21"])
            self._add(groups, "rates", name, f"{name}_chg21", f"{name}_z", f"{name}_chg21_z")

        if {"rate_10y", "rate_2y"} <= set(f.columns):
            f["curve_10y_2y"] = f["rate_10y"] - f["rate_2y"]
            f["curve_10y_2y_z"] = self._z(f["curve_10y_2y"])
            self._add(groups, "rates", "curve_10y_2y", "curve_10y_2y_z")
        if {"rate_10y", "rate_3m"} <= set(f.columns):
            f["curve_10y_3m"] = f["rate_10y"] - f["rate_3m"]
            f["curve_10y_3m_z"] = self._z(f["curve_10y_3m"])
            self._add(groups, "rates", "curve_10y_3m", "curve_10y_3m_z")
        if {"rate_30y", "rate_5y"} <= set(f.columns):
            f["curve_30y_5y"] = f["rate_30y"] - f["rate_5y"]
            self._add(groups, "rates", "curve_30y_5y")

        for sid, name in (("T5YIE", "breakeven_5y"), ("T10YIE", "breakeven_10y")):
            p = raw.get(sid)
            if p is None or p.empty:
                continue
            f[name] = p["level"]
            f[f"{name}_chg21"] = p.get("diff_21")
            f[f"{name}_z"] = self._z(f[name])
            f[f"{name}_chg21_z"] = self._z(f[f"{name}_chg21"])
            self._add(groups, "inflation", name, f"{name}_chg21", f"{name}_z", f"{name}_chg21_z")

        for sid, name in (("BAMLH0A0HYM2", "hy_oas"), ("BAMLC0A0CM", "ig_oas")):
            p = raw.get(sid)
            if p is None or p.empty:
                continue
            f[name] = p["level"]
            f[f"{name}_chg21"] = p.get("diff_21")
            f[f"{name}_z"] = self._z(f[name])
            f[f"{name}_chg21_z"] = self._z(f[f"{name}_chg21"])
            self._add(groups, "credit", name, f"{name}_chg21", f"{name}_z", f"{name}_chg21_z")

        p = raw.get("VIXCLS")
        if p is not None and not p.empty:
            f["vix"] = p["level"]
            f["vix_chg21"] = p.get("diff_21")
            f["vix_z"] = self._z(f["vix"])
            self._add(groups, "volatility", "vix", "vix_chg21", "vix_z")

        # Strict target/input separation:
        # headline CPI/PCE are target anchors only; core CPI/PCE are candidate inputs.
        for sid, name in {"CPIAUCSL": "cpi", "PCEPI": "pce"}.items():
            p = raw.get(sid)
            if p is None or p.empty:
                continue
            anchor_3m = _annualize_pct_change(p.get("pct_3"), 4.0)
            targets[f"inflation_{name}_acceleration"] = anchor_3m - anchor_3m.shift(63)

        for sid, name in {"CPILFESL": "core_cpi", "PCEPILFE": "core_pce"}.items():
            p = raw.get(sid)
            if p is None or p.empty:
                continue
            f[f"{name}_mom"] = p.get("pct_1") * 100.0
            f[f"{name}_3m_ann"] = _annualize_pct_change(p.get("pct_3"), 4.0)
            f[f"{name}_yoy"] = p.get("pct_12") * 100.0
            for metric in ("mom", "3m_ann", "yoy"):
                f[f"{name}_{metric}_z"] = self._z(f[f"{name}_{metric}"])
            self._add(groups, "inflation", f"{name}_mom", f"{name}_3m_ann", f"{name}_yoy", f"{name}_mom_z", f"{name}_3m_ann_z", f"{name}_yoy_z")

        # Industrial production is a growth target anchor only.
        p = raw.get("INDPRO")
        if p is not None and not p.empty:
            anchor_3m = _annualize_pct_change(p.get("pct_3"), 4.0)
            targets["growth_indpro_acceleration"] = anchor_3m - anchor_3m.shift(63)

        # Retail sales remains an independent candidate growth input.
        p = raw.get("RSAFS")
        if p is not None and not p.empty:
            f["retail_sales_mom"] = p.get("pct_1") * 100.0
            f["retail_sales_3m_ann"] = _annualize_pct_change(p.get("pct_3"), 4.0)
            f["retail_sales_yoy"] = p.get("pct_12") * 100.0
            for metric in ("mom", "3m_ann", "yoy"):
                f[f"retail_sales_{metric}_z"] = self._z(f[f"retail_sales_{metric}"])
            self._add(groups, "growth", "retail_sales_mom", "retail_sales_3m_ann", "retail_sales_yoy",
                      "retail_sales_mom_z", "retail_sales_3m_ann_z", "retail_sales_yoy_z")

        p = raw.get("PAYEMS")
        if p is not None and not p.empty:
            payems_3m_ann = _annualize_pct_change(p.get("pct_3"), 4.0)
            targets["growth_payems_acceleration"] = payems_3m_ann - payems_3m_ann.shift(63)

        p = raw.get("UNRATE")
        if p is not None and not p.empty:
            # Falling unemployment is growth-up, hence negative sign.
            targets["growth_unemployment_direction"] = -p.get("diff_3")

        p = raw.get("GDPC1")
        if p is not None and not p.empty:
            real_gdp_qoq_ann = _annualize_pct_change(p.get("pct_1"), 4.0)
            targets["growth_real_gdp_acceleration"] = real_gdp_qoq_ann - real_gdp_qoq_ann.shift(63)

        p = raw.get("ICSA")
        if p is not None and not p.empty:
            f["claims_4w"] = p.get("pct_4") * 100.0
            f["claims_yoy"] = p.get("pct_52") * 100.0
            f["claims_4w_z"] = self._z(f["claims_4w"])
            f["claims_yoy_z"] = self._z(f["claims_yoy"])
            self._add(groups, "growth", "claims_4w", "claims_yoy", "claims_4w_z", "claims_yoy_z")

        return f.replace([np.inf, -np.inf], np.nan), groups, targets.replace([np.inf, -np.inf], np.nan)

    def build_market(self, market_prices: pd.DataFrame, sessions: pd.DatetimeIndex) -> Tuple[pd.DataFrame, Dict[str, List[str]]]:
        idx = pd.DatetimeIndex(pd.to_datetime(sessions)).tz_localize(None).normalize().sort_values().unique()
        px = market_prices.copy()
        px.index = pd.DatetimeIndex(pd.to_datetime(px.index)).tz_localize(None).normalize()
        px = px[~px.index.duplicated(keep="last")].sort_index().reindex(idx).ffill()
        known = px.shift(1)
        f = pd.DataFrame(index=idx, dtype=float)
        groups: Dict[str, List[str]] = {"market": [], "volatility": [], "growth": [], "inflation": []}

        if "SPY" in known:
            r = known["SPY"].pct_change()
            f["spy_ret21"] = known["SPY"].pct_change(21)
            f["spy_ret63"] = known["SPY"].pct_change(63)
            f["realized_vol21"] = r.rolling(21, min_periods=10).std(ddof=0) * np.sqrt(252)
            f["realized_vol63"] = r.rolling(63, min_periods=30).std(ddof=0) * np.sqrt(252)
            groups["market"] += ["spy_ret21", "spy_ret63"]
            groups["volatility"] += ["realized_vol21", "realized_vol63"]

        for name, (a, b) in {
            "breadth_rsp_vs_spy_63": ("RSP", "SPY"),
            "small_vs_large_63": ("IWM", "SPY"),
            "growth_vs_value_63": ("IWF", "IWD"),
            "cyclical_vs_defensive_63": ("XLY", "XLP"),
        }.items():
            if a in known and b in known:
                f[name] = _ratio_return(known[a], known[b], 63)
                groups["market"].append(name)
        groups["growth"] += [n for n in ("breadth_rsp_vs_spy_63", "small_vs_large_63", "cyclical_vs_defensive_63") if n in f]

        for ticker, name in (
            ("UUP", "dollar_trend63"), ("GLD", "gold_trend63"), ("USO", "oil_trend63"),
            ("IEF", "treasury_7_10y_ret63"), ("TLT", "treasury_20y_ret63"),
        ):
            if ticker in known:
                f[name] = known[ticker].pct_change(63)
                groups["market"].append(name)
        for name in ("oil_trend63", "gold_trend63", "dollar_trend63"):
            if name in f:
                groups["inflation"].append(name)

        base_cols = list(f.columns)
        for col in base_cols:
            f[f"{col}_z"] = self._z(f[col])
            for group in groups.values():
                if col in group and f"{col}_z" not in group:
                    group.append(f"{col}_z")
        return f.replace([np.inf, -np.inf], np.nan), groups

    def build(self, repo: MacroRepository, market_prices: pd.DataFrame, *, surprise_builder: Optional[SurpriseBuilder] = None, stock_context: Optional[pd.DataFrame] = None) -> FeatureBundle:
        sessions = pd.DatetimeIndex(pd.to_datetime(market_prices.index)).tz_localize(None).normalize().sort_values().unique()
        macro, macro_groups, targets = self.build_macro(repo, sessions)
        market, market_groups = self.build_market(market_prices, sessions)
        frame = macro.join(market, how="outer").sort_index()
        if stock_context is not None:
            context = stock_context.copy()
            context.index = pd.DatetimeIndex(pd.to_datetime(context.index)).tz_localize(None).normalize()
            context = context[~context.index.duplicated(keep="last")].sort_index().reindex(frame.index)
            duplicate = set(frame.columns).intersection(context.columns)
            if duplicate:
                raise ValueError(f"Beta stock context collides with existing feature columns: {sorted(duplicate)[:8]}")
            frame = frame.join(context, how="left")

        groups: Dict[str, List[str]] = {}
        for source in (macro_groups, market_groups):
            for group, cols in source.items():
                groups.setdefault(group, [])
                for col in cols:
                    if col in frame.columns and col not in groups[group]:
                        groups[group].append(col)

        if stock_context is not None:
            context_cols = [c for c in stock_context.columns if c in frame.columns]
            groups.setdefault("alpha_alt_context", []).extend(context_cols)
            groups.setdefault("market", [])
            groups["market"].extend(c for c in context_cols if c not in groups["market"])

        if surprise_builder is not None:
            allowed_surprises = BETA_V1_CONFIG["target_definition"]["candidate_surprise_metrics"]
            surprise = surprise_builder.session_panel(frame.index, metrics=allowed_surprises)
            frame = frame.join(surprise, how="left")
            for col in surprise.columns:
                category = "inflation" if any(x in col for x in ("cpi", "pce")) else "growth"
                groups.setdefault(category, []).append(col)
                groups.setdefault("surprise", []).append(col)

        informative = [c for c in frame.columns if frame[c].notna().sum() >= max(10, int(len(frame) * 0.02))]
        frame = frame[informative].replace([np.inf, -np.inf], np.nan)
        for group in list(groups):
            groups[group] = [c for c in groups[group] if c in frame.columns]

        forbidden = set(BETA_V1_CONFIG["independence"]["forbidden_forecast_inputs"])
        present_forbidden = forbidden.intersection(frame.columns)
        if present_forbidden:
            raise RuntimeError(f"Beta independence violation: forbidden forecast inputs {sorted(present_forbidden)}")
        if any(c.startswith("target_") for c in frame.columns):
            raise RuntimeError("Target leakage: target-only columns entered candidate feature frame")

        # Stronger than column-name separation: dedicated label-anchor series never
        # contribute candidate features in v1. Anchor-derived prefixes are checked too.
        forbidden_anchor_prefixes = ("cpi_", "pce_", "indpro_", "payems_", "unrate_", "real_gdp_")
        anchor_leaks = [c for c in frame.columns if c.startswith(forbidden_anchor_prefixes)]
        if anchor_leaks:
            raise RuntimeError(f"Beta target/input separation violation: {anchor_leaks[:10]}")

        target_frame = targets.reindex(frame.index)
        # An effective macro-event session is a session on which any target anchor changes.
        event_mask = target_frame.ne(target_frame.shift(1)) & target_frame.notna()
        event_mask = event_mask.any(axis=1).fillna(False)
        quality = frame.notna().mean(axis=1) if len(frame.columns) else pd.Series(0.0, index=frame.index)
        provenance = {
            "market": dict(getattr(market_prices, "attrs", {}) or {}),
            "macro": repo.integrity_report(),
            "alpha_alt_context": dict(getattr(stock_context, "attrs", {}) or {}) if stock_context is not None else {
                "enabled": False, "tba_anchor_consumed": False, "alpha_forecast_consumed": False
            },
            "target_input_separation": {
                "growth_anchor_series": BETA_V1_CONFIG["target_definition"]["growth_anchor_series"],
                "inflation_anchor_series": BETA_V1_CONFIG["target_definition"]["inflation_anchor_series"],
                "candidate_surprise_metrics": BETA_V1_CONFIG["target_definition"]["candidate_surprise_metrics"],
                "anchor_feature_overlap": [],
            },
        }
        return FeatureBundle(
            frame=frame,
            groups=groups,
            data_quality=quality,
            target_frame=target_frame,
            target_event_mask=event_mask,
            provenance=provenance,
        )
