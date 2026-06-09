/**
 * Entrainment Tracker — frequency-domain RSA entrainment score via Welch PSD.
 *
 * Measures how much HRV power is concentrated at the guidance breathing
 * frequency using Welch's method (segmented, windowed, averaged periodograms).
 * This is more robust than a single-window DFT for short (30–60 s) recordings
 * because segment averaging reduces spectral variance and the Hanning window
 * suppresses sidelobe leakage.
 *
 * Algorithm (per ~1 Hz update):
 *   1. Buffer HR samples in a sliding window (default 60 s).
 *   2. Resample to an even time grid (default 4 Hz) via linear interpolation.
 *   3. Welch PSD:
 *      a. Split the resampled signal into overlapping segments (75 % overlap).
 *      b. Apply a Hanning window to each segment.
 *      c. Zero-pad each segment to the next power of two.
 *      d. Compute the FFT periodogram |X[k]|² for each segment.
 *      e. Average periodograms across segments → smooth PSD estimate.
 *   4. Compute guidance-band power (f_g ± 0.02 Hz) and total power (all bins
 *      except DC) from the averaged PSD.
 *   5. Raw entrainment score = P_band / P_total, clamped to [0, 1].
 *   6. EMA-smooth the score.
 *   7. At the end of each breathing cycle, evaluate the smoothed score against
 *      a single threshold ± hysteresis band, counting consecutive good/bad
 *      cycles to drive colour-level transitions.
 *
 * Colour levels (white → black for AR glasses, where black = transparent):
 *   Level 0: White       rgb(255, 255, 255)
 *   Level 1: Light Gray  rgb(170, 170, 170)
 *   Level 2: Dark Gray   rgb( 80,  80,  80)
 *   Level 3: Near Black  rgb( 20,  20,  20)
 *
 * Level transitions (after calibration delay, evaluated per breathing cycle):
 *   Good cycle: smoothed score ≥ threshold + hysteresis → streak++
 *   Bad  cycle: smoothed score <  threshold - hysteresis → streak++
 *   Neutral:    threshold - hysteresis ≤ score < threshold + hysteresis
 *               → both streaks reset, colour unchanged.
 *   After streakRequired consecutive good/bad cycles → level up/down by 1.
 *
 * Usage:
 *   const tracker = new EntrainmentTracker();
 *   const { coherence, level, color } = tracker.update(hr, timestamp, _phase, freqHz);
 */

(function () {
  'use strict';

  // White → Black gradient for AR glasses (black = transparent in AR)
  const COLOR_LEVELS = [
    { name: 'White',      color: 'rgb(255, 255, 255)' },
    { name: 'Light Gray', color: 'rgb(170, 170, 170)' },
    { name: 'Dark Gray',  color: 'rgb( 80,  80,  80)' },
    { name: 'Near Black', color: 'rgb( 20,  20,  20)' },
  ];

  class EntrainmentTracker {
    /**
     * @param {Object} [opts]
     * @param {number} [opts.windowSize=60]        Sliding window in seconds
     * @param {number} [opts.sampleRate=4]         Resample rate in Hz
     * @param {number} [opts.bandHalfWidth=0.02]   ±Hz around guidance freq
     * @param {number} [opts.segmentLen=128]       Welch segment length (samples)
     * @param {number} [opts.segmentHop=32]        Hop between segments (75 % overlap)
     * @param {number} [opts.fftLen=256]           Zero-padded FFT length (power of 2)
     * @param {number} [opts.emaBlend=0.3]         EMA smoothing factor
     * @param {number} [opts.calibrationDelay=60]  Seconds before colour changes
     * @param {number} [opts.threshold=0.5]        Central coherence threshold
     * @param {number} [opts.hysteresis=0.05]      ±tolerance band around threshold
     * @param {number} [opts.streakRequired=3]     Consecutive good/bad cycles for level change
     */
    constructor(opts) {
      opts = opts || {};
      this.windowSize       = opts.windowSize       || 60;
      this.sampleRate       = opts.sampleRate       || 4;
      this.bandHalfWidth    = opts.bandHalfWidth    || 0.02;
      this.segmentLen       = opts.segmentLen       || 128;
      this.segmentHop       = opts.segmentHop       || 32;
      this.fftLen           = opts.fftLen           || 256;
      this.emaBlend         = opts.emaBlend         || 0.3;
      this.calibrationDelay = opts.calibrationDelay || 60;
      this.threshold        = opts.threshold        || 0.5;
      this.hysteresis       = opts.hysteresis       || 0.05;
      this.streakRequired   = opts.streakRequired   || 3;

      // Pre-compute Hanning window for the segment length
      this._hanning = this._makeHanning(this.segmentLen);

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
     */
    get smoothedCoherence() {
      return this.entrainmentScore;
    }

    // ── Public API ──────────────────────────────────────────────────────────

    /**
     * Update the entrainment score with a new HR sample.
     *
     * @param {number} hr              - Heart rate in BPM
     * @param {number} timestamp       - Time in seconds (performance.now() / 1000)
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

      // Resample → Welch PSD → band-power ratio → smooth → colour
      const resampled = this._resampleLinear();
      if (resampled.length < this.segmentLen) {
        return {
          coherence: this.entrainmentScore,
          level: this.currentLevel,
          color: COLOR_LEVELS[this.currentLevel].color,
        };
      }

      const rawScore = this._computeWelchEntrainment(resampled, resonantFreqHz);

      // EMA smoothing
      this.entrainmentScore =
        this.emaBlend * rawScore + (1 - this.emaBlend) * this.entrainmentScore;

      // Level transitions are evaluated per breathing cycle via endCycle(),
      // not on every sample update.  Return the current state for display.

      return {
        coherence: this.entrainmentScore,
        level: this.currentLevel,
        color: COLOR_LEVELS[this.currentLevel].color,
      };
    }

    // ── Resampling ──────────────────────────────────────────────────────────

    /**
     * Linearly interpolate the unevenly-sampled HR history onto an even
     * time grid at this.sampleRate Hz. Returns a zero-mean signal.
     *
     * @returns {number[]} Evenly-spaced, zero-mean HR values.
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

      // Remove mean (DC)
      let sum = 0;
      for (let i = 0; i < n; i++) sum += grid[i];
      const mean = sum / n;
      for (let i = 0; i < n; i++) grid[i] -= mean;

      return grid;
    }

    // ── Welch PSD ───────────────────────────────────────────────────────────

    /**
     * Compute the entrainment score via Welch's method.
     *
     * Steps:
     *   1. Split the zero-mean signal into overlapping segments.
     *   2. Apply a Hanning window to each segment.
     *   3. Zero-pad → FFT → |X[k]|² (periodogram).
     *   4. Average periodograms across segments → smooth PSD.
     *   5. Guidance-band power / total power → entrainment score.
     *
     * @param {number[]} signal - Zero-mean, evenly-sampled HR values.
     * @param {number}   fGuidanceHz - Guidance breathing frequency in Hz.
     * @returns {number} Entrainment score in [0, 1].
     */
    _computeWelchEntrainment(signal, fGuidanceHz) {
      const N = signal.length;
      const segLen = this.segmentLen;
      const hop = this.segmentHop;
      const fftLen = this.fftLen;
      const fs = this.sampleRate;
      const halfFft = fftLen / 2;

      // Accumulate averaged periodogram (real-valued magnitudes²)
      const psdAccum = new Array(halfFft + 1).fill(0);
      let numSegments = 0;

      // Slide overlapping segments across the signal
      for (let start = 0; start + segLen <= N; start += hop) {
        // Real + imaginary arrays, zero-padded to fftLen
        const re = new Array(fftLen).fill(0);
        const im = new Array(fftLen).fill(0);

        // Apply Hanning window and copy into FFT buffer
        for (let i = 0; i < segLen; i++) {
          re[i] = signal[start + i] * this._hanning[i];
        }

        // In-place radix-2 FFT
        this._fft(re, im);

        // Accumulate |X[k]|² (periodogram for this segment)
        for (let k = 0; k <= halfFft; k++) {
          psdAccum[k] += re[k] * re[k] + im[k] * im[k];
        }

        numSegments++;
      }

      if (numSegments === 0) return 0;

      // Average periodograms → Welch PSD estimate
      for (let k = 0; k <= halfFft; k++) {
        psdAccum[k] /= numSegments;
      }

      // Frequency resolution
      const df = fs / fftLen;

      // Sum power in guidance band [f_g ± bandHalfWidth] and total power
      const fLo = Math.max(df, fGuidanceHz - this.bandHalfWidth);
      const fHi = fGuidanceHz + this.bandHalfWidth;
      let bandPower = 0;
      let totalPower = 0;

      for (let k = 0; k <= halfFft; k++) {
        const f = k * df;
        const p = psdAccum[k];
        totalPower += p;
        if (f >= fLo && f <= fHi) {
          bandPower += p;
        }
      }

      // Exclude DC (bin 0) from total power
      totalPower -= psdAccum[0];
      if (totalPower < 0.001) return 0;

      // Entrainment score = fraction of HRV power locked to guidance frequency
      const ratio = bandPower / totalPower;
      return Math.min(1, Math.max(0, ratio));
    }

    // ── Hanning window ──────────────────────────────────────────────────────

    /**
     * Generate a Hanning (Hann) window of length N.
     *   w[n] = 0.5 × (1 − cos(2πn / (N−1)))
     *
     * @param {number} N - Window length.
     * @returns {number[]} Window coefficients.
     */
    _makeHanning(N) {
      const w = new Array(N);
      for (let i = 0; i < N; i++) {
        w[i] = 0.5 * (1 - Math.cos((2 * Math.PI * i) / (N - 1)));
      }
      return w;
    }

    // ── Radix-2 Cooley–Tukey FFT (in-place) ─────────────────────────────────

    /**
     * Compute the in-place radix-2 decimation-in-time FFT.
     *
     * @param {number[]} re - Real part (modified in-place).
     * @param {number[]} im - Imaginary part (modified in-place).
     */
    _fft(re, im) {
      const N = re.length;

      // ── Bit-reversal permutation ──────────────────────────────────────
      let j = 0;
      for (let i = 0; i < N - 1; i++) {
        if (i < j) {
          [re[i], re[j]] = [re[j], re[i]];
          [im[i], im[j]] = [im[j], im[i]];
        }
        let k = N >> 1;
        while (k <= j) {
          j -= k;
          k >>= 1;
        }
        j += k;
      }

      // ── Butterfly stages ──────────────────────────────────────────────
      for (let len = 2; len <= N; len <<= 1) {
        const half = len >> 1;
        const angle = (-2 * Math.PI) / len;
        const wRe = Math.cos(angle);
        const wIm = Math.sin(angle);

        for (let i = 0; i < N; i += len) {
          let curRe = 1;
          let curIm = 0;

          for (let k = 0; k < half; k++) {
            const i1 = i + k;
            const i2 = i + k + half;

            const tRe = curRe * re[i2] - curIm * im[i2];
            const tIm = curRe * im[i2] + curIm * re[i2];

            re[i2] = re[i1] - tRe;
            im[i2] = im[i1] - tIm;
            re[i1] = re[i1] + tRe;
            im[i1] = im[i1] + tIm;

            // Advance twiddle factor
            const nRe = curRe * wRe - curIm * wIm;
            curIm = curRe * wIm + curIm * wRe;
            curRe = nRe;
          }
        }
      }
    }

    // ── Per-cycle colour-level evaluation ────────────────────────────────────

    /**
     * Called at the end of each breathing cycle to evaluate whether the
     * colour level should change.
     *
     * Uses a single threshold ± hysteresis band shared across all levels:
     *   score ≥ threshold + hysteresis  → good cycle (streak up)
     *   score <  threshold - hysteresis  → bad  cycle (streak down)
     *   otherwise                        → neutral (both streaks reset)
     *
     * After `streakRequired` consecutive good/bad cycles, level moves by ±1.
     */
    endCycle() {
      // Only evaluate after calibration delay
      const elapsed = this.lastUpdateTime - this.sessionStart;
      if (elapsed < this.calibrationDelay) {
        return;
      }
      this._updateColorLevel();
    }

    /**
     * Internal: apply single-threshold hysteresis to the current smoothed score.
     */
    _updateColorLevel() {
      const s = this.entrainmentScore;
      const upThreshold = this.threshold + this.hysteresis;
      const downThreshold = this.threshold - this.hysteresis;
      const maxLevel = COLOR_LEVELS.length - 1;

      if (s >= upThreshold) {
        // Good cycle
        this.goodStreak++;
        this.badStreak = 0;
        if (this.goodStreak >= this.streakRequired && this.currentLevel < maxLevel) {
          this.currentLevel++;
          this.goodStreak = 0;
        }
      } else if (s < downThreshold) {
        // Bad cycle
        this.badStreak++;
        this.goodStreak = 0;
        if (this.badStreak >= this.streakRequired && this.currentLevel > 0) {
          this.currentLevel--;
          this.badStreak = 0;
        }
      } else {
        // Hysteresis dead zone — colour stays put, forget streak progress
        this.goodStreak = 0;
        this.badStreak = 0;
      }
    }
  }

  // ── Backward-compatible alias ─────────────────────────────────────────────
  window.EntrainmentTracker = EntrainmentTracker;
  window.RSACoherenceTracker = EntrainmentTracker;
})();
