"""Opt-in QPO input contracts; exact Full-QRS/solver code remains unchanged."""
from __future__ import annotations
from dataclasses import fields, replace
from numbers import Integral, Real
from typing import Any, Iterable, Mapping
import numpy as np
import pandas as pd
from .forecast_contract import ForecastRecord, select_point_in_time_forecasts
from .problem import QOptimaPortfolioProblem


class GammaContractError(ValueError):
    """Unavailable/incompatible research input; never silently use a proxy mean."""


class GammaQRMRejection(GammaContractError):
    """A valid research decision rejected by QRM, distinct from bad inputs."""
    def __init__(self, message: str, *, qrm: Mapping[str, Any], stage: str):
        super().__init__(message)
        self.qrm = dict(qrm)
        self.stage = str(stage)


def _integer_parameter(value: Any, name: str, *, minimum: int) -> int:
    """Reject booleans and fractional/string counts instead of truncating them."""
    if isinstance(value, (bool, np.bool_)) or not isinstance(value, Integral) or value < minimum:
        raise GammaContractError(f"Gamma {name} must be an integer >= {minimum}")
    return int(value)


def _finite_parameter(value: Any, name: str) -> float:
    if isinstance(value, (bool, np.bool_)) or not isinstance(value, Real):
        raise GammaContractError(f"Gamma {name} must be a finite real number")
    try:
        value = float(value)
    except (ValueError, OverflowError) as exc:
        raise GammaContractError(f"Gamma {name} must be a finite real number") from exc
    if not np.isfinite(value):
        raise GammaContractError(f"Gamma {name} must be a finite real number")
    return value


def _gamma_record(raw: Any) -> ForecastRecord:
    # The shared legacy forecast parser coerces horizons with int(). Gamma's
    # exact target identity must be checked before that conversion loses data.
    if not isinstance(raw, (ForecastRecord, dict)):
        raise GammaContractError("Gamma forecast record must be an object")
    horizon = raw.horizon_sessions if isinstance(raw, ForecastRecord) else raw.get("horizon_sessions")
    _integer_parameter(horizon, "forecast horizon_sessions", minimum=1)
    return raw if isinstance(raw, ForecastRecord) else ForecastRecord.from_mapping(raw)


def _config_fingerprint(record: ForecastRecord) -> str | None:
    metadata = record.metadata or {}
    if "config_fingerprint" not in metadata:
        return None
    value = metadata["config_fingerprint"]
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise GammaContractError("Gamma config_fingerprint must be a nonempty string when present")
    return value


def utc_timestamp(value: Any) -> pd.Timestamp:
    if value is None or isinstance(value, (int, float, bool)):
        raise GammaContractError("Gamma requires an explicit nonnumeric timestamp")
    try:
        value = pd.Timestamp(value)
        if pd.isna(value):
            raise ValueError("NaT")
        return value.tz_localize("UTC") if value.tzinfo is None else value.tz_convert("UTC")
    except (ValueError, TypeError, OverflowError) as exc:
        raise GammaContractError("Gamma timestamp is missing or invalid") from exc


def _annual_equivalent(record: ForecastRecord) -> dict[str, Any]:
    k = 252.0 / record.horizon_sessions
    # All mutable numeric mappings are replaced below, and nested provenance
    # has always been retained by reference in the replacement metadata. Avoid
    # deep-copying that lineage only to discard the copy immediately.
    row = {field.name: getattr(record, field.name) for field in fields(record)}
    row["values"] = {key: k * value for key, value in record.values.items()}
    if record.realized_outcome is not None:
        row["realized_outcome"] = {key: k * value for key, value in record.realized_outcome.items()}
    row["units"] = "annualized_return"
    row["metadata"] = {**dict(record.metadata or {}),
        "scoring_units": "linear_annual_equivalent_not_compound_return",
        "period_to_score_multiplier": k, "input_period_values": dict(record.values)}
    return row


def prepare_gamma_inputs(records, *, decision_time: Any, feature_cutoff: Any,
                         symbols: Iterable[str], horizon_sessions: int,
                         min_error_records: int = 2, error_records=None, required_beta: bool = False, required_sec: bool = False,
                         require_verified_macro_vintages: bool = False, session_index=None) -> dict[str, Any]:
    """Select a fresh absolute mean and sorted matched, matured error ledger.

    Full historical panels can contain future predictions/outcomes. They are
    excluded by timestamps; no unavailable current mean becomes a trailing proxy.
    """
    decision, cutoff = utc_timestamp(decision_time), utc_timestamp(feature_cutoff)
    if cutoff >= decision:
        raise GammaContractError("Gamma feature cutoff must strictly precede entry close")
    h = _integer_parameter(horizon_sessions, "horizon_sessions", minimum=1)
    minimum_errors = _integer_parameter(min_error_records, "min_error_records", minimum=2)
    calendar = None if session_index is None else pd.DatetimeIndex([utc_timestamp(day) for day in session_index])
    if calendar is not None and (calendar.has_duplicates or not calendar.is_monotonic_increasing):
        raise GammaContractError("Gamma source calendar must be unique and increasing")
    syms = tuple(str(s).upper().strip() for s in symbols)
    rows = [_gamma_record(r) for r in list(records or [])]
    history_rows = rows if error_records is None else [_gamma_record(r) for r in list(error_records)]

    def validate(record: ForecastRecord, *, historical: bool) -> None:
        record.validate(decision_time=decision, symbols=syms, required_horizon_sessions=h)
        meta = dict(record.metadata or {})
        expected = {"producer": "gamma_qpo_research_v1", "target_type": "absolute", "return_convention": "close_to_close"}
        if any(meta.get(key) != value for key, value in expected.items()):
            raise GammaContractError("Gamma requires absolute close-to-close gamma_qpo_research_v1 records")
        if record.units != "period_return":
            raise GammaContractError("Gamma ingress accepts period_return only; refusing double conversion")
        if not meta.get("reference_definition"):
            raise GammaContractError("Gamma reference definition is required")
        if require_verified_macro_vintages and (meta.get("beta_status") == "PIT_supplied" or meta.get("beta_provenance") is not None):
            from .gamma_forecast import validate_macro_vintage_admission
            provenance = meta.get("beta_provenance")
            if not isinstance(provenance, Mapping):
                raise GammaContractError("Gamma supplied Beta forecast lacks macro-vintage provenance")
            try:
                validate_macro_vintage_admission(provenance.get("macro_provenance"))
            except ValueError as exc:
                raise GammaContractError(str(exc)) from exc
        _integer_parameter(meta.get("feature_lag_sessions"), "feature_lag_sessions", minimum=1)
        origin = utc_timestamp(meta.get("target_origin"))
        if calendar is not None:
            loc = int(calendar.searchsorted(origin))
            if loc >= len(calendar) or calendar[loc] != origin:
                raise GammaContractError("Gamma forecast origin is absent from source session calendar")
            if loc+h < len(calendar):
                if utc_timestamp(meta.get("target_end")) != calendar[loc+h]:
                    raise GammaContractError("Gamma target duration does not match source trading sessions")
            elif historical:
                raise GammaContractError("Gamma realized target extends beyond available source calendar")
        info = utc_timestamp(record.information_cutoff)
        if info >= origin or info > cutoff:
            raise GammaContractError("Gamma forecast uses information at/after entry or after feature cutoff")
        for key in ("training_information_cutoff", "reference_training_cutoff"):
            if utc_timestamp(meta.get(key)) > info:
                raise GammaContractError(f"Gamma {key} exceeds forecast information cutoff")
        if historical:
            if not record.realized_outcome or not record.realized_at:
                raise GammaContractError("Gamma error row lacks realized target")
            if not all(s in record.realized_outcome and np.isfinite(record.realized_outcome[s]) for s in syms):
                raise GammaContractError("Gamma error row has missing/nonfinite realized values")
            if utc_timestamp(record.realized_at) <= origin:
                raise GammaContractError("Gamma realized target must strictly follow entry close")
            if meta.get("target_end") is None or utc_timestamp(meta["target_end"]) != utc_timestamp(record.realized_at):
                raise GammaContractError("Gamma target-end and realization timestamps differ")
        else:
            if origin != decision:
                raise GammaContractError("Gamma target_origin does not match entry close")
            if meta.get("residual_status") != "bounded_ridge_OOF_residual":
                raise GammaContractError("Gamma residual model is not ready; Alpha-only warmup is not a Gamma backtest")
            if required_beta and meta.get("beta_status") != "PIT_supplied":
                raise GammaContractError("Gamma required PIT Beta sleeve unavailable")
            if required_sec and meta.get("sec_status") != "available":
                raise GammaContractError("Gamma required SEC sleeve unavailable")

    current = [r for r in rows if utc_timestamp(r.created_at) <= decision
               and utc_timestamp((r.metadata or {}).get("target_origin", r.created_at)) == decision]
    if len(current) != 1:
        raise GammaContractError("Gamma requires exactly one fresh forecast for entry close")
    record = current[0]
    validate(record, historical=False)
    fingerprint = _config_fingerprint(record)
    compatibility = {
        "mode": "exact_config_fingerprint" if fingerprint is not None else "legacy_unfingerprinted",
        "current_config_fingerprint": fingerprint,
        "excluded_matured_error_records": 0,
        "excluded_missing_fingerprint": 0,
        "excluded_different_fingerprint": 0,
        "excluded_fingerprinted_from_legacy": 0,
        "scope": "matured_model_and_version_matched_errors_only; future_rows_not_counted",
    }
    matched = []
    for item in history_rows:
        if utc_timestamp(item.created_at) >= decision:
            continue
        if item.realized_at is None or item.realized_outcome is None or utc_timestamp(item.realized_at) > cutoff:
            continue
        if (item.model_id, item.model_version) != (record.model_id, record.model_version):
            continue
        historical_fingerprint = _config_fingerprint(item)
        if historical_fingerprint != fingerprint:
            compatibility["excluded_matured_error_records"] += 1
            if fingerprint is None:
                compatibility["excluded_fingerprinted_from_legacy"] += 1
            elif historical_fingerprint is None:
                compatibility["excluded_missing_fingerprint"] += 1
            else:
                compatibility["excluded_different_fingerprint"] += 1
            continue
        validate(item, historical=True)
        if (item.metadata or {}).get("reference_definition") != (record.metadata or {}).get("reference_definition"):
            raise GammaContractError("Gamma residual ledger mixes reference definitions")
        matched.append(item)
    matched.sort(key=lambda r: utc_timestamp((r.metadata or {})["target_origin"]))
    origins = [utc_timestamp((r.metadata or {})["target_origin"]) for r in matched]
    if len(set(origins)) != len(origins):
        raise GammaContractError("Gamma error ledger has duplicate origins")
    if len(matched) < minimum_errors:
        error = GammaContractError(f"Gamma requires {minimum_errors} matured matched errors; found {len(matched)}")
        error.gamma_input_diagnostics = {"forecast_config_compatibility": compatibility,
                                         "matched_error_records": len(matched)}
        error.research_diagnostics = {"gamma_input_validation": error.gamma_input_diagnostics}
        raise error
    normalized_current = [_annual_equivalent(record)]
    normalized_current[0]["realized_outcome"] = None
    normalized_current[0]["realized_at"] = None
    normalized_history = [_annual_equivalent(item) for item in matched]
    pit = select_point_in_time_forecasts(normalized_history + normalized_current,
        decision_time=decision, symbols=syms, required_horizon_sessions=h)
    pit["historical_realized_records"] = [ForecastRecord.from_mapping(r) for r in normalized_history]
    return {"current_records": normalized_current, "history_records": normalized_history, "pit": pit,
        "audit": {"enabled": True, "entry_close": decision.isoformat(), "feature_cutoff": cutoff.isoformat(),
            "horizon_sessions": h, "error_records": len(matched), "target_type": "absolute",
            "forecast_config_compatibility": compatibility,
            "return_convention": "close_to_close", "scoring_units": "linear_annual_equivalent_not_compound_return",
            "period_to_score_multiplier": 252.0/h,
            "covariance_horizon_assumption": "Sigma_h=Sigma_annual*h/252; linear scaling assumption",
            "risk_budget_horizon_assumption": "budget_h=budget_annual*sqrt(h/252)",
            "tail_support_policy": "insufficient V1A standardized tail remains explicitly unavailable",
            "macro_vintage_admission": {"required": require_verified_macro_vintages,
                "scope": "current_and_matched_error_forecast_Beta_provenance_flags_only",
                "independent_source_or_market_vintage_attestation": False,
                "separate_Risk205_Beta_selection_covered": False},
            "forecast_metadata": dict(record.metadata or {})}}


def gamma_score_rates(horizon_sessions: int, risk_free_rate: float,
                      borrowing_rate: float | None, transaction_cost_bps: float) -> dict[str, float]:
    h = _integer_parameter(horizon_sessions, "score horizon_sessions", minimum=1)
    k = 252.0/h
    lend = _finite_parameter(risk_free_rate, "risk_free_rate")
    borrow = lend if borrowing_rate is None else _finite_parameter(borrowing_rate, "borrowing_rate")
    transaction_cost_bps = _finite_parameter(transaction_cost_bps, "transaction_cost_bps")
    if min(lend, borrow) <= -1 or transaction_cost_bps < 0:
        raise GammaContractError("Invalid Gamma financing or transaction cost")
    return {"risk_free_rate": k*((1.0+lend)**(1.0/k)-1.0),
            "borrowing_rate": k*((1.0+borrow)**(1.0/k)-1.0),
            "transaction_cost_bps": k*float(transaction_cost_bps)}


def select_gamma_beta_state(records, feature_cutoff: Any) -> dict[str, Any] | None:
    cutoff = utc_timestamp(feature_cutoff)
    eligible = []
    for raw in list(records or []):
        if not isinstance(raw, Mapping):
            continue
        if raw.get("source") == "alfred_pit_beta":
            from .gamma_forecast import _validated_beta_records, REGIME_NAMES
            accepted, _rejected = _validated_beta_records([raw])
            if not accepted:
                continue
            parsed = accepted[0]
            state = {**dict(raw), "as_of_session": raw["as_of"],
                **{f"p_{name}": float(value) for name, value in zip(REGIME_NAMES, parsed["p"])}}
        else:
            # A timestamp and production flag do not establish historical model
            # selection or macro-vintage causality in this new research route.
            continue
        try:
            stamp = utc_timestamp(state.get("as_of_session"))
            available = utc_timestamp(raw.get("available_at", raw.get("created_at", state.get("as_of_session"))))
            if stamp <= cutoff and available <= cutoff:
                eligible.append((stamp, available, state))
        except GammaContractError:
            continue
    return max(eligible, key=lambda row: (row[0], row[1]))[2] if eligible else None


def apply_gamma_risk205(problem: QOptimaPortfolioProblem, weights: np.ndarray, *, returns_history: np.ndarray,
    feature_cutoff: Any, beta_records=None, config_overrides: Mapping[str, Any] | None = None,
    state: Mapping[str, Any] | None = None, actual_transaction_cost_bps: float = 0.0,
    scope: str = "full_supervisor"):
    """Option A: supervise selected portfolio once, not inside enumeration.

    Final QRM uses approved gross. It cannot restore a deliberate reduction.
    The returned state is a new snapshot; candidate evaluation does not mutate it.
    """
    from ..universal_risk_supervisor import supervise_cross_sectional_weights, v205_risk_config, _v204_control_config, _v205_control_config
    from .qrm import evaluate_risk
    proposed = np.asarray(weights, dtype=float).reshape(-1)
    if proposed.shape != (problem.n,) or not np.isfinite(proposed).all() or (proposed < 0).any():
        raise GammaContractError("Risk205 requires finite aligned long-only proposed weights")
    before = evaluate_risk(problem, proposed)
    if not before["passes_qrm_structural_gates"]:
        raise GammaQRMRejection("Risk205 cannot authorize a QRM-rejected proposal", qrm=before, stage="pre_risk205")
    cfg = v205_risk_config(**dict(config_overrides or {}))
    if not cfg.enabled or not cfg.v205_enabled:
        raise GammaContractError("Risk205 variant cannot disable its supervisor")
    if scope not in {"full_supervisor", "state_overlay_only"}:
        raise GammaContractError("Unknown gamma_risk205_scope")
    active_count = int(np.count_nonzero(proposed > 1e-12))
    if scope == "full_supervisor" and cfg.component_risk_enabled and active_count * cfg.maximum_risk_contribution < 1.0-1e-12:
        required = int(np.ceil(1.0/cfg.maximum_risk_contribution))
        raise GammaContractError(
            f"Risk205 component-risk cap {cfg.maximum_risk_contribution:g} is infeasible for "
            f"{active_count} positive positions; at least {required} are necessary. "
            "No exposure shrinkage can solve a normalized-share cardinality impossibility."
        )
    prior = dict(state or {})
    keys = ("predictive_persistence_count", "beta_persistence_count", "v1a_persistence_count")
    if set(prior)-set(keys):
        raise GammaContractError("Unknown risk persistence field")
    counts = {key: int(prior.get(key, 0)) for key in keys}
    if any(value < 0 for value in counts.values()):
        raise GammaContractError("Risk persistence counts must be nonnegative")
    if scope == "state_overlay_only":
        from ..risk.v204 import beta_regime_modifier_v204
        from ..risk.v205 import v1a_regime_modifier_v205, combine_state_modifiers_v205, apply_state_overlay_v205
        beta_control = _v204_control_config(replace(cfg, v204_research_candidate_mode=cfg.v205_research_candidate_mode))
        vcontrol = _v205_control_config(cfg)
        beta_modifier = beta_regime_modifier_v204(select_gamma_beta_state(beta_records, feature_cutoff),
            control=beta_control, previous_persistence_rebalances=counts["beta_persistence_count"],
            current_session=str(utc_timestamp(feature_cutoff)), v203_predictive_triggered=False)
        v1a_modifier = v1a_regime_modifier_v205(dict(problem.v1a_state_metadata or {}), weights=proposed,
            covariance=np.asarray(problem.covariance)/252.0, returns_history=np.asarray(returns_history, dtype=float),
            control=vcontrol, previous_persistence_rebalances=counts["v1a_persistence_count"],
            current_session=str(utc_timestamp(feature_cutoff)))
        combined = combine_state_modifiers_v205(beta_modifier, v1a_modifier, control=vcontrol)
        final, overlay = apply_state_overlay_v205(proposed, combined,
            current_weights=np.asarray(problem.current_weights), control=vcontrol)
        audit = {"baseline": "QRM_approved_QPO_proposal_not_V203", "inherited_v203_supervisor_applied": False,
            "beta_modifier": beta_modifier, "v1a_modifier": v1a_modifier,
            "state_combination": combined, "state_overlay": overlay,
            "persistence_count": counts["predictive_persistence_count"],
            "beta_persistence_rebalances": int(beta_modifier.get("persistence_rebalances", 0)),
            "v1a_persistence_rebalances": int(v1a_modifier.get("persistence_rebalances", 0))}
    else:
        final, audit = supervise_cross_sectional_weights(proposed, np.asarray(problem.covariance)/252.0, cfg,
            returns_history=np.asarray(returns_history, dtype=float), expected_alpha=np.asarray(problem.expected_returns)/(252.0/problem.horizon_sessions),
            current_weights=np.asarray(problem.current_weights), symbols=problem.symbols,
            predictive_forecast={"available": False, "reason": "no_matched_gamma_predictive_risk_panel"},
            previous_persistence_count=counts["predictive_persistence_count"],
            beta_state=select_gamma_beta_state(beta_records, feature_cutoff), previous_beta_persistence_count=counts["beta_persistence_count"],
            v1a_state=dict(problem.v1a_state_metadata or {}), previous_v1a_persistence_count=counts["v1a_persistence_count"],
            current_session=str(utc_timestamp(feature_cutoff)))
    final = np.asarray(final, dtype=float)
    if not np.isfinite(final).all() or (final < -1e-12).any() or (final > proposed+1e-10).any():
        raise GammaContractError("Risk205 violated no-increase/finite-weight invariant")
    final_gross = float(final.sum())
    if final_gross > 1e-12:
        final_qrm = evaluate_risk(replace(problem, target_gross=final_gross), final)
        if not final_qrm["passes_qrm_structural_gates"]:
            raise GammaQRMRejection("Post-Risk205 failed scale-aware QRM; refusing gross restoration: " + str({k:v for k,v in final_qrm["structural_gates"].items() if not v}) + f"; gross={final_gross:.8f}; weights={final.tolist()}", qrm=final_qrm, stage="post_risk205")
    else:
        final_qrm = {"passes_qrm_structural_gates": True, "policy": "all_cash_no_risky_positions"}
    turnover = float(np.abs(final-np.asarray(problem.current_weights)).sum())
    cost = turnover*float(actual_transaction_cost_bps)/10000.0
    if not np.isfinite(cost) or cost < 0 or cost >= 1:
        raise GammaContractError("Post-Risk205 transaction cost is invalid")
    next_state = {"predictive_persistence_count": int(audit.get("persistence_count", 0)),
        "beta_persistence_count": int(audit.get("beta_persistence_rebalances", 0)),
        "v1a_persistence_count": int(audit.get("v1a_persistence_rebalances", 0))}
    from ..universal_risk_v201 import component_risk_shares
    base_shares = component_risk_shares(final, np.asarray(problem.covariance)/252.0)
    base_maximum = float(np.max(base_shares)) if final_gross > 1e-12 else 0.0
    ensemble_maximum = ((audit.get("what_if") or {}).get("v203") or {}).get("maximum_component_risk")
    if final_gross <= 1e-12:
        ensemble_maximum = 0.0
    component_diagnostics = {"configured_share_limit": float(cfg.maximum_risk_contribution),
        "positive_positions": int(np.count_nonzero(final > 1e-12)),
        "base_covariance_maximum_share": base_maximum,
        "base_covariance_within_limit": bool(base_maximum <= cfg.maximum_risk_contribution+1e-8),
        "supervisor_ensemble_maximum_share": ensemble_maximum,
        "supervisor_ensemble_within_limit": (None if ensemble_maximum is None else bool(float(ensemble_maximum) <= cfg.maximum_risk_contribution+1e-8)),
        "interpretation": ("Existing V203 structural repair is economically gated; cardinality is necessary, not proof of final component-share compliance. The V205 state overlay scales uniformly and preserves nonzero component shares."
            if scope == "full_supervisor" else "State-overlay-only does not apply the inherited component-risk supervisor.")}
    return final, {"enabled": True, "policy": "post_qpo_risk205_option_a_not_jointly_optimized",
        "scope": scope, "inherited_v203_supervisor_applied": scope == "full_supervisor",
        "pre_risk_weights": proposed.tolist(), "post_risk_weights": final.tolist(),
        "pre_risk_metrics": problem.metrics(proposed), "post_risk_metrics": problem.metrics(final),
        "approved_final_gross": final_gross, "actual_turnover_l1": turnover,
        "actual_one_event_transaction_cost": cost, "final_qrm": final_qrm, "risk_audit": audit,
        "state_before": counts, "state_after": next_state, "persistence_advancement": "once_per_selected_scheduled_decision",
        "component_risk_diagnostics": component_diagnostics,
        "execution_allowed": False, "remaining_gate": "Control and configured final volatility ceiling"}, next_state


def validate_gamma_config(raw: Mapping[str, Any] | None) -> dict[str, Any]:
    from .gamma_forecast import GammaForecastConfig
    from dataclasses import fields
    value = dict(raw or {})
    unknown = set(value)-{field.name for field in fields(GammaForecastConfig)}
    if unknown:
        raise GammaContractError(f"Unknown Gamma configuration fields: {sorted(unknown)}")
    if value:
        GammaForecastConfig(**value)
    return value
