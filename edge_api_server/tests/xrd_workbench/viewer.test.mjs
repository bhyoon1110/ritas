import test from 'node:test';
import assert from 'node:assert/strict';
import { createState, buildFigure, displayUnit, viewWarnings } from '../../app/xrd_workbench/web/viewer/model.mjs';
import { escapeHTML } from '../../app/xrd_workbench/web/shared/dom.mjs';

const project = {
  id: 'fixture',
  samples: [
    {
      id: 's1',
      name: 'A',
      measurement: {
        x: [10, 11],
        y: [2, 4],
        axis_kind: 'two_theta',
        axis_unit: 'deg',
        intensity_unit: 'cps',
      },
      phases: [{ id: 'p1', name: 'PhaseA', card: '123', source: 'A.pdf' }],
      reference_peaks: [
        {
          phase: 'PhaseA',
          x: 10.5,
          hkl_raw: '1 0 0',
          d_raw: '3',
          intensity: 100,
          intensity_raw: '100',
          page: 3,
        },
        {
          phase: 'PhaseA',
          x: 10.5,
          hkl_raw: '0 1 0',
          d_raw: '3',
          intensity: 10,
          intensity_raw: '10',
          page: 3,
        },
      ],
      observed_peaks: [{ no: 1, x: 10.6, height_raw: '3(1)', page: 2 }],
      assignments: [
        { no: 1, x: 10.6, x_raw: '10.6(1)', page: 2, matches: [{ phase: 'PhaseA', hkl: '1 0 0' }] },
      ],
    },
    {
      id: 's2',
      name: 'B',
      measurement: {
        x: [10, 11],
        y: [1, 8],
        axis_kind: 'two_theta',
        axis_unit: 'deg',
        intensity_unit: 'cps',
      },
      phases: [{ id: 'p2', name: 'PhaseA', card: '123', source: 'B.pdf' }],
      reference_peaks: [
        {
          phase: 'PhaseA',
          x: 10.7,
          hkl_raw: '1 0 0',
          intensity: 100,
          intensity_raw: '100',
          page: 4,
        },
      ],
      observed_peaks: [],
      assignments: [],
    },
  ],
};
const curves = (f) => f.data.filter((t) => t.meta?.role === 'measurement');

test('offset is display-only: the first sample goes on top and the project is untouched', () => {
  const original = JSON.stringify(project),
    state = createState(project);
  state.offsets.raw.spacing = 10;
  state.offsets.raw.individual.s2 = -2;
  assert.deepEqual(
    curves(buildFigure(project, state)).map((t) => t.y),
    [
      [12, 14],
      [-1, 6],
    ],
  );
  state.offsets.raw.spacing = 0;
  state.offsets.raw.individual = {};
  assert.deepEqual(curves(buildFigure(project, state))[1].y, [1, 8]);
  assert.equal(JSON.stringify(project), original);
});
test('many Peakbar rows have readable spacing without compressing the measurement area', () => {
  const batch = structuredClone(project);
  batch.samples = Array.from({ length: 12 }, (_, i) => {
    const s = structuredClone(project.samples[i % 2]);
    s.id = 'sample-' + i;
    // Each sample has a phase of its own (the same compound would be one shared row).
    s.phases = s.phases.map((p, j) => ({ ...p, id: s.id + '-phase-' + j, name: 'Phase' + i }));
    s.reference_peaks = s.reference_peaks.map((r) => ({ ...r, phase: 'Phase' + i }));
    return s;
  });
  const state = createState(batch);
  const figure = buildFigure(batch, state, { height: 700 });
  const rows = figure.data.filter((t) => t.meta?.role === 'peakbar').length;
  const drawable = figure.layout.height - figure.layout.margin.t - figure.layout.margin.b;
  const domain = figure.layout.yaxis2.domain;
  assert.equal(rows, 12);
  assert.ok(
    (drawable * (domain[1] - domain[0])) / rows >= 18 - 1e-9,
    'Peakbar labels need separate rows',
  );
  assert.ok(drawable * (1 - figure.layout.yaxis.domain[0]) >= 638 * 0.77 - 1e-9);
  assert.ok(figure.layout.height > 700);
  assert.equal(figure.layout.xaxis2.matches, 'x');
  state.peakbar = false;
  assert.equal(buildFigure(batch, state, { height: 700 }).layout.height, 700);
});
test('markers use reported observed positions and bars keep per-sample reference positions', () => {
  // Two different phases, one per sample (the same phase in both would be a single shared row).
  const split = structuredClone(project);
  split.samples[1].phases[0].name = 'PhaseB';
  for (const r of split.samples[1].reference_peaks) if (r.phase === 'PhaseA') r.phase = 'PhaseB';
  for (const a of split.samples[1].assignments || [])
    for (const m of a.matches) if (m.phase === 'PhaseA') m.phase = 'PhaseB';
  const state = createState(split);
  state.markers = true;
  const fig = buildFigure(split, state);
  assert.equal(fig.data.find((t) => t.meta?.role === 'marker').x[0], 10.6);
  const bars = fig.data.filter((t) => t.meta?.role === 'peakbar');
  assert.equal(bars.length, 2);
  assert.ok(bars[0].x.includes(10.5));
  assert.ok(bars[1].x.includes(10.7));
  assert.match(bars[0].text[0], /1 0 0/);
  assert.match(bars[0].text[0], /0 1 0/);
  // The same phase in both samples is one row, taken from the first sample.
  const shared = buildFigure(project, createState(project)).data.filter(
    (t) => t.meta?.role === 'peakbar',
  );
  assert.equal(shared.length, 1);
  assert.ok(shared[0].x.includes(10.5));
  state.markers = false;
  state.peakbar = false;
  assert.equal(buildFigure(split, state).data.length, 2);
});
test('empty selection removes all old traces', () => {
  const state = createState(project);
  state.visible = [];
  assert.equal(buildFigure(project, state).data.length, 0);
});
test('unknown axes and incompatible intensity units never appear under cps silently', () => {
  const mixed = structuredClone(project);
  mixed.samples[1].measurement.intensity_unit = 'counts';
  assert.equal(curves(buildFigure(mixed, createState(mixed))).length, 1);
  mixed.samples[0].measurement.axis_kind = null;
  assert.equal(curves(buildFigure(mixed, createState(mixed))).length, 1);
});
test('source text is escaped for Plotly HTML hover', () => {
  assert.equal(escapeHTML('<img onerror="x">'), '&lt;img onerror=&quot;x&quot;&gt;');
});
test('offset unit follows displayed sample and all project-level exclusions remain readable', () => {
  const mixed = structuredClone(project);
  mixed.samples[1].measurement.intensity_unit = 'counts';
  mixed.warnings = ['Orphan: TXT 미등록, 그래프에 포함하지 않았습니다.'];
  const state = createState(mixed);
  state.visible = ['s2'];
  assert.equal(displayUnit(mixed, state), 'counts');
  assert.deepEqual(viewWarnings(mixed, []), ['Orphan: TXT 미등록, 그래프에 포함하지 않았습니다.']);
  state.visible = [];
  assert.ok(
    viewWarnings(mixed, buildFigure(mixed, state).warnings).includes(
      'Orphan: TXT 미등록, 그래프에 포함하지 않았습니다.',
    ),
  );
});
test('new offline snapshots cannot inherit an earlier prepared view cache', async () => {
  const { viewerStorageKey } = await import('../../app/xrd_workbench/web/viewer/model.mjs');
  const project = { id: 'p', analysis_revision: 2 };
  assert.notEqual(viewerStorageKey(project, 'new'), viewerStorageKey(project, 'old'));
  assert.notEqual(viewerStorageKey(project, 'new'), viewerStorageKey(project));
  assert.equal(viewerStorageKey(project, 'new'), viewerStorageKey(project, 'new'));
});
