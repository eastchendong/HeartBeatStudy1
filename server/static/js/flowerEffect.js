/**
 * LF Power Blossom — particle burst reward effect.
 *
 * Matches Unity FlowerBlossomController logic:
 *   - LF Power >= 500 ms^2 for 3 consecutive readings → trigger
 *   - 120s cooldown between triggers
 *   - Max 5 blossoms per session
 *
 * Usage:
 *   const blossom = new LFBlossomEffect(canvasElement);
 *   blossom.startSession();
 *   // Every ~1s, call:
 *   blossom.update(lfPower);
 *   // Returns { triggered, count, progress }
 */
(function () {
  'use strict';

  const DEFAULT_CONFIG = {
    threshold: 500,            // LF Power threshold (ms^2)
    requiredStreak: 3,         // consecutive good readings
    cooldown: 120,             // seconds between blossoms
    maxBlossoms: 5,            // max per session
    sessionDuration: 600,      // seconds
  };

  class LFBlossomEffect {
    constructor(canvasEl, opts) {
      opts = opts || {};
      this.canvas = canvasEl;
      this.ctx = canvasEl.getContext('2d');
      this.particles = [];
      this.running = false;
      this.config = Object.assign({}, DEFAULT_CONFIG, opts);
      this.reset();
    }

    reset() {
      this.particles = [];
      this.blossomCount = 0;
      this.goodStreak = 0;
      this.lastBlossomTime = -999;
      this.sessionStart = null;
      this.sessionTime = 0;
      this.progress = 0;
    }

    startSession() {
      this.reset();
      this.sessionStart = performance.now() / 1000;
      this.resize();
    }

    resize() {
      const rect = this.canvas.parentElement.getBoundingClientRect();
      this.canvas.width = rect.width;
      this.canvas.height = rect.height;
    }

    /**
     * Call every ~1 second with current LF power value.
     * @returns {{ triggered: boolean, count: number, progress: number }}
     */
    update(lfPower) {
      if (this.sessionStart === null) {
        return { triggered: false, count: 0, progress: 0 };
      }

      this.sessionTime = performance.now() / 1000 - this.sessionStart;

      // Check if can still blossom
      const canBlossom = this.blossomCount < this.config.maxBlossoms &&
        (this.sessionTime - this.lastBlossomTime) >= this.config.cooldown;

      let triggered = false;

      if (canBlossom && lfPower !== null && lfPower >= this.config.threshold) {
        this.goodStreak++;
        if (this.goodStreak >= this.config.requiredStreak) {
          // Stability delay (0.5s in Unity, instant here since we check at 1s)
          this._triggerBlossom();
          triggered = true;
        }
      } else {
        this.goodStreak = 0;
      }

      // Update progress
      this._updateProgress(lfPower || 0);

      return {
        triggered: triggered,
        count: this.blossomCount,
        progress: this.progress,
      };
    }

    _triggerBlossom() {
      this.blossomCount++;
      this.lastBlossomTime = this.sessionTime;
      this.goodStreak = 0;
      this._spawnParticles();
    }

    _updateProgress(lfPower) {
      if (this.blossomCount >= this.config.maxBlossoms) {
        this.progress = 1;
        return;
      }
      const elapsed = this.sessionTime - this.lastBlossomTime;
      const cooldownRatio = Math.min(1, Math.max(0, elapsed / this.config.cooldown));
      const powerRatio = Math.min(1, Math.max(0, lfPower / (this.config.threshold * 2)));
      // Combined: time-dependent + LF-dependent
      this.progress = cooldownRatio * (0.5 + 0.5 * powerRatio);
    }

    /**
     * Spawn a burst of particles from the center of the canvas.
     */
    _spawnParticles() {
      const cx = this.canvas.width / 2;
      const cy = this.canvas.height / 2;
      const count = 100;

      for (let i = 0; i < count; i++) {
        const angle = (Math.PI * 2 * i) / count + (Math.random() - 0.5) * 0.6;
        const speed = 120 + Math.random() * 320;
        const hue = 190 + Math.random() * 80; // cyan to purple range
        this.particles.push({
          x: cx,
          y: cy,
          vx: Math.cos(angle) * speed,
          vy: Math.sin(angle) * speed - 20,
          life: 1.0,
          decay: 0.4 + Math.random() * 0.5,
          size: 4 + Math.random() * 8,
          hue: hue,
        });
      }

      if (!this.running) {
        this.running = true;
        this._animate();
      }
    }

    _animate() {
      if (!this.running) return;

      const dt = 0.016;
      this.ctx.clearRect(0, 0, this.canvas.width, this.canvas.height);

      let alive = false;
      for (const p of this.particles) {
        p.x += p.vx * dt;
        p.y += p.vy * dt;
        p.vy += 60 * dt; // stronger gravity
        p.life -= p.decay * dt;

        if (p.life <= 0) continue;
        alive = true;

        const alpha = Math.min(1, p.life * 1.2);
        this.ctx.save();
        this.ctx.globalAlpha = alpha;
        const lightness = 55 + p.life * 20;
        this.ctx.fillStyle = `hsl(${p.hue}, 90%, ${lightness}%)`;
        this.ctx.shadowColor = `hsl(${p.hue}, 90%, ${lightness}%)`;
        this.ctx.shadowBlur = 12;
        this.ctx.beginPath();
        this.ctx.arc(p.x, p.y, p.size * (0.5 + p.life * 0.5), 0, Math.PI * 2);
        this.ctx.fill();
        this.ctx.restore();
      }

      if (alive) {
        requestAnimationFrame(() => this._animate());
      } else {
        this.running = false;
        this.ctx.clearRect(0, 0, this.canvas.width, this.canvas.height);
      }
    }
  }

  window.LFBlossomEffect = LFBlossomEffect;
})();
