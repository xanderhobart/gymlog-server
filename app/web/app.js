/* GymLog dashboard: sign-in, filters and the seven views. Data comes from /api/dash/... with the signed-in user's own token. */
(function () {
  'use strict';
  const { h, fmt } = GL;
  const $ = (id) => document.getElementById(id);

  const VIEWS = [['overview', 'Overview'], ['exercises', 'Exercises'], ['muscles', 'Muscles'], ['body', 'Body'], ['cardio', 'Cardio'], ['backups', 'Backups'], ['export', 'Export']];
  const PRESETS = [['30', 'Last 30 days'], ['90', 'Last 90 days'], ['180', 'Last 6 months'], ['365', 'Last year'], ['all', 'All time']];
  const REGION_COLOR = { Chest: 'var(--s1)', Back: 'var(--s2)', Shoulders: 'var(--s3)', Arms: 'var(--s4)', Core: 'var(--s5)', Legs: 'var(--s6)', Other: 'var(--s7)' };
  const REGION_ORDER = ['Chest', 'Back', 'Shoulders', 'Arms', 'Core', 'Legs', 'Other'];
  const SLOTS = ['var(--s1)', 'var(--s2)', 'var(--s3)', 'var(--s4)', 'var(--s5)', 'var(--s6)'];

  const state = { token: null, user: null, view: 'overview', preset: 'all', from: '', to: '', backup: 'latest', span: null, backupInfo: null,
    exercise: null, variantMode: 'combined', metric: 'e1rm', lift: null, search: '' };
  let renderToken = 0;

  // ------------------------------------------------------------------ talking to the server
  class ApiError extends Error { constructor(status, message) { super(message); this.status = status; } }

  async function api(path, params, raw) {
    const q = new URLSearchParams();
    for (const [k, v] of Object.entries(params || {})) if (v !== null && v !== undefined && v !== '') q.set(k, v);
    const res = await fetch(path + (q.toString() ? '?' + q : ''), { headers: { Authorization: 'Bearer ' + state.token } });
    if (res.status === 401) { signOut('Your token was not accepted. Sign in again.'); throw new ApiError(401, 'Not signed in'); }
    if (!res.ok) {
      let detail = res.statusText;
      try { detail = (await res.json()).detail || detail; } catch (e) { /* not JSON */ }
      throw new ApiError(res.status, detail);
    }
    return raw ? res : res.json();
  }
  const scope = () => ({ from: state.from, to: state.to, backup: state.backup });

  async function download(path, params, fallbackName) {
    const res = await api(path, params, true);
    const blob = await res.blob();
    const m = /filename="?([^"]+)"?/.exec(res.headers.get('content-disposition') || '');
    const a = h('a', { href: URL.createObjectURL(blob), download: m ? m[1] : fallbackName });
    document.body.appendChild(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(a.href), 10000);
  }

  // ------------------------------------------------------------------ sign in / out
  function stored() { return sessionStorage.getItem('gl_token') || localStorage.getItem('gl_token'); }
  function signOut(message) {
    sessionStorage.removeItem('gl_token'); localStorage.removeItem('gl_token');
    state.token = null;
    $('app').classList.add('hidden');
    $('signin').classList.remove('hidden');
    const e = $('signin-error');
    e.textContent = message || '';
    e.classList.toggle('hidden', !message);
  }
  async function signIn(token, remember) {
    state.token = token.trim();
    try {
      state.user = await api('/api/me');
    } catch (e) {
      if (e.status === 401) return;                                // signOut already showed the message
      signOut(e.status === 429 ? 'Too many wrong tries. Wait a minute.' : 'Could not reach the server: ' + e.message);
      return;
    }
    (remember ? localStorage : sessionStorage).setItem('gl_token', state.token);
    $('signin').classList.add('hidden');
    $('app').classList.remove('hidden');
    $('who').textContent = state.user.name;
    await boot();
  }

  // ------------------------------------------------------------------ frame: tabs, filters, units, theme
  async function boot() {
    state.view = location.hash.slice(1) || 'overview';
    if (!VIEWS.some(([k]) => k === state.view)) state.view = 'overview';
    try {
      const o = await api('/api/dash/overview', { backup: state.backup });
      state.span = o.span; state.backupInfo = o.backup;
    } catch (e) { state.span = null; }
    buildTabs(); buildFilters(); render();
  }
  function buildTabs() {
    const nav = $('tabs'); nav.textContent = '';
    for (const [k, label] of VIEWS) {
      nav.appendChild(h('button', { role: 'tab', 'aria-selected': String(k === state.view), onclick: () => { state.view = k; location.hash = k; buildTabs(); buildFilters(); render(); } }, label));
    }
  }
  function applyPreset() {
    if (state.preset === 'all' || !state.span || !state.span.last) { state.from = ''; state.to = ''; return; }
    if (state.preset === 'custom') return;
    const end = new Date(state.span.last + 'T12:00:00'); end.setDate(end.getDate() - Number(state.preset) + 1);
    state.from = end.toISOString().slice(0, 10); state.to = '';
  }
  function buildFilters() {
    const box = $('filters'); box.textContent = '';
    const filtered = ['overview', 'exercises', 'muscles', 'body', 'cardio', 'export'].includes(state.view);
    if (filtered) {
      const sel = h('select', { 'aria-label': 'Time range', onchange: (e) => { state.preset = e.target.value; applyPreset(); buildFilters(); render(); } },
        PRESETS.map(([v, l]) => h('option', { value: v, selected: v === state.preset }, l)), h('option', { value: 'custom', selected: state.preset === 'custom' }, 'Custom range'));
      box.appendChild(h('span', { class: 'field' }, h('span', { class: 'sec' }, 'Show'), sel));
      if (state.preset === 'custom') {
        const from = h('input', { type: 'date', value: state.from, 'aria-label': 'From' });
        const to = h('input', { type: 'date', value: state.to, 'aria-label': 'To' });
        const go = () => { state.from = from.value; state.to = to.value; render(); };
        from.addEventListener('change', go); to.addEventListener('change', go);
        box.appendChild(h('span', { class: 'field' }, from, h('span', { class: 'sec' }, 'to'), to));
      } else if (state.preset !== 'all' && state.span) {
        box.appendChild(h('span', { class: 'range-note' }, 'ending ' + fmt.date(state.span.last)));
      }
    }
    if (state.backup !== 'latest') {
      box.appendChild(h('span', { class: 'badge note' }, 'Viewing an older backup'));
      box.appendChild(h('button', { class: 'btn small', onclick: () => { state.backup = 'latest'; boot(); } }, 'Back to the latest'));
    }
  }
  function setUnit(u) {
    GL.unit = u; localStorage.setItem('gl_unit', u);
    $('u-lb').setAttribute('aria-pressed', String(u === 'lb')); $('u-kg').setAttribute('aria-pressed', String(u === 'kg'));
    if (state.token) render();
  }
  function setTheme(t) {
    localStorage.setItem('gl_theme', t);
    if (t === 'auto') document.documentElement.removeAttribute('data-theme'); else document.documentElement.setAttribute('data-theme', t);
    for (const k of ['auto', 'light', 'dark']) $('t-' + k).setAttribute('aria-pressed', String(k === t));
  }

  // ------------------------------------------------------------------ rendering a view
  async function render() {
    const my = ++renderToken;
    const main = $('main');
    if (!main.firstChild) main.appendChild(h('p', { class: 'sec', style: 'margin-top:20px' }, 'Loading...'));
    main.classList.add('loading');
    try {
      const node = await VIEW_FN[state.view]();
      if (my !== renderToken) return;
      main.textContent = '';
      main.appendChild(node);
    } catch (e) {
      if (my !== renderToken || e.status === 401) return;
      main.textContent = '';
      main.appendChild(h('section', { class: 'card', style: 'margin-top:14px' }, h('h2', {}, e.status === 404 ? 'Nothing here yet' : 'Something went wrong'),
        h('p', { class: 'sec' }, e.message), h('button', { class: 'btn small', onclick: render }, 'Try again')));
    } finally {
      if (my === renderToken) main.classList.remove('loading');
    }
  }
  const grid = (...kids) => h('div', { class: 'grid', style: 'margin-top:14px' }, kids);
  const tile = (label, value, delta, spark) => h('section', { class: 'card tile col-3' }, h('div', { class: 'label' }, label), h('div', { class: 'value' }, value), delta ? h('div', { class: 'delta' }, delta) : null, spark || null);
  const dayOf = (iso) => GL.t(iso);

  // ------------------------------------------------------------------ Overview
  async function overview() {
    const [o, cal] = await Promise.all([api('/api/dash/overview', scope()), api('/api/dash/calendar', { ...scope(), from: calendarFrom() })]);
    const s = o.summary, b = o.body;
    const wl = b.latest_lb !== undefined ? b.latest_lb - b.first_lb : null;
    const tiles = [
      tile('Workouts', fmt.int(s.workouts), s.weeks ? `${fmt.one(s.avg_per_week)} a week on average` : ''),
      tile('Sets and reps', fmt.int(s.sets) + ' sets', `${fmt.int(s.reps)} reps across ${s.exercises} exercises`),
      tile('Total volume', fmt.compact(GL.w(s.volume_lb)) + ' ' + GL.unit, `weight x reps, partial reps count half`),
      tile('Body weight', b.latest_lb !== undefined ? fmt.weight(b.latest_lb, 1) : '-',
        wl !== null ? `${wl >= 0 ? '+' : ''}${fmt.one(GL.w(wl))} ${GL.unit} since ${fmt.short(b.points[0].date)}` : 'no weigh-ins in this period',
        b.points && b.points.length > 2 ? GL.sparkline(b.points.slice(-60).map((p) => GL.w(p.avg7)), 200, 30) : null),
    ];
    const insights = h('section', { class: 'card col-6' }, h('div', { class: 'card-head' }, h('div', { class: 'grow' }, h('h2', {}, 'Worth a look'), h('p', { class: 'sub' }, 'Based on the selected period'))),
      o.insights.length ? h('ul', { class: 'insights' }, o.insights.map((i) => h('li', {}, GL.icon(i.kind), h('div', {}, h('div', { class: 'title' }, i.title), h('div', { class: 'detail' }, i.detail)))))
        : h('div', { class: 'sec' }, 'Nothing stands out. Steady is good.'));
    const records = GL.chartCard({
      title: 'New bests, last 30 days', sub: 'Estimated one-rep max beat everything before', cls: 'col-6',
      build: (body) => body.appendChild(o.records.length ? GL.table([{ label: 'Exercise' }, { label: 'Set', right: true }, { label: 'Est. max', right: true }, { label: 'Gain', right: true }],
        o.records.map((r) => [r.name + (r.variant && r.variant !== 'Standard' ? ' (' + r.variant + ')' : ''), `${fmt.weight(r.weight_lb)} x ${r.reps}`, fmt.weight(r.e1rm_lb), '+' + fmt.one(GL.w(r.gain_lb)) + ' ' + GL.unit]))
        : h('div', { class: 'sec' }, 'No new bests in this period.')),
    });
    const w = o.weekly;
    const weekly = GL.chartCard({
      title: 'Weekly volume', sub: `Total weight lifted each week (${GL.unit}), Monday to Sunday`, cls: 'col-12',
      build: (body) => GL.columnChart(body, {
        labels: w.weeks.map(fmt.short), tooltipHead: w.weeks.map((d, i) => `Week of ${fmt.short(d)} (${w.workouts[i]} workout${w.workouts[i] === 1 ? '' : 's'})`),
        series: [{ name: 'Volume', color: 'var(--s1)', values: w.series.length ? w.series[0].volume_lb.map(GL.w) : [] }], yFormat: fmt.compact, valueFormat: (v) => fmt.int(v) + ' ' + GL.unit, height: 240,
      }),
      table: () => ({ columns: [{ label: 'Week of' }, { label: 'Workouts', right: true }, { label: 'Sets', right: true }, { label: 'Volume (' + GL.unit + ')', right: true }],
        rows: w.weeks.map((d, i) => [fmt.date(d), w.workouts[i], w.series[0].sets[i], fmt.int(GL.w(w.series[0].volume_lb[i]))]) }),
    });
    const calFrom = calendarFrom() || (state.span && state.span.first) || '2026-01-01';
    const calTo = state.to || (state.span && state.span.last) || calFrom;
    const calendar = GL.chartCard({
      title: 'Consistency', sub: 'One square a day. Darker means more workouts.', cls: 'col-12',
      build: (body) => (cal.days.length ? GL.calendar(body, cal.days, calFrom, calTo) : body.appendChild(h('div', { class: 'empty' }, 'No workouts in this period.'))),
      table: () => ({ columns: [{ label: 'Date' }, { label: 'Workouts', right: true }, { label: 'Cardio', right: true }, { label: 'Photo' }], rows: cal.days.map((d) => [fmt.date(d.date), d.workouts, d.cardio, d.photo ? 'yes' : '']) }),
    });
    return grid(...tiles, insights, records, weekly, calendar);
  }
  function calendarFrom() {                                       // the calendar shows at most the last year of the chosen period
    const end = state.to || (state.span && state.span.last);
    if (!end) return state.from;
    const d = new Date(end + 'T12:00:00'); d.setDate(d.getDate() - 364);
    const cap = d.toISOString().slice(0, 10);
    return state.from && state.from > cap ? state.from : cap;
  }

  // ------------------------------------------------------------------ Exercises
  const METRICS = {
    e1rm: { label: 'Estimated max', key: 'best_e1rm_lb', weight: true },
    top: { label: 'Top set weight', key: 'top_weight_lb', weight: true },
    avg: { label: 'Average weight', key: 'avg_weight_lb', weight: true },
    volume: { label: 'Volume per workout', key: 'volume_lb', weight: true },
    reps: { label: 'Reps per workout', key: 'reps', weight: false },
    sets: { label: 'Sets per workout', key: 'sets', weight: false },
  };
  async function exercises() {
    const list = (await api('/api/dash/exercises', scope())).exercises;
    if (!list.length) return grid(h('section', { class: 'card col-12' }, h('div', { class: 'empty' }, 'No logged sets in this period.')));
    if (!state.exercise || !list.some((e) => e.exercise_id === state.exercise.exercise_id && e.variant_id === state.exercise.variant_id)) {
      state.exercise = { exercise_id: list[0].exercise_id, variant_id: list[0].variant_id };
    }
    const q = state.search.trim().toLowerCase();
    const shown = list.filter((e) => !q || (e.name + ' ' + e.variant).toLowerCase().includes(q));
    const search = h('input', { type: 'search', placeholder: 'Search exercises', value: state.search, 'aria-label': 'Search exercises' });
    const listBody = h('div', { class: 'table-wrap', style: 'max-height:640px' });
    const draw = () => {
      listBody.textContent = '';
      listBody.appendChild(GL.table([{ label: 'Exercise' }, { label: 'Times', right: true }, { label: 'Best max', right: true }], shown.map((e) => [
        h('span', {}, e.name, e.variant && e.variant !== 'Standard' ? h('span', { class: 'muted' }, ' · ' + e.variant) : null, e.plateau_days && e.plateau_days >= 42 ? h('span', { class: 'badge note', style: 'margin-left:6px', title: 'No new best for ' + Math.floor(e.plateau_days / 7) + ' weeks' }, 'stalled') : null),
        e.sessions, fmt.weight(e.best_e1rm_lb)]), (i) => { state.exercise = { exercise_id: shown[i].exercise_id, variant_id: shown[i].variant_id }; render(); }));
      const sel = listBody.querySelector(`tr[data-i="${shown.findIndex((e) => e.exercise_id === state.exercise.exercise_id && e.variant_id === state.exercise.variant_id)}"]`);
      if (sel) sel.classList.add('selected');
    };
    search.addEventListener('input', () => { state.search = search.value; shown.length = 0; list.filter((e) => !search.value.trim() || (e.name + ' ' + e.variant).toLowerCase().includes(search.value.trim().toLowerCase())).forEach((e) => shown.push(e)); draw(); });
    draw();
    const left = h('section', { class: 'card' }, h('div', { class: 'card-head' }, h('div', { class: 'grow' }, h('h2', {}, 'Exercises'), h('p', { class: 'sub' }, `${list.length} in this period. Click one to see it.`))), h('div', { class: 'field', style: 'margin-bottom:8px' }, search), listBody);

    const sel = list.find((e) => e.exercise_id === state.exercise.exercise_id && e.variant_id === state.exercise.variant_id) || list[0];
    const detail = await api('/api/dash/exercise', { ...scope(), exercise: sel.exercise_id, variant: state.variantMode === 'combined' ? 'all' : 'all' });
    const right = h('div', { style: 'display:grid;gap:14px;min-width:0' });
    right.appendChild(exerciseChart(sel, detail));
    if (detail.records.length) right.appendChild(GL.chartCard({
      title: 'Heaviest for each rep count', sub: 'Your best set at 1 to 12 reps (all variants)',
      build: (body) => body.appendChild(GL.table([{ label: 'Reps', right: true }, { label: 'Weight', right: true }, { label: 'When' }], detail.records.map((r) => [r.reps, fmt.weight(r.weight_lb, 1), fmt.date(r.date)]))),
    }));
    right.appendChild(GL.chartCard({
      title: 'Latest sets', sub: 'The most recent 24 sets',
      build: (body) => body.appendChild(GL.table([{ label: 'Date' }, { label: 'Variant' }, { label: 'Set', right: true }, { label: 'Weight', right: true }, { label: 'Reps', right: true }],
        detail.recent_sets.map((r) => [fmt.date(r.date), r.variant || 'Standard', r.set, (r.weight < 0 ? 'assist ' : '') + fmt.one(Math.abs(r.weight)) + ' ' + r.unit.toLowerCase(), r.reps + (r.partials ? ' + ' + r.partials + ' partial' : '')]))),
    }));
    return h('div', { class: 'split', style: 'margin-top:14px' }, left, right);
  }
  function exerciseChart(sel, d) {
    const metric = METRICS[state.metric];
    const conv = (v) => (v === null || v === undefined ? null : metric.weight ? GL.w(v) : v);
    const metricSel = h('select', { 'aria-label': 'What to chart', onchange: (e) => { state.metric = e.target.value; render(); } }, Object.entries(METRICS).map(([k, m]) => h('option', { value: k, selected: k === state.metric }, m.label)));
    const modeSel = h('select', { 'aria-label': 'Variants', onchange: (e) => { state.variantMode = e.target.value; render(); } },
      h('option', { value: 'combined', selected: state.variantMode === 'combined' }, 'All variants combined'), h('option', { value: 'variants', selected: state.variantMode === 'variants' }, 'One line per variant'));
    const ySuffix = metric.weight ? ' ' + GL.unit : '';
    const lines = state.variantMode === 'combined' || d.variants.length < 2
      ? [{ name: sel.name, color: 'var(--s1)', rows: d.combined }]
      : d.variants.slice(0, 6).map((v, i) => ({ name: v.name, color: SLOTS[i], rows: v.sessions }));
    const extra = state.variantMode === 'variants' && d.variants.length > 6 ? ` (${d.variants.length - 6} lesser-used variants not drawn)` : '';
    return GL.chartCard({
      title: sel.name + (sel.variant && sel.variant !== 'Standard' && state.variantMode === 'combined' && d.variants.length === 1 ? ' (' + sel.variant + ')' : ''),
      sub: `${metric.label}${extra}. ${d.muscle ? 'Works: ' + d.muscle + '.' : ''}`,
      tools: h('span', { class: 'field' }, metricSel, modeSel),
      build: (body) => GL.lineChart(body, {
        series: lines.map((l) => ({ name: l.name, color: l.color, points: l.rows.filter((r) => r[metric.key] !== null && r[metric.key] !== undefined).map((r) => [dayOf(r.date), conv(r[metric.key])]) })),
        yFormat: (v) => fmt.compact(v), valueFormat: (v) => fmt.one(v) + ySuffix, zero: state.metric === 'volume' || state.metric === 'reps' || state.metric === 'sets', area: lines.length === 1, height: 300,
        empty: 'No ' + metric.label.toLowerCase() + ' for this exercise (assisted sets have no estimated max).',
      }),
      table: () => ({ columns: [{ label: 'Date' }, { label: 'Variant' }, { label: 'Sets', right: true }, { label: 'Reps', right: true }, { label: 'Top set', right: true }, { label: 'Est. max', right: true }, { label: 'Avg weight', right: true }, { label: 'Volume', right: true }],
        rows: (state.variantMode === 'variants' ? d.variants.flatMap((v) => v.sessions.map((r) => [v.name, r])) : d.combined.map((r) => ['All', r])).map(([v, r]) => [fmt.date(r.date), v, r.sets, r.reps, r.top_weight_lb === null ? '' : fmt.weight(r.top_weight_lb, 1), r.best_e1rm_lb === null ? '' : fmt.weight(r.best_e1rm_lb, 1), r.avg_weight_lb === null ? '' : fmt.weight(r.avg_weight_lb, 1), fmt.int(GL.w(r.volume_lb))]) }),
    });
  }

  // ------------------------------------------------------------------ Muscles
  async function muscles() {
    const [m, wk] = await Promise.all([api('/api/dash/muscles', scope()), api('/api/dash/weekly', { ...scope(), by: 'muscle' })]);
    const order = REGION_ORDER.filter((r) => wk.series.some((s) => s.name === r));
    const byName = Object.fromEntries(wk.series.map((s) => [s.name, s]));
    const regions = GL.chartCard({
      title: 'Sets a week by body part', sub: 'Hard sets per week; helper muscles count half a set', cls: 'col-4',
      build: (body) => GL.ranked(body, m.by_region.map((r) => ({ name: r.name, value: r.sets_per_week, label: fmt.one(r.sets_per_week), color: REGION_COLOR[r.name] }))),
      table: () => ({ columns: [{ label: 'Body part' }, { label: 'Sets', right: true }, { label: 'Per week', right: true }, { label: 'Share', right: true }], rows: m.by_region.map((r) => [r.name, fmt.one(r.sets), fmt.one(r.sets_per_week), fmt.pct(r.share_pct)]) }),
    });
    const stacked = GL.chartCard({
      title: 'Weekly volume by body part', sub: `Volume per week (${GL.unit}); each part keeps its colour`, cls: 'col-8',
      build: (body) => GL.columnChart(body, {
        labels: wk.weeks.map(fmt.short), tooltipHead: wk.weeks.map((d) => 'Week of ' + fmt.short(d)), stacked: true,
        series: order.map((n) => ({ name: n, color: REGION_COLOR[n], values: byName[n].volume_lb.map(GL.w) })), yFormat: fmt.compact, valueFormat: (v) => fmt.int(v) + ' ' + GL.unit, height: 260,
      }),
      table: () => ({ columns: [{ label: 'Week of' }, ...order.map((n) => ({ label: n, right: true }))], rows: wk.weeks.map((d, i) => [fmt.date(d), ...order.map((n) => fmt.int(GL.w(byName[n].volume_lb[i])))]) }),
    });
    const muscleTable = GL.chartCard({
      title: 'Muscles in detail', sub: 'Sets counted per muscle', cls: 'col-6',
      build: (body) => body.appendChild(GL.table([{ label: 'Muscle' }, { label: 'Body part' }, { label: 'Sets', right: true }, { label: 'Per week', right: true }, { label: 'Share', right: true }],
        m.by_muscle.map((r) => [r.name, r.region, fmt.one(r.sets), fmt.one(r.sets_per_week), fmt.pct(r.share_pct)]))),
    });
    const exTable = GL.chartCard({
      title: 'Most-used exercises', sub: 'Top 25 by sets', cls: 'col-6',
      build: (body) => body.appendChild(GL.table([{ label: 'Exercise' }, { label: 'Sets', right: true }, { label: 'Reps', right: true }, { label: 'Volume (' + GL.unit + ')', right: true }],
        m.by_exercise.map((r) => [r.name, r.sets, fmt.int(r.reps), fmt.int(GL.w(r.volume_lb))]))),
    });
    const days = GL.chartCard({
      title: 'By workout day', sub: 'Sets per template day', cls: 'col-6',
      build: (body) => GL.ranked(body, m.by_gym_day.map((r) => ({ name: r.name, value: r.sets, label: fmt.int(r.sets) }))),
    });
    const variants = GL.chartCard({
      title: 'By variant', sub: 'Sets per variant (machine, cable, dumbbell...)', cls: 'col-6',
      build: (body) => GL.ranked(body, m.by_variant.slice(0, 12).map((r) => ({ name: r.name, value: r.sets, label: fmt.int(r.sets) }))),
    });
    const note = m.unmapped.length ? h('section', { class: 'card col-12' }, h('p', { class: 'sub' }, 'Not matched to a body part: ' + m.unmapped.join(', ') + '. Their sets show under "Other".')) : null;
    return grid(regions, stacked, muscleTable, exTable, days, variants, note);
  }

  // ------------------------------------------------------------------ Body
  async function bodyView() {
    const exList = (await api('/api/dash/exercises', scope())).exercises.filter((e) => e.best_e1rm_lb);
    if (!state.lift && exList.length) state.lift = exList[0].exercise_id + ':' + exList[0].variant_id;
    const b = await api('/api/dash/body', { ...scope(), lift: state.lift });
    if (!b.points.length) return grid(h('section', { class: 'card col-12' }, h('div', { class: 'empty' }, 'No weigh-ins in this period.')));
    const delta = b.latest_lb - b.first_lb;
    const tiles = [tile('Latest', fmt.weight(b.latest_lb, 1), fmt.short(b.points[b.points.length - 1].date)),
      tile('Change', (delta >= 0 ? '+' : '') + fmt.one(GL.w(delta)) + ' ' + GL.unit, `since ${fmt.short(b.points[0].date)}`),
      tile('Lowest', fmt.weight(b.min_lb, 1)), tile('Highest', fmt.weight(b.max_lb, 1))];
    const weight = GL.chartCard({
      title: 'Body weight', sub: 'Each weigh-in, and the average of the last 7 days', cls: 'col-12',
      build: (body) => GL.lineChart(body, {
        series: [{ name: 'Weigh-in', color: 'var(--muted)', width: 1.5, muted: true, points: b.points.map((p) => [dayOf(p.date), GL.w(p.lb)]) },
          { name: '7-day average', color: 'var(--s1)', points: b.points.map((p) => [dayOf(p.date), GL.w(p.avg7)]) }],
        yFormat: (v) => fmt.one(v), valueFormat: (v) => fmt.one(v) + ' ' + GL.unit, height: 300, gapDays: 60,
      }),
      table: () => ({ columns: [{ label: 'Date' }, { label: 'Weight (' + GL.unit + ')', right: true }, { label: '7-day avg', right: true }], rows: b.points.map((p) => [fmt.date(p.date), fmt.one(GL.w(p.lb)), fmt.one(GL.w(p.avg7))]) }),
    });
    const liftSel = h('select', { 'aria-label': 'Lift', onchange: (e) => { state.lift = e.target.value; render(); } },
      exList.slice(0, 40).map((e) => h('option', { value: e.exercise_id + ':' + e.variant_id, selected: state.lift === e.exercise_id + ':' + e.variant_id }, e.name + (e.variant && e.variant !== 'Standard' ? ' (' + e.variant + ')' : ''))));
    const rel = b.relative_strength || [];
    const strength = GL.chartCard({
      title: 'Strength compared with body weight', sub: 'Estimated max divided by your body weight that day: is the lift growing faster than you are?', cls: 'col-12', tools: exList.length ? liftSel : null,
      build: (body) => GL.lineChart(body, { series: [{ name: 'Max / body weight', color: 'var(--s2)', points: rel.map((r) => [dayOf(r.date), r.ratio]) }], yFormat: (v) => fmt.one(v) + 'x', valueFormat: (v) => v.toFixed(2) + 'x', area: true, height: 260, empty: 'No weighted sets of this lift in this period.' }),
      table: () => ({ columns: [{ label: 'Date' }, { label: 'Est. max', right: true }, { label: 'Body weight', right: true }, { label: 'Ratio', right: true }], rows: rel.map((r) => [fmt.date(r.date), fmt.weight(r.e1rm_lb, 1), fmt.weight(r.bodyweight_lb, 1), r.ratio.toFixed(2) + 'x']) }),
    });
    return grid(...tiles, weight, strength);
  }

  // ------------------------------------------------------------------ Cardio
  async function cardioView() {
    const c = await api('/api/dash/cardio', scope());
    if (!c.sessions) return grid(h('section', { class: 'card col-12' }, h('div', { class: 'empty' }, 'No cardio sessions in this period.')));
    const tiles = [tile('Sessions', fmt.int(c.sessions)), tile('Total time', fmt.one(c.total_minutes / 60) + ' hours', fmt.int(c.total_minutes) + ' minutes')];
    const weekly = GL.chartCard({
      title: 'Cardio minutes a week', sub: 'Monday to Sunday', cls: 'col-12',
      build: (body) => GL.columnChart(body, { labels: c.weeks.map(fmt.short), tooltipHead: c.weeks.map((d, i) => `Week of ${fmt.short(d)} (${c.sessions_per_week[i]} sessions)`), series: [{ name: 'Minutes', color: 'var(--s3)', values: c.minutes }], yFormat: fmt.int, valueFormat: (v) => fmt.int(v) + ' min', height: 240 }),
      table: () => ({ columns: [{ label: 'Week of' }, { label: 'Sessions', right: true }, { label: 'Minutes', right: true }], rows: c.weeks.map((d, i) => [fmt.date(d), c.sessions_per_week[i], c.minutes[i]]) }),
    });
    const act = GL.chartCard({
      title: 'Time by activity', sub: 'Minutes in the selected period', cls: 'col-6',
      build: (body) => GL.ranked(body, c.by_activity.map((a) => ({ name: a.name, value: a.minutes, label: fmt.int(a.minutes) + ' min' })), 'var(--s3)'),
      table: () => ({ columns: [{ label: 'Activity' }, { label: 'Sessions', right: true }, { label: 'Minutes', right: true }, { label: 'Miles', right: true }], rows: c.by_activity.map((a) => [a.name, a.sessions, a.minutes, a.distance_m ? fmt.one(a.distance_m / 1609.344) : '']) }),
    });
    const recent = GL.chartCard({
      title: 'Latest sessions', cls: 'col-6',
      build: (body) => body.appendChild(GL.table([{ label: 'Date' }, { label: 'Activity' }, { label: 'Minutes', right: true }, { label: 'Miles', right: true }, { label: 'Note' }],
        c.recent.map((r) => [fmt.date(r.date), r.activity, fmt.one(r.minutes), r.distance_m ? fmt.one(r.distance_m / 1609.344) : '', r.note || '']))),
    });
    return grid(...tiles, h('div', { class: 'col-6' }), weekly, act, recent);
  }

  // ------------------------------------------------------------------ Backups
  async function backupsView() {
    const [b, chk] = await Promise.all([api('/api/dash/backups'), state.backup === 'latest' || true ? api('/api/dash/checks', { backup: state.backup }).catch(() => ({ checks: [] })) : { checks: [] }]);
    if (!b.count) return grid(h('section', { class: 'card col-12' }, h('div', { class: 'empty' }, 'No backups on the server yet. In the GymLog app: Settings, Server backup, Back up now.')));
    const ageDays = (Date.now() - b.newest) / 864e5;
    const status = ageDays <= 3 ? ['good', 'Up to date'] : ageDays <= 14 ? ['note', 'Getting old'] : ['warn', 'Overdue'];
    const age = ageDays < 1 ? 'today' : Math.floor(ageDays) + (Math.floor(ageDays) === 1 ? ' day ago' : ' days ago');
    const used = b.used_bytes / b.quota_bytes;
    const newest = b.backups[0];
    const tiles = [
      h('section', { class: 'card tile col-3' }, h('div', { class: 'label' }, 'Newest backup'), h('div', { class: 'value' }, age), h('div', { class: 'delta', style: 'display:flex;gap:6px;align-items:center' }, GL.icon(status[0]), status[1] + ' · ' + fmt.when(b.newest))),
      tile('Backups kept', b.count + ' of ' + b.keep, 'the oldest is removed when a new one arrives'),
      h('section', { class: 'card tile col-3' }, h('div', { class: 'label' }, 'Storage used'), h('div', { class: 'value' }, fmt.mb(b.used_bytes)),
        h('div', { class: 'meter ' + (used > 0.9 ? 'crit' : used > 0.7 ? 'warn' : ''), style: 'margin-top:8px', role: 'meter', 'aria-valuenow': Math.round(used * 100), 'aria-valuemin': 0, 'aria-valuemax': 100 }, h('div', { style: `width:${Math.max(1, used * 100)}%` })),
        h('div', { class: 'delta' }, fmt.pct(used * 100) + ' of ' + fmt.mb(b.quota_bytes))),
      tile('In the newest backup', fmt.int(newest.summary.workouts || 0) + ' workouts', `${fmt.int(newest.summary.sets || 0)} sets · ${fmt.int(newest.summary.weighIns || 0)} weigh-ins · ${fmt.int(newest.summary.photos || 0)} photos`),
    ];
    const growthMetric = { key: 'workouts', label: 'Workouts' };
    const growth = GL.chartCard({
      title: 'Growth of your data', sub: 'Workouts stored in each backup', cls: 'col-6',
      build: (body) => GL.lineChart(body, { series: [{ name: 'Workouts', color: 'var(--s1)', points: [...b.backups].reverse().map((x) => [x.createdAt, x.summary.workouts || 0]) }], xFormat: (t) => fmt.when(t), yFormat: fmt.int, area: true, height: 200, endLabels: true }),
    });
    const sizeChart = GL.chartCard({
      title: 'Backup size', sub: 'Megabytes per backup (photos make most of it)', cls: 'col-6',
      build: (body) => GL.lineChart(body, { series: [{ name: 'Size', color: 'var(--s3)', points: [...b.backups].reverse().map((x) => [x.createdAt, x.size / 1e6]) }], xFormat: (t) => fmt.when(t), yFormat: (v) => fmt.one(v) + ' MB', valueFormat: (v) => fmt.one(v) + ' MB', area: true, height: 200 }),
    });
    const sign = (n) => (n === undefined || n === null ? '' : n === 0 ? '·' : (n > 0 ? '+' : '') + fmt.int(n));
    const table = h('section', { class: 'card col-12' }, h('div', { class: 'card-head' }, h('div', { class: 'grow' }, h('h2', {}, 'Every backup on the server'), h('p', { class: 'sub' }, 'Newest first. The change columns compare with the backup before it.'))),
      GL.table([{ label: 'When' }, { label: 'Device' }, { label: 'Size', right: true }, { label: 'Workouts', right: true }, { label: 'Sets', right: true }, { label: 'Weigh-ins', right: true }, { label: 'Photos', right: true }, { label: '' }],
        b.backups.map((x) => [fmt.when(x.createdAt), x.device || '-', fmt.mb(x.size),
          fmt.int(x.summary.workouts || 0) + (x.delta ? '  ' + sign(x.delta.workouts) : ''), fmt.int(x.summary.sets || 0) + (x.delta ? '  ' + sign(x.delta.sets) : ''),
          fmt.int(x.summary.weighIns || 0) + (x.delta ? '  ' + sign(x.delta.weighIns) : ''), fmt.int(x.summary.photos || 0) + (x.delta ? '  ' + sign(x.delta.photos) : ''),
          h('span', { class: 'tools' },
            h('button', { class: 'btn small', title: 'Show the dashboard as it was in this backup', onclick: () => { state.backup = x.id; location.hash = 'overview'; boot(); } }, 'View'),
            h('button', { class: 'btn small', title: 'Download this backup file', onclick: async (ev) => { const btn = ev.target; btn.disabled = true; btn.textContent = 'Downloading...'; try { await download('/api/backups/' + x.id, {}, 'gymlog-backup.zip'); } finally { btn.disabled = false; btn.textContent = 'Download'; } } }, 'Download'))])));
    const checks = h('section', { class: 'card col-12' }, h('div', { class: 'card-head' }, h('div', { class: 'grow' }, h('h2', {}, 'Possible typing mistakes'), h('p', { class: 'sub' }, 'Sets far from what that exercise usually uses, or one exercise logged in two units. Fix these in the app.'))),
      chk.checks.length ? GL.table([{ label: 'Date' }, { label: 'Exercise' }, { label: 'Set' }, { label: 'Why' }],
        chk.checks.map((c) => [fmt.date(c.date), c.exercise + (c.variant && c.variant !== 'Standard' ? ' (' + c.variant + ')' : ''), c.weight === null ? '' : `${fmt.one(c.weight)} ${c.unit.toLowerCase()} x ${c.reps}`, c.why])) : h('div', { class: 'sec' }, 'Nothing suspicious found.'));
    const few = h('section', { class: 'card col-12' }, h('p', { class: 'sub' }, 'Charts of how your data and backup size grow appear here once the server holds three or more backups.'));
    return grid(...tiles, b.backups.length >= 3 ? growth : few, b.backups.length >= 3 ? sizeChart : null, table, checks);
  }

  // ------------------------------------------------------------------ Export
  async function exportView() {
    const tables = [['sets', 'Every set', 'one row per set, with weight, reps, volume and estimated max'], ['workouts', 'Workouts', 'one row per workout'], ['exercises', 'Exercises', 'sessions, best estimated max and totals per exercise'],
      ['weekly', 'Weekly volume', 'volume per week, split by body part'], ['bodyweight', 'Body weight', 'each weigh-in and the 7-day average'], ['cardio', 'Cardio', 'one row per session'], ['backups', 'Backup history', 'what is stored on the server']];
    const range = state.preset === 'all' ? 'all of your data' : state.from ? `${fmt.date(state.from)} to ${state.to ? fmt.date(state.to) : fmt.date(state.span.last)}` : 'the selected period';
    const busy = (btn, label, fn) => async () => { btn.disabled = true; const old = btn.textContent; btn.textContent = label; try { await fn(); } catch (e) { if (e.status !== 401) alert(e.message); } finally { btn.disabled = false; btn.textContent = old; } };
    const all = (format, text, primary) => { const b = h('button', { class: 'btn' + (primary ? ' primary' : '') }, text); b.addEventListener('click', busy(b, 'Preparing...', () => download('/api/dash/export', { ...scope(), table: 'all', format }, 'gymlog-export'))); return b; };
    const rows = tables.map(([key, name, about]) => {
      const x = h('button', { class: 'btn small' }, 'Excel'), c = h('button', { class: 'btn small' }, 'CSV');
      x.addEventListener('click', busy(x, '...', () => download('/api/dash/export', { ...scope(), table: key, format: 'xlsx' }, 'export.xlsx')));
      c.addEventListener('click', busy(c, '...', () => download('/api/dash/export', { ...scope(), table: key, format: 'csv' }, 'export.csv')));
      return [h('span', {}, h('strong', {}, name), h('span', { class: 'muted' }, ' · ' + about)), h('span', { class: 'tools' }, x, c)];
    });
    return grid(
      h('section', { class: 'card col-12' }, h('div', { class: 'card-head' }, h('div', { class: 'grow' }, h('h2', {}, 'Download for your own analysis'), h('p', { class: 'sub' }, `Covers ${range}. Weights are in pounds. Dates are written 2026-03-31 so spreadsheets sort them properly.`))),
        h('div', { class: 'tools', style: 'flex-wrap:wrap;gap:10px' }, all('xlsx', 'Everything as one Excel workbook', true), all('csv', 'Everything as CSV files (zip)')),
        h('p', { class: 'sub', style: 'margin-top:10px' }, 'The Excel workbook has one sheet per table below, with frozen headings and filters ready to use. Change the time range at the top first if you only want part of it.')),
      h('section', { class: 'card col-12' }, h('div', { class: 'card-head' }, h('div', { class: 'grow' }, h('h2', {}, 'One table at a time'))), GL.table([{ label: 'Table' }, { label: 'Download' }], rows)));
  }

  const VIEW_FN = { overview, exercises, muscles, body: bodyView, cardio: cardioView, backups: backupsView, export: exportView };

  // ------------------------------------------------------------------ start
  $('signin-form').addEventListener('submit', (e) => { e.preventDefault(); signIn($('token').value, $('remember').checked); $('token').value = ''; });
  $('signout').addEventListener('click', () => signOut(''));
  $('u-lb').addEventListener('click', () => setUnit('lb')); $('u-kg').addEventListener('click', () => setUnit('kg'));
  for (const k of ['auto', 'light', 'dark']) $('t-' + k).addEventListener('click', () => setTheme(k));
  window.addEventListener('hashchange', () => { const v = location.hash.slice(1); if (state.token && VIEWS.some(([k]) => k === v) && v !== state.view) { state.view = v; buildTabs(); buildFilters(); render(); } });
  setTheme(localStorage.getItem('gl_theme') || 'auto');
  setUnit(localStorage.getItem('gl_unit') || 'lb');
  const saved = stored();
  if (saved) signIn(saved, !!localStorage.getItem('gl_token')); else signOut('');
})();
