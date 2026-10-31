from __future__ import annotations

import csv
import io
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Dict, List, Mapping, Optional, Sequence

import numpy as np
import pandas as pd

from .repository import DEFAULT_SERIES_POLICIES, MacroRepository, SeriesPolicy
from .types import ConsensusEstimate, MacroRevision


@dataclass(frozen=True)
class ActualRelease:
    metric: str
    series_id: str
    observation_date: date
    available_to_model_at: datetime
    eligible_session: date
    actual: float
    units: str
    event_id: str


class ConsensusRepository:
    """Timestamped economist expectations store.

    Consensus is never fabricated. An estimate must have been collected strictly
    before its own scheduled release timestamp.
    """

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(self.db_path)
        con.row_factory = sqlite3.Row
        return con

    def _init_db(self) -> None:
        with self._connect() as con:
            con.executescript(
                """
                CREATE TABLE IF NOT EXISTS consensus_estimates (
                    metric TEXT NOT NULL,
                    release_at TEXT NOT NULL,
                    collected_at TEXT NOT NULL,
                    expected REAL NOT NULL,
                    source TEXT NOT NULL,
                    event_id TEXT NOT NULL,
                    units TEXT NOT NULL,
                    PRIMARY KEY(metric, event_id, source, collected_at)
                );
                CREATE INDEX IF NOT EXISTS idx_consensus_release
                    ON consensus_estimates(metric, release_at, collected_at);
                CREATE INDEX IF NOT EXISTS idx_consensus_event
                    ON consensus_estimates(metric, event_id, collected_at);
                """
            )

    @staticmethod
    def _validate(estimate: ConsensusEstimate) -> None:
        if estimate.release_at.tzinfo is None or estimate.collected_at.tzinfo is None:
            raise ValueError("release_at and collected_at must be timezone-aware")
        if estimate.collected_at >= estimate.release_at:
            raise ValueError(f"Consensus {estimate.metric}/{estimate.event_id} was collected at or after release")
        if not estimate.units:
            raise ValueError("Consensus units are required")
        if not estimate.source:
            raise ValueError("Consensus source is required")
        if not estimate.event_id:
            raise ValueError("Consensus event_id is required")

    def add(self, estimate: ConsensusEstimate) -> None:
        self._validate(estimate)
        with self._connect() as con:
            con.execute(
                """
                INSERT INTO consensus_estimates(metric, release_at, collected_at, expected, source, event_id, units)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(metric, event_id, source, collected_at) DO UPDATE SET
                    release_at=excluded.release_at, expected=excluded.expected, units=excluded.units
                """,
                (
                    estimate.metric, estimate.release_at.isoformat(), estimate.collected_at.isoformat(),
                    float(estimate.expected), estimate.source, estimate.event_id, estimate.units,
                ),
            )

    def import_csv(self, csv_text_or_path: str | Path) -> dict:
        path = Path(csv_text_or_path) if not isinstance(csv_text_or_path, Path) else csv_text_or_path
        if isinstance(csv_text_or_path, Path) or (
            isinstance(csv_text_or_path, str) and "\n" not in csv_text_or_path and path.exists()
        ):
            text = path.read_text(encoding="utf-8")
        else:
            text = str(csv_text_or_path)
        reader = csv.DictReader(io.StringIO(text))
        inserted, rejected = 0, []
        for row in reader:
            try:
                self.add(
                    ConsensusEstimate(
                        metric=row["metric"].strip(),
                        release_at=datetime.fromisoformat(row["release_at"].strip()),
                        collected_at=datetime.fromisoformat(row["collected_at"].strip()),
                        expected=float(row["expected"]),
                        source=row["source"].strip(),
                        event_id=row["event_id"].strip(),
                        units=row["units"].strip(),
                    )
                )
                inserted += 1
            except Exception as exc:
                rejected.append({"row": dict(row), "error": str(exc)})
        return {"inserted": inserted, "rejected": rejected}

    def best_for_event(
        self,
        metric: str,
        event_id: str,
        *,
        units: Optional[str] = None,
    ) -> Optional[ConsensusEstimate]:
        """Latest valid estimate for an event, using the estimate's own release clock."""
        with self._connect() as con:
            rows = con.execute(
                "SELECT * FROM consensus_estimates WHERE metric=? AND event_id=?",
                (metric, event_id),
            ).fetchall()
        candidates = []
        for row in rows:
            release_at = datetime.fromisoformat(row["release_at"])
            collected = datetime.fromisoformat(row["collected_at"])
            if units is not None and row["units"] != units:
                continue
            if collected >= release_at:
                continue
            candidates.append((collected, row))
        if not candidates:
            return None
        _, row = max(candidates, key=lambda x: x[0])
        return ConsensusEstimate(
            metric=row["metric"],
            release_at=datetime.fromisoformat(row["release_at"]),
            collected_at=datetime.fromisoformat(row["collected_at"]),
            expected=float(row["expected"]),
            source=row["source"],
            event_id=row["event_id"],
            units=row["units"],
        )

    def best_before_release(
        self,
        metric: str,
        release_at: datetime,
        *,
        event_id: Optional[str] = None,
        units: Optional[str] = None,
    ) -> Optional[ConsensusEstimate]:
        # Compatibility API. Event ID is the preferred stable join key.
        if event_id is not None:
            return self.best_for_event(metric, event_id, units=units)
        with self._connect() as con:
            rows = con.execute("SELECT * FROM consensus_estimates WHERE metric=?", (metric,)).fetchall()
        candidates = []
        for row in rows:
            r_release = datetime.fromisoformat(row["release_at"])
            collected = datetime.fromisoformat(row["collected_at"])
            if r_release != release_at or (units is not None and row["units"] != units) or collected >= release_at:
                continue
            candidates.append((collected, row))
        if not candidates:
            return None
        _, row = max(candidates, key=lambda x: x[0])
        return ConsensusEstimate(
            metric=row["metric"], release_at=datetime.fromisoformat(row["release_at"]),
            collected_at=datetime.fromisoformat(row["collected_at"]), expected=float(row["expected"]),
            source=row["source"], event_id=row["event_id"], units=row["units"],
        )

    def count(self) -> int:
        with self._connect() as con:
            return int(con.execute("SELECT COUNT(*) AS n FROM consensus_estimates").fetchone()["n"])


class SurpriseBuilder:
    """Build actual-minus-expected surprises without fabricating consensus."""

    def __init__(self, macro: MacroRepository, consensus: ConsensusRepository) -> None:
        self.macro = macro
        self.consensus = consensus

    @staticmethod
    def _event_id(metric: str, obs_date: date) -> str:
        return f"{metric}-{obs_date.isoformat()}"

    @staticmethod
    def _state_from_events(events: Sequence[MacroRevision], when: datetime) -> Dict[date, float]:
        state: Dict[date, float] = {}
        for event in events:
            if event.available_to_model_at <= when:
                state[event.observation_date] = event.value
        return state

    def actual_releases(self, policy: SeriesPolicy) -> List[ActualRelease]:
        if not policy.actual_metric or not policy.actual_transform or not policy.actual_units:
            return []
        all_events = self.macro.revisions(policy.series_id)
        first = self.macro.first_release_events(policy.series_id)
        out: List[ActualRelease] = []
        for current in first:
            state = self._state_from_events(all_events, current.available_to_model_at)
            obs_dates = sorted(d for d in state if d <= current.observation_date)
            if not obs_dates or obs_dates[-1] != current.observation_date:
                continue
            value = state[current.observation_date]
            actual: Optional[float] = None
            if policy.actual_transform == "level":
                actual = value
            elif len(obs_dates) >= 2:
                prev = state[obs_dates[-2]]
                if policy.actual_transform == "diff_1":
                    actual = value - prev
                elif policy.actual_transform == "pct_1" and prev != 0:
                    actual = (value / prev - 1.0) * 100.0
                elif policy.actual_transform == "qoq_ann" and prev > 0 and value > 0:
                    actual = ((value / prev) ** 4 - 1.0) * 100.0
            if actual is None or not np.isfinite(actual):
                continue
            out.append(
                ActualRelease(
                    metric=policy.actual_metric,
                    series_id=policy.series_id,
                    observation_date=current.observation_date,
                    available_to_model_at=current.available_to_model_at,
                    eligible_session=current.eligible_session,
                    actual=float(actual),
                    units=policy.actual_units,
                    event_id=self._event_id(policy.actual_metric, current.observation_date),
                )
            )
        return out

    def surprise_events(self, policies: Sequence[SeriesPolicy] = DEFAULT_SERIES_POLICIES) -> pd.DataFrame:
        records = []
        for policy in policies:
            for event in self.actual_releases(policy):
                consensus = self.consensus.best_for_event(event.metric, event.event_id, units=event.units)
                if consensus is None:
                    continue
                records.append(
                    {
                        "metric": event.metric,
                        "series_id": event.series_id,
                        "observation_date": pd.Timestamp(event.observation_date),
                        "scheduled_release_at": consensus.release_at,
                        "available_to_model_at": event.available_to_model_at,
                        "eligible_session": pd.Timestamp(event.eligible_session),
                        "actual": event.actual,
                        "expected": consensus.expected,
                        "surprise": event.actual - consensus.expected,
                        "units": event.units,
                        "source": consensus.source,
                        "event_id": event.event_id,
                    }
                )
        columns = [
            "metric", "series_id", "observation_date", "scheduled_release_at", "available_to_model_at",
            "eligible_session", "actual", "expected", "surprise", "units", "source", "event_id",
        ]
        if not records:
            return pd.DataFrame(columns=columns)
        return pd.DataFrame.from_records(records)[columns].sort_values(["eligible_session", "metric"])

    def coverage_report(self, policies: Sequence[SeriesPolicy] = DEFAULT_SERIES_POLICIES, *, metrics: Optional[Sequence[str]] = None) -> dict:
        total_events = matched_events = 0
        metric_rows = {}
        growth_metrics, inflation_metrics = set(), set()
        allowed = set(metrics) if metrics is not None else None
        for policy in policies:
            if not policy.actual_metric or (allowed is not None and policy.actual_metric not in allowed):
                continue
            events = self.actual_releases(policy)
            matched = 0
            for event in events:
                if self.consensus.best_for_event(event.metric, event.event_id, units=event.units) is not None:
                    matched += 1
            total_events += len(events)
            matched_events += matched
            metric_rows[policy.actual_metric] = {
                "category": policy.category,
                "available_actual_events": len(events),
                "matched_consensus_events": matched,
                "coverage": matched / len(events) if events else 0.0,
            }
            if matched > 0:
                if policy.category == "growth":
                    growth_metrics.add(policy.actual_metric)
                if policy.category == "inflation":
                    inflation_metrics.add(policy.actual_metric)
        return {
            "total_actual_events": total_events,
            "matched_events": matched_events,
            "event_coverage": matched_events / total_events if total_events else 0.0,
            "growth_metrics_with_consensus": len(growth_metrics),
            "inflation_metrics_with_consensus": len(inflation_metrics),
            "metrics": metric_rows,
        }

    def session_panel(self, sessions: pd.DatetimeIndex, *, min_history: int = 8, metrics: Optional[Sequence[str]] = None) -> pd.DataFrame:
        sessions = pd.DatetimeIndex(pd.to_datetime(sessions)).tz_localize(None).normalize().sort_values().unique()
        events = self.surprise_events()
        if metrics is not None and not events.empty:
            events = events[events['metric'].isin(set(metrics))]
        panel = pd.DataFrame(index=sessions)
        if events.empty:
            return panel
        for metric, group in events.groupby("metric"):
            group = group.sort_values("eligible_session").copy()
            vals = group["surprise"].astype(float)
            mean = vals.shift(1).expanding(min_periods=min_history).mean()
            std = vals.shift(1).expanding(min_periods=min_history).std(ddof=0).replace(0, np.nan)
            group["z"] = (vals - mean) / std
            raw_series = pd.Series(group["surprise"].to_numpy(), index=pd.DatetimeIndex(group["eligible_session"])).groupby(level=0).last()
            z_series = pd.Series(group["z"].to_numpy(), index=pd.DatetimeIndex(group["eligible_session"])).groupby(level=0).last()
            panel[f"surprise_{metric}"] = raw_series.reindex(sessions).ffill()
            panel[f"surprise_{metric}_z"] = z_series.reindex(sessions).ffill()
        return panel
