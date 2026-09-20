import numpy as np
import pandas as pd

from backend.alpha_v1 import AlphaV1Config, alpha_v1_scores
from backend.minerva import alpha_v1_candidate, alpha_v11_candidate
from backend.models.registry import get_model_spec, list_model_specs


def _panel(seed=17, sessions=900, names=12):
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2018-01-01", periods=sessions)
    market = rng.normal(.0002, .009, sessions)
    data = {}
    for j in range(names):
        # Give names persistent but modest relative structure.
        idio = rng.normal(.00003 * (j - names / 2) / names, .010 + .00025 * j, sessions)
        data[f"S{j:02d}"] = 100 * np.cumprod(1 + .35 * market + idio)
    return pd.DataFrame(data, index=idx), pd.Series(market, index=idx, dtype=float)


def test_alpha_v11_is_separate_registry_candidate_and_preserves_v1():
    ids = {item.id for item in list_model_specs(refresh=True)}
    assert "alpha_v1" in ids and "alpha_v11" in ids
    assert get_model_spec("alpha_v11").label.startswith("Alpha V1.1")
    v1 = alpha_v1_candidate()
    v11 = alpha_v11_candidate()
    assert not v1.alpha_v1.overlap_robust_ic
    assert not v1.alpha_v1.robust_daily_calibration
    assert v11.alpha_v1.overlap_robust_ic
    assert v11.alpha_v1.robust_daily_calibration
    assert v11.alpha_v1.exact_label_completion
    assert v1.alpha_v1.fingerprint != v11.alpha_v1.fingerprint


def test_alpha_v11_future_changes_do_not_change_past_decision():
    closes, bench = _panel(sessions=850)
    cfg = AlphaV1Config(
        enabled=True,
        overlap_robust_ic=True,
        robust_daily_calibration=True,
        exact_label_completion=True,
        retrain_sessions=7,
    )
    cutoff = 700
    first = alpha_v1_scores(closes, benchmark_returns=bench, config=cfg)
    changed = closes.copy()
    changed.iloc[cutoff + 1:] *= np.linspace(1.0, 2.5, len(changed) - cutoff - 1)[:, None]
    second = alpha_v1_scores(changed, benchmark_returns=bench, config=cfg)
    np.testing.assert_allclose(first["score"].iloc[cutoff], second["score"].iloc[cutoff], atol=1e-12)
    np.testing.assert_allclose(first["predicted_return"].iloc[cutoff], second["predicted_return"].iloc[cutoff], atol=1e-12)
    np.testing.assert_allclose(first["prediction_error"].iloc[cutoff], second["prediction_error"].iloc[cutoff], atol=1e-12)
    assert first["model_id"] == "alpha_v11_robust_cross_sectional"
    assert first["evidence_method"] == "overlap_hac"
    assert first["calibration_method"] == "daily_cross_sectional_robust"


def test_alpha_v11_optional_quality_panel_is_used_when_supplied():
    closes, bench = _panel(names=8)
    quality = pd.DataFrame(np.nan, index=closes.index, columns=closes.columns)
    quality.iloc[300:, :] = np.tile(np.linspace(-1, 1, len(closes.columns)), (len(closes) - 300, 1))
    cfg = AlphaV1Config(enabled=True, overlap_robust_ic=True, robust_daily_calibration=True, exact_label_completion=True)
    out = alpha_v1_scores(closes, benchmark_returns=bench, quality_scores=quality, config=cfg)
    assert "quality" in out["feature_names"]
