"""
HRV computation utilities.

Provides RMSSD and LF-power calculations from RR-interval series.
"""

import math
import numpy as np


def compute_rmssd(rr_ms: list[float]) -> float | None:
    """Compute RMSSD from a list of RR intervals (in ms)."""
    if len(rr_ms) < 2:
        return None
    diffs = [rr_ms[i + 1] - rr_ms[i] for i in range(len(rr_ms) - 1)]
    sq = [d * d for d in diffs]
    return math.sqrt(sum(sq) / len(sq))


def compute_lf_power(rr_ms: list[float], fs: float = 4.0) -> float | None:
    """
    Compute LF power (0.04–0.15 Hz) from RR intervals using FFT.

    rr_ms : list of RR intervals in ms, chronological order.
    Requires at least ~30 data points for a meaningful estimate.
    Returns LF power in ms².
    """
    if len(rr_ms) < 30:
        return None

    # Build cumulative time axis (seconds)
    t = np.cumsum(np.array(rr_ms) / 1000.0)
    t = t - t[0]
    total_duration = t[-1]
    if total_duration < 20:
        return None

    # Interpolate to uniform sampling
    n_samples = int(total_duration * fs)
    if n_samples < 16:
        return None
    t_uniform = np.linspace(t[0], t[-1], n_samples)
    rr_interp = np.interp(t_uniform, t, np.array(rr_ms))

    # Remove mean
    rr_interp = rr_interp - np.mean(rr_interp)

    # Apply Hann window
    window = np.hanning(len(rr_interp))
    rr_windowed = rr_interp * window

    # FFT
    n_fft = len(rr_windowed)
    fft_vals = np.fft.rfft(rr_windowed)
    psd = (np.abs(fft_vals) ** 2) / (fs * n_fft)
    psd[1:-1] *= 2  # double one-sided spectrum (except DC and Nyquist)
    freqs = np.fft.rfftfreq(n_fft, d=1.0 / fs)

    # Integrate LF band (0.04 – 0.15 Hz)
    lf_mask = (freqs >= 0.04) & (freqs <= 0.15)
    if not np.any(lf_mask):
        return None
    df = freqs[1] - freqs[0] if len(freqs) > 1 else 1.0
    lf_power = float(np.sum(psd[lf_mask]) * df)
    return lf_power
