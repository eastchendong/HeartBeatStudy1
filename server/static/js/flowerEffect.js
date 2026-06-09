/**
 * LF Power Blossom — particle burst reward effect.
 *
 * Matches Unity FlowerBlossomController logic:
 *   - LF Power >= 500 ms^2 for 3 consecutive readings → trigger
 *   - Cooldown = sessionDuration / 5 between triggers
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
    maxBlossoms: 5,            // max per session
    sessionDuration: 600,      // seconds — cooldown = sessionDuration / 5
  };

  class LFBlossomEffect {
    constructor(canvasEl, opts) {
      opts = opts || {};
      this.canvas = canvasEl;
      this.ctx = canvasEl.getContext('2d');
      this.particles = [];
      this.running = false;
      this.config = Object.assign({}, DEFAULT_CONFIG, opts);
      // Auto-compute cooldown from session duration if not explicitly set
      if (!opts.cooldown) {
        this.config.cooldown = this.config.sessionDuration / 5;
      }
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
      this.canvas.width = window.innerWidth;
      this.canvas.height = window.innerHeight;
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

  /**
   * PersistentParticleRing — durable progress indicator around the breathing circle.
   *
   * Design (from CDI-PRBF design rationale):
   *   - 6 visual states (0 through 5), corresponding to blossoms earned.
   *   - Total Q = 60 particles at max stage, distributed evenly.
   *   - Particles orbit just outside the circle with gentle drift.
   *   - Stage transitions fade new particles in over 1.2s (ease-out).
   *
   * Usage:
   *   const ring = new PersistentParticleRing(canvasEl, circleWrapEl);
   *   ring.start();
   *   // On blossom trigger:
   *   ring.setStage(newCount);
   *   // Each frame:
   *   ring.render(timestamp);
   */
  class PersistentParticleRing {
    constructor(canvasEl, circleWrapEl, opts) {
      opts = opts || {};
      this.canvas = canvasEl;
      this.ctx = canvasEl.getContext('2d');
      this.circleWrap = circleWrapEl;

      this.totalParticles = opts.totalParticles || 100;
      this.maxStage = opts.maxStage || 5;
      this.ringOffset = opts.ringOffset || 35; // px outside circle edge
      this.overflowMargin = opts.overflowMargin || 55; // extra canvas padding beyond circle
      this.fadeDuration = opts.fadeDuration || 1.2; // seconds

      this.stage = 0;
      this.particles = [];
      this._initParticles();

      this._running = false;
      this._lastTs = null;
    }

    _initParticles() {
      this.particles = [];
      for (let i = 0; i < this.totalParticles; i++) {
        this.particles.push({
          phi: (Math.PI * 2 * i) / this.totalParticles,  // home angle
          omega: 0.05 + Math.random() * 0.15,   // tangential drift speed (rad/s)
          swayAmp: 0.03 + Math.random() * 0.07, // azimuthal sway amplitude (rad)
          swayOmega: 0.3 + Math.random() * 0.4, // sway frequency (rad/s)
          swayPhase: Math.random() * Math.PI * 2,
          radialAmp: 2 + Math.random() * 6,     // radial breathing amplitude (px)
          radialOmega: 0.3 + Math.random() * 0.4,
          radialPhase: Math.random() * Math.PI * 2,
          hue: 190 + Math.random() * 80,         // cyan-to-purple
          alpha: 0,          // current opacity (0 = invisible)
          targetAlpha: 0,    // target opacity
          fadeStart: 0,      // when fade-in started
        });
      }
    }

    /**
     * Activate particles up to the given stage.
     * @param {number} newStage - 0..maxStage
     */
    setStage(newStage) {
      newStage = Math.max(0, Math.min(this.maxStage, newStage));
      if (newStage === this.stage) return;

      const prevActive = this._activeCount(this.stage);
      const newActive = this._activeCount(newStage);
      const now = performance.now() / 1000;

      if (newStage > this.stage) {
        // Activating more particles — fade them in
        for (let i = prevActive; i < newActive && i < this.totalParticles; i++) {
          const p = this.particles[i];
          p.targetAlpha = 0.75;
          p.fadeStart = now;
          // Stagger: each particle starts slightly after the previous
          p.fadeStart += (i - prevActive) * 0.04;
        }
      } else {
        // Deactivating — fade out (shouldn't normally happen, but handle it)
        for (let i = newActive; i < prevActive && i < this.totalParticles; i++) {
          this.particles[i].targetAlpha = 0;
          this.particles[i].fadeStart = now;
        }
      }

      this.stage = newStage;

      if (!this._running && newStage > 0) {
        this.start();
      }
    }

    _activeCount(stage) {
      if (stage <= 0) return 0;
      return Math.floor((stage / this.maxStage) * this.totalParticles);
    }

    start() {
      if (this._running) return;
      this._running = true;
      this._lastTs = null;
      this._resize();
      this._onResize = () => this._resize();
      window.addEventListener('resize', this._onResize);
      requestAnimationFrame((ts) => this._loop(ts));
    }

    stop() {
      this._running = false;
      if (this._onResize) {
        window.removeEventListener('resize', this._onResize);
        this._onResize = null;
      }
      this.ctx.clearRect(0, 0, this.canvas.width, this.canvas.height);
    }

    _resize() {
      const rect = this.circleWrap.getBoundingClientRect();
      const dpr = window.devicePixelRatio || 1;
      const m = this.overflowMargin;
      const w = rect.width + m * 2;
      const h = rect.height + m * 2;
      this.canvas.width = w * dpr;
      this.canvas.height = h * dpr;
      this.canvas.style.width = w + 'px';
      this.canvas.style.height = h + 'px';
      this.canvas.style.left = -m + 'px';
      this.canvas.style.top = -m + 'px';
      this.ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      this._cx = w / 2;
      this._cy = h / 2;
      this._ringRadius = rect.width / 2 + this.ringOffset;
    }

    _loop(ts) {
      if (!this._running) return;

      const t = ts / 1000;
      if (this._lastTs === null) this._lastTs = t;
      const dt = Math.min(t - this._lastTs, 0.1); // cap dt
      this._lastTs = t;

      this.ctx.clearRect(0, 0, this.canvas.width, this.canvas.height);

      const activeCount = this._activeCount(this.stage);
      let anyAlive = false;

      for (let i = 0; i < activeCount && i < this.totalParticles; i++) {
        const p = this.particles[i];

        // Update fade
        if (p.targetAlpha > 0 && p.alpha < p.targetAlpha) {
          const fadeElapsed = t - p.fadeStart;
          const fadeProgress = Math.min(1, fadeElapsed / this.fadeDuration);
          // Ease-out cubic: 1 - (1-p)^3
          p.alpha = p.targetAlpha * (1 - Math.pow(1 - fadeProgress, 3));
          if (p.alpha >= p.targetAlpha - 0.001) {
            p.alpha = p.targetAlpha;
          }
        } else if (p.targetAlpha <= 0 && p.alpha > 0) {
          const fadeElapsed = t - p.fadeStart;
          const fadeProgress = Math.min(1, fadeElapsed / (this.fadeDuration * 0.5));
          p.alpha = p.targetAlpha + (0.75 - p.targetAlpha) * Math.pow(1 - fadeProgress, 3);
          if (p.alpha <= 0.001) p.alpha = 0;
        }

        if (p.alpha <= 0.001) continue;
        anyAlive = true;

        // Orbital kinematics (from design doc)
        const sway = p.swayAmp * Math.sin(p.swayOmega * t + p.swayPhase);
        const angle = p.phi + p.omega * t + sway;
        const radialDist = this._ringRadius + p.radialAmp * Math.sin(p.radialOmega * t + p.radialPhase);

        const x = this._cx + Math.cos(angle) * radialDist;
        const y = this._cy + Math.sin(angle) * radialDist;

        // Subtle shimmer in lightness
        const shimmer = 65 + 15 * Math.sin(p.omega * t + p.phi);

        this.ctx.save();
        this.ctx.globalAlpha = p.alpha;
        this.ctx.fillStyle = `hsl(${p.hue}, 70%, ${shimmer}%)`;
        this.ctx.shadowColor = `hsl(${p.hue}, 70%, ${shimmer}%)`;
        this.ctx.shadowBlur = 10;
        this.ctx.beginPath();
        this.ctx.arc(x, y, 4, 0, Math.PI * 2);
        this.ctx.fill();
        this.ctx.restore();
      }

      if (anyAlive || this.stage > 0) {
        requestAnimationFrame((ts2) => this._loop(ts2));
      } else {
        this._running = false;
        this.ctx.clearRect(0, 0, this.canvas.width, this.canvas.height);
      }
    }
  }

  window.PersistentParticleRing = PersistentParticleRing;
})();
