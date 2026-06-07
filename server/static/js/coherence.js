/**
 * Entrainment Tracker — frequency-domain RSA entrainment score.
 *
 * Replaces the previous time-domain cross-correlation approach with a
 * frequency-domain method that measures how much HRV power is concentrated
 * at (and near) the guidance breathing frequency.
 *
 * Algorithm (per update):
 *   1. Buffer HR samples in a sliding window (default 60 s).
 *   2. Resample to an even time grid (default 4 Hz) via linear interpolation.
 *   3. Compute power in a narrow band [f_g ± bandHalfWidth] using the
 *      Goertzel algorithm (evaluates DFT at arbitrary non-integer bins).
 *   4. Compute total HRV power from the zero-mean variance of the resampled
 *      signal (Parseval-equivalent).
 *   5. Raw entrainment score = band_power / total_power, clamped to [0, 1].
 *   6. EMA-smooth the score, then map to 4 colour levels with hysteresis.
 *
 * Colour levels (unchanged from previous version):
 *   Level 0 (score < 0.4): White       rgb(255, 255, 255)
 *   Level 1 (score 0.4–0.6): Light Blue rgb(179, 217, 255)
 *   Level 2 (score 0.6–0.8): Sky Blue   rgb(102, 179, 255)
 *   Level 3 (score ≥ 0.8):  Deep Blue   rgb(26, 128, 230)
 *
 * Usage:
 *   const tracker = new EntrainmentTracker();
 *   const { coherence, level, color } = tracker.update(hr, timestamp, _phase, freqHz);
 */

(function () {
  'use strict';

  const COLOR_LEVELS = [
    { name: 'White',      color: 'rgb(255, 255, 255)', scoreMin: 0.0 },
    { name: 'Light Blue', color: 'rgb(179, 217, 255)', scoreMin: 0.4 },
    { name: 'Sky Blue',   color: 'rgb(102, 179, 255)', scoreMin: 0.6 },
    { name: 'Deep Blue',  color: 'rgb(26, 128, 230)',  scoreMin: 0.8 },
  ];

  class EntrainmentTracker {
    /**
     * @param {Object} [opts]
     * @param {number} [opts.windowSize=60]        Sliding window in seconds
     * @param {number} [opts.sampleRate=4]         Resample rate in Hz
     * @param {number} [opts.bandHalfWidth=0.02]   ±Hz around guidance freq
     * @param {number} [opts.bandStep=0.01]        Hz step within the band
     * @param {number} [opts.emaBlend=0.3]         EMA smoothing factor
     * @param {number} [opts.calibrationDelay=60]  Seconds before colour changes
     * @param {number} [opts.levelUpStreak=3]      Consecutive good cycles to level up
     * @param {number} [opts.levelDownStreak=3]    Consecutive bad cycles to level down
     * @param {number} [opts.hysteresis=0.1]       ±threshold band for level transitions
     */
    constructor(opts) {
      opts = opts || {};
      this.windowSize       = opts.windowSize       || 60;
      this.sampleRate       = opts.sampleRate       || 4;
      this.bandHalfWidth    = opts.bandHalfWidth    || 0.02;
      this.bandStep         = opts.bandStep         || 0.01;
      this.emaBlend         = opts.emaBlend         || 0.3;
      this.calibrationDelay = opts.calibrationDelay || 60;
      this.levelUpStreak    = opts.levelUpStreak    || 3;
      this.levelDownStreak  = opts.levelDownStreak  || 3;
      this.hysteresis       = opts.hysteresis       || 0.1;

      this.reset();
    }

    reset() {
      /** @type {Array<{hr: number, timestamp: number}>} */
      this.hrHistory = [];
      this.entrainmentScore = 0;
      this.currentLevel = 0;
      this.goodStreak = 0;
      this.badStreak = 0;
      this.sessionStart = null;
      this.lastUpdateTime = 0;
    }

    /**
     * Backward-compatible alias for `entrainmentScore`.
     * Legacy code accesses `.smoothedCoherence` directly.
     */
    get smoothedCoherence() {
      return this.entrainmentScore;
    }

    // ── Public API ──────────────────────────────────────────────────────────

    /**
     * Update the entrainment score with a new HR sample.
     *
     * @param {number} hr            - Heart rate in BPM
     * @param {number} timestamp     - Time in seconds (performance.now() / 1000)
     * @param {number} _breathingPhase - Unused (kept for API compatibility)
     * @param {number} resonantFreqHz  - Guidance breathing frequency in Hz (bpm/60)
     * @returns {{ coherence: number, level: number, color: string }}
     */
    update(hr, timestamp, _breathingPhase, resonantFreqHz) {
      if (this.sessionStart === null) {
        this.sessionStart = timestamp;
      }

      // Append sample
      this.hrHistory.push({ hr, timestamp });

      // Prune samples outside the sliding window
      const cutoff = timestamp - this.windowSize;
      while (this.hrHistory.length > 1 && this.hrHistory[0].timestamp < cutoff) {
        this.hrHistory.shift();
      }

      this.lastUpdateTime = timestamp;

      // Not enough data yet
      if (this.hrHistory.length < 10) {
        return {
          coherence: 0,
          level: 0,
          color: COLOR_LEVELS[0].color,
        };
      }

      // Resample → Goertzel band power → ratio → smooth → colour
      const resampled = this._resampleLinear();
      if (resampled.length < 20) {
        return {
          coherence: this.entrainmentScore,
          level: this.currentLevel,
          color: COLOR_LEVELS[this.currentLevel].color,
        };
      }

      const rawScore = this._computeEntrainment(resampled, resonantFreqHz);

      // EMA smoothing
      this.entrainmentScore =
        this.emaBlend * rawScore + (1 - this.emaBlend) * this.entrainmentScore;

      // Colour-level update (after calibration delay)
      const elapsed = timestamp - this.sessionStart;
      if (elapsed >= this.calibrationDelay) {
        this._updateColorLevel();
      }

      return {
        coherence: this.entrainmentScore,
        level: this.currentLevel,
        color: COLOR_LEVELS[this.currentLevel].color,
      };
    }

    // ── Resampling ──────────────────────────────────────────────────────────

    /**
     * Linearly interpolate the unevenly-sampled HR history onto an even
     * time grid at this.sampleRate Hz.
     *
     * @returns {number[]} Evenly-spaced HR values (zero-mean removed).
     */
    _resampleLinear() {
      const history = this.hrHistory;
      const t0 = history[0].timestamp;
      const tEnd = history[history.length - 1].timestamp;
      const dt = 1.0 / this.sampleRate;
      const n = Math.floor((tEnd - t0) / dt);
      if (n < 2) return [];

      const grid = new Array(n);
      let cursor = 0;

      for (let i = 0; i < n; i++) {
        const t = t0 + i * dt;

        // Advance cursor so that history[cursor].timestamp <= t < history[cursor+1].timestamp
        while (
          cursor < history.length - 2 &&
          history[cursor + 1].timestamp < t
        ) {
          cursor++;
        }

        const a = history[cursor];
        const b = history[Math.min(cursor + 1, history.length - 1)];
        const denom = b.timestamp - a.timestamp;
        if (denom <= 0) {
          grid[i] = a.hr;
        } else {
          const frac = Math.max(0, Math.min(1, (t - a.timestamp) / denom));
          grid[i] = a.hr + (b.hr - a.hr) * frac;
        }
      }

      // Remove mean (DC) — essential for meaningful power ratio
      let sum = 0;
      for (let i = 0; i < n; i++) sum += grid[i];
      const mean = sum / n;
      for (let i = 0; i < n; i++) grid[i] -= mean;

      return grid;
    }

    // ── Entrainment computation ─────────────────────────────────────────────

    /**
     * Compute the entrainment score as the ratio of HRV power in the band
     * [f_g ± bandHalfWidth] to total HRV power (zero-mean variance).
     *
     * Uses the Goertzel algorithm to evaluate DFT magnitude at specific
     * non-integer bin frequencies — far cheaper than a full FFT when only
     * a handful of bins are needed.
     *
     * @param {number[]} signal - Zero-mean, evenly-sampled HR values.
     * @param {number}   fGuidanceHz - Guidance breathing frequency in Hz.
     * @returns {number} Entrainment score in [0, 1].
     */
    _computeEntrainment(signal, fGuidanceHz) {
      const N = signal.length;
      const fs = this.sampleRate;

      // --- total power = variance (Parseval-equivalent for zero-mean signal) ---
      let totalPower = 0;
      for (let i = 0; i < N; i++) {
        totalPower += signal[i] * signal[i];
      }
      totalPower /= N;
      if (totalPower < 0.001) return 0;

      // --- band power via Goertzel at f_g ± k·step Hz ---
      const fLo = Math.max(0.02, fGuidanceHz - this.bandHalfWidth);
      const fHi = fGuidanceHz + this.bandHalfWidth;
      let bandPower = 0;

      for (let f = fLo; f <= fHi + 0.0001; f += this.bandStep) {
        const k = (N * f) / fs;           // non-integer bin — Goertzel handles this
        const gPower = this._goertzel(signal, k);
        bandPower += gPower / (N * N);     // normalise to match time-domain power scale
      }

      const ratio = bandPower / totalPower;
      return Math.min(1, Math.max(0, ratio));
    }

    // ── Goertzel single-bin DFT ─────────────────────────────────────────────

    /**
     * Goertzel algorithm — evaluates |X(k)|² for a DFT bin k (may be non-integer).
     *
     * Standard recurrence:
     *   s[n] = x[n] + 2·cos(2πk/N)·s[n−1] − s[n−2]
     *
     * Final power:
     *   |X(k)|² = s[N−1]² + s[N−2]² − 2·cos(2πk/N)·s[N−1]·s[N−2]
     *
     * @param {number[]} signal - Input samples.
     * @param {number}   k - Fractional DFT bin index.
     * @returns {number} Raw |X(k)|² (unnormalised).
     */
    _goertzel(signal, k) {
      const coeff = 2 * Math.cos((2 * Math.PI * k) / signal.length);
      let s0 = 0; // s[n-1]
      let s1 = 0; // s[n-2]

      for (let i = 0; i < signal.length; i++) {
        const s = signal[i] + coeff * s0 - s1;
        s1 = s0;
        s0 = s;
      }

      return s1 * s1 + s0 * s0 - coeff * s1 * s0;
    }

    // ── Colour-level hysteresis ─────────────────────────────────────────────

    /**
     * Update colour level with streak-based hysteresis.
     *
     * Thresholds (score → level):
     *   Level 0: < 0.4          Level 2: 0.6 – 0.8
     *   Level 1: 0.4 – 0.6      Level 3: ≥ 0.8
     *
     * Each transition requires `levelUpStreak` / `levelDownStreak` consecutive
     * values beyond the threshold ± hysteresis band.
     */
    _updateColorLevel() {
      const s = this.entrainmentScore;
      const hys = this.hysteresis;

      if (this.currentLevel >= 3) {
        if (s < 0.8 - hys) {
          this.badStreak++;
          this.goodStreak = 0;
          if (this.badStreak >= this.levelDownStreak) {
            this.currentLevel = 2;
            this.badStreak = 0;
          }
        } else {
          this.badStreak = 0;
        }
      } else if (this.currentLevel === 2) {
        if (s >= 0.8 + hys) {
          this.goodStreak++;
          this.badStreak = 0;
          if (this.goodStreak >= this.levelUpStreak) {
            this.currentLevel = 3;
            this.goodStreak = 0;
          }
        } else if (s < 0.6 - hys) {
          this.badStreak++;
          this.goodStreak = 0;
          if (this.badStreak >= this.levelDownStreak) {
            this.currentLevel = 1;
            this.badStreak = 0;
          }
        } else {
          this.goodStreak = 0;
          this.badStreak = 0;
        }
      } else if (this.currentLevel === 1) {
        if (s >= 0.6 + hys) {
          this.goodStreak++;
          this.badStreak = 0;
          if (this.goodStreak >= this.levelUpStreak) {
            this.currentLevel = 2;
            this.goodStreak = 0;
          }
        } else if (s < 0.4 - hys) {
          this.badStreak++;
          this.goodStreak = 0;
          if (this.badStreak >= this.levelDownStreak) {
            this.currentLevel = 0;
            this.badStreak = 0;
          }
        } else {
          this.goodStreak = 0;
          this.badStreak = 0;
        }
      } else {
        // Level 0: only way is up
        if (s >= 0.4 + hys) {
          this.goodStreak++;
          this.badStreak = 0;
          if (this.goodStreak >= this.levelUpStreak) {
            this.currentLevel = 1;
            this.goodStreak = 0;
          }
        } else {
          this.goodStreak = 0;
          this.badStreak = 0;
        }
      }
    }
  }

  // ── Backward-compatible alias ─────────────────────────────────────────────
  // Old code references `RSACoherenceTracker`; the new class is
  // `EntrainmentTracker`.  Export both so existing pages still work.
  window.EntrainmentTracker = EntrainmentTracker;
  window.RSACoherenceTracker = EntrainmentTracker;
})();
