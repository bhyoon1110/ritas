import test from 'node:test';
import assert from 'node:assert/strict';
import {
  buildFigure,
  createState,
  exportFigure,
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
const tips = (f) => f.data.filter((t) => t.meta?.role === 'peakbar-tip');
const withAll = () => {
  const state = createState(project);
  state.markers = true;
  return state;
};

test('phase toggle controls upper and reference-tip markers without changing reference lines or measurements', () => {
  const before = JSON.stringify(project);
  for (const viewMode of ['multi', 'single']) {
    for (const originStyle of [false, true]) {
      for (const render of [buildFigure, exportFigure]) {
        const state = createState(project);
        Object.assign(state, { viewMode, originStyle, markerSize: 17, phaseColors: { 's1-p': '#12ab34' } });
        const off = render(project, state);
        assert.equal(marks(off).length, 0);
        assert.equal(tips(off).length, 0);
        assert.ok(bars(off).length > 0);
        state.markers = true;
        const on = render(project, state);
        assert.ok(marks(on).length > 0);
        assert.equal(tips(on).length, bars(on).length);
        assert.deepEqual(bars(on), bars(off));
        assert.deepEqual(
          on.data.filter(t => t.meta?.role === 'measurement'),
          off.data.filter(t => t.meta?.role === 'measurement'),
        );
        for (const tip of tips(on)) {
          const bar = bars(on).find(t => t.meta.phaseId === tip.meta.phaseId);
          assert.equal(tip.marker.color, bar.line.color);
          assert.equal(tip.marker.size, 17);
        }
        state.peakbar = false;
        const noBars = render(project, state);
        assert.ok(marks(noBars).length > 0);
        assert.equal(bars(noBars).length, 0);
        assert.equal(tips(noBars).length, 0);
        state.markers = false;
        state.peakbar = true;
        assert.deepEqual(bars(render(project, state)), bars(off));
        assert.equal(tips(render(project, state)).length, 0);
      }
    }
  }
  assert.equal(JSON.stringify(project), before);
});

test('restored marker toggle also controls reference tips when observed assignments are unavailable', () => {
  const p = structuredClone(project);
  for (const s of p.samples) {
    s.observed_peaks = [];
    s.assignments = [];
  }
  for (const markers of [false, true]) {
    const state = restoreState(p, { projectId: p.id, markers, peakbar: true });
    const figure = buildFigure(p, state);
    assert.equal(marks(figure).length, 0);
    assert.ok(bars(figure).length > 0);
    assert.equal(tips(figure).length, markers ? bars(figure).length : 0);
  }
});

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
