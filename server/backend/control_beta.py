"""Operational Beta / Model C integration helpers for private Oryntra Control.

The integration is fail-closed: V2.0.4 receives Beta only from a genuine
production-frozen artifact. Missing, stale, uncalibrated, or low-quality Beta
state never increases risk and leaves the V2.0.3 baseline untouched.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Any

import pandas as pd

from .beta_v1 import BetaV1Config, beta_v1_alpha_context, beta_v1_contract, beta_v1_risk_payload, beta_v1_service
from .models.beta.features import BetaFeatureEngine

_SERVICE = None


def beta_root() -> Path:
    return Path(os.getenv("ORYNTRA_BETA_ROOT", str(Path(__file__).resolve().parents[1] / "data" / "beta_v1")))


def _fred_key_for_user(user_id: int | None = None) -> str | None:
    key = os.getenv("FRED_API_KEY")
    if key or user_id is None:
        return key
    try:
        from .provider_credentials import decrypted_credentials
        return decrypted_credentials(int(user_id)).get("fred")
    except Exception:
        return None


def beta_service(user_id: int | None = None):
    global _SERVICE
    key = _fred_key_for_user(user_id)
    if _SERVICE is None:
        _SERVICE = beta_v1_service(
            beta_root(), fred_api_key=key, strict_pit=True,
            config=BetaV1Config(enabled=True),
        )
    else:
        _SERVICE.macro.fred_api_key = key
    return _SERVICE


def _artifact_sha(service) -> str | None:
    if service.frozen_path.exists():
        return hashlib.sha256(service.frozen_path.read_bytes()).hexdigest()
    return None


def beta_operational_status(user_id: int | None = None) -> dict[str, Any]:
    service = beta_service(user_id)
    status = service.artifact_status()
    return {
        "model": beta_v1_contract(BetaV1Config(enabled=True)),
        "artifact_status": status.to_dict(),
        "policy": service.grade(),
        "state": None,
        "artifact_sha256": _artifact_sha(service),
        "fred_key_configured": bool(_fred_key_for_user(user_id)),
        "risk_v204_consumable": False,
        "risk_v204_research_consumable": bool(status.trained and status.candidate_diagnostics_match),
        "fallback": "Production Universal Risk V2.0.4 uses its V2.0.3 baseline until a valid production-frozen Beta state is available; interactive Research may use the current audited candidate separately.",
    }


def frozen_beta_state_panel(sessions: pd.DatetimeIndex | None = None, *, user_id: int | None = None) -> tuple[pd.DataFrame | None, dict[str, Any]]:
    """Return persisted OOS Beta states only for a production-frozen artifact."""
    status = beta_operational_status(user_id)
    service = beta_service(user_id)
    if not bool((status.get("artifact_status") or {}).get("production_frozen")):
        return None, status
    if not service.oos_path.exists():
        status["fallback"] = "Production artifact exists but the audited OOS state ledger is unavailable."
        return None, status
    try:
        frame = pd.read_csv(service.oos_path, index_col=0, parse_dates=True).sort_index()
        idx = pd.DatetimeIndex(frame.index)
        if idx.tz is not None:
            idx = idx.tz_localize(None)
        frame.index = idx.normalize()
        frame = frame[~frame.index.duplicated(keep="last")]
        frame["production_frozen"] = True
        frame["artifact_sha256"] = status.get("artifact_sha256")
        if sessions is not None and len(sessions):
            start, end = pd.Timestamp(sessions.min()).normalize(), pd.Timestamp(sessions.max()).normalize()
            frame = frame.loc[(frame.index >= start) & (frame.index <= end)]
        status["persisted_state_rows"] = int(len(frame))
        return (frame if len(frame) else None), status
    except Exception as exc:
        status["fallback"] = f"Beta state ledger could not be loaded: {type(exc).__name__}: {exc}"
        return None, status




def research_beta_state_panel(sessions: pd.DatetimeIndex | None = None, *, user_id: int | None = None) -> tuple[pd.DataFrame | None, dict[str, Any]]:
    """Return the current candidate OOS state ledger for interactive research only.

    Unlike ``frozen_beta_state_panel`` this does not require production freeze.
    It still requires a current trained artifact whose diagnostics match, and it
    never changes the production consumption contract.
    """
    status = beta_operational_status(user_id)
    service = beta_service(user_id)  # also refreshes the saved per-user FRED key
    artifact = status.get("artifact_status") or {}
    status["research_candidate_panel"] = True
    if not artifact.get("trained"):
        status["research_panel_error"] = "Beta candidate is not trained"
        return None, status
    if not artifact.get("candidate_diagnostics_match"):
        status["research_panel_error"] = "Beta candidate/diagnostics mismatch"
        return None, status
    if not service.oos_path.exists():
        status["research_panel_error"] = "Audited Beta OOS state ledger is unavailable"
        return None, status
    try:
        frame = service.oos_states().copy().sort_index()
        idx = pd.DatetimeIndex(frame.index)
        if idx.tz is not None:
            idx = idx.tz_localize(None)
        frame.index = idx.normalize()
        frame = frame[~frame.index.duplicated(keep="last")].sort_index()
        frame["production_frozen"] = False
        diagnostics = service.diagnostics()
        prior = (((diagnostics.get("final_holdout") or {}).get("baselines") or {}).get("train_class_prior") or {}).get("probabilities")
        neutral_probs = None
        if isinstance(prior, (list, tuple)) and len(prior) == 4:
            try:
                values = [float(x) for x in prior]
                total = sum(values)
                if all(x >= 0 for x in values) and total > 0:
                    neutral_probs = [x / total for x in values]
            except Exception:
                neutral_probs = None
        frame.attrs.update({
            "production_frozen": False,
            "research_candidate": True,
            "artifact_sha256": artifact.get("candidate_sha256"),
            "artifact_id": artifact.get("candidate_artifact_id"),
            "research_neutral_probabilities": neutral_probs,
            "research_neutral_source": "train_class_prior" if neutral_probs is not None else "uniform_four_state_fallback",
        })
        if sessions is not None and len(sessions):
            start, end = pd.Timestamp(sessions.min()).normalize(), pd.Timestamp(sessions.max()).normalize()
            # Keep the last pre-window state so the first researched session can
            # causally inherit the most recent known regime.
            pre = frame.loc[frame.index < start].tail(1)
            within = frame.loc[(frame.index >= start) & (frame.index <= end)]
            frame = pd.concat([pre, within]).sort_index()
            frame = frame[~frame.index.duplicated(keep="last")]
        status["persisted_state_rows"] = int(len(frame))
        status["risk_v204_research_consumable"] = bool(len(frame))
        status["research_panel_error"] = None
        return (frame if len(frame) else None), status
    except Exception as exc:
        status["research_panel_error"] = f"{type(exc).__name__}: {exc}"
        return None, status

def required_market_tickers() -> list[str]:
    return list(BetaFeatureEngine.MARKET_TICKERS)


def _close(frame: pd.DataFrame, symbol: str) -> tuple[pd.Series, str]:
    for column, basis in (("Adj Close", "adjusted_close"), ("Adjusted Close", "adjusted_close"),
                          ("Total Return Close", "total_return")):
        if column in frame.columns:
            return pd.to_numeric(frame[column], errors="coerce").rename(symbol), basis
    if "Close" in frame.columns:
        declared = str(frame.attrs.get("price_basis") or "").lower()
        meta = frame.attrs.get("oryntra_metadata") or {}
        adjusted = meta.get("adjusted") is True or declared in {"adjusted", "adjusted_close", "total_return"}
        return pd.to_numeric(frame["Close"], errors="coerce").rename(symbol), ("adjusted_close" if adjusted else "unadjusted_close")
    raise ValueError(f"{symbol} has no supported close column")


def beta_current_status_from_histories(histories: dict[str, pd.DataFrame], universe: list[str], *, user_id: int | None = None) -> dict[str, Any]:
    """Infer the latest Beta state from already-fetched Control market histories."""
    status = beta_operational_status(user_id)
    artifact = status.get("artifact_status") or {}
    if not artifact.get("trained"):
        status["research_state_available"] = False
        status["next_action"] = "Sync real FRED/ALFRED PIT macro data and run the Beta research study."
        return status
    try:
        market_series = []; bases = {}
        for symbol in required_market_tickers():
            if symbol not in histories:
                raise ValueError(f"missing Beta market context {symbol}")
            close, basis = _close(histories[symbol], symbol)
            market_series.append(close); bases[symbol] = basis
        market = pd.concat(market_series, axis=1).sort_index().dropna(how="all")
        market.attrs.update({
            "provenance": "oryntra_canonical",
            "price_basis": "adjusted_or_total_return" if all(x in {"adjusted_close", "total_return"} for x in bases.values()) else "unadjusted_close",
            "series_price_basis": bases,
        })
        stock_series = []
        for symbol in universe:
            if symbol not in histories:
                continue
            close, _ = _close(histories[symbol], symbol)
            stock_series.append(close)
        if len(stock_series) < 12:
            raise ValueError("Beta requires at least 12 usable stock histories for Alpha-context aggregation")
        stocks = pd.concat(stock_series, axis=1).sort_index()
        benchmark = market["SPY"].reindex(stocks.index).pct_change(fill_method=None) if "SPY" in market else None
        context = beta_v1_alpha_context(stocks, benchmark_returns=benchmark, config=BetaV1Config(enabled=True))
        service = beta_service(user_id)
        state = service.latest_state(market, stock_context=context)
        payload = beta_v1_risk_payload(state, service=service)
        status["state"] = payload
        status["research_state_available"] = True
        status["risk_v204_consumable"] = bool(
            artifact.get("production_frozen") and payload.get("production_frozen") and payload.get("calibrated")
            and float(payload.get("data_quality", 0.0)) >= 0.60
            and float(payload.get("uncertainty", 1.0)) <= 0.70
        )
        status["risk_v204_research_consumable"] = bool(
            artifact.get("trained") and artifact.get("candidate_diagnostics_match") and payload.get("calibrated")
            and float(payload.get("data_quality", 0.0)) >= 0.60
        )
        if status["risk_v204_consumable"]:
            status["fallback"] = None
        elif not artifact.get("production_frozen"):
            status["fallback"] = "Interactive Research may consume this audited Beta candidate in V2.0.4; production V2.0.4 remains fail-closed on V2.0.3 until production-freeze gates pass."
        else:
            status["fallback"] = "Current production Beta state failed one or more V2.0.4 consumption gates."
    except Exception as exc:
        status["state_error"] = f"{type(exc).__name__}: {exc}"
    return status
