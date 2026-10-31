from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence, Tuple

import numpy as np
import pandas as pd


REGIME_NAMES = (
    "growth_up_inflation_up",
    "growth_up_inflation_down",
    "growth_down_inflation_up",
    "growth_down_inflation_down",
)
REGIME_TO_INT = {name: i for i, name in enumerate(REGIME_NAMES)}
INT_TO_REGIME = {i: name for i, name in enumerate(REGIME_NAMES)}


@dataclass
class LabelResult:
    labels: pd.Series
    growth_score: pd.Series
    inflation_score: pd.Series
    growth_signal_count: pd.Series
    inflation_signal_count: pd.Series
    event_mask: pd.Series


class EconomicDirectionTargetBuilder:
    """Independent PIT target builder for regime semantics.

    Inputs are *target-only* directional acceleration series generated separately
    from candidate features. Positive growth score means growth is rising; negative
    means falling. Positive inflation score means inflation is rising; negative means
    falling. No candidate feature or model probability is used to define the target.
    """

    def __init__(self, *, min_growth_signals: int = 2, min_inflation_signals: int = 2) -> None:
        self.min_growth_signals = int(min_growth_signals)
        self.min_inflation_signals = int(min_inflation_signals)

    @staticmethod
    def _robust_direction(frame: pd.DataFrame, prefix: str) -> tuple[pd.Series, pd.Series]:
        cols = [c for c in frame.columns if c.startswith(prefix)]
        if not cols:
            return pd.Series(np.nan, index=frame.index), pd.Series(0, index=frame.index, dtype=int)
        matrix = frame[cols].astype(float)
        # Direction is a robust sign vote, not a mixed-unit magnitude composite.
        signed = np.sign(matrix)
        score = signed.median(axis=1, skipna=True)
        count = matrix.notna().sum(axis=1)
        return score, count

    def build(self, target_frame: pd.DataFrame, *, event_mask: pd.Series | None = None) -> LabelResult:
        growth, ng = self._robust_direction(target_frame, "growth_")
        inflation, ni = self._robust_direction(target_frame, "inflation_")
        valid = (ng >= self.min_growth_signals) & (ni >= self.min_inflation_signals) & growth.notna() & inflation.notna()
        labels = pd.Series(pd.NA, index=target_frame.index, dtype="Int64", name="regime_target")
        labels.loc[valid & (growth >= 0) & (inflation >= 0)] = 0
        labels.loc[valid & (growth >= 0) & (inflation < 0)] = 1
        labels.loc[valid & (growth < 0) & (inflation >= 0)] = 2
        labels.loc[valid & (growth < 0) & (inflation < 0)] = 3
        if event_mask is None:
            changed = target_frame.ne(target_frame.shift(1)) & target_frame.notna()
            event_mask = changed.any(axis=1).fillna(False)
        else:
            event_mask = event_mask.reindex(target_frame.index).fillna(False).astype(bool)
        return LabelResult(
            labels=labels,
            growth_score=growth.rename("growth_direction_score"),
            inflation_score=inflation.rename("inflation_direction_score"),
            growth_signal_count=ng.rename("growth_signal_count"),
            inflation_signal_count=ni.rename("inflation_signal_count"),
            event_mask=event_mask.rename("effective_macro_event"),
        )


# Backward-compatible name; semantics are now directional and target-only.
RegimeLabeler = EconomicDirectionTargetBuilder
