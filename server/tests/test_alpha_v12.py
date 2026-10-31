import numpy as np
import pandas as pd

from backend.alpha_v12 import AlphaV12Config, alpha_v12_scores
from backend.minerva import alpha_v12_alt_candidate, alpha_v12_candidate
from backend.models.registry import get_model_spec, list_model_specs
from backend.universal_engine import signal_panel


def _panel(sessions: int = 390, names: int = 10):
    rng = np.random.default_rng(12026)
    idx = pd.bdate_range("2023-01-03", periods=sessions)
    cols = [f"S{i:02d}" for i in range(names)]
    market = rng.normal(0.00025, 0.008, sessions)
    style = np.linspace(-0.00045, 0.00045, names)
    noise = rng.normal(0.0, 0.010, (sessions, names))
    returns = market[:, None] + style[None, :] + noise
    close = pd.DataFrame(100.0 * np.exp(np.cumsum(returns, axis=0)), index=idx, columns=cols)
    overnight = rng.normal(0.0, 0.002, (sessions, names))
    open_ = close.shift(1).fillna(close.iloc[0]).mul(np.exp(overnight))
    volume = pd.DataFrame(rng.integers(2_000_000, 7_000_000, (sessions, names)), index=idx, columns=cols, dtype=float)
    bench = pd.Series(market, index=idx, dtype=float)
    bench.iloc[0] = 0.0
    return close, open_, volume, bench


def test_alpha_v12_registry_has_main_and_true_no_anchor_alt():
    ids = {spec.id for spec in list_model_specs(refresh=True)}
    assert {"alpha_v12", "alpha_v12_alt"}.issubset(ids)
    assert get_model_spec("alpha_v12").label.startswith("Alpha V1.2")
    main = alpha_v12_candidate()
    alt = alpha_v12_alt_candidate()
    assert main.alpha_model == "alpha_v12"
    assert alt.alpha_model == "alpha_v12"
    assert main.alpha_v12.include_tba_anchor is True
    assert main.alpha_v12.anchor_prior_weight == 0.55
    assert alt.alpha_v12.include_tba_anchor is False
    assert alt.alpha_v12.anchor_prior_weight == 0.0
    assert main.alpha_v12.fingerprint != alt.alpha_v12.fingerprint


def test_alpha_v12_alt_contains_no_tba_anchor_output():
    close, open_, volume, bench = _panel()
    cfg = AlphaV12Config(
        enabled=True,
        include_tba_anchor=False,
        anchor_prior_weight=0.0,
        calibration_minimum_days=20,
        calibration_window_sessions=126,
    )
    out = alpha_v12_scores(close, open_, volume, benchmark_returns=bench, config=cfg)
    assert out["model_id"] == "alpha_v12_alt_cross_sectional"
    assert out["anchor_enabled"] is False
    assert out["anchor_prior_weight"] == 0.0
    assert float(out["anchor_score"].abs().to_numpy().max()) == 0.0
    assert np.isfinite(out["predicted_return"].to_numpy()).all()
    assert np.isfinite(out["prediction_error"].to_numpy()).all()


def test_alpha_v12_hybrid_uses_anchor_and_stays_bounded():
    close, open_, volume, bench = _panel(sessions=430)
    cfg = AlphaV12Config(
        enabled=True,
        include_tba_anchor=True,
        anchor_prior_weight=0.55,
        anchor_training_sessions=252,
        calibration_minimum_days=20,
        calibration_window_sessions=126,
    )
    out = alpha_v12_scores(close, open_, volume, benchmark_returns=bench, config=cfg)
    assert out["model_id"] == "alpha_v12_hybrid"
    assert out["anchor_enabled"] is True
    assert float(out["anchor_score"].abs().to_numpy().sum()) > 0.0
    assert float(out["score"].abs().to_numpy().max()) <= 1.0 + 1e-12
    assert out["blend_method"] == "fixed_prior_rank_blend"


def test_alpha_v12_future_changes_do_not_change_past_decision_for_both_variants():
    close, open_, volume, bench = _panel(sessions=410)
    cutoff = 350
    changed_close = close.copy()
    changed_open = open_.copy()
    changed_volume = volume.copy()
    changed_close.iloc[cutoff + 1 :] *= np.linspace(1.0, 1.7, len(changed_close) - cutoff - 1)[:, None]
    changed_open.iloc[cutoff + 1 :] *= np.linspace(1.0, 0.7, len(changed_open) - cutoff - 1)[:, None]
    changed_volume.iloc[cutoff + 1 :] *= 3.0

    for cfg in (
        AlphaV12Config(enabled=True, include_tba_anchor=False, anchor_prior_weight=0.0,
                       calibration_minimum_days=20, calibration_window_sessions=126),
        AlphaV12Config(enabled=True, include_tba_anchor=True, anchor_prior_weight=0.55,
                       anchor_training_sessions=252, calibration_minimum_days=20, calibration_window_sessions=126),
    ):
        first = alpha_v12_scores(close, open_, volume, benchmark_returns=bench, config=cfg)
        second = alpha_v12_scores(changed_close, changed_open, changed_volume, benchmark_returns=bench, config=cfg)
        for key in ("score", "predicted_return", "prediction_error"):
            np.testing.assert_allclose(
                first[key].iloc[cutoff].to_numpy(dtype=float),
                second[key].iloc[cutoff].to_numpy(dtype=float),
                atol=1e-12,
            )


def test_universal_signal_panel_accepts_both_alpha_v12_candidates():
    close, open_, volume, bench = _panel(sessions=430)
    for builder in (alpha_v12_candidate, alpha_v12_alt_candidate):
        base = builder()
        cfg = base.__class__(**{
            **base.__dict__,
            "alpha_v12": AlphaV12Config(
                enabled=True,
                include_tba_anchor=base.alpha_v12.include_tba_anchor,
                anchor_prior_weight=base.alpha_v12.anchor_prior_weight,
                anchor_training_sessions=252,
                calibration_minimum_days=20,
                calibration_window_sessions=126,
            ),
        })
        out = signal_panel(close, cfg, benchmark_returns=bench, opens=open_, volumes=volume)
        assert out["score"].shape == close.shape
        assert out["predicted_return"].shape == close.shape
        assert out["prediction_error"].shape == close.shape
