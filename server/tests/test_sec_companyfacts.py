import pandas as pd
import pytest

from backend.sec_companyfacts import conservative_available_at, normalize_quarterly_companyfacts


def _fact(start, end, filed, value, tag, form="10-Q"):
    return {"start": start, "end": end, "filed": filed, "val": value, "form": form, "tag": tag}


def test_companyfacts_normalization_uses_first_filing_and_next_session_availability():
    revenue_tag, gross_tag, operating_tag, income_tag = "RevenueFromContractWithCustomerExcludingAssessedTax", "GrossProfit", "OperatingIncomeLoss", "NetIncomeLoss"
    payload = {"cik": 320193, "facts": {"us-gaap": {
        revenue_tag: {"units": {"USD": [
            _fact("2022-01-01", "2022-03-31", "2022-05-01", 100, revenue_tag),
            _fact("2023-01-01", "2023-03-31", "2023-05-03", 120, revenue_tag),
            _fact("2023-01-01", "2023-03-31", "2023-08-01", 999, revenue_tag),
        ]}},
        gross_tag: {"units": {"USD": [
            _fact("2023-01-01", "2023-03-31", "2023-05-03", 54, gross_tag),
        ]}},
        operating_tag: {"units": {"USD": [
            _fact("2023-01-01", "2023-03-31", "2023-05-03", 24, operating_tag),
        ]}},
        income_tag: {"units": {"USD": [
            _fact("2023-01-01", "2023-03-31", "2023-05-03", 18, income_tag),
        ]}},
    }}}
    records = normalize_quarterly_companyfacts(payload, "aapl")
    growth = next(row for row in records if row["metric"] == "revenue_growth_yoy")
    margin = next(row for row in records if row["metric"] == "operating_margin")
    gross_margin = next(row for row in records if row["metric"] == "gross_margin")
    income_margin = next(row for row in records if row["metric"] == "net_income_margin")
    assert growth["value"] == pytest.approx(20)
    assert margin["value"] == pytest.approx(20)
    assert gross_margin["value"] == pytest.approx(45)
    assert income_margin["value"] == pytest.approx(15)
    assert growth["available_at"].startswith("2023-05-04")
    assert growth["metadata"]["filed"] == "2023-05-03"
    assert growth["source_url"].endswith("CIK0000320193.json")
    assert pd.Timestamp(conservative_available_at("2023-05-03")).tzinfo is not None


def test_companyfacts_normalization_refuses_annual_and_incomplete_payloads():
    payload = {"cik": 1, "facts": {"us-gaap": {"Revenues": {"units": {"USD": [
        _fact("2022-01-01", "2022-12-31", "2023-02-01", 100, "Revenues", "10-K"),
    ]}}}}}
    assert normalize_quarterly_companyfacts(payload, "AAA") == []


def test_companyfacts_normalization_keeps_zero_revenue_growth_but_not_undefined_margin():
    payload = {"cik": 1, "facts": {"us-gaap": {
        "Revenues": {"units": {"USD": [
            _fact("2022-01-01", "2022-03-31", "2022-05-01", 100, "Revenues"),
            _fact("2023-01-01", "2023-03-31", "2023-05-01", 0, "Revenues"),
        ]}},
        "OperatingIncomeLoss": {"units": {"USD": [
            _fact("2023-01-01", "2023-03-31", "2023-05-01", 10, "OperatingIncomeLoss"),
        ]}},
    }}}
    records = normalize_quarterly_companyfacts(payload, "AAA")
    assert [row["metric"] for row in records] == ["revenue_growth_yoy"]
    assert records[0]["value"] == -100
