"""Causal, research-only Alpha/Beta/SEC forecast producer for the unchanged QPO core.

This is a new Alpha-feature adapter, not a reproduction of Alpha V1.1's complete
adaptive weighting/calibration. It reuses its implemented economic sleeves and
priors. An entry-close t forecast sees only completed session t-1 (or earlier),
and targets absolute simple return C[t+h]/C[t]-1. It never substitutes ranks for
returns, manufactures a Beta state, downloads SEC data, or uses provider keys.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from collections.abc import Mapping
from datetime import date
from hashlib import sha256
import json
from numbers import Real
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import parse_qsl, urlparse

import numpy as np
import pandas as pd

from ..alpha_v1 import AlphaV1Config, _PRIORS, _build_features
from ..models.beta.labels import REGIME_NAMES
from ..sec_pit import QUALITY_FEATURES, VALUE_FEATURES, build_factor_panels, normalize_companyfacts

GAMMA_PRODUCER = "gamma_qpo_research_v1"
GAMMA_VERSION = "0.4.3-feature-qa"
SEC_SEMANTICS_REVISION = "sec_accounting_qa3_calendar_anniversary_v1"


@dataclass(frozen=True)
class GammaForecastConfig:
    horizon_sessions: int = 21
    feature_lag_sessions: int = 1
    minimum_history_sessions: int = 252
    min_calibration_days: int = 42
    min_residual_days: int = 42
    training_window_sessions: int = 504
    ridge_penalty: float = 1.0
    residual_kappa: float = 0.25
    reference_shrinkage: float = 0.5
    minimum_assets: int = 6
    scale_floor: float = 1e-4
    alpha_weighting: str = "frozen_prior"
    sec_diagnostic_residual_features: bool = False
    require_beta: bool = False
    require_sec: bool = False
    require_verified_macro_vintages: bool = False
    beta_max_age_sessions: int = 5
    return_convention: str = "close_to_close"
    sec_semantics_version: str = "strict_v1"
    # Part of the fit/config identity, not a selectable model parameter. An
    # earlier implementation cannot be relabeled as this revision by a caller.
    sec_semantics_revision: str = SEC_SEMANTICS_REVISION

    def __post_init__(self):
        if self.alpha_weighting not in {"frozen_prior", "matured_feature_ic"}:
            raise ValueError("Gamma alpha_weighting must be frozen_prior or matured_feature_ic")
        if not isinstance(self.sec_diagnostic_residual_features, bool):
            raise ValueError("Gamma sec_diagnostic_residual_features must be boolean")
        if self.sec_semantics_revision != SEC_SEMANTICS_REVISION:
            raise ValueError("Gamma SEC semantics revision does not match this producer")
        if not isinstance(self.sec_semantics_version, str) or self.sec_semantics_version not in ("legacy", "strict_v1"):
            raise ValueError("Gamma SEC semantics must be legacy or strict_v1")
        for name in ("horizon_sessions", "feature_lag_sessions", "minimum_history_sessions",
                     "min_calibration_days", "min_residual_days", "training_window_sessions",
                     "minimum_assets", "beta_max_age_sessions"):
            if isinstance(getattr(self, name), bool) or not isinstance(getattr(self, name), int):
                raise ValueError(f"Gamma {name} must be an integer")
        if not 2 <= self.horizon_sessions <= 21:
            raise ValueError("Gamma Alpha-feature adapter supports horizons 2..21 sessions")
        if not isinstance(self.feature_lag_sessions, int) or self.feature_lag_sessions < 1:
            raise ValueError("Gamma requires at least one completed-session feature lag")
        if self.return_convention != "close_to_close":
            raise ValueError("Gamma QPO adapter requires explicit close_to_close target")
        if self.minimum_history_sessions < 126 or self.minimum_assets < 6:
            raise ValueError("Gamma requires at least 126 history sessions and six assets")
        if self.min_calibration_days < 20 or self.min_residual_days < 20:
            raise ValueError("Gamma requires at least 20 completed calibration/residual dates")
        if self.training_window_sessions < max(self.min_calibration_days, self.min_residual_days):
            raise ValueError("Gamma training window is shorter than required support")
        for name in ("ridge_penalty", "residual_kappa", "reference_shrinkage", "scale_floor"):
            value = getattr(self, name)
            if isinstance(value, (bool, np.bool_)) or not isinstance(value, Real):
                raise ValueError(f"Gamma {name} must be a finite real number")
            try:
                finite = np.isfinite(float(value))
            except (OverflowError, ValueError) as exc:
                raise ValueError(f"nonfinite Gamma parameter {name}") from exc
            if not finite:
                raise ValueError(f"nonfinite Gamma parameter {name}")
        if self.ridge_penalty <= 0 or not 0 <= self.residual_kappa <= 2:
            raise ValueError("Gamma ridge must be positive and correction bound within [0,2]")
        if not 0 <= self.reference_shrinkage <= 1 or self.scale_floor <= 0:
            raise ValueError("invalid Gamma reference shrinkage/scale floor")
        if self.beta_max_age_sessions < 0:
            raise ValueError("Gamma Beta age must be nonnegative")
        if (not isinstance(self.require_beta, bool) or not isinstance(self.require_sec, bool)
                or not isinstance(self.require_verified_macro_vintages, bool)):
            raise ValueError("Gamma required-sleeve switches must be boolean")


def _stamp(x: Any) -> pd.Timestamp:
    t = pd.Timestamp(x)
    if pd.isna(t):
        raise ValueError("missing timestamp")
    return t.tz_localize("UTC") if t.tzinfo is None else t.tz_convert("UTC")


def _json_hash(x: Any) -> str:
    return sha256(json.dumps(x, sort_keys=True, default=str).encode()).hexdigest()


def load_cached_sec_context(symbols: list[str], sessions: pd.DatetimeIndex, *,
                            cache_directory: str | Path | None,
                            histories: dict[str, pd.DataFrame] | None = None,
                            semantics_version: str = "legacy") -> dict[str, Any]:
    """Read existing official SEC JSON only; never network or infer missing facts.

    Value ratios are withheld unless explicit unadjusted historical Close data
    carries ``attrs['price_basis']=='unadjusted'``. Adjusted QPO prices cannot be
    substituted for market cap. Filing acceptance/fallback timing is preserved by
    the source SEC normalizer. Raw filing values are never fabricated/imputed.
    """
    if semantics_version not in ("legacy", "strict_v1"):
        raise ValueError("Unknown Gamma SEC semantics version")
    empty = pd.DataFrame(np.nan, index=sessions, columns=symbols)
    result = {"quality": empty.copy(), "value": empty.copy(), "facts_by_symbol": {},
              "diagnostic_features": {}, "feature_validity": {}, "feature_events": {}, "feature_states": {},
              "metadata": {"status": "unavailable", "source": "SEC_cached_companyfacts",
                           "semantics_version": semantics_version,
                           "network_used": False, "reason": "no_sec_cache_directory"}}
    if cache_directory is None:
        return result
    # SEC eligibility is expressed in exchange-session dates, while cached
    # daily prices can retain 04:00/05:00 UTC labels.  Align by session date,
    # then restore the caller's original labels without moving any filing's
    # first eligible session or changing the forecast/execution clock.
    factor_sessions = pd.DatetimeIndex(sessions)
    if factor_sessions.tz is not None:
        factor_sessions = factor_sessions.tz_localize(None)
    factor_sessions = factor_sessions.normalize()
    if factor_sessions.has_duplicates or not factor_sessions.is_monotonic_increasing:
        raise ValueError("SEC context requires one increasing observation per trading-session date")
    root = Path(cache_directory)
    manifest = root / "company_tickers.json"
    if not manifest.is_file():
        result["metadata"]["reason"] = "cached_SEC_ticker_manifest_missing"
        return result
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    rows = payload.values() if isinstance(payload, dict) else payload
    mapping = {str(x["ticker"]).upper(): int(x["cik_str"]) for x in rows
               if isinstance(x, dict) and x.get("ticker") and x.get("cik_str") is not None}
    facts: dict[str, list] = {}
    sources: dict[str, dict] = {}
    errors: dict[str, str] = {}
    raw_histories: dict[str, pd.DataFrame] = {}
    supplied_histories = {}
    for name, history in (histories or {}).items():
        if isinstance(history, dict):
            if not isinstance(history.get("rows"), list) or not history["rows"]:
                raise ValueError("SEC JSON history requires nonempty rows with Date and Close")
            frame = pd.DataFrame(history["rows"])
            if not {"Date", "Close"}.issubset(frame.columns):
                raise ValueError("SEC JSON history requires Date and Close columns")
            frame["Date"] = pd.to_datetime(frame["Date"], errors="raise")
            frame = frame.set_index("Date")
            frame.attrs["price_basis"] = history.get("price_basis")
            frame.attrs["sec_value_contracts"] = history.get("sec_value_contracts")
            history = frame
        if not isinstance(history, pd.DataFrame):
            raise ValueError("SEC histories must be DataFrames or documented JSON history objects")
        if (not isinstance(history.index, pd.DatetimeIndex) or history.index.has_duplicates
                or not history.index.is_monotonic_increasing or "Close" not in history):
            raise ValueError("SEC histories require unique increasing dates and Close")
        vals = history["Close"].to_numpy(dtype=float)
        if not np.isfinite(vals).all() or (vals <= 0).any():
            raise ValueError("SEC unadjusted observed closes must be positive and finite")
        supplied_histories[str(name).upper().strip()] = history
    for symbol in symbols:
        facts[symbol] = []
        cik = mapping.get(symbol)
        if cik is None:
            errors[symbol] = "cached_CIK_missing"
            continue
        cf_path = root / "companyfacts" / f"CIK{cik:010d}.json"
        sub_path = root / "submissions" / f"CIK{cik:010d}.json"
        if not cf_path.is_file():
            errors[symbol] = "cached_companyfacts_missing"
            continue
        try:
            cf_raw = cf_path.read_bytes()
            cf = json.loads(cf_raw)
            sub_raw = sub_path.read_bytes() if sub_path.is_file() else None
            sub = json.loads(sub_raw) if sub_raw is not None else None
            facts[symbol] = normalize_companyfacts(
                cf, symbol, factor_sessions, sub,
                **({"semantics_version": "strict_v1"} if semantics_version == "strict_v1" else {}))
            sources[symbol] = {
                "companyfacts_url": f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json",
                "submissions_url": f"https://data.sec.gov/submissions/CIK{cik:010d}.json",
                "companyfacts_sha256": sha256(cf_raw).hexdigest(),
                "submissions_sha256": sha256(sub_raw).hexdigest() if sub_raw else None,
            }
            history = supplied_histories.get(symbol)
            if isinstance(history, pd.DataFrame) and history.attrs.get("price_basis") == "unadjusted":
                raw_histories[symbol] = history
        except (ValueError, TypeError, KeyError, OSError) as exc:
            errors[symbol] = f"{type(exc).__name__}: {exc}"
    panels = build_factor_panels(
        facts, factor_sessions, histories=raw_histories,
        **({"semantics_version": "strict_v1", "strict_options": {"diagnostic_features": True}}
           if semantics_version == "strict_v1" else {}))
    def composite(names, negative=()):
        # The legacy composite sums NaNs and can suppress partially observed
        # issuers. This adapter averages only observed ranks, requiring >=2
        # actual components; it never replaces missing fundamentals with zero.
        numer = pd.DataFrame(0.0, index=factor_sessions, columns=symbols)
        counts = numer.copy()
        for name in names:
            raw = panels["features"][name].reindex(index=factor_sessions, columns=symbols)
            rank = raw.rank(axis=1, pct=True).mul(2).sub(1)
            if name in negative:
                rank = -rank
            numer += rank.fillna(0)
            counts += raw.notna().astype(float)
        return numer.div(counts.where(counts >= 2)).set_axis(sessions, axis=0)
    quality = composite(QUALITY_FEATURES, {"asset_growth_yoy", "debt_to_assets"})
    value = composite(VALUE_FEATURES)
    diagnostic_names = panels["metadata"].get("diagnostic_feature_names", [])
    diagnostic_panels = {
        name: panels["features"][name].reindex(index=factor_sessions, columns=symbols).set_axis(sessions, axis=0)
        for name in diagnostic_names}
    return {"quality": quality, "value": value, "diagnostic_features": diagnostic_panels,
            "feature_validity": {name: panel.reindex(index=factor_sessions, columns=symbols).set_axis(sessions, axis=0)
                                 for name, panel in panels.get("feature_validity", {}).items()},
            "feature_events": panels.get("feature_events", {}), "feature_states": panels.get("feature_states", {}),
            "facts_by_symbol": facts, "sources": sources,
            "metadata": {**panels["metadata"], "status": "cached_inputs_loaded",
                         "gamma_sec_semantics_version": semantics_version,
                         "diagnostic_features_predictor_usage": "none; separately exposed raw features, excluded from current priors and residual design",
                         "diagnostic_feature_coverage": {name: int(panel.notna().to_numpy().sum())
                                                         for name, panel in diagnostic_panels.items()},
                         "source": "SEC_cached_companyfacts", "network_used": False,
                         "aggregation": "observed_component_rank_mean_minimum_two",
                         "session_alignment": "SEC_session_dates_mapped_to_original_price_session_labels",
                         "quality_observed_cell_count": int(quality.notna().to_numpy().sum()),
                         "value_observed_cell_count": int(value.notna().to_numpy().sum()),
                         "quality_observed_cell_pct": float(100 * quality.notna().to_numpy().mean()) if quality.size else 0.0,
                         "value_observed_cell_pct": float(100 * value.notna().to_numpy().mean()) if value.size else 0.0,
                         "errors": errors, "value_price_basis_verified_symbols": sorted(raw_histories)}}


def validate_macro_vintage_admission(provenance: Any) -> None:
    """Check declared macro flags only; never attest source/market authenticity."""
    if not isinstance(provenance, list) or not provenance:
        raise ValueError("Verified macro admission requires nonempty macro provenance")
    for number, source in enumerate(provenance):
        if not isinstance(source, Mapping) or source.get("historical_vintage_verified") is not True:
            raise ValueError(f"Beta macro provenance entry {number} requires historical_vintage_verified=True; "
                             "missing or unverified source metadata is not verified")


def _validated_beta_records(records: Iterable[dict[str, Any]] | None, *,
                            require_verified_macro_vintages: bool = False):
    accepted, rejected = [], []
    for number, raw in enumerate(records or []):
        try:
            record = dict(raw)
            if record.get("source") != "alfred_pit_beta":
                raise ValueError("Beta needs source=alfred_pit_beta; generic dated dictionaries are insufficient")
            if not record.get("artifact_id"):
                raise ValueError("Beta artifact_id missing")
            times = {k: _stamp(record[k]) for k in (
                "as_of", "available_at", "information_cutoff", "training_information_cutoff",
                "calibration_information_cutoff", "selection_information_cutoff")}
            if times["as_of"] > times["available_at"] or times["information_cutoff"] > times["as_of"]:
                raise ValueError("Beta state/source cutoff is after its availability")
            for key in ("training_information_cutoff", "calibration_information_cutoff", "selection_information_cutoff"):
                if times[key] >= times["as_of"] or times[key] > times["information_cutoff"]:
                    raise ValueError(f"Beta {key} must precede the historical prediction")
            provenance = record.get("macro_provenance")
            if not isinstance(provenance, list) or not provenance:
                raise ValueError("Beta needs actual PIT macro source/vintage provenance")
            if require_verified_macro_vintages:
                validate_macro_vintage_admission(provenance)
            for source in provenance:
                if not isinstance(source, Mapping):
                    raise ValueError("Beta macro provenance entries must be objects")
                parsed_url = urlparse(str(source.get("source_url", "")))
                host = parsed_url.hostname
                if parsed_url.scheme != "https" or parsed_url.username or parsed_url.password:
                    raise ValueError("Beta macro provenance needs a credential-free HTTPS source URL")
                if any(key.lower() in {"api_key", "key", "token", "access_token", "secret"}
                       for key, _ in parse_qsl(parsed_url.query)):
                    raise ValueError("Beta provenance URL must not contain provider credentials")
                if host not in {"api.stlouisfed.org", "fred.stlouisfed.org", "alfred.stlouisfed.org"}:
                    raise ValueError("Beta macro provenance needs official FRED/ALFRED source URL")
                if not source.get("series_id") or not source.get("realtime_start"):
                    raise ValueError("Beta macro provenance lacks series/vintage identity")
                source_available = _stamp(source["available_at"])
                vintage = date.fromisoformat(str(source["realtime_start"]))
                if vintage > times["information_cutoff"].date() or vintage > source_available.date():
                    raise ValueError("Beta macro vintage is later than claimed availability/cutoff")
                if source.get("realtime_end") is not None and date.fromisoformat(str(source["realtime_end"])) < vintage:
                    raise ValueError("Beta macro vintage end precedes vintage start")
                if source.get("observation_end") is not None and _stamp(source["observation_end"]) > source_available:
                    raise ValueError("Beta actual observation ends after claimed availability")
                if source_available > times["information_cutoff"]:
                    raise ValueError("Beta macro source was unavailable at its information cutoff")
            p_raw = record.get("probabilities")
            if isinstance(p_raw, dict):
                p = np.array([p_raw.get(name, p_raw.get("p_" + name, np.nan)) for name in REGIME_NAMES], dtype=float)
            else:
                p = np.asarray(p_raw, dtype=float)
            if p.shape != (4,) or not np.isfinite(p).all() or (p < 0).any() or not np.isclose(p.sum(), 1, atol=1e-8):
                raise ValueError("Beta requires four finite nonnegative normalized probabilities")
            accepted.append({"record": record, "times": times, "p": p})
        except (ValueError, TypeError, KeyError, OverflowError) as exc:
            rejected.append({"record_index": number, "reason": str(exc)})
    accepted.sort(key=lambda x: (x["times"]["available_at"], str(x["record"]["artifact_id"])))
    return accepted, rejected


def _beta_at(accepted: list[dict], cutoff, index: pd.DatetimeIndex, max_age: int):
    eligible = [r for r in accepted if r["times"]["available_at"] <= _stamp(cutoff)]
    if not eligible:
        return None
    row = eligible[-1]
    age = int(index.searchsorted(_stamp(cutoff).tz_convert(None), side="right") -
              index.searchsorted(row["times"]["as_of"].tz_convert(None), side="right"))
    return None if age > max_age else row


def _ridge(X: np.ndarray, y: np.ndarray, ridge: float, weights: np.ndarray):
    mean = np.average(X, axis=0, weights=weights)
    scale = np.sqrt(np.average((X - mean) ** 2, axis=0, weights=weights))
    scale = np.where(scale > 1e-8, scale, 1.0)
    z = np.clip((X - mean) / scale, -8, 8)
    design = np.column_stack([np.ones(len(z)), z])
    reg = np.eye(design.shape[1]) * ridge
    reg[0, 0] = 0
    gram = design.T @ (design * weights[:, None]) + reg
    coef = np.linalg.solve(gram, design.T @ (y * weights))
    return coef, mean, scale


def _alpha_health(slope: float, centered_scores: np.ndarray,
                  direct_alpha: np.ndarray, correction: np.ndarray) -> dict[str, Any]:
    """Describe the actual issued components; never fit or modify predictions."""
    score_rms = float(np.sqrt(np.mean(centered_scores**2)))
    nonzero = int(np.count_nonzero(direct_alpha))
    coefficient_zero = bool(slope == 0.0)
    flat_scores = bool(np.count_nonzero(centered_scores) == 0)
    reason = None
    if nonzero == 0:
        if coefficient_zero and flat_scores:
            reason = "zero_calibration_coefficient_and_flat_scores"
        elif coefficient_zero:
            reason = "zero_calibration_coefficient"
        elif flat_scores:
            reason = "flat_scores"
        else:
            reason = "all_zero_product_without_zero_coefficient_or_flat_scores"
    return {
        "calibration_coefficient": float(slope),
        "centered_rank_dispersion": score_rms,
        "centered_rank_dispersion_definition": "population_RMS_of_current_centered_rank_scores",
        "direct_alpha_rms": float(np.sqrt(np.mean(direct_alpha**2))),
        "direct_alpha_nonzero_count": nonzero,
        "eligible_asset_count": int(len(direct_alpha)),
        "residual_rms": float(np.sqrt(np.mean(correction**2))),
        "direct_alpha_zero_reason": reason,
        "zero_test": "exact_float_zero_without_tolerance",
    }


def _forecast_readiness(records: list[dict], sessions: pd.DatetimeIndex,
                        allowed: set | None, early_blocks: list[dict],
                        skipped: list[dict]) -> dict[str, Any]:
    """Add source/readiness explanations without changing the legacy status."""
    ready = [r for r in records if r["metadata"]["residual_status"] == "bounded_ridge_OOF_residual"]
    panel_dates = {_stamp(day) for day in sessions}
    requested = panel_dates if allowed is None else allowed
    absent = sorted(requested - panel_dates)
    counts: dict[str, int] = {}
    for row in early_blocks + skipped:
        reason = row["reason"]
        counts[reason] = counts.get(reason, 0) + 1
    if absent:
        counts["requested_date_absent_from_panel"] = len(absent)
    return {
        "status": "residual_model_ready" if ready else "warmup_only" if records else "no_emitted_forecasts",
        "scope": "emitted_forecast_residual_readiness_only; not_QPO_ingress_or_risk_approval",
        "requested_origin_count": len(requested),
        "emitted_records": len(records), "residual_ready_records": len(ready),
        "warmup_records": len(records) - len(ready),
        "first_residual_ready_origin": ready[0]["metadata"]["target_origin"] if ready else None,
        "last_residual_ready_origin": ready[-1]["metadata"]["target_origin"] if ready else None,
        "blocked_reason_counts": counts,
        "requested_dates_absent_from_panel": [day.isoformat() for day in absent],
        "strict_ingress_checks_remaining": ["exactly_one_fresh_forecast_at_entry",
            "sufficient_matured_matched_error_records", "source_horizon_calendar_identity",
            "configured_required_sleeves", "downstream_QRM_and_optional_risk_gates"],
    }


def _sec_diagnostic_snapshot(sec, names, cutoff_pos, cutoff, *, residual_usage=False):
    """As-of raw extra factors only: no role in current forecast or eligibility."""
    out = {}
    day = pd.Timestamp(cutoff).normalize()
    for name, panel in sec.get("diagnostic_features", {}).items():
        values = panel.iloc[cutoff_pos].reindex(names)
        rows = {}
        for symbol in names:
            value = values[symbol]
            known = [event for event in sec.get("feature_events", {}).get(symbol, {}).get(name, [])
                     if pd.Timestamp(event["eligible_session"]) <= day]
            latest = max(known, key=lambda event: event["eligible_session"]) if known else None
            rows[symbol] = {
                "value": float(value) if np.isfinite(value) else None,
                "status": "observed" if np.isfinite(value) else "unavailable",
                "feature_state_id": latest.get("feature_state_id") if latest else None,
                "eligible_session": latest.get("eligible_session") if latest else None,
            }
        out[name] = rows
    lineage = {}
    for symbol in names:
        components = {}
        for feature in (*QUALITY_FEATURES, *VALUE_FEATURES):
            known = [event for event in sec.get("feature_events", {}).get(symbol, {}).get(feature, [])
                     if pd.Timestamp(event["eligible_session"]) <= day]
            if known:
                latest = max(known, key=lambda event: event["eligible_session"])
                components[feature] = {"feature_state_id": latest["feature_state_id"],
                                       "resolved_status": latest["resolved_status"],
                                       "eligible_session": latest["eligible_session"]}
        if components:
            lineage[symbol] = components
    return {"usage": ("extra raw factors enter the separately selected bounded residual design; no change to direct Alpha priors" if residual_usage else "extra raw factors are diagnostic only; component lineage documents existing quality/value inputs"),
            "features": out, "quality_value_component_lineage": lineage}


def build_gamma_forecast_records(
    closes: pd.DataFrame, *, config: GammaForecastConfig | None = None,
    beta_records: list[dict[str, Any]] | None = None,
    sec_cache_directory: str | Path | None = None,
    sec_histories: dict[str, pd.DataFrame] | None = None,
    decision_dates: Iterable[Any] | None = None,
) -> dict[str, Any]:
    """Generate fresh absolute period forecasts and matching realized error rows.

    Optional sources never turn into invented neutral observations: values are
    masked with explicit observed flags and sleeve status. Required unavailable
    sources emit no record. Every relative-mean map and reference forecast uses
    complete target baskets matured by origin-minus-feature_lag. The residual
    fit uses historical *issued* baseline predictions, not retrospective fits.
    Future outcomes are attached only with their actual maturity date; consumers
    must exclude them until then. No whole-sample model/feature selection occurs.
    """
    cfg = config or GammaForecastConfig()
    if not isinstance(closes, pd.DataFrame) or closes.empty:
        raise ValueError("Gamma requires a non-empty close panel")
    if not isinstance(closes.index, pd.DatetimeIndex) or closes.index.has_duplicates or not closes.index.is_monotonic_increasing:
        raise ValueError("Gamma requires unique increasing trading sessions")
    if closes.index.tz is not None:
        raise ValueError("Gamma session panel must use timezone-naive exchange-session dates")
    if closes.columns.has_duplicates:
        raise ValueError("Gamma symbols must be unique")
    px = closes.astype(float).copy()
    symbols = [str(x).upper().strip() for x in px.columns]
    if len(set(symbols)) != len(symbols) or any(not s for s in symbols):
        raise ValueError("Gamma normalized symbols must be nonempty and unique")
    px.columns = symbols
    arr = px.to_numpy()
    if np.isinf(arr).any() or (arr[np.isfinite(arr)] <= 0).any():
        raise ValueError("Gamma observed prices must be positive and finite; missing values remain missing")
    n, count = px.shape
    h, lag = cfg.horizon_sessions, cfg.feature_lag_sessions
    index = px.index
    allowed = None if decision_dates is None else {_stamp(x) for x in decision_dates}
    sec = load_cached_sec_context(symbols, index, cache_directory=sec_cache_directory, histories=sec_histories,
                                  semantics_version=cfg.sec_semantics_version)
    beta, beta_rejected = _validated_beta_records(beta_records,
        require_verified_macro_vintages=cfg.require_verified_macro_vintages)
    returns = px.pct_change(fill_method=None)
    market = returns.mean(axis=1, skipna=True)
    alpha_cfg = AlphaV1Config(enabled=True, horizon_sessions=h)
    features = _build_features(px, market, alpha_cfg, sector_labels=None,
                               value_scores=sec["value"], quality_scores=sec["quality"],
                               external_panel_policy="resolved_dense_v1" if cfg.sec_semantics_version == "strict_v1" else "legacy_ffill")
    # Reuse actual Alpha economic feature equations, but a frozen-prior aggregate
    # avoids calling a legacy full-panel API that requires future-complete prices.
    momentum_names = [name for name in features if name not in {"value", "quality", "sector_relative"}]
    feature_names = momentum_names + ["value", "quality"]
    panels = [features.get(name, pd.DataFrame(np.nan, index=index, columns=symbols)).to_numpy() for name in feature_names]
    cube = np.stack(panels, axis=-1)
    priors = np.array([_PRIORS[name] for name in feature_names])
    available = np.isfinite(cube)
    denominator = (available * priors).sum(axis=2)
    composite = np.divide((np.nan_to_num(cube) * priors).sum(axis=2), denominator,
                          out=np.full((n, count), np.nan), where=denominator > 0)
    history_count = returns.rolling(cfg.minimum_history_sessions, min_periods=cfg.minimum_history_sessions).count().to_numpy()
    known_eligible = (history_count >= cfg.minimum_history_sessions) & np.isfinite(px.to_numpy())
    # Required price sleeves must all exist; optional fundamental missingness is
    # represented separately rather than deciding universe from future coverage.
    known_eligible &= available[:, :, :len(momentum_names)].all(axis=2)
    rank = pd.DataFrame(composite, index=index, columns=symbols).where(known_eligible).rank(axis=1, pct=True).mul(2).sub(1).to_numpy()
    feature_ranks = np.stack([pd.DataFrame(panel,index=index,columns=symbols).where(known_eligible)
        .rank(axis=1,pct=True).mul(2).sub(1).to_numpy() for panel in panels],axis=-1)
    origin_eligible = np.zeros((n, count), dtype=bool)
    score = np.full((n, count), np.nan)
    for origin in range(lag, n):
        origin_eligible[origin] = known_eligible[origin-lag]
        score[origin] = rank[origin-lag]
    forward = np.full((n, count), np.nan)
    if n > h:
        forward[:-h] = px.iloc[h:].to_numpy() / px.iloc[:-h].to_numpy() - 1.0
    relative_y = np.full_like(forward, np.nan)
    benchmark_y = np.full(n, np.nan)
    complete_label = np.zeros(n, dtype=bool)
    for origin in range(n-h):
        mask = origin_eligible[origin]
        # Never silently change q after seeing a delisting/missing future exit.
        if mask.sum() >= cfg.minimum_assets and np.isfinite(forward[origin, mask]).all():
            benchmark_y[origin] = float(np.mean(forward[origin, mask]))
            relative_y[origin, mask] = forward[origin, mask] - benchmark_y[origin]
            complete_label[origin] = True

    # Feature skill is calculated only from labels matured at the current cutoff.
    feature_ics = np.full((n,len(feature_names)),np.nan)
    for j in np.flatnonzero(complete_label):
        for f in range(len(feature_names)):
            valid = origin_eligible[j] & np.isfinite(feature_ranks[j-lag,:,f])
            if valid.sum() >= cfg.minimum_assets:
                x = feature_ranks[j-lag,valid,f]
                y = pd.Series(relative_y[j,valid]).rank().to_numpy()
                if np.std(x)>1e-12 and np.std(y)>1e-12:
                    feature_ics[j,f] = float(np.corrcoef(x,y)[0,1])
    base_mean = np.full((n, count), np.nan)
    base_scale = np.full(n, np.nan)
    reference_mean = np.full(n, np.nan)
    designs: dict[int, np.ndarray] = {}
    records, skipped = [], []
    readiness_blocks = [{"target_origin": str(index[pos]), "reason": "insufficient_feature_lag_history"}
                        for pos in range(min(lag, n))
                        if allowed is None or _stamp(index[pos]) in allowed]
    support = {"beta_rows": 0, "sec_rows": 0, "residual_rows": 0}
    sec_quality_rows = sec_value_rows = 0
    fit_spec_hash = _json_hash(asdict(cfg))

    for origin in range(lag, n):
        cutoff_pos = origin-lag
        cutoff = index[cutoff_pos]
        mask = origin_eligible[origin]
        names = [symbols[j] for j in np.flatnonzero(mask)]
        if mask.sum() < cfg.minimum_assets:
            if allowed is None or _stamp(index[origin]) in allowed:
                readiness_blocks.append({"target_origin": str(index[origin]),
                                         "reason": "insufficient_eligible_assets_or_Alpha_history"})
            continue
        first_train = max(0, cutoff_pos - cfg.training_window_sessions + 1)
        last_train = cutoff_pos-h  # inclusive: outcome close must already exist
        train = [j for j in range(first_train, last_train+1) if complete_label[j]]
        if len(train) < cfg.min_calibration_days:
            if allowed is None or _stamp(index[origin]) in allowed:
                readiness_blocks.append({"target_origin": str(index[origin]),
                                         "reason": "insufficient_completed_calibration_dates"})
            continue
        adaptive_weights = priors/priors.sum()
        per_feature_ic = []
        for f in range(len(feature_names)):
            values = feature_ics[train,f]; valid_ic=values[np.isfinite(values)]
            per_feature_ic.append(float(valid_ic.mean()) if len(valid_ic)>=cfg.min_calibration_days else 0.0)
        if cfg.alpha_weighting == "matured_feature_ic":
            positive = np.maximum(per_feature_ic,0.0)
            # Fixed 50% shrinkage to economic priors; no selection on evaluation
            # returns and no positive floor on the calibrated Alpha coefficient.
            if positive.sum()>1e-12:
                adaptive_weights = .5*adaptive_weights+.5*positive/positive.sum()
            values=cube[cutoff_pos]
            observed=np.isfinite(values)
            denom=(observed*adaptive_weights).sum(axis=1)
            current_composite=np.divide((np.nan_to_num(values)*adaptive_weights).sum(axis=1),denom,
                out=np.full(count,np.nan),where=denom>0)
            score[origin] = pd.Series(current_composite).where(mask).rank(pct=True).mul(2).sub(1).to_numpy()
        slopes = []
        signed_slopes = []
        for j in train:
            valid = origin_eligible[j]
            x = score[j, valid]
            x = x-x.mean()
            y = relative_y[j, valid]
            signed_slopes.append(float(x @ y / (x @ x + cfg.ridge_penalty)))
            slopes.append(max(0.0, signed_slopes[-1]))
        slope = float(np.median(slopes))
        current_score = score[origin, mask]
        base_mean[origin, mask] = slope * (current_score - current_score.mean())
        # Explicit separately fitted fixed-start-basket reference mean. This is
        # an economic shrinkage assumption, not an assertion of predictive edge.
        reference_mean[origin] = cfg.reference_shrinkage * float(np.mean(benchmark_y[train]))

        previous = [j for j in train if np.isfinite(base_mean[j, origin_eligible[j]]).all()]
        # Truly OOF baseline residuals only. Before support exists, export the
        # mapped Alpha/reference mean with residual refinement unavailable.
        if len(previous) >= cfg.min_residual_days:
            day_mse = [float(np.mean((relative_y[j, origin_eligible[j]] - base_mean[j, origin_eligible[j]])**2)) for j in previous]
            base_scale[origin] = max(float(np.sqrt(np.mean(day_mse))), cfg.scale_floor)
        else:
            # Scale unavailable is not a floor pretending to be calibrated.
            base_scale[origin] = np.nan

        beta_row = _beta_at(beta, cutoff, index, cfg.beta_max_age_sessions)
        bp = np.zeros(4) if beta_row is None else beta_row["p"]
        sec_q = sec["quality"].iloc[cutoff_pos].to_numpy()[mask]
        sec_v = sec["value"].iloc[cutoff_pos].to_numpy()[mask]
        sec_observed = np.isfinite(sec_q) | np.isfinite(sec_v)
        observed_beta = float(beta_row is not None)
        raw_features = cube[cutoff_pos, mask, :]
        observed_features = np.isfinite(raw_features)
        centered_raw = current_score - current_score.mean()
        design = np.column_stack([
            np.nan_to_num(raw_features), observed_features.astype(float),
            np.tile(bp, (len(names), 1)), np.full(len(names), observed_beta),
            centered_raw[:, None] * bp[None, :],
        ])
        if cfg.sec_diagnostic_residual_features:
            from ..sec_pit_strict import DIAGNOSTIC_FEATURES
            extra=np.column_stack([sec.get("diagnostic_features",{}).get(name,
                pd.DataFrame(np.nan,index=index,columns=symbols)).iloc[cutoff_pos].to_numpy()[mask]
                for name in DIAGNOSTIC_FEATURES])
            design=np.column_stack([design,np.nan_to_num(extra),np.isfinite(extra).astype(float)])
        designs[origin] = design
        # Future residual fits use these daily baseline/scale/design rows, not
        # final corrected predictions. Avoid fitting a model on non-output days.
        if allowed is not None and _stamp(index[origin]) not in allowed:
            continue
        if cfg.require_beta and beta_row is None:
            if allowed is None or _stamp(index[origin]) in allowed:
                skipped.append({"target_origin": str(index[origin]), "reason": "required_PIT_Beta_unavailable"})
            continue
        if cfg.require_sec and not sec_observed.all():
            if allowed is None or _stamp(index[origin]) in allowed:
                skipped.append({"target_origin": str(index[origin]), "reason": "required_SEC_missing_for_eligible_assets"})
            continue

        correction = np.zeros(len(names))
        residual_status = "warmup_mapped_Alpha_only"
        residual_train = [j for j in previous if j in designs and np.isfinite(base_scale[j])]
        if len(residual_train) >= cfg.min_residual_days and np.isfinite(base_scale[origin]):
            X = np.concatenate([designs[j] for j in residual_train])
            Y = np.concatenate([(relative_y[j, origin_eligible[j]] - base_mean[j, origin_eligible[j]]) / base_scale[j] for j in residual_train])
            # Equal date weights, then equal asset weights within each date.
            weights = np.concatenate([np.full(len(designs[j]), 1.0/len(designs[j])) for j in residual_train])
            coef, xm, xs = _ridge(X, Y, cfg.ridge_penalty, weights)
            z = np.column_stack([np.ones(len(design)), np.clip((design-xm)/xs, -8, 8)])
            correction = cfg.residual_kappa * base_scale[origin] * np.tanh(z @ coef)
            correction -= correction.mean()
            residual_status = "bounded_ridge_OOF_residual"
        relative = base_mean[origin, mask] + correction
        relative -= relative.mean()
        absolute = relative + reference_mean[origin]
        if not np.isfinite(absolute).all() or (absolute <= -1).any():
            skipped.append({"target_origin": str(index[origin]), "reason": "invalid_absolute_forecast"})
            continue
        if allowed is not None and _stamp(index[origin]) not in allowed:
            continue
        known_facts = {symbol: [f for f in sec.get("facts_by_symbol", {}).get(symbol, [])
                                if pd.Timestamp(f.eligible_session) <= cutoff] for symbol in names}
        sec_provenance = {}
        for symbol, facts in known_facts.items():
            if facts:
                latest = max(facts, key=lambda f: (f.eligible_session, f.filed))
                sec_provenance[symbol] = {
                    **sec.get("sources", {}).get(symbol, {}), "eligible_fact_count": len(facts),
                    "latest_eligible_session": latest.eligible_session,
                    "latest_publication": latest.accepted or latest.filed,
                    "availability_basis": latest.acceptance_source,
                    "accession": latest.accession,
                }
        beta_meta = None if beta_row is None else {key: beta_row["record"][key] for key in (
            "artifact_id", "as_of", "available_at", "information_cutoff", "training_information_cutoff",
            "calibration_information_cutoff", "selection_information_cutoff", "macro_provenance")}
        if beta_meta is not None:
            for key in ("source_lineage_schema", "source_prefix_sha256", "market_provenance",
                        "artifact_sha256", "feature_schema_hash", "code_hash", "model_version",
                        "research_reconstruction", "generated_at", "availability_semantics",
                        "timestamp_semantics", "candidate", "calibrated", "calibration_method"):
                if key in beta_row["record"]:
                    beta_meta[key] = beta_row["record"][key]
        maturity = index[max(train)+h]
        record = {
            "model_id": GAMMA_PRODUCER, "model_version": GAMMA_VERSION,
            "created_at": index[origin].isoformat(), "information_cutoff": cutoff.isoformat(),
            "horizon_sessions": h, "units": "period_return",
            "values": dict(zip(names, map(float, absolute))),
            "metadata": {
                "producer": GAMMA_PRODUCER, "target_type": "absolute", "return_convention": "close_to_close",
                "target_origin": index[origin].isoformat(),
                "target_end": index[origin+h].isoformat() if origin+h < n else None,
                "feature_lag_sessions": lag,
                "training_information_cutoff": maturity.isoformat(), "reference_training_cutoff": maturity.isoformat(),
                "reference_definition": "fixed_start_equal_weight_eligible_universe",
                "reference_weights": dict(zip(names, [1.0/len(names)]*len(names))),
                "reference_mean": float(reference_mean[origin]), "reference_shrinkage": cfg.reference_shrinkage,
                "relative_means": dict(zip(names, map(float, relative))),
                "alpha_prior_means": dict(zip(names, map(float, base_mean[origin, mask]))),
                "residual_corrections": dict(zip(names, map(float, correction))),
                "alpha_health": _alpha_health(slope, centered_raw, base_mean[origin, mask], correction),
                "alpha_source": "implemented_Alpha_economic_features_frozen_prior_rank" if cfg.alpha_weighting=="frozen_prior" else "implemented_Alpha_economic_features_matured_feature_ic",
                "alpha_calibration_diagnostics": {
                    "signed_slope_median":float(np.median(signed_slopes)),
                    "nonpositive_slope_fraction":float(np.mean(np.asarray(signed_slopes)<=0)),
                    "calibration_date_count":len(train),
                    "overlapping_horizon_sessions":h,
                    "independent_sample_count_claim":False,
                    "per_feature_matured_mean_rank_ic":dict(zip(feature_names,per_feature_ic)),
                    "feature_weights":dict(zip(feature_names,map(float,adaptive_weights))),
                    "adaptation":"fixed_50pct_prior_shrinkage_to_positive_matured_IC" if cfg.alpha_weighting!="frozen_prior" else "none",
                    "latest_label_maturity":maturity.isoformat(),
                    "signed_slopes_force_positive":False},
                "sec_diagnostic_residual_features":cfg.sec_diagnostic_residual_features,
                "alpha_feature_names": feature_names, "calibration_days": len(train),
                "residual_training_days": len(residual_train), "residual_status": residual_status,
                "residual_fit_objective": "date_equal_weight_ridge_proxy_on_standardized_OOF_baseline_errors_then_bounded_tanh; not exact_loss_minimization_for_final_mean",
                "OOF_baseline_error_scale": None if not np.isfinite(base_scale[origin]) else float(base_scale[origin]),
                "scale_meaning": "past_OOF_relative_residual_RMS_diagnostic_not_Student_t_scale_or_joint_covariance",
                "beta_status": "PIT_supplied" if beta_row is not None else "unavailable",
                "beta_provenance": beta_meta,
                "macro_vintage_admission": {
                    "required": cfg.require_verified_macro_vintages,
                    "scope": "forecast_Beta_declared_macro_provenance_flags_only",
                    "source_authenticity_independently_verified": False,
                    "whole_prefix_vintages_independently_verified": False,
                    "market_adjustment_vintages_independently_verified": False,
                    "separate_Risk205_Beta_selection_covered": False,
                },
                "sec_status": "available" if sec_observed.all() else "partial" if sec_observed.any() else "unavailable",
                "sec_observed_symbols": [s for s, seen in zip(names, sec_observed) if seen],
                "sec_quality_observed_symbols": [s for s, seen in zip(names, np.isfinite(sec_q)) if seen],
                "sec_value_observed_symbols": [s for s, seen in zip(names, np.isfinite(sec_v)) if seen],
                "sec_provenance": sec_provenance, "sec_network_used": False,
                "sec_semantics_version": cfg.sec_semantics_version,
                "sec_semantics_revision": cfg.sec_semantics_revision,
                "sec_extra_features": _sec_diagnostic_snapshot(sec, names, cutoff_pos, cutoff, residual_usage=cfg.sec_diagnostic_residual_features),
                "config_fingerprint": fit_spec_hash, "research_only": True,
                "execution_assumption": "entry at close t with all decision inputs through t-lag; execution requires matching one-session-lag QPO mode",
                "session_timestamp_semantics": "price-panel timestamps label exchange sessions, not an attestation that closing prices existed at midnight; all such bars precede target-entry session",
            },
        }
        if complete_label[origin]:
            record["realized_outcome"] = dict(zip(names, map(float, forward[origin, mask])))
            record["realized_at"] = index[origin+h].isoformat()
        records.append(record)
        support["beta_rows"] += int(beta_row is not None)
        support["sec_rows"] += int(sec_observed.any())
        sec_quality_rows += int(np.isfinite(sec_q).any())
        sec_value_rows += int(np.isfinite(sec_v).any())
        support["residual_rows"] += int(residual_status == "bounded_ridge_OOF_residual")
    return {"records": records, "diagnostics": {
        "status": "ready_research" if records else "not_ready_no_compatible_forecasts",
        "producer": GAMMA_PRODUCER, "model_version": GAMMA_VERSION, "config": asdict(cfg),
        "records_emitted": len(records), "optional_sleeve_use": support,
        "readiness": _forecast_readiness(records, index, allowed, readiness_blocks, skipped),
        "sleeves": {
            "alpha": {"source": "implemented_Alpha_economic_features_"+cfg.alpha_weighting,
                      "records_emitted": len(records)},
            "beta": {"required": cfg.require_beta, "records_supplied": len(beta)+len(beta_rejected),
                     "records_accepted": len(beta), "forecast_records_using_state": support["beta_rows"],
                     "require_verified_macro_vintages": cfg.require_verified_macro_vintages,
                     "vintage_admission_scope": "all_declared_forecast_macro_provenance_entries; supplied_metadata_only; not_Risk205_or_market_vintage_attestation",
                     "status": "observed" if support["beta_rows"] else "unavailable"},
            "sec_quality": {"cache_requested": sec_cache_directory is not None,
                            "forecast_records_using_quality": sec_quality_rows,
                            "status": "observed" if sec_quality_rows else "unavailable"},
            "sec_value": {"cache_requested": sec_cache_directory is not None,
                          "verified_unadjusted_history_symbols": sec["metadata"].get("value_price_basis_verified_symbols", []),
                          "forecast_records_using_value": sec_value_rows,
                          "status": "observed" if sec_value_rows else "unavailable"},
            "sec_requirement": "quality_or_value_must_be_observed_for_each_eligible_asset" if cfg.require_sec else "optional_observed_only",
            "coverage_scope": "all_emitted_forecast_records_including_requested_historical_ledger_origins",
        },
        "beta_records_accepted": len(beta), "beta_records_rejected": beta_rejected,
        "sec_source": sec["metadata"], "skipped": skipped,
        "sec_feature_lineage": {"scope": "source-panel diagnostics; each forecast references only states eligible at its cutoff",
                                "feature_states": sec.get("feature_states", {})},
        "target": "absolute_fixed_entry_close_to_horizon_close_simple_return",
        "fallback": "none; no record means Gamma must fail closed, never substitute a trailing-return proxy",
        "limitations": ["research model; no confirmatory holdout/promotion claim", "no automatic FRED/SEC network collection",
                        "Beta provenance fields are validated but source authenticity is not independently attested",
                        "date-only cutoff is literal 00:00 UTC; later same-date intraday states are conservatively excluded",
                        "Alpha market features use available-name daily returns; reference target is separately fixed per origin",
                        "no inferred delisting proceeds; incomplete future baskets excluded from training as whole dates",
                        "static supplied candidate-universe research only; survivorship bias unresolved; dynamic universe snapshots are unsupported and must fail closed before this adapter"],
    }}
