"""
HRV computation utilities.

Provides RMSSD and LF-power calculations from RR-interval series.
LF-power uses Lomb-Scargle periodogram matching Unity LFPowerAnalyzer.cs.
"""

import math
import numpy as np
from typing import Optional


def compute_rmssd(rr_ms: list) -> Optional[float]:
    """Compute RMSSD from a list of RR intervals (in ms)."""
    if len(rr_ms) < 2:
        return None
    diffs = [rr_ms[i + 1] - rr_ms[i] for i in range(len(rr_ms) - 1)]
    sq = [d * d for d in diffs]
    return math.sqrt(sum(sq) / len(sq))


# ── Lomb-Scargle parameters (matching Unity LFPowerAnalyzer.cs) ────────────

_WINDOW_DURATION = 120.0   # seconds (LFPowerAnalyzer.windowDuration)
_LF_LOW_HZ       = 0.04
_LF_HIGH_HZ      = 0.15
_HF_LOW_HZ       = 0.15
_HF_HIGH_HZ      = 0.4
_MIN_RR          = 8       # LFPowerAnalyzer.minRRIntervals
_F_SCAN_START    = 0.003
_F_SCAN_END      = 0.5


def _lomb_scargle_power(times: np.ndarray, values: np.ndarray,
                        frequency: float) -> float:
    """
    Lomb-Scargle power at a single frequency.

    Matches Unity LFPowerAnalyzer.LombScarglePower() exactly:
      tau = atan(Σ sin(2ωt_i) / Σ cos(2ωt_i)) / (2ω)
      P(ω) = 0.5 * [ (Σ y_i cos(ωt_i−ωτ))² / Σ cos²(ωt_i−ωτ)
                    + (Σ y_i sin(ωt_i−ωτ))² / Σ sin²(ωt_i−ωτ) ]
    """
    omega = 2.0 * math.pi * frequency

    # ── τ: time offset that makes sine/cosine orthogonal for uneven samples ──
    sum_sin2 = 0.0
    sum_cos_sin = 0.0
    for t in times:
        omega_t = omega * t
        sum_sin2 += math.sin(2.0 * omega_t)
        sum_cos_sin += math.cos(2.0 * omega_t)

    tau = math.atan2(sum_sin2, sum_cos_sin) / (2.0 * omega) if sum_cos_sin != 0 else 0.0
    omega_tau = omega * tau

    cos_omega_tau = math.cos(omega_tau)
    sin_omega_tau = math.sin(omega_tau)

    sum_y_cos = 0.0
    sum_y_sin = 0.0
    sum_cos2 = 0.0
    sum_sin2_out = 0.0

    for i in range(len(times)):
        omega_t = omega * times[i]
        cos_val = math.cos(omega_t - omega_tau)
        sin_val = math.sin(omega_t - omega_tau)

        sum_y_cos += values[i] * cos_val
        sum_y_sin += values[i] * sin_val
        sum_cos2 += cos_val * cos_val
        sum_sin2_out += sin_val * sin_val

    if sum_cos2 < 1e-4 or sum_sin2_out < 1e-4:
        return 0.0

    power = 0.5 * ((sum_y_cos * sum_y_cos / sum_cos2)
                   + (sum_y_sin * sum_y_sin / sum_sin2_out))
    return power


def compute_lf_power(rr_ms: list) -> Optional[float]:
    """
    Compute LF power (0.04–0.15 Hz) from RR intervals using Lomb-Scargle
    periodogram — matching Unity LFPowerAnalyzer.CalculatePowerSpectrum().

    Steps (per Unity):
      1. Convert RR intervals → instantaneous HR (60000 / rrMs).
      2. Build cumulative time axis from RR intervals.
      3. Take the last 120 s sliding window.
      4. Lomb-Scargle periodogram from 0.003–0.5 Hz, step df = 1/span.
      5. Integrate power in LF band (0.04–0.15 Hz).

    rr_ms : list of RR intervals in ms, chronological order.
    Requires at least 8 RR intervals.
    Returns LF power (arbitrary units — consistent with Unity).
    """
    if len(rr_ms) < _MIN_RR:
        return None

    rr_arr = np.array(rr_ms, dtype=np.float64)

    # ── Build cumulative time axis (seconds) ──────────────────────────────
    t = np.cumsum(rr_arr / 1000.0)
    t = t - t[0]
    total_duration = float(t[-1])

    if total_duration < 10.0:
        return None

    # ── 120 s sliding window ──────────────────────────────────────────────
    cutoff = total_duration - _WINDOW_DURATION
    if cutoff > 0.0:
        mask = t >= cutoff
        t_win = t[mask] - cutoff
        rr_win = rr_arr[mask]
    else:
        t_win = t
        rr_win = rr_arr

    win_duration = float(t_win[-1] - t_win[0])
    if win_duration < 10.0:
        return None

    # ── Convert RR → instantaneous HR (matching Unity) ────────────────────
    times = t_win.tolist()
    hr_values = [60000.0 / float(rr) for rr in rr_win]

    if len(times) < 10:
        return None

    # Normalise time to start at 0 (matching Unity)
    t0 = times[0]
    times = [ti - t0 for ti in times]

    # ── Lomb-Scargle periodogram scan ─────────────────────────────────────
    time_span = times[-1] - times[0]
    df = 1.0 / time_span

    lf_power = 0.0
    hf_power = 0.0

    f = _F_SCAN_START
    while f <= _F_SCAN_END:
        power = _lomb_scargle_power(np.array(times), np.array(hr_values), f)

        if _LF_LOW_HZ <= f <= _LF_HIGH_HZ:
            lf_power += power
        elif _HF_LOW_HZ < f <= _HF_HIGH_HZ:
            hf_power += power

        f += df

    return round(lf_power, 4)
