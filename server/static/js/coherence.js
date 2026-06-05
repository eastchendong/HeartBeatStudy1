/**
 * RSA Coherence Tracker
 *
 * Computes coherence between HR and breathing pattern using cross-correlation
 * with lag search, matching the Unity RSAAnalyzer.cs algorithm.
 *
 * Color levels match Unity CloudVisualController:
 *   Level 0 (coherence < 0.4): White       rgb(255, 255, 255)
 *   Level 1 (coherence 0.4-0.6): Light Blue rgb(179, 217, 255)
 *   Level 2 (coherence 0.6-0.8): Sky Blue   rgb(102, 179, 255)
 *   Level 3 (coherence >= 0.8): Deep Blue   rgb(26, 128, 230)
 *
 * Usage:
 *   const tracker = new RSACoherenceTracker();
 *   const { coherence, level, color } = tracker.update(hr, timestamp, phase, freqHz);
 */
(function () {
  'use strict';

  const COLOR_LEVELS = [
    { name: 'White',      color: 'rgb(255, 255, 255)', coherenceMin: 0.0 },
    { name: 'Light Blue', color: 'rgb(179, 217, 255)', coherenceMin: 0.4 },
    { name: 'Sky Blue',   color: 'rgb(102, 179, 255)', coherenceMin: 0.6 },
    { name: 'Deep Blue',  color: 'rgb(26, 128, 230)',  coherenceMin: 0.8 },
  ];

  class RSACoherenceTracker {
    /**
     * @param {Object} opts
     * @param {number} [opts.hrBufferSize=100] Max HR samples for cross-correlation
     * @param {number} [opts.lagMax=15] ±lag search range in samples
     * @param {number} [opts.emaBlend=0.3] EMA smoothing factor
     * @param {number} [opts.calibrationDelay=60] Seconds before color changes
     * @param {number} [opts.levelUpStreak=3] Consecutive good cycles to level up
     * @param {number} [opts.levelDownStreak=3] Consecutive bad cycles to level down
     * @param {number} [opts.hysteresis=0.1] ±threshold band
     */
    constructor(opts) {
      opts = opts || {};
      this.hrBufferSize = opts.hrBufferSize || 100;
      this.lagMax = opts.lagMax || 15;
      this.emaBlend = opts.emaBlend || 0.3;
      this.calibrationDelay = opts.calibrationDelay || 60;
      this.levelUpStreak = opts.levelUpStreak || 3;
      this.levelDownStreak = opts.levelDownStreak || 3;
      this.hysteresis = opts.hysteresis || 0.1;

      this.reset();
    }

    reset() {
      this.hrHistory = [];       // [{hr, timestamp, phase}]
      this.smoothedCoherence = 0;
      this.currentLevel = 0;
      this.goodStreak = 0;
      this.badStreak = 0;
      this.sessionStart = null;
      this.lastUpdateTime = 0;
    }

    /**
     * Update coherence with a new HR sample.
     * @param {number} hr - Heart rate in BPM
     * @param {number} timestamp - Time in seconds
     * @param {number} breathingPhase - Breathing phase 0-1 (0=start inhale, 1=end exhale)
     * @param {number} resonantFreqHz - Current breathing frequency in Hz (bpm / 60)
     * @returns {{coherence: number, level: number, color: string}}
     */
    update(hr, timestamp, breathingPhase, resonantFreqHz) {
      if (this.sessionStart === null) {
        this.sessionStart = timestamp;
      }

      // Store HR sample with breathing phase
      // For phase from animation: 0-0.5=inhale, 0.5-1=exhale
      // We reconstruct as continuous 0-1 cycle phase
      this.hrHistory.push({
        hr: hr,
        timestamp: timestamp,
        phase: breathingPhase,
      });

      // Trim buffer
      while (this.hrHistory.length > this.hrBufferSize) {
        this.hrHistory.shift();
      }

      // Calculate raw coherence
      const rawCoherence = this._computeCrossCorrelationCoherence(resonantFreqHz);

      // EMA smoothing
      this.smoothedCoherence = this.emaBlend * rawCoherence + (1 - this.emaBlend) * this.smoothedCoherence;

      // Update color level (with calibration delay)
      const elapsed = timestamp - this.sessionStart;
      if (elapsed >= this.calibrationDelay) {
        this._updateColorLevel();
      }

      return {
        coherence: this.smoothedCoherence,
        level: this.currentLevel,
        color: COLOR_LEVELS[this.currentLevel].color,
      };
    }

    /**
     * Cross-correlation coherence matching Unity RSAAnalyzer.CalculateCrossCorrelationCoherence.
     */
    _computeCrossCorrelationCoherence(resonantFreqHz) {
      const sampleCount = this.hrHistory.length;
      if (sampleCount < 10) return 0;

      // Extract HR values and compute breathing sine wave
      const hrSamples = new Array(sampleCount);
      const breathingWave = new Array(sampleCount);

      let hrMean = 0;
      for (let i = 0; i < sampleCount; i++) {
        hrSamples[i] = this.hrHistory[i].hr;
        hrMean += hrSamples[i];
      }
      hrMean /= sampleCount;

      // Normalize HR to zero mean and compute breathing sine
      for (let i = 0; i < sampleCount; i++) {
        hrSamples[i] -= hrMean;
        // Breathing phase from animation: 0-0.5=inhale (0 to PI), 0.5-1=exhale (PI to 2PI)
        // Map to [0, 2*PI] for sine wave
        const phase = this.hrHistory[i].phase * 2 * Math.PI;
        breathingWave[i] = Math.sin(phase);
      }

      // Pre-compute energies for normalization
      let hrPower = 0;
      let breathPower = 0;
      for (let i = 0; i < sampleCount; i++) {
        hrPower += hrSamples[i] * hrSamples[i];
        breathPower += breathingWave[i] * breathingWave[i];
      }

      const fullDenom = Math.sqrt(hrPower * breathPower);
      if (fullDenom < 0.0001) return 0;

      // Lag search for max absolute correlation
      const maxLag = Math.min(this.lagMax, Math.floor(sampleCount / 4));
      let bestAbsCorrelation = 0;

      for (let lag = -maxLag; lag <= maxLag; lag++) {
        let numerator = 0;
        const iStart = Math.max(0, lag);
        const iEnd = Math.min(sampleCount, sampleCount + lag);

        for (let i = iStart; i < iEnd; i++) {
          numerator += hrSamples[i] * breathingWave[i - lag];
        }

        const correlation = numerator / fullDenom;
        const absCorr = Math.abs(correlation);
        if (absCorr > bestAbsCorrelation) {
          bestAbsCorrelation = absCorr;
        }
      }

      return Math.min(1, Math.max(0, bestAbsCorrelation));
    }

    /**
     * Update color level with hysteresis, matching Unity CoherenceColorController.
     * Thresholds with hysteresis band:
     *   Level 0→1: coherence ≥ 0.4 + hysteresis (when below) or ≥ 0.4 - hysteresis (when above)
     *   Level 1→2: coherence ≥ 0.6 ± hysteresis
     *   Level 2→3: coherence ≥ 0.8 ± hysteresis
     */
    _updateColorLevel() {
      const c = this.smoothedCoherence;

      if (this.currentLevel >= 3) {
        // At max: check if should level down (below 0.8 - hysteresis)
        if (c < 0.8 - this.hysteresis) {
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
        if (c >= 0.8 + this.hysteresis) {
          this.goodStreak++;
          this.badStreak = 0;
          if (this.goodStreak >= this.levelUpStreak) {
            this.currentLevel = 3;
            this.goodStreak = 0;
          }
        } else if (c < 0.6 - this.hysteresis) {
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
        if (c >= 0.6 + this.hysteresis) {
          this.goodStreak++;
          this.badStreak = 0;
          if (this.goodStreak >= this.levelUpStreak) {
            this.currentLevel = 2;
            this.goodStreak = 0;
          }
        } else if (c < 0.4 - this.hysteresis) {
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
        // Level 0: check if should level up
        if (c >= 0.4 + this.hysteresis) {
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

  window.RSACoherenceTracker = RSACoherenceTracker;
})();
