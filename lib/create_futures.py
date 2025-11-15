"""Utility to build the eight best features for the exported RF model.

The logic mirrors the feature engineering pipeline inside
`20251115モデル書き出し.ipynb` so it can be reused outside the notebook.
"""

from __future__ import annotations

from typing import Iterable, Sequence, Tuple

import numpy as np

DEFAULT_SAMPLE_PERIOD = 1 / 60  # Fitbit accelerometer ~60Hz

try:  # SciPy is used inside the notebook; fallback implementations exist just in case.
    from scipy.signal import find_peaks as _scipy_find_peaks
except ImportError:  # pragma: no cover - SciPy should normally be available.
    _scipy_find_peaks = None

try:
    from scipy.stats import skew as _scipy_skew
except ImportError:  # pragma: no cover
    _scipy_skew = None


def _naive_find_peaks(values: np.ndarray, height: float, distance: int) -> np.ndarray:
    """Simple replacement for scipy.signal.find_peaks."""
    if values.size < 3:
        return np.empty(0, dtype=int)

    peaks: list[int] = []
    for idx in range(1, values.size - 1):
        if values[idx] < height:
            continue
        if values[idx] <= values[idx - 1] or values[idx] <= values[idx + 1]:
            continue
        if peaks and idx - peaks[-1] < distance:
            # keep the taller peak when two are too close
            if values[idx] > values[peaks[-1]]:
                peaks[-1] = idx
            continue
        peaks.append(idx)
    return np.asarray(peaks, dtype=int)


def _find_peaks(values: np.ndarray, *, height: float, distance: int) -> np.ndarray:
    if _scipy_find_peaks is not None:
        peaks, _ = _scipy_find_peaks(values, height=height, distance=distance)
        return peaks
    return _naive_find_peaks(values, height=height, distance=distance)


def _safe_skew(values: np.ndarray) -> float:
    if values.size < 3:
        return float("nan")
    if _scipy_skew is not None:
        return float(_scipy_skew(values, bias=False))

    mean = float(np.mean(values))
    std = float(np.std(values, ddof=1))
    if std == 0 or np.isnan(std):
        return 0.0
    centered = values - mean
    n = values.size
    m3 = np.mean(centered**3)
    g1 = m3 / (std**3)
    correction = np.sqrt(n * (n - 1)) / (n - 2)
    return float(correction * g1)


def _first_last_valid(arr: np.ndarray) -> Tuple[float, float]:
    first = next((float(v) for v in arr if not np.isnan(v)), float("nan"))
    last = next((float(v) for v in arr[::-1] if not np.isnan(v)), float("nan"))
    return first, last


def _y_peak_mean(y_values: np.ndarray) -> float:
    if y_values.size == 0:
        return float("nan")
    peaks = _find_peaks(y_values, height=5.0, distance=20)
    if peaks.size == 0:
        return float("nan")
    return float(np.mean(y_values[peaks]))


def _xyz_cycle_stats(xyz: np.ndarray, sample_period: float) -> Tuple[float, float]:
    peaks = _find_peaks(xyz, height=5.0, distance=20)
    if peaks.size < 2:
        return float("nan"), float("nan")
    intervals = np.diff(peaks) * sample_period
    if intervals.size == 0:
        return float("nan"), float("nan")
    return float(np.mean(intervals)), float(np.std(intervals, ddof=0))


def _xyz_high_low_ratio(xyz: np.ndarray, sample_period: float) -> float:
    if xyz.size < 4:
        return float("nan")

    signal = xyz - np.nanmean(xyz)
    spectrum = np.fft.rfft(signal)
    power = np.abs(spectrum) ** 2
    total_power = float(np.sum(power))
    if total_power <= 0:
        return float("nan")

    freqs = np.fft.rfftfreq(signal.size, d=sample_period)
    low_mask = (freqs >= 0.5) & (freqs < 1.5)
    high_mask = freqs >= 2.0

    low_power = float(np.sum(power[low_mask])) if np.any(low_mask) else 0.0
    high_power = float(np.sum(power[high_mask])) if np.any(high_mask) else 0.0
    if low_power == 0:
        return float("nan")
    return high_power / low_power


def create_features(
    heart_rates: Sequence[float],
    accelerations: Sequence[Sequence[float]],
    *,
    sample_period: float = DEFAULT_SAMPLE_PERIOD,
) -> list[float]:
    """Return the eight RF features from raw heart rate and accelerometer data.

    Args:
        heart_rates: Sequence of heart-rate readings for the set.
        accelerations: Sequence of [x, y, z] accelerometer triplets.
        sample_period: Sampling period in seconds. Defaults to 1/30 (~Fitbit rate).

    Returns:
        List of floats ordered as:
        [heart_rate, max_hr, lambda_hr, y_skewness,
         xyz_cycle_std, xyz_cycle_mean, y, xyz_high_low_ratio]
    """

    hr_arr = np.asarray(heart_rates, dtype=float)
    acc_arr = np.asarray(accelerations, dtype=float)

    if hr_arr.size == 0 or np.all(np.isnan(hr_arr)):
        raise ValueError("heart_rates must contain at least one valid value.")
    if acc_arr.size == 0 or acc_arr.ndim != 2 or acc_arr.shape[1] != 3:
        raise ValueError("accelerations must be an Nx3 sequence of [x, y, z].")

    mean_hr = float(np.nanmean(hr_arr))
    max_hr = float(np.nanmax(hr_arr))
    first_hr, last_hr = _first_last_valid(hr_arr)
    lambda_hr = (
        float(last_hr - first_hr) if not np.isnan(first_hr + last_hr) else float("nan")
    )

    y_values = acc_arr[:, 1]
    y_skewness = _safe_skew(y_values)
    y_peak = _y_peak_mean(y_values)

    xyz = np.linalg.norm(acc_arr, axis=1)
    xyz_cycle_mean, xyz_cycle_std = _xyz_cycle_stats(xyz, sample_period)
    xyz_high_low_ratio = _xyz_high_low_ratio(xyz, sample_period)

    return [
        mean_hr,
        max_hr,
        lambda_hr,
        y_skewness,
        xyz_cycle_std,
        xyz_cycle_mean,
        y_peak,
        xyz_high_low_ratio,
    ]
