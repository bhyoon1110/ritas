import test from 'node:test';
import assert from 'node:assert/strict';
import {
  buildFigure,
  createState,
  phaseMatches,
  restoreState,
  sourceHref,
} from '../../app/xrd_workbench/web/viewer/model.mjs';

const sample = (id, name, phase) => ({
  id,
  name,
  measurement: {
    x: [10, 11, 12],
    y: [2, 4, 3],
    axis_kind: 'two_theta',
    axis_unit: 'deg',
    intensity_unit: 'cps',
  },
  phases: [{ id: id + '-p', ...phase, source: id + '.pdf' }],
  reference_peaks: [
    {
      phase: phase.name,
      x: 10.5,
      hkl_raw: '1 0 0',
      d_raw: '3',
      intensity: 100,
      intensity_raw: '100',
      page: 3,
    },
  ],
  observed_peaks: [{ no: 1, x: 11, height_raw: '3(1)', page: 2, x_raw: '11' }],
  assignments: [
    { no: 1, x: 11, x_raw: '11', page: 5, matches: [{ phase: phase.name, hkl: '1 0 0' }] },
  ],
  sources: [
    { id: 'abcdef0123456789', filename: id + '_상매칭_Report.pdf', role: 'phase_report' },
    { id: 'ffff000011112222', filename: id + '.txt', role: 'measurement' },
  ],
});
const project = {
  id: 'fx',
  storage_id: 'store1',
  samples: [
    sample('s1', 'A', { name: 'PhaseA', card: '111', db: 'DB' }),
    sample('s2', 'B', { name: 'PhaseA', card: '222', db: 'DB' }),
    sample('s3', 'C', { name: 'PhaseB', card: '333', db: 'DB' }),
  ],
};
const bars = (f) => f.data.filter((t) => t.meta?.role === 'peakbar');
const marks = (f) => f.data.filter((t) => t.meta?.role === 'marker');
const withAll = () => {
  const state = createState(project);
  state.markers = true;
  return state;
};

test('a new state has no highlight and a figure without highlight carries no opacity changes', () => {
  const state = withAll();
  assert.equal(state.highlight, null);
  const figure = buildFigure(project, state);
  for (const t of [...bars(figure), ...marks(figure)]) {
    assert.equal(t.opacity, undefined);
    assert.equal(t.marker?.opacity, undefined);
  }
  assert.ok(bars(figure).every((t) => t.line.width === 1.1));
});

test('phaseMatches needs the name and, when given, the same card and database', () => {
  const p = { name: 'PhaseA', card: '111', db: 'DB' };
  assert.equal(phaseMatches(null, p), false);
  assert.equal(phaseMatches({ name: 'PhaseA', card: null, db: null }, p), true);
  assert.equal(phaseMatches({ name: 'PhaseA', card: '111', db: 'DB' }, p), true);
  assert.equal(phaseMatches({ name: 'PhaseA', card: '222', db: 'DB' }, p), false);
  assert.equal(phaseMatches({ name: 'PhaseA', card: '111', db: 'Other' }, p), false);
  assert.equal(phaseMatches({ name: 'PhaseB', card: null, db: null }, p), false);
});

test('highlighting a phase keeps its rows strong and dims every other phase, cards included', () => {
  const state = withAll();
  state.highlight = { name: 'PhaseA', card: '111', db: 'DB' };
  const figure = buildFigure(project, state);
  const opacity = Object.fromEntries(bars(figure).map((t) => [t.meta.sampleId, t.opacity]));
  // PhaseA of s1 and s2 is one shared row (drawn from s1); it is lit because s1 matches.
  assert.deepEqual(opacity, { s1: 1, s2: 0.25, s3: 0.25 });
  const width = Object.fromEntries(bars(figure).map((t) => [t.meta.sampleId, t.line.width]));
  assert.ok(width.s1 > width.s3);
  const mark = Object.fromEntries(marks(figure).map((t) => [t.meta.sampleId, t.marker.opacity]));
  assert.deepEqual(mark, { s1: 1, s2: 0.25, s3: 0.25 });
  const notes = figure.layout.annotations.map((a) => a.font.color);
  assert.ok(
    notes.some((c) => String(c).startsWith('rgba(')) && notes.some((c) => c.startsWith('#')),
  );
});

test('a link by name highlights the same name with every card', () => {
  const state = withAll();
  state.highlight = { name: 'PhaseA', card: null, db: null };
  const opacity = Object.fromEntries(
    bars(buildFigure(project, state)).map((t) => [t.meta.sampleId, t.opacity]),
  );
  assert.deepEqual(opacity, { s1: 1, s2: 1, s3: 0.25 });
  const mark = Object.fromEntries(
    marks(buildFigure(project, state)).map((t) => [t.meta.sampleId, t.marker.opacity]),
  );
  assert.deepEqual(mark, { s1: 1, s2: 1, s3: 0.25 });
});

test('highlighting changes display only: curves and the project stay as they were', () => {
  const before = JSON.stringify(project);
  const plain = withAll(),
    lit = withAll();
  lit.highlight = { name: 'PhaseB', card: null, db: null };
  const curves = (f) => f.data.filter((t) => t.meta?.role === 'measurement').map((t) => [t.x, t.y]);
  assert.deepEqual(curves(buildFigure(project, lit)), curves(buildFigure(project, plain)));
  assert.equal(JSON.stringify(project), before);
});

test('restoreState keeps a highlight only when the project has a matching phase', () => {
  const ok = restoreState(project, {
    projectId: 'fx',
    highlight: { name: 'PhaseA', card: null, db: null },
  });
  assert.deepEqual(ok.highlight, { name: 'PhaseA', card: null, db: null });
  for (const bad of [
    { name: 'Unknown', card: null, db: null },
    { name: 'PhaseA', card: '999', db: null },
    { name: 5 },
    'PhaseA',
    [],
  ])
    assert.equal(restoreState(project, { projectId: 'fx', highlight: bad }).highlight, null);
});

test('marker and Peakbar points carry the PDF pages that justify them', () => {
  const figure = buildFigure(project, withAll());
  assert.deepEqual(marks(figure)[0].customdata[0], [1, 5, 2]);
  const bar = bars(figure)[0];
  assert.deepEqual(bar.customdata[0], [3, '1 0 0']);
  assert.equal(bar.customdata[2], null);
  assert.equal(bar.customdata.length, bar.x.length);
});

test('source links point at the registered PDF page, differently for the app and the offline viewer', () => {
  const s = project.samples[0];
  assert.equal(
    sourceHref(project, s, 'phase_report', 5, false),
    '/api/files/store1/abcdef0123456789#page=5',
  );
  assert.equal(
    sourceHref(project, s, 'phase_report', 5, true),
    'Report/abcdef01-' + encodeURIComponent('s1_상매칭_Report.pdf') + '#page=5',
  );
  for (const page of [undefined, null, 0, -1, NaN, 1.5, '3'])
    assert.equal(sourceHref(project, s, 'phase_report', page, false), null);
  assert.equal(sourceHref(project, { ...s, sources: [] }, 'phase_report', 2, false), null);
  assert.equal(sourceHref(project, s, 'wppf_report', 2, false), null);
});
