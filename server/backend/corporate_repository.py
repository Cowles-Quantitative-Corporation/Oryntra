"""Point-in-time public corporate and macro research records.

This repository stores source metadata with every observation. It accepts only
public-source records supplied by a researcher or a compliant collector; it
does not scrape credentials, bypass site controls, or infer unpublished facts.
"""
from __future__ import annotations

import json
import math
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Iterable

import numpy as np
import pandas as pd

from .database import get_connection

CORPORATE_METRICS = {
    "revenue_growth_yoy", "gross_margin", "operating_margin", "net_income_margin", "free_cash_flow_margin",
    "earnings_surprise_pct", "guidance_revision_pct", "estimate_revision_pct",
    "insider_net_buy_pct", "share_count_growth_yoy", "net_debt_to_ebitda",
}
MACRO_METRICS = {
    "policy_rate", "yield_2y", "yield_10y", "credit_spread_bps", "inflation_yoy",
}
PUBLIC_SOURCE_CLASSES = {
    "sec_filing", "company_ir_pdf", "regulator_correspondence", "exchange_document",
    "central_bank_release", "official_macro_dataset",
}


def _timestamp(value: Any, field: str) -> str:
    try:
        parsed = pd.Timestamp(value)
    except Exception as exc:
        raise ValueError(f"{field} must be a valid timestamp") from exc
    if parsed.tzinfo is None:
        parsed = parsed.tz_localize("UTC")
    return parsed.tz_convert("UTC").isoformat()


def _number(value: Any, field: str) -> float:
    try:
        numeric = float(value)
    except Exception as exc:
        raise ValueError(f"{field} must be numeric") from exc
    if not math.isfinite(numeric):
        raise ValueError(f"{field} must be finite")
    return numeric


def ensure_corporate_schema() -> None:
    conn = get_connection()
    try:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS corporate_documents (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ticker TEXT NOT NULL COLLATE NOCASE,
                issuer TEXT NOT NULL,
                source_class TEXT NOT NULL,
                disclosure_status TEXT NOT NULL,
                document_title TEXT NOT NULL,
                published_at TEXT,
                available_at TEXT NOT NULL,
                original_url TEXT NOT NULL UNIQUE,
                landing_url TEXT,
                source_hash TEXT,
                metadata_json TEXT NOT NULL DEFAULT '{}',
                imported_at TEXT NOT NULL DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS corporate_facts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ticker TEXT NOT NULL COLLATE NOCASE,
                metric TEXT NOT NULL,
                value REAL NOT NULL,
                units TEXT NOT NULL DEFAULT 'ratio',
                period_end TEXT,
                published_at TEXT,
                available_at TEXT NOT NULL,
                source_class TEXT NOT NULL,
                source_url TEXT NOT NULL,
                metadata_json TEXT NOT NULL DEFAULT '{}',
                imported_at TEXT NOT NULL DEFAULT (datetime('now')),
                UNIQUE(ticker, metric, available_at, source_url)
            );
            CREATE TABLE IF NOT EXISTS macro_observations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                metric TEXT NOT NULL,
                value REAL NOT NULL,
                units TEXT NOT NULL DEFAULT 'percent',
                observation_at TEXT NOT NULL,
                available_at TEXT NOT NULL,
                source_class TEXT NOT NULL,
                source_url TEXT NOT NULL,
                metadata_json TEXT NOT NULL DEFAULT '{}',
                imported_at TEXT NOT NULL DEFAULT (datetime('now')),
                UNIQUE(metric, observation_at, available_at, source_url)
            );
            CREATE INDEX IF NOT EXISTS idx_corporate_facts_pit
                ON corporate_facts(ticker, metric, available_at);
            CREATE INDEX IF NOT EXISTS idx_corporate_documents_pit
                ON corporate_documents(ticker, available_at);
            CREATE INDEX IF NOT EXISTS idx_macro_observations_pit
                ON macro_observations(metric, available_at);
            """
        )
        conn.commit()
    finally:
        conn.close()


class CorporateRepository:
    """Local, auditable point-in-time store for public disclosures."""

    def import_documents(self, documents: Iterable[dict[str, Any]]) -> int:
        ensure_corporate_schema()
        clean: list[tuple[Any, ...]] = []
        for raw in documents:
            source_class = str(raw.get("source_class") or "").strip()
            if source_class not in PUBLIC_SOURCE_CLASSES:
                raise ValueError("source_class must identify an approved public source")
            url = str(raw.get("original_url") or "").strip()
            if not url.startswith("https://"):
                raise ValueError("original_url must be a public HTTPS URL")
            ticker = str(raw.get("ticker") or "").upper().strip()
            issuer = str(raw.get("issuer") or "").strip()
            title = str(raw.get("document_title") or "").strip()
            if not ticker or not issuer or not title:
                raise ValueError("ticker, issuer, and document_title are required")
            available_at = _timestamp(raw.get("available_at") or raw.get("published_at"), "available_at")
            published = raw.get("published_at")
            clean.append((
                ticker, issuer, source_class, str(raw.get("disclosure_status") or "public"),
                title, _timestamp(published, "published_at") if published else None, available_at,
                url, str(raw.get("landing_url") or "").strip() or None,
                str(raw.get("source_hash") or "").strip() or None,
                json.dumps(raw.get("metadata") or {}, sort_keys=True),
            ))
        conn = get_connection()
        try:
            conn.executemany(
                """INSERT INTO corporate_documents
                (ticker, issuer, source_class, disclosure_status, document_title, published_at,
                 available_at, original_url, landing_url, source_hash, metadata_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(original_url) DO UPDATE SET
                  available_at=excluded.available_at, source_hash=excluded.source_hash,
                  metadata_json=excluded.metadata_json""", clean,
            )
            conn.commit()
            return len(clean)
        finally:
            conn.close()

    def import_facts(self, facts: Iterable[dict[str, Any]]) -> int:
        ensure_corporate_schema()
        clean: list[tuple[Any, ...]] = []
        for raw in facts:
            metric = str(raw.get("metric") or "").strip()
            if metric not in CORPORATE_METRICS:
                raise ValueError(f"Unsupported corporate metric: {metric}")
            source_class = str(raw.get("source_class") or "").strip()
            if source_class not in PUBLIC_SOURCE_CLASSES:
                raise ValueError("source_class must identify an approved public source")
            url = str(raw.get("source_url") or "").strip()
            if not url.startswith("https://"):
                raise ValueError("source_url must be a public HTTPS URL")
            ticker = str(raw.get("ticker") or "").upper().strip()
            if not ticker:
                raise ValueError("ticker is required")
            available_at = _timestamp(raw.get("available_at") or raw.get("published_at"), "available_at")
            published = raw.get("published_at")
            period_end = raw.get("period_end")
            clean.append((
                ticker, metric, _number(raw.get("value"), "value"), str(raw.get("units") or "ratio"),
                _timestamp(period_end, "period_end") if period_end else None,
                _timestamp(published, "published_at") if published else None, available_at,
                source_class, url, json.dumps(raw.get("metadata") or {}, sort_keys=True),
            ))
        conn = get_connection()
        try:
            conn.executemany(
                """INSERT INTO corporate_facts
                (ticker, metric, value, units, period_end, published_at, available_at,
                 source_class, source_url, metadata_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(ticker, metric, available_at, source_url) DO UPDATE SET
                  value=excluded.value, units=excluded.units, metadata_json=excluded.metadata_json""", clean,
            )
            conn.commit()
            return len(clean)
        finally:
            conn.close()

    def import_macro(self, observations: Iterable[dict[str, Any]]) -> int:
        ensure_corporate_schema()
        clean: list[tuple[Any, ...]] = []
        for raw in observations:
            metric = str(raw.get("metric") or "").strip()
            if metric not in MACRO_METRICS:
                raise ValueError(f"Unsupported macro metric: {metric}")
            source_class = str(raw.get("source_class") or "").strip()
            url = str(raw.get("source_url") or "").strip()
            if source_class not in PUBLIC_SOURCE_CLASSES or not url.startswith("https://"):
                raise ValueError("Macro observations require an approved public HTTPS source")
            clean.append((
                metric, _number(raw.get("value"), "value"), str(raw.get("units") or "percent"),
                _timestamp(raw.get("observation_at"), "observation_at"),
                _timestamp(raw.get("available_at"), "available_at"), source_class, url,
                json.dumps(raw.get("metadata") or {}, sort_keys=True),
            ))
        conn = get_connection()
        try:
            conn.executemany(
                """INSERT INTO macro_observations
                (metric, value, units, observation_at, available_at, source_class, source_url, metadata_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(metric, observation_at, available_at, source_url) DO UPDATE SET
                  value=excluded.value, units=excluded.units, metadata_json=excluded.metadata_json""", clean,
            )
            conn.commit()
            return len(clean)
        finally:
            conn.close()

    def latest_snapshot(self, ticker: str, as_of: Any | None = None) -> dict[str, Any]:
        ensure_corporate_schema()
        cutoff = _timestamp(as_of or datetime.now(timezone.utc), "as_of")
        conn = get_connection()
        try:
            rows = conn.execute(
                """SELECT metric, value, units, available_at, source_class, source_url
                   FROM corporate_facts WHERE ticker=? AND available_at<=?
                   ORDER BY metric, available_at DESC, id DESC""", (ticker.upper().strip(), cutoff)
            ).fetchall()
        finally:
            conn.close()
        selected: dict[str, dict[str, Any]] = {}
        for row in rows:
            item = dict(row)
            selected.setdefault(item["metric"], item)
        return {"ticker": ticker.upper().strip(), "as_of": cutoff, "facts": selected,
                "coverage": round(len(selected) / len(CORPORATE_METRICS), 3)}

    def factor_panel(self, tickers: list[str], index: pd.DatetimeIndex) -> tuple[pd.DataFrame, dict[str, Any]]:
        """Return a point-in-time composite quality panel and provenance coverage."""
        ensure_corporate_schema()
        symbols = [str(t).upper().strip() for t in tickers]
        if not symbols or len(index) == 0:
            return pd.DataFrame(index=index, columns=symbols, dtype=float), {"coverage_pct": 0.0, "facts_used": 0}
        cutoff = _timestamp(index.max(), "index_end")
        placeholders = ",".join("?" for _ in symbols)
        conn = get_connection()
        try:
            rows = conn.execute(
                f"""SELECT ticker, metric, value, available_at, source_url FROM corporate_facts
                     WHERE ticker IN ({placeholders}) AND available_at<=?
                     ORDER BY ticker, available_at""", (*symbols, cutoff)
            ).fetchall()
        finally:
            conn.close()
        by_symbol: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in rows:
            item = dict(row); item["available_at"] = pd.Timestamp(item["available_at"])
            if item["available_at"].tzinfo is not None:
                item["available_at"] = item["available_at"].tz_convert(None)
            by_symbol[item["ticker"]].append(item)
        raw = pd.DataFrame(np.nan, index=index, columns=symbols, dtype=float)
        positive = {"revenue_growth_yoy", "gross_margin", "operating_margin", "net_income_margin", "free_cash_flow_margin", "earnings_surprise_pct", "guidance_revision_pct", "estimate_revision_pct", "insider_net_buy_pct"}
        negative = {"share_count_growth_yoy", "net_debt_to_ebitda"}
        for ticker in symbols:
            current: dict[str, float] = {}
            cursor = 0; rows_for_ticker = by_symbol.get(ticker, [])
            for date in index:
                naive = pd.Timestamp(date).tz_localize(None) if pd.Timestamp(date).tzinfo else pd.Timestamp(date)
                while cursor < len(rows_for_ticker) and rows_for_ticker[cursor]["available_at"] <= naive:
                    record = rows_for_ticker[cursor]
                    current[record["metric"]] = float(record["value"]); cursor += 1
                components = [np.tanh(current[m] / 25.0) for m in positive if m in current]
                components += [-np.tanh(current[m] / 4.0) for m in negative if m in current]
                if len(components) >= 2:
                    raw.at[date, ticker] = float(np.mean(components))
        ranks = raw.rank(axis=1, pct=True, method="average")
        score = (ranks - 0.5) * 2.0
        coverage = raw.notna().sum().sum() / max(1, raw.size)
        return score.fillna(0.0), {"coverage_pct": round(float(coverage * 100), 2), "facts_used": len(rows), "source_urls": sorted({str(r["source_url"]) for r in rows})[:100]}

    def macro_snapshot(self, as_of: Any | None = None) -> dict[str, Any]:
        ensure_corporate_schema()
        cutoff = _timestamp(as_of or datetime.now(timezone.utc), "as_of")
        conn = get_connection()
        try:
            rows = conn.execute(
                """SELECT metric, value, units, observation_at, available_at, source_url
                   FROM macro_observations WHERE available_at<=?
                   ORDER BY metric, available_at DESC, id DESC""", (cutoff,)
            ).fetchall()
        finally:
            conn.close()
        selected: dict[str, dict[str, Any]] = {}
        for row in rows:
            item = dict(row); selected.setdefault(item["metric"], item)
        curve = None
        if "yield_10y" in selected and "yield_2y" in selected:
            curve = float(selected["yield_10y"]["value"]) - float(selected["yield_2y"]["value"])
        return {"as_of": cutoff, "observations": selected, "yield_curve_slope_pct": curve,
                "coverage": round(len(selected) / len(MACRO_METRICS), 3)}

    def macro_panel(self, index: pd.DatetimeIndex) -> tuple[pd.DataFrame, dict[str, Any]]:
        """As-of macro features, forward-filled only after recorded availability."""
        ensure_corporate_schema()
        panel = pd.DataFrame(index=index, columns=sorted(MACRO_METRICS), dtype=float)
        if len(index) == 0:
            return panel, {"coverage_pct": 0.0, "observations_used": 0}
        cutoff = _timestamp(index.max(), "index_end")
        conn = get_connection()
        try:
            rows = conn.execute(
                """SELECT metric, value, available_at, source_url FROM macro_observations
                   WHERE available_at<=? ORDER BY available_at, id""", (cutoff,)
            ).fetchall()
        finally:
            conn.close()
        current: dict[str, float] = {}
        cursor = 0
        normalized = []
        for row in rows:
            item = dict(row); available = pd.Timestamp(item["available_at"])
            item["available_at"] = available.tz_convert(None) if available.tzinfo else available
            normalized.append(item)
        for date in index:
            naive = pd.Timestamp(date).tz_localize(None) if pd.Timestamp(date).tzinfo else pd.Timestamp(date)
            while cursor < len(normalized) and normalized[cursor]["available_at"] <= naive:
                current[str(normalized[cursor]["metric"])] = float(normalized[cursor]["value"])
                cursor += 1
            for metric, value in current.items():
                panel.at[date, metric] = value
        coverage = panel.notna().sum().sum() / max(1, panel.size)
        return panel, {"coverage_pct": round(float(coverage * 100), 2), "observations_used": len(rows), "source_urls": sorted({str(row["source_url"]) for row in normalized})[:100]}


_REPOSITORY: CorporateRepository | None = None


def get_corporate_repository() -> CorporateRepository:
    global _REPOSITORY
    if _REPOSITORY is None:
        _REPOSITORY = CorporateRepository()
    return _REPOSITORY
