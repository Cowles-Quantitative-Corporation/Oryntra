"""Strict point-in-time SEC fundamentals for private Oryntra Control 5.4.

The module intentionally separates collection from eligibility.  Companyfacts
and submissions may contain later amendments/restatements, but a value becomes
model-eligible only on the first trading session strictly *after* the SEC
acceptance date for that filing.  When acceptance time is unavailable, the
filed date is retained as an explicitly diagnosed fallback and is likewise
eligible only from the following trading session.

Network collection is cached and enabled by default only inside the private
Oryntra Control desktop runtime when the operator explicitly selects SEC PIT.
Server/non-local deployments remain fail-closed unless ``ORYNTRA_SEC_PIT_ALLOW_API``
is enabled. The pure normalization/feature functions are deterministic and fixture-testable.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import json
import math
import os
from pathlib import Path
import time
from typing import Any, Iterable
from urllib.request import Request, urlopen

import numpy as np
import pandas as pd

SEC_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SEC_COMPANYFACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json"
SEC_SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik:010d}.json"

# First tag present wins only when comparing observations with the same filing
# availability; all aliases are otherwise preserved as observations.
CANONICAL_TAGS: dict[str, tuple[str, ...]] = {
    "revenue": ("RevenueFromContractWithCustomerExcludingAssessedTax", "Revenues", "SalesRevenueNet", "Revenue"),
    "gross_profit": ("GrossProfit",),
    "net_income": ("NetIncomeLoss", "ProfitLoss"),
    "operating_cash_flow": ("NetCashProvidedByUsedInOperatingActivities", "NetCashFlowsFromUsedInOperatingActivities"),
    "capex": ("PaymentsToAcquirePropertyPlantAndEquipment", "PurchaseOfPropertyPlantAndEquipment"),
    "assets": ("Assets",),
    "equity": ("StockholdersEquity", "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest", "Equity"),
    "debt": ("LongTermDebtAndFinanceLeaseObligationsCurrent", "LongTermDebtCurrent", "LongTermDebtNoncurrent", "LongTermDebt"),
    "cash": ("CashAndCashEquivalentsAtCarryingValue", "CashAndCashEquivalents"),
    "shares_outstanding": ("EntityCommonStockSharesOutstanding", "CommonStockSharesOutstanding"),
}

FLOW_METRICS = {"revenue", "gross_profit", "net_income", "operating_cash_flow", "capex"}
INSTANT_METRICS = {"assets", "equity", "debt", "cash", "shares_outstanding"}
VALUE_FEATURES = ("book_to_price", "earnings_yield", "fcf_yield", "sales_yield")
QUALITY_FEATURES = (
    "accrual_quality", "asset_growth_yoy", "cashflow_profitability", "debt_to_assets",
    "fcf_profitability", "gross_margin", "gross_profitability", "revenue_growth_yoy", "roa", "roe",
)


@dataclass(frozen=True)
class PitFact:
    ticker: str
    cik: str
    issuer: str
    canonical: str
    namespace: str
    tag: str
    unit: str
    value: float
    start: str | None
    end: str
    filed: str
    accepted: str | None
    accession: str | None
    form: str
    eligible_session: str
    acceptance_source: str

    @property
    def start_ts(self) -> pd.Timestamp | None:
        return None if self.start is None else pd.Timestamp(self.start)

    @property
    def end_ts(self) -> pd.Timestamp:
        return pd.Timestamp(self.end)

    @property
    def duration_days(self) -> int | None:
        return None if self.start is None else int((self.end_ts - self.start_ts).days)


@dataclass(frozen=True)
class StrictPitFact(PitFact):
    # SEC's fy/fp identify the reporting filing, not necessarily this comparative
    # fact's economic fiscal year/quarter. Retain them as provenance only.
    reported_fiscal_year: int | None = None
    reported_fiscal_period: str | None = None
    sec_frame: str | None = None
    # An accession's own report date is independent of comparative fact fp/fy.
    # Used only to establish causally available economic period identity.
    filing_report_date: str | None = None
    source_value_status: str = "VALID"
    source_value_reason: str | None = None
    source_value_repr: str | None = None


def _bool_env(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def sec_network_allowed() -> bool:
    """Whether SEC collection may use the network.

    An explicit ORYNTRA_SEC_PIT_ALLOW_API value always wins. In the native private
    desktop runtime, selecting SEC PIT is itself the operator opt-in, so collection
    defaults on. Non-local/server contexts preserve the historical fail-closed default.
    """
    raw = os.getenv("ORYNTRA_SEC_PIT_ALLOW_API")
    if raw is not None:
        return raw.strip().lower() in {"1", "true", "yes", "on"}
    return _bool_env("ORYNTRA_CONTROL_LOCAL_MODE", False)


def cache_root() -> Path:
    configured = os.getenv("ORYNTRA_SEC_PIT_CACHE")
    if configured:
        return Path(configured).expanduser()
    if _bool_env("ORYNTRA_CONTROL_LOCAL_MODE", False):
        return Path.home() / "Library" / "Application Support" / "Oryntra Control" / "cache" / "sec_pit"
    return Path(__file__).resolve().parents[1] / "data" / "sec_pit"


def _clean_sessions(sessions: pd.DatetimeIndex) -> pd.DatetimeIndex:
    idx = pd.DatetimeIndex(sessions)
    if idx.tz is not None:
        idx = idx.tz_localize(None)
    return idx.normalize().sort_values().drop_duplicates()


def first_session_strictly_after(value: Any, sessions: pd.DatetimeIndex) -> pd.Timestamp | None:
    """Return the first supplied trading session after the disclosure date."""
    idx = _clean_sessions(sessions)
    if not len(idx):
        return None
    ts = pd.Timestamp(value)
    if ts.tzinfo is not None:
        ts = ts.tz_convert("UTC").tz_localize(None)
    disclosure_date = ts.normalize()
    pos = int(idx.searchsorted(disclosure_date, side="right"))
    return None if pos >= len(idx) else idx[pos]


def submissions_acceptance_map(payload: dict[str, Any]) -> dict[str, str]:
    """Map SEC accession number -> acceptanceDateTime from submissions JSON."""
    recent = ((payload or {}).get("filings") or {}).get("recent") or {}
    accns = list(recent.get("accessionNumber") or [])
    accepted = list(recent.get("acceptanceDateTime") or [])
    out: dict[str, str] = {}
    for i, accn in enumerate(accns):
        if not accn:
            continue
        value = accepted[i] if i < len(accepted) else None
        if value:
            out[str(accn)] = str(value)
    return out


def submissions_report_date_map(payload: dict[str, Any]) -> dict[str, str]:
    """Read report-date evidence without changing disclosure eligibility.

    Optional historical_report_dates is an accession -> reportDate mapping
    materialized from separately archived SEC submissions metadata. Conflicting
    or malformed declarations remain unavailable, never last-write-wins.
    """
    filings = (payload or {}).get("filings") or {}
    recent = filings.get("recent") or {}
    accns, dates = recent.get("accessionNumber") or [], recent.get("reportDate") or []
    declarations: dict[str, set[str | None]] = {}
    pairs = [(accn, dates[i] if i < len(dates) else None) for i, accn in enumerate(accns)]
    history = filings.get("historical_report_dates") or {}
    if not isinstance(history, dict):
        raise ValueError("historical_report_dates must map accession numbers to report dates")
    pairs.extend(history.items())
    for accn, value in pairs:
        if not accn or not value:
            continue
        try:
            date = pd.Timestamp(value)
            valid = not pd.isna(date) and str(value) == str(date.date())
            cleaned = str(date.date()) if valid else None
        except (ValueError, TypeError, OverflowError):
            cleaned = None
        declarations.setdefault(str(accn), set()).add(cleaned)
    return {accn: next(iter(values)) for accn, values in declarations.items()
            if len(values) == 1 and None not in values}


def _canonical_for_tag(tag: str) -> str | None:
    for canonical, aliases in CANONICAL_TAGS.items():
        if tag in aliases:
            return canonical
    return None


def _iter_units(concept: dict[str, Any]) -> Iterable[tuple[str, dict[str, Any]]]:
    for unit, rows in (concept.get("units") or {}).items():
        if not isinstance(rows, list):
            continue
        for row in rows:
            if isinstance(row, dict):
                yield str(unit), row


def normalize_companyfacts(
    companyfacts: dict[str, Any], ticker: str, sessions: pd.DatetimeIndex,
    submissions: dict[str, Any] | None = None,
    *, semantics_version: str = "legacy",
) -> list[PitFact]:
    """Normalize canonical US-GAAP/IFRS facts with strict PIT eligibility."""
    idx = _clean_sessions(sessions)
    if semantics_version not in {"legacy", "strict_v1"}:
        raise ValueError("Unknown SEC normalization semantics_version")
    forms = {"10-Q", "10-Q/A", "10-K", "10-K/A", "20-F", "20-F/A", "40-F", "40-F/A", "6-K", "6-K/A"}
    if semantics_version == "strict_v1":
        # Preserve new transition targets so they become explicitly unsupported
        # rather than vanishing and leaving stale pre-transition features alive.
        forms |= {"10-KT", "10-KT/A", "10-QT", "10-QT/A"}
    clean_ticker = str(ticker).upper().strip()
    if not clean_ticker:
        raise ValueError("ticker is required")
    try:
        cik = f"{int(companyfacts.get('cik')):010d}"
    except Exception as exc:
        raise ValueError("Companyfacts payload requires numeric cik") from exc
    issuer = str(companyfacts.get("entityName") or clean_ticker)
    accepted_by_accn = submissions_acceptance_map(submissions or {})
    report_dates = submissions_report_date_map(submissions or {}) if semantics_version == "strict_v1" else {}
    facts: list[PitFact] = []
    namespaces = companyfacts.get("facts") or {}
    for namespace in ("us-gaap", "ifrs-full"):
        concepts = namespaces.get(namespace) or {}
        for tag, concept in concepts.items():
            canonical = _canonical_for_tag(str(tag))
            if canonical is None:
                continue
            alias_priority = CANONICAL_TAGS[canonical].index(str(tag))
            for unit, raw in _iter_units(concept):
                invalid_value = False
                try:
                    end = pd.Timestamp(raw["end"]).normalize()
                    filed = pd.Timestamp(raw["filed"]).normalize()
                    form = str(raw.get("form") or "")
                    if semantics_version == "strict_v1" and (pd.isna(end) or pd.isna(filed)):
                        raise ValueError("missing date")
                except Exception:
                    if semantics_version == "strict_v1":
                        raise ValueError(f"Strict SEC fact has unusable target/disclosure dates: {tag}")
                    continue
                try:
                    value = float(raw["val"])
                    invalid_value = not math.isfinite(value) or (semantics_version == "strict_v1" and isinstance(raw["val"], bool))
                except (KeyError, ValueError, TypeError, OverflowError):
                    invalid_value = True
                if form not in forms:
                    continue
                if invalid_value:
                    if semantics_version == "legacy":
                        continue
                    # Numeric storage sentinel is never admissible as a fact:
                    # the strict resolver reads source_value_status first.
                    value = 0.0
                start = None
                if raw.get("start"):
                    try:
                        start = pd.Timestamp(raw["start"]).normalize()
                        if semantics_version == "strict_v1" and pd.isna(start):
                            raise ValueError("missing start")
                    except Exception:
                        start = None
                        if semantics_version == "strict_v1":
                            raise ValueError(f"Strict SEC fact has unusable start date: {tag}")
                accn = str(raw.get("accn") or "") or None
                accepted = accepted_by_accn.get(accn or "")
                disclosed = accepted or str(filed.date())
                eligible = first_session_strictly_after(disclosed, idx)
                if eligible is None:
                    continue
                # Store alias priority in a deterministic suffix used only in sort
                # order; it is not exposed as a model feature.
                extra = {}
                if semantics_version == "strict_v1":
                    extra = {"reported_fiscal_year": raw.get("fy") if isinstance(raw.get("fy"), int) else None,
                             "reported_fiscal_period": str(raw["fp"]) if raw.get("fp") is not None else None,
                             "sec_frame": str(raw["frame"]) if raw.get("frame") is not None else None,
                             "filing_report_date": report_dates.get(accn or ""),
                             "source_value_status": "INVALID" if invalid_value else "VALID",
                             "source_value_reason": "missing_or_nonfinite_or_boolean_source_value" if invalid_value else None,
                             "source_value_repr": repr(raw.get("val")) if invalid_value else None}
                fact_class = StrictPitFact if semantics_version == "strict_v1" else PitFact
                facts.append(fact_class(
                    ticker=clean_ticker, cik=cik, issuer=issuer, canonical=canonical,
                    namespace=namespace, tag=str(tag), unit=unit, value=value,
                    start=None if start is None else str(start.date()), end=str(end.date()),
                    filed=str(filed.date()), accepted=accepted, accession=accn, form=form,
                    eligible_session=str(eligible.date()),
                    acceptance_source="acceptance_datetime" if accepted else "filed_date_fallback",
                    **extra,
                ))
    # Deterministic event order. Same-filing aliases prefer canonical tag order.
    facts.sort(key=lambda f: (
        pd.Timestamp(f.eligible_session), pd.Timestamp(f.end), f.canonical,
        CANONICAL_TAGS[f.canonical].index(f.tag), f.accession or "", f.tag,
    ))
    return facts


def _eligible_latest_versions(facts: Iterable[PitFact], session: pd.Timestamp) -> dict[tuple[str, str, str | None], PitFact]:
    """Latest eligible filing version for each canonical period.

    A later amendment replaces an earlier value only from the amendment's own
    eligibility session forward, which is the key restatement PIT rule.
    """
    cutoff = pd.Timestamp(session).normalize()
    versions: dict[tuple[str, str, str | None], PitFact] = {}
    for fact in facts:
        if pd.Timestamp(fact.eligible_session) > cutoff:
            break
        key = (fact.canonical, fact.end, fact.start)
        previous = versions.get(key)
        if previous is None or pd.Timestamp(fact.eligible_session) >= pd.Timestamp(previous.eligible_session):
            # Prefer the higher-priority alias for observations becoming eligible
            # on the exact same session/period.
            if previous is not None and fact.eligible_session == previous.eligible_session:
                if CANONICAL_TAGS[fact.canonical].index(fact.tag) > CANONICAL_TAGS[previous.canonical].index(previous.tag):
                    continue
            versions[key] = fact
    return versions


def _flow_rows(versions: dict[tuple[str, str, str | None], PitFact], metric: str) -> list[PitFact]:
    rows = [f for f in versions.values() if f.canonical == metric and f.start is not None]
    return sorted(rows, key=lambda f: (f.end_ts, pd.Timestamp(f.eligible_session)))


def _instant_latest(versions: dict[tuple[str, str, str | None], PitFact], metric: str) -> PitFact | None:
    rows = [f for f in versions.values() if f.canonical == metric]
    return max(rows, key=lambda f: (f.end_ts, pd.Timestamp(f.eligible_session))) if rows else None


def ttm_value(versions: dict[tuple[str, str, str | None], PitFact], metric: str) -> tuple[float | None, str | None]:
    """Point-in-time TTM: reported annual; annual + YTD - prior YTD; four quarters."""
    rows = _flow_rows(versions, metric)
    if not rows:
        return None, None
    latest_end = max(f.end_ts for f in rows)
    # If the newest reported period itself is annual, use it directly.
    annuals = [f for f in rows if f.duration_days is not None and 330 <= f.duration_days <= 400]
    latest_annual = max(annuals, key=lambda f: f.end_ts) if annuals else None
    if latest_annual is not None and latest_annual.end_ts == latest_end:
        return float(latest_annual.value), "reported_annual"

    # Annual + current YTD - prior-year comparable YTD.
    ytd = [f for f in rows if f.duration_days is not None and 135 <= f.duration_days <= 300]
    current_ytd = max((f for f in ytd if f.end_ts == latest_end), key=lambda f: f.duration_days or 0, default=None)
    if current_ytd is not None:
        prior_ytd = [f for f in ytd if 330 <= (current_ytd.end_ts - f.end_ts).days <= 400 and abs((current_ytd.duration_days or 0) - (f.duration_days or 0)) <= 20]
        prior_annuals = [f for f in annuals if f.end_ts < current_ytd.end_ts]
        if prior_ytd and prior_annuals:
            py = max(prior_ytd, key=lambda f: f.end_ts)
            pa = max(prior_annuals, key=lambda f: f.end_ts)
            return float(pa.value + current_ytd.value - py.value), "annual_plus_ytd_minus_prior_ytd"

    # Standalone-quarter fallback: four most recent 75-110 day observations.
    quarters = [f for f in rows if f.duration_days is not None and 75 <= f.duration_days <= 110]
    if len(quarters) >= 4:
        q = sorted(quarters, key=lambda f: f.end_ts)[-4:]
        if (q[-1].end_ts - q[0].end_ts).days <= 400:
            return float(sum(f.value for f in q)), "four_standalone_quarters"
    return None, None


def _prior_year_instant(versions: dict[tuple[str, str, str | None], PitFact], metric: str, current: PitFact) -> PitFact | None:
    rows = [f for f in versions.values() if f.canonical == metric and f.end_ts < current.end_ts and 330 <= (current.end_ts - f.end_ts).days <= 400]
    return max(rows, key=lambda f: f.end_ts) if rows else None


def _prior_year_flow_value(versions: dict[tuple[str, str, str | None], PitFact], metric: str, current_end: pd.Timestamp) -> float | None:
    # For growth diagnostics prefer comparable standalone/annual period ending a year earlier.
    rows = [f for f in _flow_rows(versions, metric) if 330 <= (current_end - f.end_ts).days <= 400]
    if not rows:
        return None
    return float(max(rows, key=lambda f: f.end_ts).value)


def _split_adjusted_shares(shares: PitFact, history: pd.DataFrame | None, session: pd.Timestamp) -> float:
    value = float(shares.value)
    if history is None or "Stock Splits" not in history.columns:
        return value
    split = pd.to_numeric(history["Stock Splits"], errors="coerce").fillna(0.0)
    idx = pd.DatetimeIndex(split.index)
    if idx.tz is not None:
        idx = idx.tz_localize(None)
    split.index = idx.normalize()
    mask = (split.index > shares.end_ts) & (split.index <= pd.Timestamp(session).normalize()) & (split > 0)
    if mask.any():
        value *= float(split.loc[mask].prod())
    return value


def derive_features(
    facts: list[PitFact], session: pd.Timestamp, history: pd.DataFrame | None = None,
    *, semantics_version: str = "legacy", strict_options: dict[str, Any] | None = None,
) -> tuple[dict[str, float], dict[str, Any]]:
    if semantics_version == "strict_v1":
        from .sec_pit_strict import derive
        return derive(facts, session, history, strict_options)
    if semantics_version != "legacy" or strict_options is not None:
        raise ValueError("Use explicit semantics_version='strict_v1' for strict SEC options")
    versions = _eligible_latest_versions(facts, session)
    features: dict[str, float] = {}
    ttm: dict[str, float] = {}
    ttm_policy: dict[str, str] = {}
    for metric in FLOW_METRICS:
        value, policy = ttm_value(versions, metric)
        if value is not None:
            ttm[metric] = value
            ttm_policy[metric] = str(policy)
    inst = {metric: _instant_latest(versions, metric) for metric in INSTANT_METRICS}
    assets = float(inst["assets"].value) if inst.get("assets") else None
    equity = float(inst["equity"].value) if inst.get("equity") else None
    debt = float(inst["debt"].value) if inst.get("debt") else None
    shares_fact = inst.get("shares_outstanding")
    revenue = ttm.get("revenue"); gross = ttm.get("gross_profit"); ni = ttm.get("net_income"); ocf = ttm.get("operating_cash_flow")
    capex = ttm.get("capex")
    fcf = None if ocf is None else float(ocf - abs(capex or 0.0))

    def ratio(name: str, num: float | None, den: float | None, *, negate_abs: bool = False):
        if num is None or den is None or not math.isfinite(float(den)) or abs(float(den)) <= 1e-12:
            return
        v = float(num) / float(den)
        features[name] = -abs(v) if negate_abs else v

    ratio("gross_margin", gross, revenue)
    ratio("gross_profitability", gross, assets)
    ratio("cashflow_profitability", ocf, assets)
    ratio("fcf_profitability", fcf, assets)
    ratio("roa", ni, assets)
    ratio("roe", ni, equity)
    ratio("debt_to_assets", debt, assets)
    if ni is not None and ocf is not None:
        ratio("accrual_quality", ni - ocf, assets, negate_abs=True)
    if inst.get("assets") and assets not in (None, 0):
        prior_assets = _prior_year_instant(versions, "assets", inst["assets"])
        if prior_assets is not None and prior_assets.value:
            features["asset_growth_yoy"] = float(assets / prior_assets.value - 1.0)
    # Revenue growth uses the latest reported revenue flow compared with a comparable year-prior flow.
    rev_rows = _flow_rows(versions, "revenue")
    if rev_rows:
        latest_rev = max(rev_rows, key=lambda f: f.end_ts)
        prior_rev = _prior_year_flow_value(versions, "revenue", latest_rev.end_ts)
        if prior_rev not in (None, 0):
            features["revenue_growth_yoy"] = float(latest_rev.value / prior_rev - 1.0)

    foreign_or_adr = any(f.namespace == "ifrs-full" or f.form.startswith(("20-F", "40-F")) for f in versions.values())
    market_cap = None
    if not foreign_or_adr and shares_fact is not None and history is not None and len(history):
        h = history.copy()
        hidx = pd.DatetimeIndex(h.index)
        if hidx.tz is not None:
            hidx = hidx.tz_localize(None)
        h.index = hidx.normalize()
        close_rows = h.loc[h.index <= pd.Timestamp(session).normalize()]
        if not close_rows.empty and "Close" in close_rows:
            close = float(close_rows.iloc[-1]["Close"])
            shares = _split_adjusted_shares(shares_fact, h, session)
            if close > 0 and shares > 0:
                market_cap = close * shares
    if market_cap and market_cap > 0:
        ratio("book_to_price", equity, market_cap)
        ratio("earnings_yield", ni, market_cap)
        ratio("fcf_yield", fcf, market_cap)
        ratio("sales_yield", revenue, market_cap)

    diag = {
        "eligible_fact_versions": len(versions),
        "ttm_policy": ttm_policy,
        "market_cap_basis": "unadjusted_close_x_sec_shares_split_adjusted_after_fact_period" if market_cap else None,
        "market_cap": market_cap,
        "foreign_or_adr_value_basis_withheld": bool(foreign_or_adr),
    }
    return features, diag


def _rank_composite(feature_panels: dict[str, pd.DataFrame], names: tuple[str, ...], *, negative: set[str] | None = None) -> pd.DataFrame:
    negative = negative or set()
    available = [feature_panels[name] for name in names if name in feature_panels]
    if not available:
        return pd.DataFrame()
    idx, cols = available[0].index, available[0].columns
    parts = []
    for name in names:
        frame = feature_panels.get(name)
        if frame is None:
            continue
        ranked = frame.rank(axis=1, pct=True, method="average")
        centered = (ranked - 0.5) * 2.0
        if name in negative:
            centered = -centered
        parts.append(centered)
    stacked = sum(parts) / len(parts)
    # Preserve NaN unless at least two component observations exist at the row/name.
    count = sum(frame.notna().astype(int) for frame in [feature_panels[n] for n in names if n in feature_panels])
    return stacked.where(count >= 2).reindex(index=idx, columns=cols)


def build_factor_panels(
    facts_by_symbol: dict[str, list[PitFact]], sessions: pd.DatetimeIndex,
    histories: dict[str, pd.DataFrame] | None = None,
    *, semantics_version: str = "legacy", strict_options: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if semantics_version == "strict_v1":
        from .sec_pit_strict import build
        return build(facts_by_symbol, sessions, histories, strict_options)
    if semantics_version != "legacy" or strict_options is not None:
        raise ValueError("Use explicit semantics_version='strict_v1' for strict SEC options")
    idx = _clean_sessions(sessions)
    symbols = sorted(facts_by_symbol)
    all_names = tuple(dict.fromkeys((*QUALITY_FEATURES, *VALUE_FEATURES)))
    panels = {name: pd.DataFrame(np.nan, index=idx, columns=symbols, dtype=float) for name in all_names}
    issuer_diagnostics: dict[str, Any] = {}
    for symbol in symbols:
        facts = facts_by_symbol[symbol]
        events = sorted({pd.Timestamp(f.eligible_session) for f in facts if pd.Timestamp(f.eligible_session) in idx})
        latest_diag: dict[str, Any] = {}
        for event in events:
            values, latest_diag = derive_features(facts, event, (histories or {}).get(symbol))
            for name, value in values.items():
                if name in panels and math.isfinite(float(value)):
                    panels[name].at[event, symbol] = float(value)
        for name in panels:
            panels[name][symbol] = panels[name][symbol].ffill()
        issuer_diagnostics[symbol] = {
            "facts": len(facts),
            "acceptance_timestamp_observations": sum(1 for f in facts if f.acceptance_source == "acceptance_datetime"),
            "filed_date_fallback_observations": sum(1 for f in facts if f.acceptance_source == "filed_date_fallback"),
            "latest": latest_diag,
        }
    quality = _rank_composite(panels, QUALITY_FEATURES, negative={"asset_growth_yoy", "debt_to_assets"})
    value = _rank_composite(panels, VALUE_FEATURES)
    qcov = 0.0 if quality.empty else float(quality.notna().to_numpy().mean() * 100)
    vcov = 0.0 if value.empty else float(value.notna().to_numpy().mean() * 100)
    return {
        "quality": quality, "value": value, "features": panels,
        "metadata": {
            "mode": "sec_pit", "pit_policy": "SEC filing information becomes model-eligible on the first trading session strictly after SEC acceptance date",
            "ttm_policy": "reported annual; otherwise annual + current YTD - prior-year comparable YTD; standalone-quarter fallback",
            "value_basis_policy": "unadjusted historical close x SEC shares outstanding; split-adjusted only for split events after shares-fact period end; unsafe foreign/ADR value basis withheld",
            "latest_quality_coverage_pct": round(qcov, 2), "latest_value_coverage_pct": round(vcov, 2),
            "missing_values_preserved": True, "issuer_diagnostics": issuer_diagnostics,
            "normalized_fact_observations": sum(len(v) for v in facts_by_symbol.values()),
        },
    }


def _request_json(url: str) -> dict[str, Any]:
    agent = os.getenv(
        "ORYNTRA_SEC_USER_AGENT",
        "Cowles Quantitative Corporation Oryntra Control research admin@cowlesquantcorp.com",
    )
    # Do not advertise gzip here: urllib does not transparently decode all content
    # encodings. A plain JSON response avoids sporadic decode failures on macOS.
    req = Request(
        url,
        headers={
            "User-Agent": agent,
            "Accept": "application/json",
            "Host": "data.sec.gov" if "data.sec.gov" in url else "www.sec.gov",
        },
    )
    last_exc: Exception | None = None
    for attempt in range(4):
        try:
            with urlopen(req, timeout=30) as response:  # nosec - fixed official SEC URLs only
                return json.loads(response.read().decode("utf-8"))
        except Exception as exc:
            last_exc = exc
            if attempt >= 3:
                raise
            time.sleep(0.5 * (2 ** attempt))
    raise RuntimeError(f"SEC request failed: {last_exc}")


def _load_or_fetch(path: Path, url: str) -> dict[str, Any] | None:
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            pass
    if not sec_network_allowed():
        return None
    data = _request_json(url)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")
    time.sleep(0.12)  # conservative fair-access pacing
    return data


def ticker_cik_map() -> dict[str, int]:
    root = cache_root(); path = root / "company_tickers.json"
    payload = _load_or_fetch(path, SEC_TICKERS_URL)
    if not payload:
        return {}
    out: dict[str, int] = {}
    rows = payload.values() if isinstance(payload, dict) else payload
    for row in rows:
        if not isinstance(row, dict):
            continue
        try:
            out[str(row["ticker"]).upper()] = int(row["cik_str"])
        except Exception:
            continue
    return out


def cached_factor_panels(
    symbols: list[str], sessions: pd.DatetimeIndex, *, histories: dict[str, pd.DataFrame] | None = None,
    semantics_version: str = "legacy", strict_options: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Load/fetch cached SEC PIT inputs and construct quality/value panels.

    Missing issuers remain missing rather than becoming neutral scores.
    """
    if semantics_version == "strict_v1":
        from .sec_pit_strict import validate_options
        validate_options(strict_options)
    elif semantics_version != "legacy" or strict_options is not None:
        raise ValueError("Use explicit semantics_version='strict_v1' for strict SEC options")
    root = cache_root(); cik_map = ticker_cik_map(); facts_by_symbol: dict[str, list[PitFact]] = {}
    unresolved: list[str] = []; errors: dict[str, str] = {}
    for symbol in [str(x).upper().strip() for x in symbols]:
        cik = cik_map.get(symbol)
        if not cik:
            unresolved.append(symbol); facts_by_symbol[symbol] = []; continue
        try:
            cf = _load_or_fetch(root / "companyfacts" / f"CIK{cik:010d}.json", SEC_COMPANYFACTS_URL.format(cik=cik))
            sub = _load_or_fetch(root / "submissions" / f"CIK{cik:010d}.json", SEC_SUBMISSIONS_URL.format(cik=cik))
            if not cf:
                facts_by_symbol[symbol] = []; errors[symbol] = "companyfacts_unavailable"; continue
            facts_by_symbol[symbol] = normalize_companyfacts(cf, symbol, sessions, sub, semantics_version=semantics_version)
        except Exception as exc:
            facts_by_symbol[symbol] = []; errors[symbol] = f"{type(exc).__name__}: {exc}"
    result = build_factor_panels(facts_by_symbol, sessions, histories=histories,
                                 semantics_version=semantics_version, strict_options=strict_options)
    meta = result["metadata"]
    meta.update({
        "ciks_resolved": len(symbols) - len(unresolved), "companies_with_usable_facts": sum(bool(v) for v in facts_by_symbol.values()),
        "coverage_pct": round(100.0 * sum(bool(v) for v in facts_by_symbol.values()) / max(1, len(symbols)), 2),
        "unresolved_tickers": unresolved, "errors": errors,
        "network_collection_enabled": sec_network_allowed(),
        "cache_path": str(root),
    })
    return result
