from __future__ import annotations

from typing import Any

from .v8_engine import canonical_direction, directional_alignment

GRADE_TABLE = [(92, "A+"), (88, "A"), (84, "A-"), (80, "B+"), (76, "B"), (72, "B-"), (68, "C+"), (64, "C"), (60, "C-"), (55, "D"), (0, "F")]


def _grade(score: float) -> str:
    value = max(0.0, min(100.0, float(score)))
    return next((grade for cutoff, grade in GRADE_TABLE if value >= cutoff), "F")


def _top_pattern(pattern_report: dict[str, Any] | None) -> dict[str, Any]:
    report = pattern_report or {}
    top = report.get("top_pattern") or {}
    if isinstance(top, dict) and top:
        return top
    recent = report.get("recent") or report.get("patterns") or []
    return recent[0] if recent and isinstance(recent[0], dict) else {}


def lab_based_stock_grade(
    indicators: dict[str, Any] | None,
    setup: dict[str, Any] | None = None,
    pattern_report: dict[str, Any] | None = None,
) -> dict[str, Any]:


    ind = indicators or {}
    setup = setup or {}
    direction = canonical_direction(setup.get("direction") or setup.get("signal"))
    top = _top_pattern(pattern_report)
    alignment = directional_alignment(
        ind,
        direction,
        pattern_direction=top.get("direction"),
        pattern_confidence=top.get("confidence"),
    ) if direction in {"LONG", "SHORT"} else {
        "score": 50.0,
        "factors": [],
        "evidence": [],
        "warnings": ["No directional setup is active."],
        "risk": {},
    }
    score = float(alignment.get("score") or 50.0)
    return {
        "grade": _grade(score),
        "score": round(score, 1),
        "direction": direction,
        "regime": str(ind.get("trend") or "MIXED"),
        "top_pattern": top.get("pattern_name") or top.get("name"),
        "top_pattern_direction": top.get("direction"),
        "top_pattern_confidence": top.get("confidence"),
        "lab_basis": "Current V1.0 analytics evidence model; no Pattern Lab result is fed back into live scoring.",
        "evidence": list(alignment.get("evidence") or [])[:8],
        "warnings": list(alignment.get("warnings") or [])[:8],
        "factor_breakdown": alignment.get("factor_breakdown") or [],
        "risk": alignment.get("risk") or {},
        "legend": {"A+": "Strongest evidence alignment.", "A": "Strong evidence alignment.", "B": "Useful but incomplete alignment.", "C": "Mixed evidence.", "D": "Weak evidence.", "F": "Evidence conflicts with the setup."},
    }
