import test from 'node:test';
import assert from 'node:assert/strict';
import {
  buildFigure,
  createState,
  legendHTML,
  offsetFor,
  restoreState,
  spacingLimit,
  unknownPeaks,
} from '../../app/xrd_workbench/web/viewer/model.mjs';

const sample = (id, name, y, phase, extra = {}) => ({
  id,
  name,
  measurement: {
    x: [10, 11, 12, 13],
    y,
    axis_kind: 'two_theta',
    axis_unit: 'deg',
    intensity_unit: 'cps',
  },
  phases: [{ id: id + '-p', source: id + '.pdf', ...phase }],
  reference_peaks: [
    { phase: phase.name, x: 11, hkl_raw: '1 0 0', intensity: 100, intensity_raw: '100', page: 3 },
    { phase: phase.name, x: 12.5, hkl_raw: '1 1 0', intensity: 40, intensity_raw: '40', page: 3 },
  ],
  observed_peaks: [
    { no: 1, x: 11, x_raw: '11', page: 2, height_raw: '5' },
    { no: 2, x: 12, x_raw: '12', page: 2, height_raw: '2' },
  ],
  assignments: [
    { no: 1, x: 11, x_raw: '11', page: 2, matches: [{ phase: phase.name, hkl: '1 0 0' }] },
    { no: 2, x: 12, x_raw: '12', page: 2, matches: [] },
  ],
  sources: [],
  ...extra,
});
const project = {
  id: 'pb',
  samples: [
    sample('a', 'A', [1, 5, 2, 1], { name: 'PhaseA', formula: 'Ti O2', card: '1', db: 'DB' }),
    sample('b', 'B', [1, 3, 2, 1], { name: 'PhaseB', formula: 'Ca C O3', card: '2', db: 'DB' }),
    sample('c', 'C', [2, 4, 3, 2], { name: 'PhaseC', formula: '', card: '3', db: 'DB' }),
  ],
};
const curves = (f) => f.data.filter((t) => t.meta?.role === 'measurement');

test('the offset slider: 0 overlays, positive puts the first sample on top, negative reverses', () => {
  assert.deepEqual(
    [0, 1, 2].map((i) => offsetFor(i, 3, 0)),
    [0, 0, 0],
  );
  assert.deepEqual(
    [0, 1, 2].map((i) => offsetFor(i, 3, 10)),
    [20, 10, 0],
  );
  assert.deepEqual(
    [0, 1, 2].map((i) => offsetFor(i, 3, -10)),
    [0, 10, 20],
  );
  const state = createState(project);
  state.offsets.raw.spacing = 10;
  assert.deepEqual(
    curves(buildFigure(project, state)).map((t) => t.y[0]),
    [21, 11, 2],
  );
  state.offsets.raw.spacing = -10;
  assert.deepEqual(
    curves(buildFigure(project, state)).map((t) => t.y[0]),
    [1, 11, 22],
  );
});

test('the slider range is symmetric around overlay and covers the default spacing', () => {
  const state = createState(project);
  const limit = spacingLimit(project, state);
  assert.ok(limit >= state.offsets.raw.spacing * 2 && limit > 0);
  state.offsets.raw.spacing = -limit * 3;
  assert.ok(spacingLimit(project, state) >= limit * 3);
});

test('overlay shows intensity values on the y axis; stacked shows only the axis title', () => {
  const state = createState(project);
  state.offsets.raw.spacing = 0;
  let layout = buildFigure(project, state).layout;
  assert.equal(layout.yaxis.showticklabels, true);
  assert.equal(layout.yaxis.title.text, 'Intensity, cps');
  state.offsets.raw.spacing = 5;
  layout = buildFigure(project, state).layout;
  assert.equal(layout.yaxis.showticklabels, false);
  assert.equal(layout.yaxis.title.text, 'Intensity, cps');
});

test('normalization is gone: old saved states open as raw intensity, overlay as zero spacing', () => {
  const restored = restoreState(project, {
    projectId: 'pb',
    intensity: 'normalized',
    arrangement: 'overlay',
    offsets: { raw: { spacing: 30 }, normalized: { spacing: 15 } },
  });
  assert.equal(restored.intensity, 'raw');
  assert.equal(restored.offsets.raw.spacing, 0);
  const negative = restoreState(project, {
    projectId: 'pb',
    offsetRule: 2,
    offsets: { raw: { spacing: -7 } },
  });
  assert.equal(negative.offsets.raw.spacing, -7);
  assert.equal(curves(buildFigure(project, restored))[0].y[0], 1);
});

test('single profile view draws only the active sample with its phases', () => {
  const state = createState(project);
  state.viewMode = 'single';
  state.active = 'b';
  state.markers = true;
  const figure = buildFigure(project, state);
  assert.deepEqual(
    curves(figure).map((t) => t.meta.sampleId),
    ['b'],
  );
  const bars = figure.data.filter((t) => t.meta?.role === 'peakbar');
  assert.deepEqual([...new Set(bars.map((t) => t.meta.sampleId))], ['b']);
  assert.equal(curves(figure)[0].y[0], 1, 'a single profile is never offset');
});

test('markers are filled, sized by the setting and use the chosen symbol per phase', () => {
  const state = createState(project);
  state.markers = true;
  state.markerSize = 11;
  state.phaseSymbols = { 'a-p': 'star' };
  const marker = buildFigure(project, state).data.find(
    (t) => t.meta?.role === 'marker' && t.meta.sampleId === 'a',
  ).marker;
  assert.equal(marker.size, 11);
  assert.equal(marker.symbol, 'star');
  assert.ok(!String(marker.symbol).endsWith('-open'));
  const other = buildFigure(project, state).data.find(
    (t) => t.meta?.role === 'marker' && t.meta.sampleId === 'b',
  ).marker;
  assert.ok(!String(other.symbol).endsWith('-open'));
});

test('unknown peaks are the observed peaks the report assigns to no phase', () => {
  assert.deepEqual(
    unknownPeaks(project.samples[0]).map((p) => p.x),
    [12],
  );
  const state = createState(project);
  assert.equal(
    buildFigure(project, state).data.filter((t) => t.meta?.role === 'unknown').length,
    0,
  );
  state.unknownMarkers = true;
  const unknown = buildFigure(project, state).data.filter((t) => t.meta?.role === 'unknown');
  assert.equal(unknown.length, 3);
  assert.deepEqual(unknown[0].x, [12]);
  // The Unknown Peak marker is a "?" glyph (black by default).
  assert.equal(unknown[0].mode, 'text');
  assert.deepEqual(unknown[0].text, ['?']);
  assert.equal(unknown[0].textfont.color, '#000000');
});

test('Peak Bar rows show tip symbols when phase markers are on and keep black phase separators', () => {
  const state = createState(project);
  state.markers = true;
  const figure = buildFigure(project, state);
  const tips = figure.data.filter((t) => t.meta?.role === 'peakbar-tip');
  assert.equal(tips.length, 3);
  assert.deepEqual(tips[0].x, [11, 12.5]);
  const separators = (figure.layout.shapes || []).filter((s) => s.yref === 'y2');
  assert.equal(separators.length, 2);
  assert.ok(separators.every((s) => s.line.color === '#000000'));
});

test('the built-in legend is off and the HTML legend lists samples with checkboxes', () => {
  const state = createState(project);
  state.visible = ['a', 'c'];
  const figure = buildFigure(project, state);
  assert.equal(figure.layout.showlegend, false);
  const html = legendHTML(project, state);
  assert.ok(html.includes('data-legend-sample="a" checked'));
  assert.ok(
    html.includes('data-legend-sample="b" ') && !html.includes('data-legend-sample="b" checked'),
  );
  assert.ok(html.includes('data-legend-unknown='));
  assert.ok(!html.includes('data-legend-toggle="markers"'));
  assert.ok(!html.includes('data-legend-toggle="phaseLabels"'));
});

test('at the overlay centre individual offsets are kept but not applied', () => {
  const state = createState(project);
  state.offsets.raw.spacing = 0;
  state.offsets.raw.individual = { a: 50 };
  assert.deepEqual(
    curves(buildFigure(project, state)).map((t) => t.y[0]),
    [1, 1, 2],
  );
  assert.equal(state.offsets.raw.individual.a, 50);
  assert.equal(buildFigure(project, state).layout.yaxis.showticklabels, true);
  state.offsets.raw.spacing = 5;
  assert.equal(curves(buildFigure(project, state))[0].y[0], 1 + 10 + 50);
});

test('a stacked view saved before the slider rule keeps its first sample at the bottom', () => {
  assert.equal(createState(project).offsetRule, 2);
  const old = restoreState(project, {
    projectId: 'pb',
    arrangement: 'stacked',
    offsets: { raw: { spacing: 30 } },
  });
  assert.equal(old.offsets.raw.spacing, -30);
  assert.equal(old.offsetRule, 2);
  const current = restoreState(project, {
    projectId: 'pb',
    offsetRule: 2,
    offsets: { raw: { spacing: 30 } },
  });
  assert.equal(current.offsets.raw.spacing, 30);
});

test('a y range saved on the normalized scale is not applied to raw counts', () => {
  const restored = restoreState(project, {
    projectId: 'pb',
    intensity: 'normalized',
    yRange: [0, 120],
    xRange: [10, 12],
  });
  assert.equal(restored.yRange, null);
  assert.deepEqual(restored.xRange, [10, 12]);
});

test('a saved state without a spacing keeps the default direction', () => {
  assert.equal(
    restoreState(project, { projectId: 'pb' }).offsets.raw.spacing,
    createState(project).offsets.raw.spacing,
  );
});
