"""Read-only health summary of saved Gamma forecast records, never a backtest.

Only forecast components and provenance flags are summarized. Realized outcomes
are deliberately ignored. Counts describe supplied records, not scored trades.
"""
from __future__ import annotations

from collections import Counter
from datetime import date, datetime, timezone
from math import isfinite, sqrt
from numbers import Real
from typing import Any, Mapping


def _date_bound(value: str | None) -> date | None:
    if value is None:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except (ValueError, TypeError) as exc:
        raise ValueError("audit date filters must be YYYY-MM-DD") from exc


def _origin(value: Any) -> date:
    if not isinstance(value, str) or not value:
        raise ValueError("each forecast requires an explicit created_at timestamp")
    try:
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if stamp.tzinfo is not None:
            stamp = stamp.astimezone(timezone.utc)
        return stamp.date()
    except ValueError as exc:
        raise ValueError("invalid forecast created_at timestamp") from exc


def _numbers(values: Any, names: list[str], field: str) -> list[float] | None:
    if values is None:
        return None
    if not isinstance(values, Mapping) or set(values) != set(names):
        raise ValueError(f"{field} must contain exactly the forecast symbols")
    out = []
    for name in names:
        value = values[name]
        if isinstance(value, bool) or not isinstance(value, Real):
            raise ValueError(f"{field} contains a nonfinite or nonnumeric value")
        try:
            value = float(value)
        except (ValueError, OverflowError) as exc:
            raise ValueError(f"{field} contains an unrepresentable numeric value") from exc
        if not isfinite(value):
            raise ValueError(f"{field} contains a nonfinite or nonnumeric value")
        out.append(value)
    return out


def audit_gamma_records(payload: Any, *, start_date: str | None = None,
                        end_date: str | None = None) -> dict[str, Any]:
    """Summarize producer JSON or a record list, with inclusive UTC-origin dates."""
    rows = payload.get("records") if isinstance(payload, Mapping) else payload
    if not isinstance(rows, list):
        raise ValueError("audit expects a Gamma record list or an object with records")
    start, end = _date_bound(start_date), _date_bound(end_date)
    if start and end and start > end:
        raise ValueError("audit start date must not be after end date")
    versions, fingerprints, residuals, beta, sec = (Counter() for _ in range(5))
    dates, alpha_values, correction_values = [], [], []
    exact_zero, alpha_supported, correction_supported = 0, 0, 0
    zero_reasons, extra_coverage = Counter(), Counter()
    missing_alpha_health = 0
    beta_macro_verified, beta_macro_unverified, beta_macro_unknown = 0, 0, 0
    direct_observations = 0
    for number, row in enumerate(rows):
        if not isinstance(row, Mapping):
            raise ValueError(f"forecast record {number} must be an object")
        day = _origin(row.get("created_at"))
        if (start and day < start) or (end and day > end):
            continue
        meta = row.get("metadata")
        if not isinstance(meta, Mapping) or meta.get("producer") != "gamma_qpo_research_v1":
            raise ValueError("audit accepts only explicitly identified Gamma forecasts")
        values = row.get("values")
        if not isinstance(values, Mapping) or not values:
            raise ValueError("forecast values must be a nonempty symbol mapping")
        names = list(values)
        _numbers(values, names, "values")
        if row.get("units") != "period_return":
            raise ValueError("audit components require original period_return records")
        a = _numbers(meta.get("alpha_prior_means"), names, "alpha_prior_means")
        c = _numbers(meta.get("residual_corrections"), names, "residual_corrections")
        if a is not None:
            alpha_supported += 1
            alpha_values.extend(a)
            exact_zero += int(all(value == 0.0 for value in a))
            health = meta.get("alpha_health")
            if not isinstance(health, Mapping):
                missing_alpha_health += 1
                if all(value == 0.0 for value in a):
                    zero_reasons["unknown_in_saved_record"] += 1
            elif all(value == 0.0 for value in a):
                reason = health.get("direct_alpha_zero_reason")
                zero_reasons[reason if isinstance(reason, str) and reason else "unknown_in_saved_record"] += 1
        if c is not None:
            correction_supported += 1
            correction_values.extend(c)
        direct_observations += len(names)
        dates.append(day.isoformat())
        versions[str(row.get("model_version", "unknown"))] += 1
        fingerprint = meta.get("config_fingerprint")
        fingerprints[str(fingerprint) if fingerprint is not None else "legacy_absent"] += 1
        residuals[str(meta.get("residual_status", "unknown"))] += 1
        beta[str(meta.get("beta_status", "unknown"))] += 1
        sec[str(meta.get("sec_status", "unknown"))] += 1
        extra = meta.get("sec_extra_features")
        extra = extra.get("features", {}) if isinstance(extra, Mapping) else {}
        if isinstance(extra, Mapping):
            for name, entries in extra.items():
                if not isinstance(entries, Mapping):
                    continue
                if set(entries) - set(names):
                    raise ValueError("extra SEC features reference symbols outside the forecast")
                extra_coverage[str(name)] += 0
                for symbol, item in entries.items():
                    if isinstance(item, Mapping) and item.get("status") == "observed":
                        _numbers({symbol: item.get("value")}, [symbol], f"extra SEC {name}")
                        extra_coverage[str(name)] += 1
        provenance = meta.get("beta_provenance")
        macro = provenance.get("macro_provenance") if isinstance(provenance, Mapping) else None
        if not isinstance(macro, list) or not macro:
            beta_macro_unknown += 1
        elif all(isinstance(item, Mapping) and item.get("historical_vintage_verified") is True
                 for item in macro):
            beta_macro_verified += 1
        else:
            beta_macro_unverified += 1
    def rms_bp(values):
        if not values:
            return None
        scale = max(map(abs, values))
        if scale == 0:
            return 0.0
        value = (scale * sqrt(sum((x / scale) ** 2 for x in values) / len(values))) * 10000.0
        if not isfinite(value):
            raise ValueError("component RMS cannot be represented in basis points")
        return value
    warnings = []
    if exact_zero:
        warnings.append("Direct Alpha is zero on some supplied origins; Alpha may still enter the residual head.")
    if len(fingerprints) > 1 or len(versions) > 1:
        warnings.append("Multiple configurations or versions are present; this audit does not pool them into a model error ledger.")
    if beta_macro_unverified or beta_macro_unknown:
        warnings.append("Some macro provenance is unverified or unavailable; this report is not a historical-vintage attestation.")
    return {
        "audit_version": "gamma_feature_audit_v1", "status": "records_audited" if dates else "no_records_in_scope",
        "scope": "supplied forecast records only; not necessarily scored decisions or trades",
        "date_filter": {"start_date": start_date, "end_date": end_date, "basis": "inclusive UTC origin date"},
        "records_supplied": len(rows), "records_in_scope": len(dates), "stock_origins": direct_observations,
        "first_origin_date": min(dates) if dates else None, "last_origin_date": max(dates) if dates else None,
        "duplicate_origin_dates": sum(count - 1 for count in Counter(dates).values()),
        "model_versions": dict(versions), "config_fingerprints": dict(fingerprints),
        "residual_status_counts": dict(residuals), "beta_status_counts": dict(beta), "sec_status_counts": dict(sec),
        "direct_alpha": {"origins_with_component": alpha_supported, "exact_zero_origins": exact_zero,
                         "rms_basis_points": rms_bp(alpha_values), "reported_zero_reason_counts": dict(zero_reasons),
                         "origins_missing_health_diagnostics": missing_alpha_health},
        "residual_correction": {"origins_with_component": correction_supported,
                                "rms_basis_points": rms_bp(correction_values)},
        "macro_provenance": {"all_declared_rows_verified": beta_macro_verified,
                             "some_declared_rows_unverified": beta_macro_unverified,
                             "missing_or_empty": beta_macro_unknown,
                             "scope": "supplied flags only; not independent source or market-vintage verification"},
        "extra_sec_features": {"observed_stock_origins": dict(extra_coverage),
                               "predictor_usage": "diagnostic-only in this QA revision"},
        "warnings": warnings, "realized_outcomes_used": False,
        "performance_conclusion": "none; no model fitting, portfolio replay, or predictive validation performed",
    }
