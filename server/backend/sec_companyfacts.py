"""Conservative SEC Company Facts normalization for Minerva research.

This module is deliberately offline: callers supply an SEC Company Facts JSON
file they obtained in compliance with SEC fair-access guidance.  It emits only
public filing-derived facts with a conservative availability timestamp; it
does not claim that the aggregate Company Facts endpoint is an as-filed
point-in-time database.
"""
from __future__ import annotations

from datetime import timedelta, timezone
from typing import Any, Iterable

import numpy as np
import pandas as pd


REVENUE_TAGS = ("RevenueFromContractWithCustomerExcludingAssessedTax", "Revenues", "SalesRevenueNet")
GROSS_PROFIT_TAGS = ("GrossProfit",)
OPERATING_INCOME_TAGS = ("OperatingIncomeLoss",)
NET_INCOME_TAGS = ("NetIncomeLoss",)
SEC_COMPANYFACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json"


def conservative_available_at(filed: Any) -> str:
    """Make a filing usable no earlier than the next calendar-day session.

    Company Facts supplies a filing date, not a trade-safe intraday timestamp.
    Advancing a full calendar day prevents same-day look-ahead; a session panel
    naturally advances weekend/holiday dates to its next available session.
    """
    try:
        date = pd.Timestamp(filed).normalize()
    except (TypeError, ValueError) as exc:
        raise ValueError("SEC fact needs a valid filed date") from exc
    return (date + timedelta(days=1)).tz_localize(timezone.utc).isoformat()


def normalize_quarterly_companyfacts(payload: dict[str, Any], ticker: str) -> list[dict[str, Any]]:
    """Build availability-dated revenue-growth and operating-margin records.

    Only 75–110 day 10-Q/10-K duration facts are accepted, excluding annual
    and year-to-date totals whose use as a quarter would create a false signal.
    A year-over-year revenue comparison requires a comparable prior duration
    ending 330–400 days earlier.  Duplicate/amended facts retain the earliest
    filing for the same period and value, never a later restatement.
    """
    clean_ticker = str(ticker).upper().strip()
    if not clean_ticker:
        raise ValueError("ticker is required")
    if not isinstance(payload, dict) or not isinstance(payload.get("facts"), dict):
        raise ValueError("Company Facts payload requires a facts object")
    cik = _cik(payload.get("cik"))
    gaap = payload["facts"].get("us-gaap")
    if not isinstance(gaap, dict):
        raise ValueError("Company Facts payload has no us-gaap concepts")
    revenue = _quarterly_observations(gaap, REVENUE_TAGS)
    gross_profit = _quarterly_observations(gaap, GROSS_PROFIT_TAGS)
    operating = _quarterly_observations(gaap, OPERATING_INCOME_TAGS)
    net_income = _quarterly_observations(gaap, NET_INCOME_TAGS)
    operating_by_end = {row["end"]: row for row in operating}
    gross_profit_by_end = {row["end"]: row for row in gross_profit}
    net_income_by_end = {row["end"]: row for row in net_income}
    records: list[dict[str, Any]] = []
    for row in revenue:
        prior = _prior_year_comparable(row, revenue)
        if prior is None or prior["value"] <= 0:
            continue
        source_url = SEC_COMPANYFACTS_URL.format(cik=cik)
        metadata = {"cik": f"{cik:010d}", "form": row["form"], "period_start": str(row["start"].date()),
                    "period_end": str(row["end"].date()), "filed": str(row["filed"].date()),
                    "tag": row["tag"], "prior_period_end": str(prior["end"].date())}
        common = {"ticker": clean_ticker, "available_at": conservative_available_at(row["filed"]),
                  "published_at": pd.Timestamp(row["filed"]).tz_localize(timezone.utc).isoformat(),
                  "period_end": pd.Timestamp(row["end"]).tz_localize(timezone.utc).isoformat(),
                  "source_class": "sec_filing", "source_url": source_url, "units": "percent", "metadata": metadata}
        records.append({**common, "metric": "revenue_growth_yoy", "value": 100 * (row["value"] / prior["value"] - 1)})
        gross = gross_profit_by_end.get(row["end"])
        if gross is not None and row["value"] != 0:
            records.append({**common, "metric": "gross_margin", "value": 100 * gross["value"] / row["value"],
                            "metadata": {**metadata, "gross_profit_tag": gross["tag"]}})
        op = operating_by_end.get(row["end"])
        if op is not None and row["value"] != 0:
            records.append({**common, "metric": "operating_margin", "value": 100 * op["value"] / row["value"],
                            "metadata": {**metadata, "operating_income_tag": op["tag"]}})
        income = net_income_by_end.get(row["end"])
        if income is not None and row["value"] != 0:
            records.append({**common, "metric": "net_income_margin", "value": 100 * income["value"] / row["value"],
                            "metadata": {**metadata, "net_income_tag": income["tag"]}})
    return records


def _cik(value: Any) -> int:
    try:
        cik = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("Company Facts payload requires a numeric cik") from exc
    if cik <= 0:
        raise ValueError("Company Facts cik must be positive")
    return cik


def _quarterly_observations(gaap: dict[str, Any], tags: Iterable[str]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for tag in tags:
        units = gaap.get(tag, {}).get("units", {})
        for raw in units.get("USD", []):
            try:
                start, end, filed = (pd.Timestamp(raw["start"]).normalize(), pd.Timestamp(raw["end"]).normalize(),
                                     pd.Timestamp(raw["filed"]).normalize())
                value = float(raw["val"])
                form = str(raw["form"])
            except (KeyError, TypeError, ValueError):
                continue
            duration = (end - start).days
            if form not in {"10-Q", "10-K"} or not (75 <= duration <= 110) or not np.isfinite(value):
                continue
            rows.append({"tag": tag, "start": start, "end": end, "filed": filed, "value": value,
                         "form": form, "duration": duration})
    # Preserve the first public fact for an end date; later amendments cannot
    # retrospectively substitute their restated value into a historical test.
    unique: dict[pd.Timestamp, dict[str, Any]] = {}
    for row in sorted(rows, key=lambda item: (item["end"], item["filed"], item["tag"])):
        unique.setdefault(row["end"], row)
    return list(unique.values())


def _prior_year_comparable(current: dict[str, Any], observations: Iterable[dict[str, Any]]) -> dict[str, Any] | None:
    candidates = [row for row in observations if 330 <= (current["end"] - row["end"]).days <= 400
                  and abs(current["duration"] - row["duration"]) <= 14 and row["end"] < current["end"]]
    return max(candidates, key=lambda row: row["end"]) if candidates else None
