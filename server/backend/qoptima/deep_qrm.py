from __future__ import annotations

import time
from typing import Any, Callable

import numpy as np

from .problem import QOptimaPortfolioProblem
from .qrm import evaluate_risk, _correlation_stress_covariance


def _max_drawdown_from_returns(daily_returns: np.ndarray) -> np.ndarray:
    wealth = np.cumprod(1.0 + daily_returns, axis=1)
    peaks = np.maximum.accumulate(wealth, axis=1)
    drawdown = wealth / np.maximum(peaks, 1e-12) - 1.0
    return np.min(drawdown, axis=1)


def _simulate_batch(rng, b, horizon_days, problem, w, mu_daily, chol, stress_chol, df=5):
    # Multivariate Student-t with covariance equal to the supplied covariance.
    # z/sqrt(chi2/df) has variance df/(df-2), so multiply by sqrt((df-2)/df).
    variance_normalizer = np.sqrt((float(df) - 2.0) / float(df))
    z = rng.standard_normal((b, horizon_days, problem.n))
    scales = np.sqrt(rng.chisquare(df=df, size=(b, horizon_days, 1)) / float(df))
    shocks = variance_normalizer * (z @ chol.T) / np.maximum(scales, 1e-8)
    asset_daily = mu_daily.reshape(1, 1, -1) + shocks
    port_daily = asset_daily @ w
    terminal = np.prod(1.0 + port_daily, axis=1) - 1.0
    dd = _max_drawdown_from_returns(port_daily)

    z2 = rng.standard_normal((b, horizon_days, problem.n))
    scales2 = np.sqrt(rng.chisquare(df=df, size=(b, horizon_days, 1)) / float(df))
    stress_asset = mu_daily.reshape(1, 1, -1) + variance_normalizer * (z2 @ stress_chol.T) / np.maximum(scales2, 1e-8)
    stress_port = stress_asset @ w
    stress_terminal = np.prod(1.0 + stress_port, axis=1) - 1.0
    return terminal, dd, stress_terminal


def evaluate_risk_deep(
    problem: QOptimaPortfolioProblem,
    weights: np.ndarray,
    *,
    minimum_paths: int = 100_000,
    target_work_seconds: float = 30.0,
    maximum_paths: int = 5_000_000,
    horizon_days: int = 21,
    seed: int = 41,
    progress_callback: Callable[[dict[str, Any]], None] | None = None,
    fat_tail_df: int = 5,
    max_p01_drawdown: float = -0.30,
    max_stress_p05_return: float = -0.22,
) -> dict[str, Any]:
    """Fat-tail risk review calibrated to the current Mac's measured throughput."""
    base = evaluate_risk(problem, weights)
    started = time.perf_counter()
    minimum_paths = max(5_000, int(minimum_paths))
    maximum_paths = max(minimum_paths, int(maximum_paths))
    target_work_seconds = max(1.0, float(target_work_seconds))
    horizon_days = max(5, min(int(horizon_days), 126))
    rng = np.random.default_rng(int(seed))
    fat_tail_df = max(3, int(fat_tail_df))

    w = np.asarray(weights, dtype=float)
    mu_daily = np.asarray(problem.expected_returns, dtype=float) / 252.0
    cov_daily = np.asarray(problem.covariance, dtype=float) / 252.0
    stressed_cov = _correlation_stress_covariance(np.asarray(problem.covariance, dtype=float), blend=0.75) / 252.0
    jitter = np.eye(problem.n) * 1e-10
    chol = np.linalg.cholesky(cov_daily + jitter)
    stress_chol = np.linalg.cholesky(stressed_cov * (1.50 ** 2) + jitter)

    batch = 4_000 if problem.n >= 12 else 8_000
    cal_start = time.perf_counter()
    t0, d0, s0 = _simulate_batch(rng, batch, horizon_days, problem, w, mu_daily, chol, stress_chol, fat_tail_df)
    cal_elapsed = max(time.perf_counter() - cal_start, 1e-4)
    paths_per_second = float(batch / cal_elapsed)
    planned_paths = int(round(paths_per_second * target_work_seconds))
    planned_paths = max(minimum_paths, min(maximum_paths, planned_paths))

    terminal: list[np.ndarray] = [t0]
    max_dd: list[np.ndarray] = [d0]
    stress_terminal: list[np.ndarray] = [s0]
    paths = int(batch)
    rounds = 1
    last_progress = 0.0

    while paths < planned_paths:
        b = min(batch, planned_paths - paths)
        t, d, s = _simulate_batch(rng, b, horizon_days, problem, w, mu_daily, chol, stress_chol, fat_tail_df)
        terminal.append(t); max_dd.append(d); stress_terminal.append(s)
        paths += b
        rounds += 1
        now = time.perf_counter()
        if progress_callback is not None and (now - last_progress >= 3.0 or paths >= planned_paths):
            last_progress = now
            try:
                progress_callback({
                    "stage": "qrm_deep_monte_carlo",
                    "message": f"QRM Deep: {paths:,}/{planned_paths:,} calibrated paths",
                    "paths": int(paths),
                    "planned_paths": int(planned_paths),
                    "elapsed_seconds": float(now - started),
                    "target_work_seconds": float(target_work_seconds),
                    "fraction": float(paths / max(1, planned_paths)),
                })
            except Exception:
                pass

    terminal_arr = np.concatenate(terminal) if terminal else np.array([], dtype=float)
    dd_arr = np.concatenate(max_dd) if max_dd else np.array([], dtype=float)
    stress_arr = np.concatenate(stress_terminal) if stress_terminal else np.array([], dtype=float)

    def pct(arr: np.ndarray, q: float) -> float | None:
        return float(np.quantile(arr, q)) if arr.size else None

    var95 = pct(terminal_arr, 0.05)
    var99 = pct(terminal_arr, 0.01)
    cvar95 = float(terminal_arr[terminal_arr <= var95].mean()) if terminal_arr.size and var95 is not None and np.any(terminal_arr <= var95) else None
    cvar99 = float(terminal_arr[terminal_arr <= var99].mean()) if terminal_arr.size and var99 is not None and np.any(terminal_arr <= var99) else None

    deep = {
        "paths": int(paths),
        "planned_paths": int(planned_paths),
        "rounds": int(rounds),
        "horizon_days": int(horizon_days),
        "fat_tail_df": int(fat_tail_df),
        "student_t_variance_normalized": True,
        "elapsed_seconds": float(time.perf_counter() - started),
        "calibration": {
            "batch_paths": int(batch),
            "batch_seconds": float(cal_elapsed),
            "paths_per_second": float(paths_per_second),
            "target_work_seconds": float(target_work_seconds),
        },
        "terminal_return": {
            "mean": float(terminal_arr.mean()) if terminal_arr.size else None,
            "median": pct(terminal_arr, 0.50),
            "p05_var95": var95,
            "p01_var99": var99,
            "cvar95": cvar95,
            "cvar99": cvar99,
            "probability_positive": float(np.mean(terminal_arr > 0.0)) if terminal_arr.size else None,
        },
        "drawdown": {
            "median": pct(dd_arr, 0.50),
            "p05": pct(dd_arr, 0.05),
            "p01": pct(dd_arr, 0.01),
            "worst_observed": float(dd_arr.min()) if dd_arr.size else None,
        },
        "correlation_vol_stress": {
            "p05_return": pct(stress_arr, 0.05),
            "p01_return": pct(stress_arr, 0.01),
            "mean_return": float(stress_arr.mean()) if stress_arr.size else None,
        },
    }
    p01_dd = deep["drawdown"]["p01"]
    stress_p05 = deep["correlation_vol_stress"]["p05_return"]
    base["deep_research"] = deep
    base["passes_qrm_deep_gates"] = bool(
        base["passes_qrm_structural_gates"]
        and (p01_dd is None or p01_dd > float(max_p01_drawdown))
        and (stress_p05 is None or stress_p05 > float(max_stress_p05_return))
    )
    # Validate the one-step simulated variance scale against the target covariance.
    # Use the already generated terminal paths only for outcome statistics; a small
    # fresh shock panel provides a direct covariance diagnostic without compounding.
    diag_rng = np.random.default_rng(int(seed) + 99991)
    diag_n = min(50000, max(5000, paths // max(1, horizon_days)))
    z = diag_rng.standard_normal((diag_n, problem.n))
    chi = np.sqrt(diag_rng.chisquare(df=fat_tail_df, size=(diag_n, 1)) / float(fat_tail_df))
    norm = np.sqrt((float(fat_tail_df) - 2.0) / float(fat_tail_df))
    shock = norm * (z @ chol.T) / np.maximum(chi, 1e-8)
    empirical = np.cov(shock, rowvar=False, ddof=1)
    target = cov_daily
    target_norm = float(np.linalg.norm(target, ord="fro"))
    deep["variance_scale_validation"] = {
        "paths": int(diag_n),
        "relative_covariance_error": float(np.linalg.norm(empirical - target, ord="fro") / target_norm) if target_norm > 0 else None,
        "mean_variance_ratio": float(np.mean(np.diag(empirical) / np.maximum(np.diag(target), 1e-18))),
    }
    deep["gate_configuration"] = {
        "max_p01_drawdown": float(max_p01_drawdown),
        "max_stress_p05_return": float(max_stress_p05_return),
    }
    return base
