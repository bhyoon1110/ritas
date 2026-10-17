// Pure display calculations. Never modifies source measurements or reports.
import { escapeHTML } from '../shared/dom.mjs';
export const sampleColors = ['#ff3535', '#3535ff', '#15966b', '#9d51c9', '#d98515', '#00a1ad'];
// Phase markers are filled; each phase gets its own colour and a shape in the order square,
// triangle, star, diamond, circle, … (the author may change colour and shape per phase).
export const phaseColors = ['#8261c9', '#df9b29', '#199c78', '#c44d76', '#4178bf', '#777777'];
export const phaseSymbols = [
  'square',
  'triangle-up',
  'star',
  'diamond',
  'circle',
  'cross',
  'triangle-down',
  'hexagon',
];
// Symbols the author can pick per phase (all filled).
export const SYMBOL_CHOICES = [
  'triangle-up',
  'triangle-down',
  'square',
  'circle',
  'diamond',
  'cross',
  'star',
  'hexagon',
];
// The unknown-peak marker may also be a thin cross; every other symbol is one of the above.
export const UNKNOWN_SYMBOLS = ['x', ...SYMBOL_CHOICES];
export function maxValue(values) {
  return values.reduce((m, v) => Math.max(m, v), -Infinity);
}
export function minValue(values) {
  return values.reduce((m, v) => Math.min(m, v), Infinity);
}
export function viewerStorageKey(project, snapshotId = null) {
  return `xrd-viewer-v1-${project.id}-analysis-${project.analysis_revision || 0}${snapshotId ? '-snapshot-' + snapshotId : ''}`;
}
const n = (v) => (Number.isFinite(Number(v)) ? Number(v) : 0);

export function createState(project) {
  const ids = project.samples.map((s) => s.id);
  const span = Math.max(
    1,
    ...project.samples.map((s) => maxValue(s.measurement.y) - minValue(s.measurement.y)),
  );
  return {
    version: 1,
    // 2: positive spacing puts the first sample on top. States without it used the opposite sign.
    offsetRule: 2,
    projectId: project.id,
    visible: [...ids],
    order: [...ids],
    active: ids[0],
    colors: Object.fromEntries(ids.map((id, i) => [id, sampleColors[i % sampleColors.length]])),
    intensity: 'raw',
    arrangement: 'stacked',
    offsets: {
      raw: { spacing: Math.round(span * 0.15), individual: {} },
      normalized: { spacing: 15, individual: {} },
    },
    markers: false,
    peakbar: true,
    phaseTarget: 'active',
    viewMode: 'multi',
    markerSize: 8,
    originStyle: false,
    unknownMarkers: false,
    unknownSymbol: 'x',
    unknownColor: '#000000',
    phaseLabels: true,
    phaseSymbols: {},
    hiddenPhases: [],
    highlight: null,
    phaseColors: {},
    xRange: null,
    yRange: null,
    dragmode: 'zoom',
  };
}

export function restoreState(project, saved) {
  const state = createState(project);
  if (!saved || saved.projectId !== project.id) return state;
  const ids = new Set(state.order),
    phaseIds = new Set(project.samples.flatMap((s) => s.phases.map((p) => p.id)));
  // Normalization and the stacked/overlay switch were removed: an old saved view opens as raw
  // intensity, and an old overlay view as zero spacing (the centre of the offset slider).
  for (const key of ['phaseTarget', 'dragmode', 'viewMode']) {
    const allowed = {
      phaseTarget: ['active', 'all'],
      dragmode: ['zoom', 'pan'],
      viewMode: ['multi', 'single'],
    }[key];
    if (allowed.includes(saved[key])) state[key] = saved[key];
  }
  for (const key of ['unknownMarkers', 'phaseLabels', 'originStyle'])
    if (typeof saved[key] === 'boolean') state[key] = saved[key];
  if (UNKNOWN_SYMBOLS.includes(saved.unknownSymbol)) state.unknownSymbol = saved.unknownSymbol;
  if (/^#[\da-f]{6}$/i.test(saved.unknownColor || '')) state.unknownColor = saved.unknownColor;
  if (Number.isFinite(saved.markerSize))
    state.markerSize = Math.min(20, Math.max(3, saved.markerSize));
  if (Array.isArray(saved.visible)) state.visible = saved.visible.filter((id) => ids.has(id));
  if (Array.isArray(saved.order))
    state.order = [...new Set([...saved.order.filter((id) => ids.has(id)), ...state.order])];
  if (ids.has(saved.active)) state.active = saved.active;
  for (const key of ['markers', 'peakbar'])
    if (typeof saved[key] === 'boolean') state[key] = saved[key];
  state.hiddenPhases = (saved.hiddenPhases || []).filter((id) => phaseIds.has(id));
  // Phases are hidden as groups now: a state that hid one member of a shared group hides it all.
  for (const g of groupsOf(project.samples))
    if (g.ids.some((id) => state.hiddenPhases.includes(id)))
      state.hiddenPhases = [...new Set([...state.hiddenPhases, ...g.ids])];
  const wanted = saved.highlight;
  if (
    wanted &&
    typeof wanted === 'object' &&
    typeof wanted.name === 'string' &&
    [wanted.card ?? null, wanted.db ?? null].every((v) => v === null || typeof v === 'string')
  ) {
    const found = {
      name: wanted.name,
      card: wanted.card ?? null,
      db: wanted.db ?? null,
    };
    if (project.samples.some((sample) => sample.phases.some((p) => phaseMatches(found, p))))
      state.highlight = found;
  }
  for (const key of ['colors', 'phaseColors'])
    for (const [id, color] of Object.entries(saved[key] || {})) {
      if ((ids.has(id) || phaseIds.has(id)) && /^#[\da-f]{6}$/i.test(color)) state[key][id] = color;
    }
  for (const [id, symbol] of Object.entries(saved.phaseSymbols || {}))
    if (phaseIds.has(id) && SYMBOL_CHOICES.includes(symbol)) state.phaseSymbols[id] = symbol;
  for (const mode of ['raw', 'normalized']) {
    const offset = saved.offsets?.[mode];
    if (offset && Number.isFinite(offset.spacing)) state.offsets[mode].spacing = offset.spacing;
    for (const [id, value] of Object.entries(offset?.individual || {}))
      if (ids.has(id) && Number.isFinite(value)) state.offsets[mode].individual[id] = value;
  }
  if (saved.arrangement === 'overlay') state.offsets.raw.spacing = 0;
  else if (saved.offsetRule !== 2 && saved.offsets?.raw?.spacing > 0)
    state.offsets.raw.spacing = -state.offsets.raw.spacing;
  for (const key of ['xRange', 'yRange'])
    if (
      // A y range on the old normalized scale means nothing on raw counts.
      !(key === 'yRange' && saved.intensity === 'normalized') &&
      Array.isArray(saved[key]) &&
      saved[key].length === 2 &&
      saved[key].every(Number.isFinite) &&
      saved[key][0] < saved[key][1]
    )
      state[key] = saved[key];
  return state;
}

// A highlight picks a phase by name and, when given, card and database. A highlight made from a
// link (card and db null) therefore marks every phase with that name.
export function phaseMatches(highlight, phase) {
  return (
    !!highlight &&
    phase.name === highlight.name &&
    (highlight.card == null || (phase.card ?? '') === highlight.card) &&
    (highlight.db == null || (phase.db ?? '') === highlight.db)
  );
}
export function phaseRef(phase) {
  return { name: phase.name, card: phase.card ?? '', db: phase.db ?? '' };
}
function fade(color, alpha) {
  const m = /^#([\da-f]{2})([\da-f]{2})([\da-f]{2})$/i.exec(color);
  return m
    ? `rgba(${parseInt(m[1], 16)},${parseInt(m[2], 16)},${parseInt(m[3], 16)},${alpha})`
    : color;
}
const DIM = 0.25;

// Link to the page of a registered PDF that justifies a marker or a reference peak. The offline
// viewer sits at the root of the delivery package, where originals are in Report/.
export function sourceHref(project, sample, role, page, offline, base = '') {
  if (!Number.isInteger(page) || page < 1) return null;
  const record = (sample.sources || []).find((r) => r.role === role);
  if (!record) return null;
  const path = offline
    ? `Report/${record.id.slice(0, 8)}-${encodeURIComponent(record.filename)}`
    : `${base}/api/files/${encodeURIComponent(project.storage_id)}/${encodeURIComponent(record.id)}`;
  return `${path}#page=${page}`;
}

// Vertical offset of the index-th of `count` curves. The slider's centre (0) overlays every
// curve; to the right the first sample goes on top, to the left the order is reversed.
export function offsetFor(index, count, spacing) {
  return spacing >= 0 ? (count - 1 - index) * spacing : index * -spacing;
}
// Half-range of the offset slider: wide enough for the data and for the current value.
export function spacingLimit(project, state) {
  const span = Math.max(
    1,
    ...project.samples.map((s) => maxValue(s.measurement.y) - minValue(s.measurement.y)),
  );
  return Math.ceil(Math.max(span, Math.abs(n(state.offsets.raw.spacing))));
}
// Observed peaks that the report assigns to no phase (same number and position).
export function unknownPeaks(sample) {
  return (sample.observed_peaks || []).filter((p) => {
    const row = (sample.assignments || []).find((a) => a.no === p.no && a.x === p.x);
    return !row || !row.matches.length;
  });
}
// HTML legend shown over the plot: a checkbox per sample, then the display toggles and the
// crystal phases (grouped across samples) that can be ticked on and off.
export function legendHTML(project, state) {
  // One sample in Single Profile, so that is the only row.
  const listed = state.viewMode === 'single' ? [state.active || state.order[0]] : state.order;
  const samples = listed
    .map((id) => project.samples.find((s) => s.id === id))
    .filter(Boolean)
    .map(
      (s, i) =>
        `<label class="legendRow"><input type="checkbox" data-legend-sample="${escapeHTML(s.id)}" ${state.visible.includes(s.id) ? 'checked' : ''}><span class="legendSwatch" style="background:${escapeHTML(state.colors[s.id] || sampleColors[i % sampleColors.length])}"></span>${escapeHTML(s.name)}</label>`,
    )
    .join('');
  const styles = phaseStyles(project, state);
  const rows = phaseGroups(project, state)
    .map((g, i) => {
      const style = styles.get(g.ids[0]);
      return `<label class="legendRow phaseRowLegend"><input type="checkbox" data-legend-group="${i}" ${groupHidden(state, g) ? '' : 'checked'}><span class="legendSymbol" style="color:${escapeHTML(style.color)}">${SYMBOL_GLYPHS[style.symbol] || '●'}</span>${escapeHTML(groupCaption(g))}</label>`;
    })
    .join('');
  // Unknown Peak is ticked like a phase: its markers sit on the observed peaks no phase explains.
  const hasUnknown = (
    state.viewMode === 'single'
      ? [project.samples.find((s) => s.id === (state.active || state.order[0]))]
      : state.order.map((id) => project.samples.find((s) => s.id === id))
  ).some((s) => s && unknownPeaks(s).length);
  const unknown = !hasUnknown
    ? ''
    : `<label class="legendRow phaseRowLegend"><input type="checkbox" data-legend-toggle="unknownMarkers" ${state.unknownMarkers ? 'checked' : ''}><span class="legendSymbol" style="color:${escapeHTML(state.unknownColor)}">?</span>Unknown Peak</label>`;
  return samples + '<div class="legendPhases">' + rows + unknown + '</div>';
}
// Text glyphs for the symbols (legend rows and phase settings).
export const SYMBOL_GLYPHS = {
  'triangle-up': '▲',
  'triangle-down': '▼',
  square: '■',
  circle: '●',
  diamond: '◆',
  cross: '✚',
  star: '★',
  hexagon: '⬢',
  x: '✕',
};

export function selectedSamples(project, state) {
  const chosen =
    state.viewMode === 'single'
      ? [
          project.samples.find((s) => s.id === state.active) ||
            project.samples.find((s) => state.visible.includes(s.id)),
        ]
      : state.order.map((id) => project.samples.find((s) => s.id === id));
  const selected = chosen.filter(
    (s) => s && (state.viewMode === 'single' || state.visible.includes(s.id)),
  );
  const warnings = [],
    ready = [];
  let axis = null,
    unit = null;
  for (const s of selected) {
    const m = s.measurement;
    if (!m.axis_kind || !m.axis_unit || !m.intensity_unit) {
      warnings.push(`${s.name}: 축 또는 강도 단위 미확인, 표시 제외`);
      continue;
    }
    const coordinate = m.axis_kind + '/' + m.axis_unit;
    if (axis && coordinate !== axis) {
      warnings.push(`${s.name}: 가로축 종류·단위가 달라 비교에서 제외`);
      continue;
    }
    if (unit && unit !== m.intensity_unit) {
      warnings.push(`${s.name}: 강도 단위가 달라 원강도 비교에서 제외`);
      continue;
    }
    axis = coordinate;
    unit = m.intensity_unit;
    ready.push(s);
  }
  return { samples: ready, warnings };
}

// Phases of different samples that are the same or nearly the same compound share one entry:
// the key is the set of elements in the chemical formula (so Ti0.91 and Ti0.9 match), or the
// phase name when the report gives no formula.
export function phaseGroupKey(phase) {
  return JSON.stringify([phase.name, phase.card || '', phase.db || '']);
}
function groupsOf(samples, project = null) {
  const map = new Map();
  for (const sample of samples) {
    // Two phases of one sample are always two groups: the second one with the same key gets
    // its own group (key#1) that pairs with the second one of the other samples.
    const seen = new Map();
    for (const phase of sample.phases) {
      const base = phaseGroupKey(phase),
        n = seen.get(base) || 0;
      seen.set(base, n + 1);
      const key = n ? `${base}#${sample.id}:${n}` : base;
      if (!map.has(key)) map.set(key, { key, members: [] });
      map.get(key).members.push({ sample, phase });
    }
  }
  // allIds: the group's phases in every sample of the project (hiding and colouring reach the
  // samples that are hidden at the moment too).
  const everyone = project ? groupsOf(project.samples) : null;
  return [...map.values()].map((g) => {
    const first = g.members[0].phase;
    return {
      ...g,
      ids: g.members.map((m) => m.phase.id),
      allIds: everyone
        ? everyone.find((x) => x.key === g.key).ids
        : g.members.map((m) => m.phase.id),
      name: first.name,
      formula: String(first.formula || '').trim(),
      card: String(first.card || '').trim(),
    };
  });
}
// The groups shown now: the visible samples in Multi Profile, the chosen sample in Single Profile.
export function phaseGroups(project, state) {
  const samples =
    state.viewMode === 'single'
      ? [
          project.samples.find((s) => s.id === state.active) ||
            project.samples.find((s) => state.visible.includes(s.id)),
        ]
      : state.order
          .map((id) => project.samples.find((s) => s.id === id))
          .filter((s) => s && state.visible.includes(s.id));
  return groupsOf(samples.filter(Boolean), project);
}
// One colour and symbol per group, the same in every sample; the author's choice for any member
// of the group wins. Groups are numbered over the whole project so they keep their look when
// samples are hidden.
export function phaseStyles(project, state) {
  const styles = new Map();
  groupsOf(project.samples).forEach((g, i) => {
    const colorId = g.ids.find((id) => state.phaseColors[id]),
      symbolId = g.ids.find((id) => state.phaseSymbols?.[id]);
    const style = {
      color: colorId ? state.phaseColors[colorId] : phaseColors[i % phaseColors.length],
      // The colour cycles every 6 phases and the shape shifts once per cycle, so a colour/shape
      // pair stays unique for the first 36 phases.
      symbol: symbolId
        ? state.phaseSymbols[symbolId]
        : phaseSymbols[((i % 6) + Math.floor(i / 6)) % phaseSymbols.length],
    };
    for (const id of g.ids) styles.set(id, style);
  });
  return styles;
}
export const groupHidden = (state, group) =>
  group.allIds.every((id) => state.hiddenPhases.includes(id));
export function setGroupHidden(state, group, hidden) {
  const rest = state.hiddenPhases.filter((id) => !group.allIds.includes(id));
  state.hiddenPhases = hidden ? [...rest, ...group.allIds] : rest;
}
// Colour and symbol are one choice for the whole group, in every sample.
export function setGroupColor(state, group, color) {
  for (const id of group.allIds) state.phaseColors[id] = color;
}
export function setGroupSymbol(state, group, symbol) {
  for (const id of group.allIds) state.phaseSymbols[id] = symbol;
}
const joinParts = (parts, glue = ' · ') => parts.filter(Boolean).join(glue);
const cardPart = (g) => (g.card ? '#' + g.card : '');
// Legend rows: phase name, chemical formula, # card number (nothing else).
export const groupCaption = (g) => joinParts([g.name, g.formula, cardPart(g)]);
// Peak Bar rows: chemical formula, phase name, # card number (no sample name).
export const barCaption = (g) => joinParts([g.formula, g.name, cardPart(g)]);
// Exported-image legend: "chemical formula (phase name)".
export const exportCaption = (g) => (g.formula ? `${g.formula} (${g.name})` : g.name);

export function displayUnit(project, state) {
  return selectedSamples(project, state).samples[0]?.measurement.intensity_unit || '미확인';
}

export function viewWarnings(project, extra = []) {
  return [
    ...new Set([
      ...(project.warnings || []),
      ...project.samples.flatMap((s) => s.warnings || []),
      ...extra,
    ]),
  ];
}

export function nearestIndex(xs, x) {
  let lo = 0,
    hi = xs.length - 1;
  while (lo < hi) {
    const mid = Math.floor((lo + hi) / 2);
    if (xs[mid] < x) lo = mid + 1;
    else hi = mid;
  }
  return lo > 0 && Math.abs(xs[lo - 1] - x) < Math.abs(xs[lo] - x) ? lo - 1 : lo;
}

export function phaseMarkerHint(project, state, figure) {
  const description = '측정 그래프의 관측 피크 위에 결정상 기호를 표시합니다. 하단 기준 피크 막대와는 별도입니다.';
  if (!state.markers) return description;
  const { samples } = selectedSamples(project, state);
  if (!samples.length) return '표시할 측정 그래프가 없습니다. 시료 선택과 축·강도 단위를 확인하세요.';
  const traces = figure.data.filter(t => t.meta?.role === 'marker');
  const inRange = (value, range) => !range || (value >= Math.min(...range) && value <= Math.max(...range));
  const total = traces.reduce((sum, t) => sum + t.x.length, 0);
  const visible = traces.reduce((sum, t) => sum + t.x.filter((x, i) =>
    inRange(x, state.xRange) && inRange(t.y[i], state.yRange)).length, 0);
  const reasons = [];
  for (const sample of samples) {
    if (traces.some(t => t.meta.sampleId === sample.id && t.x.length)) continue;
    let reason;
    if (!(sample.phases || []).length) reason = '결정상 목록이 없습니다. 상매칭 Report 연결과 분석 검토를 확인하세요.';
    else if (sample.phases.every(p => state.hiddenPhases.includes(p.id))) reason = '결정상이 모두 숨김 상태입니다. 범례에서 결정상을 체크하세요.';
    else if (!(sample.observed_peaks || []).length) reason = '관측 피크표를 읽지 못했거나 검토에서 제외했습니다. 상매칭 Report 원문과 분석 자료 검토를 확인하세요.';
    else if (!(sample.assignments || []).some(a => a.matches?.length)) reason = '관측 피크의 결정상 배정 정보가 없습니다. 상매칭 Report의 Phase Name 열을 확인하세요.';
    else reason = '상 배정의 이름·피크 번호·위치가 관측 피크 또는 RAW 범위와 연결되지 않습니다. 원문과 분석 검토를 확인하세요.';
    reasons.push(`${sample.name}: ${reason}`);
  }
  const summary = total
    ? (visible ? `현재 범위에 결정상 마커 ${visible}개 표시 (전체 ${total}개).` : `결정상 마커 ${total}개가 현재 확대 범위 밖에 있습니다. 「전체 범위」를 누르세요.`)
    : '표시할 결정상 마커가 없습니다.';
  return [summary, ...reasons.slice(0, 3), ...(reasons.length > 3 ? [`외 ${reasons.length - 3}개 시료도 피크 연결을 확인하세요.`] : [])].join(' ');
}

export function buildFigure(project, state, viewport = {}) {
  const { samples, warnings } = selectedSamples(project, state),
    data = [],
    annotations = [];
  const offsets = state.offsets.raw,
    single = state.viewMode === 'single';
  const sourceRanges = [],
    styles = phaseStyles(project, state);
  samples.forEach((s, index) => {
    const m = s.measurement,
      base = m.y;
    // At the overlay centre (spacing 0) every curve shares one scale: individual offsets are kept
    // in the state but not applied, as the old overlay mode did.
    const offset =
      single || n(offsets.spacing) === 0
        ? 0
        : offsetFor(index, samples.length, n(offsets.spacing)) + n(offsets.individual[s.id]);
    const y = base.map((v) => v + offset);
    const color = state.colors[s.id] || sampleColors[index % sampleColors.length];
    sourceRanges.push([m.x[0], m.x.at(-1)]);
    data.push({
      type: 'scatter',
      mode: 'lines',
      name: escapeHTML(s.name),
      x: m.x,
      y,
      line: { color, width: 0.9, simplify: false },
      uid: s.id,
      meta: { role: 'measurement', sampleId: s.id },
      customdata: m.y.map((raw, i) => [raw, offset, m.line_numbers?.[i] ?? '']),
      hovertemplate: `<b>${escapeHTML(s.name)}</b><br>2θ: %{x:.6f}°<br>강도: %{customdata[0]:.6g} ${escapeHTML(m.intensity_unit)}<extra></extra>`,
    });
    if (state.markers) {
      for (const phase of s.phases) {
        if (state.hiddenPhases.includes(phase.id)) continue;
        const style = styles.get(phase.id),
          dim = state.highlight && !phaseMatches(state.highlight, phase),
          xs = [],
          ys = [],
          texts = [],
          cds = [];
        for (const assignment of s.assignments || []) {
          const matches = assignment.matches.filter((a) => a.phase === phase.name);
          if (!matches.length) continue;
          const observed = (s.observed_peaks || []).find(
            (p) => p.no === assignment.no && p.x === assignment.x,
          );
          if (!observed || assignment.x < m.x[0] || assignment.x > m.x.at(-1)) continue;
          const nearest = nearestIndex(m.x, assignment.x);
          // Vertical location only: anchor to the nearest real TXT point; keep
          // x at the PDF's observed position. This does not create a measurement.
          const gap = Math.max(1, maxValue(base) - minValue(base)) * 0.018;
          const matchIndex = assignment.matches.findIndex((a) => a.phase === phase.name);
          xs.push(assignment.x);
          ys.push(y[nearest] + gap * (matchIndex + 1));
          cds.push([assignment.no, assignment.page, observed.page]);
          texts.push(
            `<b>${escapeHTML(s.name)} / ${escapeHTML(phase.name)}</b><br>관측 피크 #${assignment.no}<br>2θ: ${escapeHTML(assignment.x_raw)}°<br>hkl: ${matches.map((a) => escapeHTML(a.hkl)).join(' / ')}<br>Height: ${escapeHTML(observed.height_raw)}`,
          );
        }
        if (xs.length)
          data.push({
            type: 'scatter',
            mode: 'markers',
            x: xs,
            y: ys,
            text: texts,
            hovertemplate: '%{text}<extra></extra>',
            showlegend: false,
            customdata: cds,
            marker: {
              color: style.color,
              symbol: style.symbol,
              size: n(state.markerSize) || 8,
              line: { width: 0.6, color: '#ffffff' },
              ...(state.highlight ? { opacity: dim ? DIM : 1 } : {}),
            },
            meta: { role: 'marker', phaseId: phase.id, sampleId: s.id },
            uid: phase.id + '-marker',
          });
      }
    }
    if (state.unknownMarkers) {
      const gap = Math.max(1, maxValue(base) - minValue(base)) * 0.018;
      const peaks = unknownPeaks(s).filter((p) => p.x >= m.x[0] && p.x <= m.x.at(-1));
      if (peaks.length)
        data.push({
          type: 'scatter',
          // A "?" glyph marks a peak no phase explains.
          mode: 'text',
          x: peaks.map((p) => p.x),
          y: peaks.map((p) => y[nearestIndex(m.x, p.x)] + gap),
          text: peaks.map(() => '?'),
          textposition: 'middle center',
          textfont: {
            color: state.unknownColor,
            size: (n(state.markerSize) || 8) * 1.8,
            family: 'Arial, sans-serif',
          },
          hovertext: peaks.map(
            (p) =>
              `<b>${escapeHTML(s.name)} / Unknown peak</b><br>관측 피크 #${p.no} (배정된 상 없음)<br>2θ: ${escapeHTML(p.x_raw)}°<br>Height: ${escapeHTML(p.height_raw)}`,
          ),
          hovertemplate: '%{hovertext}<extra></extra>',
          showlegend: false,
          meta: { role: 'unknown', sampleId: s.id },
          uid: s.id + '-unknown',
        });
    }
  });
  const active = samples.find((s) => s.id === state.active) || samples[0];
  // One row per phase group (the same compound in several samples is one row); its reference
  // peaks come from the first sample that lists them.
  const phaseRows = state.peakbar
    ? groupsOf(single ? (active ? [active] : []) : samples, project)
        .filter((g) => !groupHidden(state, g))
        .map((group) => {
          const member =
            group.members.find((m) =>
              (m.sample.reference_peaks || []).some((r) => r.phase === m.phase.name),
            ) || group.members[0];
          return { sample: member.sample, phase: member.phase, group };
        })
    : [];
  const hasBars = phaseRows.length > 0;
  phaseRows.forEach(({ sample: s, phase, group }, index) => {
    const style = styles.get(phase.id),
      // A shared row is lit when the highlighted phase is any member of its group.
      dim = state.highlight && !group.members.some((m) => phaseMatches(state.highlight, m.phase)),
      lit = state.highlight && !dim,
      row = phaseRows.length - index - 1;
    const peaks = (s.reference_peaks || []).filter((p) => p.phase === phase.name);
    const grouped = new Map();
    for (const peak of peaks) {
      if (!grouped.has(peak.x)) grouped.set(peak.x, []);
      grouped.get(peak.x).push(peak);
    }
    const xs = [],
      ys = [],
      texts = [],
      cds = [];
    for (const peak of peaks) {
      if (!Number.isFinite(peak.intensity)) continue;
      const all = grouped.get(peak.x);
      const hover = `<b>${escapeHTML(s.name)} / ${escapeHTML(phase.name)}</b><br>참조 Card ${escapeHTML(phase.card)}<br>2θ: ${peak.x}°<br>${all.map((r) => `d: ${escapeHTML(r.d_raw)} Å · hkl: ${escapeHTML(r.hkl_raw)} · Norm. I.: ${escapeHTML(r.intensity_raw)} · p.${r.page}`).join('<br>')}<br>${escapeHTML(phase.source || '')}`;
      xs.push(peak.x, peak.x, null);
      ys.push(row, row + (0.75 * peak.intensity) / 100, null);
      texts.push(hover, hover, null);
      cds.push([peak.page, peak.hkl_raw], [peak.page, peak.hkl_raw], null);
    }
    data.push({
      type: 'scatter',
      mode: 'lines',
      x: xs,
      y: ys,
      text: texts,
      xaxis: 'x2',
      yaxis: 'y2',
      line: { color: style.color, width: lit ? 2.2 : 1.1 },
      customdata: cds,
      ...(state.highlight ? { opacity: dim ? DIM : 1 } : {}),
      hovertemplate: '%{text}<extra></extra>',
      showlegend: false,
      meta: { role: 'peakbar', phaseId: phase.id, sampleId: s.id },
      uid: phase.id + '-bar',
    });
    // A small marker on top of each reference line, in the phase's own symbol.
    const tips = peaks.filter((p) => Number.isFinite(p.intensity));
    data.push({
      type: 'scatter',
      mode: 'markers',
      x: tips.map((p) => p.x),
      y: tips.map((p) => row + (0.75 * p.intensity) / 100),
      xaxis: 'x2',
      yaxis: 'y2',
      hoverinfo: 'skip',
      showlegend: false,
      marker: {
        color: style.color,
        symbol: style.symbol,
        size: n(state.markerSize) || 8,
        ...(state.highlight ? { opacity: dim ? DIM : 1 } : {}),
      },
      meta: { role: 'peakbar-tip', phaseId: phase.id, sampleId: s.id },
      uid: phase.id + '-tip',
    });
    const name = barCaption(group);
    const short = name.length > 60 ? name.slice(0, 58) + '…' : name;
    annotations.push({
      xref: 'paper',
      yref: 'y2',
      x: 0.995,
      y: row + 0.85,
      xanchor: 'right',
      yanchor: 'top',
      text: escapeHTML(short),
      showarrow: false,
      font: { size: 10, color: dim ? fade(style.color, DIM) : style.color },
      bgcolor: 'rgba(255,255,255,.85)',
    });
  });
  // The default 2θ range: the data range rounded out to whole degrees (e.g. 10–90).
  const fullX = sourceRanges.length
    ? [
        Math.floor(Math.min(...sourceRanges.map((r) => r[0]))),
        Math.ceil(Math.max(...sourceRanges.map((r) => r[1]))),
      ]
    : [0, 1];
  const title = `Intensity, ${samples[0]?.measurement.intensity_unit || '단위 미확인'}`;
  // Overlaid curves share one intensity scale, so the values are shown; once curves are offset
  // the numbers would be misleading and only the axis title stays.
  const overlaid = single || samples.length < 2 || n(offsets.spacing) === 0;
  const separators = [];
  for (let k = 1; k < phaseRows.length; k++)
    separators.push({
      type: 'line',
      xref: 'paper',
      x0: 0,
      x1: 1,
      yref: 'y2',
      y0: k,
      y1: k,
      line: { color: '#000000', width: 1 },
    });
  const axis = {
    showline: true,
    mirror: true,
    linecolor: '#999',
    linewidth: 1,
    ticks: 'outside',
    ticklen: 4,
    tickcolor: '#999',
    zeroline: false,
    showgrid: false,
    automargin: false,
  };
  const layout = {
    paper_bgcolor: '#fff',
    plot_bgcolor: '#fff',
    font: { family: 'Arial, sans-serif', size: 11, color: '#555' },
    margin: { l: 70, r: 18, t: 14, b: 48 },
    hovermode: 'closest',
    dragmode: state.dragmode,
    uirevision: project.id,
    showlegend: false,
    shapes: separators,
    xaxis: {
      ...axis,
      domain: [0, 1],
      anchor: 'y',
      range: state.xRange || fullX,
      showticklabels: !hasBars,
      title: hasBars ? undefined : { text: '2θ, deg.', font: { size: 12 } },
      showspikes: true,
      spikemode: 'across',
      spikedash: 'dot',
      spikecolor: '#bcbcbc',
    },
    yaxis: {
      ...axis,
      domain: hasBars ? [0.23, 1] : [0, 1],
      title: { text: title, font: { size: 12 } },
      showticklabels: overlaid,
      ticks: overlaid ? 'outside' : '',
      showspikes: true,
      spikemode: 'across',
      spikedash: 'dot',
      spikecolor: '#bcbcbc',
      ...(state.yRange ? { range: state.yRange } : { autorange: true }),
    },
    annotations,
  };
  if (hasBars) {
    layout.xaxis2 = {
      ...axis,
      domain: [0, 1],
      anchor: 'y2',
      matches: 'x',
      range: state.xRange || fullX,
      title: { text: '2θ, deg.', font: { size: 12 } },
    };
    layout.yaxis2 = {
      ...axis,
      domain: [0, 0.19],
      // The first (lowest) row starts on the box's bottom line.
      range: [0, phaseRows.length],
      showticklabels: false,
      ticks: '',
      fixedrange: true,
    };
  }
  if (state.originStyle) applyOriginStyle(data, layout);
  if (Number.isFinite(viewport.height) && viewport.height >= 220) {
    layout.height = viewport.height;
    if (hasBars) {
      const available = viewport.height - layout.margin.t - layout.margin.b;
      const barHeight = Math.max(available * 0.19, (phaseRows.length + 0.08) * 18);
      if (barHeight > available * 0.19) {
        const profileHeight = Math.max(220, available * 0.77),
          gap = available * 0.04;
        const total = profileHeight + gap + barHeight;
        layout.height = total + layout.margin.t + layout.margin.b;
        layout.yaxis.domain = [(barHeight + gap) / total, 1];
        layout.yaxis2.domain = [0, barHeight / total];
      }
    }
  }
  if (!samples.length)
    annotations.push({
      xref: 'paper',
      yref: 'paper',
      x: 0.5,
      y: 0.5,
      text: '표시할 시료를 선택하고 축·단위를 확인하세요.',
      showarrow: false,
      font: { size: 15, color: '#888' },
    });
  return { data, layout, warnings, fullX, active: active?.id, barRows: phaseRows.length };
}

// The figure for "현재 이미지 내보내기": the current view with a legend (the samples, then the
// phases drawn as markers while Phase marker is on). All styling comes from the same state as
// the live graph; there is deliberately no independent export-only Origin option.
export function exportFigure(project, state, { height = 900 } = {}) {
  const figure = buildFigure(project, state, { height });
  const { data, layout } = figure;
  const origin = !!state.originStyle;
  const black = '#000000';
  for (const t of data) if (t.meta?.role === 'measurement') t.showlegend = true;
  if (state.markers) {
    const drawn = new Set(data.filter((t) => t.meta?.role === 'marker').map((t) => t.meta.phaseId));
    const styles = phaseStyles(project, state);
    for (const g of phaseGroups(project, state)) {
      if (groupHidden(state, g) || !g.ids.some((id) => drawn.has(id))) continue;
      const style = styles.get(g.ids[0]);
      data.push({
        type: 'scatter',
        mode: 'markers',
        x: [null],
        y: [null],
        name: escapeHTML(exportCaption(g)),
        showlegend: true,
        hoverinfo: 'skip',
        marker: { color: style.color, symbol: style.symbol, size: n(state.markerSize) || 8 },
        meta: { role: 'legend-phase' },
      });
    }
  }
  if (state.unknownMarkers && data.some((t) => t.meta?.role === 'unknown'))
    data.push({
      type: 'scatter',
      mode: 'markers',
      x: [null],
      y: [null],
      // A text trace has no legend symbol: the "?" goes into the name, the marker is invisible.
      name: '? Unknown Peak',
      showlegend: true,
      hoverinfo: 'skip',
      marker: { color: state.unknownColor, size: 9, opacity: 0 },
      meta: { role: 'legend-unknown' },
    });
  layout.showlegend = true;
  layout.legend = {
    x: 0.99,
    y: 0.99,
    xanchor: 'right',
    yanchor: 'top',
    bgcolor: origin ? 'rgba(255,255,255,0)' : 'rgba(255,255,255,.9)',
    bordercolor: origin ? 'rgba(0,0,0,0)' : '#d5d9df',
    borderwidth: origin ? 0 : 1,
    font: { size: origin ? 14 : 11, color: origin ? black : '#444' },
    tracegroupgap: 2,
    itemsizing: 'constant',
  };
  return figure;
}

// Shared by the live viewer and image export. Only presentation changes, never raw points,
// offsets, phase colours/highlight dimming, marker sizes or the current zoom.
// Stacked intensity ticks remain hidden.
function applyOriginStyle(data, layout) {
  const black = '#000000';
  const bold = { size: 18, color: black, family: 'Arial', weight: 'bold' };
  const axis = {
    showline: true,
    mirror: true,
    linecolor: black,
    linewidth: 1.5,
    ticks: 'inside',
    ticklen: 5,
    tickwidth: 1.5,
    tickcolor: black,
    tickfont: { size: 14, color: black, family: 'Arial', weight: 'bold' },
    showspikes: false,
  };
  layout.font = { family: 'Arial, sans-serif', size: 13, color: black };
  layout.margin = { l: 80, r: 30, t: 24, b: 80 };
  const lower = layout.xaxis2 ? 'xaxis2' : 'xaxis';
  Object.assign(layout.xaxis, axis);
  Object.assign(layout.yaxis, axis, {
    title: { ...layout.yaxis.title, font: bold, standoff: 14 },
    ticks: layout.yaxis.showticklabels ? 'inside' : '',
  });
  if (layout.xaxis2) Object.assign(layout.xaxis2, axis);
  if (layout.yaxis2) Object.assign(layout.yaxis2, axis, { ticks: '' });
  layout[lower].title = { text: '2-Theta (degree)', font: bold, standoff: 10 };
  layout[lower].showticklabels = true;
  if (lower === 'xaxis2') layout.xaxis.showticklabels = false;
  for (const t of data) {
    if (t.meta?.role === 'measurement') t.line = { ...t.line, width: 1.5 };
  }
  for (const a of layout.annotations) a.font = { ...a.font, size: 12, family: 'Arial' };
  for (const shape of layout.shapes) shape.line = { color: black, width: 1.2 };
}
