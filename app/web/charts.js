/* Small chart toolkit for the GymLog dashboard: plain SVG, no libraries, no network. Every chart has a hover/keyboard readout and a table view.
   Names and values from the server are put in the page with textContent, never as HTML. */
(function () {
  'use strict';
  const GL = (window.GL = {});
  const NS = 'http://www.w3.org/2000/svg';

  // ------------------------------------------------------------------ DOM helpers
  GL.h = function (tag, attrs, ...children) {
    const el = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs || {})) {
      if (v === null || v === undefined || v === false) continue;
      if (k === 'class') el.className = v;
      else if (k === 'style') el.style.cssText = v;
      else if (k.startsWith('on')) el.addEventListener(k.slice(2), v);
      else el.setAttribute(k, v === true ? '' : v);
    }
    add(el, children);
    return el;
  };
  GL.s = function (tag, attrs, ...children) {
    const el = document.createElementNS(NS, tag);
    for (const [k, v] of Object.entries(attrs || {})) if (v !== null && v !== undefined) el.setAttribute(k, v);
    add(el, children);
    return el;
  };
  function add(el, children) {
    for (const c of children.flat()) {
      if (c === null || c === undefined || c === false) continue;
      el.appendChild(c instanceof Node ? c : document.createTextNode(String(c)));
    }
  }

  // ------------------------------------------------------------------ numbers, dates, units
  GL.unit = 'lb';
  const LB_PER_KG = 2.2046226218;
  GL.w = (lb) => (lb === null || lb === undefined ? null : GL.unit === 'kg' ? lb / LB_PER_KG : lb);
  const nf0 = new Intl.NumberFormat('en-US', { maximumFractionDigits: 0 });
  const nf1 = new Intl.NumberFormat('en-US', { maximumFractionDigits: 1 });
  GL.fmt = {
    int: (n) => (n === null || n === undefined ? '-' : nf0.format(n)),
    one: (n) => (n === null || n === undefined ? '-' : nf1.format(n)),
    compact(n) {
      if (n === null || n === undefined) return '-';
      const a = Math.abs(n);
      if (a >= 1e6) return nf1.format(n / 1e6) + 'M';
      if (a >= 1e4) return nf1.format(n / 1e3) + 'K';
      return nf0.format(n);
    },
    weight: (lb, digits = 0) => (lb === null || lb === undefined ? '-' : (digits ? nf1 : nf0).format(GL.w(lb)) + ' ' + GL.unit),
    date(s) { return new Date(s + 'T12:00:00').toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' }); },
    short(s) { return new Date(s + 'T12:00:00').toLocaleDateString('en-US', { month: 'short', day: 'numeric' }); },
    pct: (n) => nf1.format(n) + '%',
    mb: (bytes) => (bytes >= 1e9 ? nf1.format(bytes / 1e9) + ' GB' : nf1.format(bytes / 1e6) + ' MB'),
    when(ms) { return new Date(ms).toLocaleString('en-US', { month: 'short', day: 'numeric', year: 'numeric', hour: 'numeric', minute: '2-digit' }); },
  };
  GL.t = (s) => new Date(s + 'T12:00:00').getTime();           // ISO date -> milliseconds

  // ------------------------------------------------------------------ scales
  function niceStep(raw) {
    const p = Math.pow(10, Math.floor(Math.log10(raw)));
    const f = raw / p;
    return (f <= 1 ? 1 : f <= 2 ? 2 : f <= 2.5 ? 2.5 : f <= 5 ? 5 : 10) * p;
  }
  function niceScale(min, max, count, zero) {
    if (zero) min = Math.min(0, min);
    if (min === max) { max = min + 1; }
    const step = niceStep((max - min) / count);
    const lo = Math.floor(min / step) * step;
    const hi = Math.ceil(max / step) * step;
    const ticks = [];
    for (let v = lo; v <= hi + step / 2; v += step) ticks.push(Math.round(v * 1e6) / 1e6);
    return { lo, hi, ticks };
  }
  function timeTicks(t0, t1, maxTicks) {
    const day = 864e5, span = t1 - t0;
    const ticks = [];
    if (span <= 70 * day) {                                    // weekly ticks, on Mondays
      const d = new Date(t0); d.setHours(12, 0, 0, 0);
      d.setDate(d.getDate() + ((8 - d.getDay()) % 7));
      const stepWeeks = Math.max(1, Math.ceil(span / day / 7 / maxTicks));
      for (let t = d.getTime(); t <= t1; t += stepWeeks * 7 * day) ticks.push({ t, label: new Date(t).toLocaleDateString('en-US', { month: 'short', day: 'numeric' }) });
      return ticks;
    }
    const months = span / (30.44 * day);
    const step = [1, 2, 3, 6, 12, 24, 60].find((k) => months / k <= maxTicks) || 60;
    const d = new Date(t0); d.setDate(1); d.setHours(12, 0, 0, 0);
    while (d.getTime() < t0) d.setMonth(d.getMonth() + 1);
    d.setMonth(d.getMonth() - (d.getMonth() % step === 0 ? 0 : d.getMonth() % step));
    while (d.getTime() <= t1) {
      if (d.getTime() >= t0) ticks.push({ t: d.getTime(), label: d.toLocaleDateString('en-US', step >= 12 || d.getMonth() === 0 ? { month: 'short', year: 'numeric' } : { month: 'short' }) });
      d.setMonth(d.getMonth() + step);
    }
    return ticks;
  }

  // ------------------------------------------------------------------ shared: tooltip, legend, resize
  function makeTip(host) {
    const tip = GL.h('div', { class: 'tip hidden', role: 'status' });
    host.appendChild(tip);
    return {
      el: tip,
      show(x, y, headText, rows) {
        tip.textContent = '';
        if (headText) tip.appendChild(GL.h('div', { class: 't-head' }, headText));
        for (const r of rows) {
          tip.appendChild(GL.h('div', { class: 't-row' },
            r.color ? GL.h('span', { class: 't-key' + (r.box ? ' box' : ''), style: (r.box ? 'background:' : 'border-color:') + r.color }) : null,
            GL.h('span', { class: 't-name' }, r.name || ''),
            GL.h('span', { class: 't-val' }, r.value)));
        }
        tip.classList.remove('hidden');
        const w = tip.offsetWidth, hostW = host.clientWidth;
        let left = x + 14;
        if (left + w > hostW) left = Math.max(0, x - w - 14);
        tip.style.left = left + 'px';
        tip.style.top = Math.max(0, y - tip.offsetHeight - 8) + 'px';
      },
      hide() { tip.classList.add('hidden'); },
    };
  }
  GL.legend = function (items, box) {
    return GL.h('div', { class: 'legend' }, items.map((it) =>
      GL.h('span', { class: 'item' }, GL.h('span', { class: 'key' + (box ? ' box' : ''), style: (box ? 'background:' : 'border-color:') + it.color }), it.name)));
  };
  function observe(host, draw) {
    let last = 0;
    const ro = new ResizeObserver(() => { const w = host.clientWidth; if (w && Math.abs(w - last) > 1) { last = w; draw(); } });
    ro.observe(host);
    return ro;
  }

  // ------------------------------------------------------------------ line chart
  /* opts: series [{name, color, points:[[ms, y], ...], width?, muted?}], height, yFormat(y)->text, xFormat(ms)->text, zero, endLabels */
  GL.lineChart = function (host, opts) {
    host.textContent = '';
    const wrap = GL.h('div', { class: 'chart' });
    host.appendChild(wrap);
    const series = opts.series.filter((s) => s.points.length);
    if (series.length >= 2) host.insertBefore(GL.legend(series.map((s) => ({ name: s.name, color: s.color }))), wrap);
    if (!series.length) { wrap.appendChild(GL.h('div', { class: 'empty' }, opts.empty || 'Nothing to show for this period.')); return; }
    const tip = makeTip(wrap);
    const yFormat = opts.yFormat || GL.fmt.int;
    const xFormat = opts.xFormat || ((t) => GL.fmt.date(new Date(t).toISOString().slice(0, 10)));
    const H = opts.height || 260;
    let state = { idx: -1 };

    // every x position any series has, so the crosshair can snap to it
    const xs = Array.from(new Set(series.flatMap((s) => s.points.map((p) => p[0])))).sort((a, b) => a - b);
    const byX = series.map((s) => new Map(s.points.map((p) => [p[0], p[1]])));

    function draw() {
      const W = wrap.clientWidth || 600;
      const m = { l: 52, r: opts.endLabels === false || series.length > 4 ? 14 : 64, t: 10, b: 26 };
      const iw = W - m.l - m.r, ih = H - m.t - m.b;
      const allY = series.flatMap((s) => s.points.map((p) => p[1]));
      const ys = niceScale(Math.min(...allY), Math.max(...allY), 5, opts.zero);
      const t0 = xs[0], t1 = xs[xs.length - 1] === xs[0] ? xs[0] + 864e5 : xs[xs.length - 1];
      const X = (t) => m.l + ((t - t0) / (t1 - t0)) * iw;
      const Y = (v) => m.t + ih - ((v - ys.lo) / (ys.hi - ys.lo)) * ih;
      const svg = GL.s('svg', { viewBox: `0 0 ${W} ${H}`, role: 'img', tabindex: '0', 'aria-label': opts.label || 'Line chart (use the table view for exact values)' });
      for (const v of ys.ticks) {
        svg.appendChild(GL.s('line', { x1: m.l, x2: W - m.r, y1: Y(v), y2: Y(v), class: v === ys.lo ? 'axis-line' : 'grid-line' }));
        svg.appendChild(GL.s('text', { x: m.l - 8, y: Y(v) + 4, 'text-anchor': 'end' }, yFormat(v)));
      }
      const ticks = timeTicks(t0, t1, Math.max(3, Math.floor(iw / 90)));
      for (const k of ticks) svg.appendChild(GL.s('text', { x: X(k.t), y: H - 6, 'text-anchor': 'middle' }, k.label));
      if (opts.area && series.length === 1) {
        const pts = series[0].points;
        const d = `M${X(pts[0][0])},${Y(ys.lo)} ` + pts.map((p) => `L${X(p[0])},${Y(p[1])}`).join(' ') + ` L${X(pts[pts.length - 1][0])},${Y(ys.lo)} Z`;
        svg.appendChild(GL.s('path', { d, fill: series[0].color, opacity: '0.10' }));
      }
      for (const s of series) {
        const gap = (opts.gapDays || 0) * 864e5;                 // a long stretch with no readings is left as a gap, not joined by a line
        const d = s.points.map((p, i) => (i && !(gap && p[0] - s.points[i - 1][0] > gap) ? 'L' : 'M') + X(p[0]).toFixed(1) + ',' + Y(p[1]).toFixed(1)).join(' ');
        svg.appendChild(GL.s('path', { d, class: 'line', style: `stroke:${s.color};stroke-width:${s.width || 2};opacity:${s.muted ? 0.45 : 1}` }));
      }
      for (const s of series) {                               // end dot, and a direct label when there are only a few series
        if (s.muted) continue;
        const p = s.points[s.points.length - 1];
        svg.appendChild(GL.s('circle', { cx: X(p[0]), cy: Y(p[1]), r: 4, class: 'ring', style: `fill:${s.color}` }));
        if (m.r > 20) svg.appendChild(GL.s('text', { x: X(p[0]) + 9, y: Y(p[1]) + 4, style: 'fill:var(--text-2)' }, yFormat(p[1])));
      }
      const cross = GL.s('line', { class: 'cross hidden', y1: m.t, y2: m.t + ih });
      const dots = series.map((s) => GL.s('circle', { r: 4.5, class: 'ring hidden', style: `fill:${s.color}` }));
      svg.appendChild(cross); dots.forEach((d) => svg.appendChild(d));
      const hit = GL.s('rect', { x: m.l, y: m.t, width: iw, height: ih, class: 'hit' });
      svg.appendChild(hit);

      function place(i) {
        state.idx = i;
        if (i < 0) { cross.classList.add('hidden'); dots.forEach((d) => d.classList.add('hidden')); tip.hide(); return; }
        const t = xs[i];
        cross.setAttribute('x1', X(t)); cross.setAttribute('x2', X(t)); cross.classList.remove('hidden');
        const rows = [];
        series.forEach((s, k) => {
          const v = byX[k].get(t);
          if (v === undefined) { dots[k].classList.add('hidden'); return; }
          dots[k].setAttribute('cx', X(t)); dots[k].setAttribute('cy', Y(v)); dots[k].classList.remove('hidden');
          rows.push({ name: s.name, value: (opts.valueFormat || yFormat)(v), color: s.color });
        });
        const topY = Math.min(...rows.map((_, j) => 0), 0) + m.t + 30;
        tip.show(X(t), Math.max(topY, m.t + 40), xFormat(t), rows);
      }
      function nearest(clientX) {
        const r = svg.getBoundingClientRect();
        const px = ((clientX - r.left) / r.width) * W;
        const t = t0 + ((px - m.l) / iw) * (t1 - t0);
        let lo = 0, hi = xs.length - 1;
        while (lo < hi) { const mid = (lo + hi) >> 1; if (xs[mid] < t) lo = mid + 1; else hi = mid; }
        return lo > 0 && Math.abs(xs[lo - 1] - t) <= Math.abs(xs[lo] - t) ? lo - 1 : lo;
      }
      svg.addEventListener('pointermove', (e) => place(nearest(e.clientX)));
      svg.addEventListener('pointerleave', () => place(-1));
      svg.addEventListener('keydown', (e) => {
        if (e.key === 'ArrowRight') { place(Math.min(xs.length - 1, (state.idx < 0 ? -1 : state.idx) + 1)); e.preventDefault(); }
        else if (e.key === 'ArrowLeft') { place(Math.max(0, (state.idx < 0 ? xs.length : state.idx) - 1)); e.preventDefault(); }
        else if (e.key === 'Escape') place(-1);
      });
      svg.addEventListener('blur', () => place(-1));
      const old = wrap.querySelector('svg');
      if (old) old.remove();
      wrap.insertBefore(svg, tip.el);
      tip.hide();
      state.idx = -1;
    }
    draw();
    observe(wrap, draw);
  };

  // ------------------------------------------------------------------ column chart (single series) and stacked columns
  /* opts: labels [text], tooltipHead [text], series [{name, color, values}], stacked (bool), height, yFormat, xTickEvery (auto) */
  GL.columnChart = function (host, opts) {
    host.textContent = '';
    const wrap = GL.h('div', { class: 'chart' });
    host.appendChild(wrap);
    const series = opts.series;
    const n = opts.labels.length;
    if (series.length >= 2) host.insertBefore(GL.legend(series.map((s) => ({ name: s.name, color: s.color })), true), wrap);
    if (!n) { wrap.appendChild(GL.h('div', { class: 'empty' }, opts.empty || 'Nothing to show for this period.')); return; }
    const tip = makeTip(wrap);
    const yFormat = opts.yFormat || GL.fmt.compact;
    const H = opts.height || 240;
    const totals = opts.labels.map((_, i) => series.reduce((a, s) => a + (s.values[i] || 0), 0));

    function draw() {
      const W = wrap.clientWidth || 600;
      const m = { l: 52, r: 12, t: 10, b: 26 };
      const iw = W - m.l - m.r, ih = H - m.t - m.b;
      const ys = niceScale(0, Math.max(...(opts.stacked ? totals : series.flatMap((s) => s.values)), 1), 5, true);
      const Y = (v) => m.t + ih - ((v - ys.lo) / (ys.hi - ys.lo)) * ih;
      const band = iw / n;
      const bw = Math.max(2, Math.min(24, band * 0.7));
      const svg = GL.s('svg', { viewBox: `0 0 ${W} ${H}`, role: 'img', 'aria-label': opts.label || 'Column chart (use the table view for exact values)' });
      for (const v of ys.ticks) {
        svg.appendChild(GL.s('line', { x1: m.l, x2: W - m.r, y1: Y(v), y2: Y(v), class: v === 0 ? 'axis-line' : 'grid-line' }));
        svg.appendChild(GL.s('text', { x: m.l - 8, y: Y(v) + 4, 'text-anchor': 'end' }, yFormat(v)));
      }
      const every = Math.max(1, Math.ceil(n / Math.max(2, Math.floor(iw / 70))));
      opts.labels.forEach((lab, i) => { if (i % every === 0) svg.appendChild(GL.s('text', { x: m.l + band * (i + 0.5), y: H - 6, 'text-anchor': 'middle' }, lab)); });
      const bars = [];
      opts.labels.forEach((lab, i) => {
        const cx = m.l + band * (i + 0.5), x = cx - bw / 2;
        const rows = series.map((s) => ({ name: s.name, value: (opts.valueFormat || yFormat)(s.values[i] || 0), color: s.color, box: true }));
        let top = ys.lo;
        const g = GL.s('g', { class: 'bar', tabindex: '-1' });
        const visible = series.map((s, k) => [s, k]).filter(([s]) => (s.values[i] || 0) > 0);
        visible.forEach(([s, k], j) => {
          const v = s.values[i] || 0;
          const y1 = opts.stacked ? Y(top + v) : Y(v), y0 = opts.stacked ? Y(top) : Y(0);
          const gap = opts.stacked && j > 0 ? 2 : 0;
          const hgt = Math.max(1, y0 - y1 - gap);
          const isTop = !opts.stacked || j === visible.length - 1;
          const r = Math.min(4, bw / 2, hgt);
          const d = isTop
            ? `M${x},${y1 + hgt} V${y1 + r} Q${x},${y1} ${x + r},${y1} H${x + bw - r} Q${x + bw},${y1} ${x + bw},${y1 + r} V${y1 + hgt} Z`
            : `M${x},${y1} H${x + bw} V${y1 + hgt} H${x} Z`;
          g.appendChild(GL.s('path', { d, style: `fill:${s.color}` }));
          if (opts.stacked) top += v;
        });
        svg.appendChild(g);
        bars.push(g);
        const hit = GL.s('rect', { x: m.l + band * i, y: m.t, width: band, height: ih, class: 'hit', tabindex: '0', role: 'img',
          'aria-label': `${opts.tooltipHead ? opts.tooltipHead[i] : lab}: ${rows.map((r) => r.name + ' ' + r.value).join(', ')}` });
        const show = () => {
          bars.forEach((b) => b.classList.remove('hot')); g.classList.add('hot');
          const shown = opts.stacked ? rows.filter((r, k) => (series[k].values[i] || 0) > 0).reverse() : rows;
          if (opts.stacked && shown.length > 1) shown.push({ name: 'Total', value: (opts.valueFormat || yFormat)(totals[i]) });
          tip.show(cx, Y(opts.stacked ? totals[i] : Math.max(...series.map((s) => s.values[i] || 0))) + m.t * 0 + 20, opts.tooltipHead ? opts.tooltipHead[i] : lab, shown);
        };
        const hide = () => { g.classList.remove('hot'); tip.hide(); };
        hit.addEventListener('pointerenter', show); hit.addEventListener('pointermove', show); hit.addEventListener('pointerleave', hide);
        hit.addEventListener('focus', show); hit.addEventListener('blur', hide);
        svg.appendChild(hit);
      });
      const old = wrap.querySelector('svg');
      if (old) old.remove();
      wrap.insertBefore(svg, tip.el);
      tip.hide();
    }
    draw();
    observe(wrap, draw);
  };

  // ------------------------------------------------------------------ sparkline (a stat tile's little trend)
  GL.sparkline = function (values, w, h, color) {
    const svg = GL.s('svg', { width: w, height: h, viewBox: `0 0 ${w} ${h}`, 'aria-hidden': 'true' });
    if (values.length < 2) return svg;
    const lo = Math.min(...values), hi = Math.max(...values), span = hi - lo || 1;
    const d = values.map((v, i) => (i ? 'L' : 'M') + ((i / (values.length - 1)) * (w - 4) + 2).toFixed(1) + ',' + (h - 3 - ((v - lo) / span) * (h - 6)).toFixed(1)).join(' ');
    svg.appendChild(GL.s('path', { d, class: 'line', style: `fill:none;stroke:${color || 'var(--s1)'};stroke-width:2;stroke-linecap:round;stroke-linejoin:round` }));
    return svg;
  };

  // ------------------------------------------------------------------ consistency calendar (heatmap)
  /* days: [{date, workouts, cardio, photo}] ; from/to ISO dates. One cell per day, weeks in columns, Monday on top. */
  GL.calendar = function (host, days, from, to) {
    host.textContent = '';
    const wrap = GL.h('div', { class: 'chart' });
    host.appendChild(wrap);
    const tip = makeTip(wrap);
    const byDate = new Map(days.map((d) => [d.date, d]));
    const start = new Date(from + 'T12:00:00'), end = new Date(to + 'T12:00:00');
    start.setDate(start.getDate() - ((start.getDay() + 6) % 7));        // back to Monday
    const weeks = Math.ceil((end - start) / 864e5 / 7) + 1;
    const cell = 13, gap = 3, left = 30, top = 18;
    const W = left + weeks * (cell + gap), H = top + 7 * (cell + gap);
    const svg = GL.s('svg', { width: W, height: H, viewBox: `0 0 ${W} ${H}`, role: 'img', 'aria-label': 'Calendar of workout days (use the table view for the list)', style: 'overflow:visible' });
    ['Mon', 'Wed', 'Fri'].forEach((n, i) => svg.appendChild(GL.s('text', { x: 0, y: top + i * 2 * (cell + gap) + cell - 2, style: 'font-size:11px' }, n)));
    const colorFor = (n) => (n >= 3 ? 'var(--seq-5)' : n === 2 ? 'var(--seq-4)' : n === 1 ? 'var(--seq-3)' : 'var(--grid)');
    let lastMonth = -1;
    for (let w = 0; w < weeks; w++) {
      for (let r = 0; r < 7; r++) {
        const d = new Date(start); d.setDate(start.getDate() + w * 7 + r);
        if (d > end || d < new Date(from + 'T00:00:00')) continue;
        const iso = d.getFullYear() + '-' + String(d.getMonth() + 1).padStart(2, '0') + '-' + String(d.getDate()).padStart(2, '0');
        if (r === 0 && d.getMonth() !== lastMonth) { lastMonth = d.getMonth(); svg.appendChild(GL.s('text', { x: left + w * (cell + gap), y: 11, style: 'font-size:11px' }, d.toLocaleDateString('en-US', { month: 'short' }))); }
        const info = byDate.get(iso) || { workouts: 0, cardio: 0, photo: 0 };
        const x = left + w * (cell + gap), y = top + r * (cell + gap);
        const rect = GL.s('rect', { x, y, width: cell, height: cell, rx: 3, class: 'cell', style: `fill:${colorFor(info.workouts)}`, tabindex: '-1' });
        const rows = [{ name: 'Workouts', value: String(info.workouts) }];
        if (info.cardio) rows.push({ name: 'Cardio sessions', value: String(info.cardio) });
        if (info.photo) rows.push({ name: 'Progress photo', value: 'yes' });
        const show = () => tip.show(x + cell / 2, y + 4, GL.fmt.date(iso), rows);
        rect.addEventListener('pointerenter', show); rect.addEventListener('pointerleave', () => tip.hide());
        svg.appendChild(rect);
        if (info.cardio) svg.appendChild(GL.s('circle', { cx: x + cell / 2, cy: y + cell / 2, r: 2, style: 'fill:var(--text);pointer-events:none;opacity:0.8' }));
      }
    }
    const scroller = GL.h('div', { class: 'cal' });
    scroller.appendChild(svg);
    wrap.insertBefore(scroller, tip.el);
    wrap.appendChild(GL.h('div', { class: 'legend', style: 'margin-top:8px' },
      GL.h('span', { class: 'item' }, 'Workouts a day:'),
      ...[['0', 'var(--grid)'], ['1', 'var(--seq-3)'], ['2', 'var(--seq-4)'], ['3+', 'var(--seq-5)']].map(([t, c]) => GL.h('span', { class: 'item' }, GL.h('span', { class: 'key box', style: `background:${c}` }), t)),
      GL.h('span', { class: 'item' }, GL.h('span', { class: 'key box', style: 'background:var(--text);border-radius:50%;width:5px;height:5px;margin:0 2px' }), 'cardio that day')));
    scroller.scrollLeft = scroller.scrollWidth;                          // newest weeks first in view
  };

  // ------------------------------------------------------------------ ranked horizontal bars (a name, a bar and a figure per row)
  /* rows: [{name, value, label, color?}] largest first */
  GL.ranked = function (host, rows, color) {
    host.textContent = '';
    if (!rows.length) { host.appendChild(GL.h('div', { class: 'empty' }, 'Nothing to show for this period.')); return; }
    const max = Math.max(...rows.map((r) => r.value), 1);
    host.appendChild(GL.h('div', { class: 'ranked' }, rows.map((r) =>
      GL.h('div', { class: 'row', title: r.name + ': ' + r.label },
        GL.h('span', { class: 'name' }, r.name),
        GL.h('span', { class: 'track' }, GL.h('span', { class: 'fill', style: `display:block;width:${Math.max(1, (r.value / max) * 100)}%;background:${r.color || color || 'var(--s1)'}` })),
        GL.h('span', { class: 'val' }, r.label)))));
  };

  // ------------------------------------------------------------------ table view
  /* columns: [{label, right?}], rows: [[text, ...]] */
  GL.table = function (columns, rows, onRow) {
    const t = GL.h('table', {},
      GL.h('thead', {}, GL.h('tr', {}, columns.map((c) => GL.h('th', { class: c.right ? 'r' : '' }, c.label)))),
      GL.h('tbody', {}, rows.map((r, i) => {
        const tr = GL.h('tr', onRow ? { class: 'clickable', tabindex: '0', 'data-i': i } : {}, r.map((v, k) => GL.h('td', { class: columns[k] && columns[k].right ? 'r' : '' }, v === null || v === undefined ? '' : v)));
        if (onRow) {
          tr.addEventListener('click', () => onRow(i));
          tr.addEventListener('keydown', (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onRow(i); } });
        }
        return tr;
      })));
    return GL.h('div', { class: 'table-wrap' }, t);
  };

  /* A chart card: title, optional subtitle, extra tools, and a Chart/Table switch that shows the same data as a table. */
  GL.chartCard = function ({ title, sub, cls, tools, build, table }) {
    const body = GL.h('div', {});
    const toggle = table ? GL.h('button', { class: 'btn small', 'aria-pressed': 'false' }, 'Table') : null;
    const card = GL.h('section', { class: 'card ' + (cls || '') },
      GL.h('div', { class: 'card-head' },
        GL.h('div', { class: 'grow' }, GL.h('h2', {}, title), sub ? GL.h('p', { class: 'sub' }, sub) : null),
        GL.h('div', { class: 'tools' }, tools || null, toggle)),
      body);
    let showTable = false;
    function render() {
      body.textContent = '';
      if (showTable) { const t = table(); body.appendChild(GL.table(t.columns, t.rows)); }
      else build(body);
    }
    if (toggle) toggle.addEventListener('click', () => { showTable = !showTable; toggle.setAttribute('aria-pressed', String(showTable)); toggle.textContent = showTable ? 'Chart' : 'Table'; render(); });
    card.rerender = render;
    render();
    return card;
  };

  // ------------------------------------------------------------------ icons for insights (status is never colour alone)
  GL.icon = function (kind) {
    const paths = {
      good: ['M5 10.5l3.2 3.2L15 6.8', 'var(--good)'],
      note: ['M10 6v5M10 14h.01', 'var(--warn)'],
      warn: ['M10 5.5v6M10 14.2h.01', 'var(--crit)'],
    };
    const [d, c] = paths[kind] || paths.note;
    const svg = GL.s('svg', { class: 'icon', viewBox: '0 0 20 20', 'aria-hidden': 'true' });
    svg.appendChild(GL.s('circle', { cx: 10, cy: 10, r: 8.5, fill: 'none', stroke: c, 'stroke-width': 1.6 }));
    svg.appendChild(GL.s('path', { d, fill: 'none', stroke: c, 'stroke-width': 1.8, 'stroke-linecap': 'round', 'stroke-linejoin': 'round' }));
    return svg;
  };
})();
