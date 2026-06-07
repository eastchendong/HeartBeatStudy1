/**
 * LiveStatsTracker — real-time SVG line charts for the Control Group screen.
 *
 * Renders 4 mini line charts in a 2×2 grid:
 *   - BPM (heart rate)
 *   - RR Interval (ms)
 *   - Coherence / Entrainment score
 *   - LF Power (ms²)
 *
 * Each chart shows a rolling window (~60 s) and updates at ~1 Hz.
 *
 * Usage:
 *   const tracker = new LiveStatsTracker(document.getElementById('stats-container'));
 *   tracker.start();
 *   // Every ~1s:
 *   tracker.addDataPoint({ bpm, rrInterval, coherence, lfPower });
 *   // On cleanup:
 *   tracker.stop();
 */

(function () {
  'use strict';

  const CONFIG = {
    windowSec: 60,         // rolling window in seconds
    updateInterval: 1000,  // ms between render passes
    chartHeight: 140,      // SVG viewBox height
    lineWidth: 2,
    gridColor: '#30363d',
    mutedColor: '#8b949e',
    bgColor: '#0d1117',
  };

  const CHART_DEFS = [
    {
      key: 'bpm',
      label: 'BPM',
      unit: '',
      color: '#f85149',
      yMin: 40, yMax: 140,
      fixedY: true,
      format: v => v.toFixed(0),
    },
    {
      key: 'rrInterval',
      label: 'RR Interval',
      unit: 'ms',
      color: '#d29922',
      yMin: 400, yMax: 1600,
      fixedY: true,
      format: v => v.toFixed(0),
    },
    {
      key: 'coherence',
      label: 'Entrainment',
      unit: '',
      color: '#58a6ff',
      yMin: 0, yMax: 1,
      fixedY: true,
      format: v => v.toFixed(2),
    },
    {
      key: 'lfPower',
      label: 'LF Power',
      unit: 'ms²',
      color: '#3fb950',
      yMin: 0, yMax: null,  // auto-scale
      fixedY: false,
      format: v => v >= 100 ? v.toFixed(0) : v.toFixed(1),
    },
  ];

  class LiveStatsTracker {
    /**
     * @param {HTMLElement} container - DOM element to inject charts into
     * @param {Object} [opts]
     */
    constructor(container, opts) {
      opts = opts || {};
      this.container = container;
      this.windowSec = opts.windowSec || CONFIG.windowSec;

      /** @type {Map<string, Array<{t: number, v: number}>>} */
      this.buffers = new Map();
      for (const def of CHART_DEFS) {
        this.buffers.set(def.key, []);
      }

      this.sessionStart = null;
      this._timer = null;
      this._built = false;
    }

    // ── Public API ──────────────────────────────────────────────────────────

    start() {
      this.sessionStart = performance.now() / 1000;
      this._buildDOM();
      this._timer = setInterval(() => this._render(), CONFIG.updateInterval);
    }

    stop() {
      if (this._timer) {
        clearInterval(this._timer);
        this._timer = null;
      }
    }

    /**
     * Push a new data point into the rolling buffers.
     * @param {{ bpm?: number, rrInterval?: number, coherence?: number, lfPower?: number }} point
     */
    addDataPoint(point) {
      const t = performance.now() / 1000 - this.sessionStart;
      for (const def of CHART_DEFS) {
        const v = point[def.key];
        if (v != null && !isNaN(v)) {
          const buf = this.buffers.get(def.key);
          buf.push({ t, v });
          // Prune old
          const cutoff = t - this.windowSec;
          while (buf.length > 1 && buf[0].t < cutoff) {
            buf.shift();
          }
        }
      }
    }

    // ── DOM construction ────────────────────────────────────────────────────

    _buildDOM() {
      if (this._built) return;
      this._built = true;

      this.container.innerHTML = '';

      const grid = document.createElement('div');
      grid.style.cssText = `
        display: grid;
        grid-template-columns: 1fr 1fr;
        gap: 0.8rem;
        width: 100%;
      `;

      this._chartEls = {};

      for (const def of CHART_DEFS) {
        const card = document.createElement('div');
        card.style.cssText = `
          background: #161b22;
          border: 1px solid #30363d;
          border-radius: 10px;
          padding: 0.6rem 0.6rem 0.3rem 0.6rem;
        `;

        // Header row
        const header = document.createElement('div');
        header.style.cssText = `
          display: flex;
          justify-content: space-between;
          align-items: baseline;
          margin-bottom: 0.2rem;
          padding: 0 0.2rem;
        `;

        const label = document.createElement('span');
        label.style.cssText = `
          font-size: 0.7rem;
          color: #8b949e;
          text-transform: uppercase;
          letter-spacing: 0.06em;
        `;
        label.textContent = def.label;

        const currentVal = document.createElement('span');
        currentVal.style.cssText = `
          font-size: 0.95rem;
          font-weight: 700;
          color: ${def.color};
          font-variant-numeric: tabular-nums;
        `;
        currentVal.textContent = '--';
        currentVal.id = `st-val-${def.key}`;

        header.appendChild(label);
        header.appendChild(currentVal);

        // SVG chart
        const svg = this._makeSVG(def);
        svg.id = `st-svg-${def.key}`;

        card.appendChild(header);
        card.appendChild(svg);
        grid.appendChild(card);

        this._chartEls[def.key] = {
          svg,
          valEl: currentVal,
        };
      }

      this.container.appendChild(grid);
    }

    _makeSVG(def) {
      const H = CONFIG.chartHeight;
      const svgNS = 'http://www.w3.org/2000/svg';
      const svg = document.createElementNS(svgNS, 'svg');
      svg.setAttribute('viewBox', `0 0 300 ${H}`);
      svg.style.cssText = 'width:100%;height:140px;display:block;';

      // Grid lines
      const gridGroup = document.createElementNS(svgNS, 'g');
      for (let i = 0; i <= 4; i++) {
        const line = document.createElementNS(svgNS, 'line');
        const y = 12 + (i / 4) * (H - 28);
        line.setAttribute('x1', '0');
        line.setAttribute('y1', String(y));
        line.setAttribute('x2', '300');
        line.setAttribute('y2', String(y));
        line.setAttribute('stroke', CONFIG.gridColor);
        line.setAttribute('stroke-width', '1');
        gridGroup.appendChild(line);
      }
      svg.appendChild(gridGroup);

      // Area fill
      const area = document.createElementNS(svgNS, 'path');
      area.setAttribute('fill', def.color);
      area.setAttribute('opacity', '0.08');
      area.setAttribute('stroke', 'none');
      area.id = `st-area-${def.key}`;
      svg.appendChild(area);

      // Line path
      const path = document.createElementNS(svgNS, 'path');
      path.setAttribute('fill', 'none');
      path.setAttribute('stroke', def.color);
      path.setAttribute('stroke-width', String(CONFIG.lineWidth));
      path.setAttribute('stroke-linecap', 'round');
      path.setAttribute('stroke-linejoin', 'round');
      path.id = `st-line-${def.key}`;
      svg.appendChild(path);

      return svg;
    }

    // ── Rendering ───────────────────────────────────────────────────────────

    _render() {
      for (const def of CHART_DEFS) {
        const buf = this.buffers.get(def.key);
        const els = this._chartEls[def.key];
        this._renderChart(def, buf, els);
      }
    }

    _renderChart(def, buf, els) {
      const H = CONFIG.chartHeight;
      const pad = { top: 12, bottom: 16, left: 2, right: 2 };
      const cw = 300 - pad.left - pad.right;
      const ch = H - pad.top - pad.bottom;

      // Current value
      if (buf.length > 0) {
        els.valEl.textContent = def.format(buf[buf.length - 1].v) + ' ' + def.unit;
      }

      if (buf.length < 2) {
        els.svg.querySelector(`[id^="st-line-"]`).setAttribute('d', '');
        els.svg.querySelector(`[id^="st-area-"]`).setAttribute('d', '');
        return;
      }

      // Y-range
      const values = buf.map(d => d.v);
      let yMin = def.yMin;
      let yMax = def.yMax;
      if (!def.fixedY || def.yMax == null) {
        const vMin = Math.min(...values);
        const vMax = Math.max(...values);
        const range = vMax - vMin || 1;
        yMin = def.fixedY ? def.yMin : vMin - range * 0.15;
        yMax = def.fixedY ? (def.yMax || vMax) : vMax + range * 0.15;
      }
      const ySpan = yMax - yMin || 1;

      // Time range
      const tMin = buf[0].t;
      const tMax = buf[buf.length - 1].t;
      const tSpan = tMax - tMin || this.windowSec;

      // Build line points
      const pts = [];
      for (const d of buf) {
        const x = pad.left + ((d.t - tMin) / tSpan) * cw;
        const y = pad.top + ch - ((d.v - yMin) / ySpan) * ch;
        pts.push(`${x.toFixed(1)},${y.toFixed(1)}`);
      }

      const pathD = `M ${pts.join(' L ')}`;
      const areaD = `${pathD} L ${pad.left + cw},${pad.top + ch} L ${pad.left},${pad.top + ch} Z`;

      els.svg.querySelector(`[id^="st-line-"]`).setAttribute('d', pathD);
      els.svg.querySelector(`[id^="st-area-"]`).setAttribute('d', areaD);
    }
  }

  window.LiveStatsTracker = LiveStatsTracker;
})();
