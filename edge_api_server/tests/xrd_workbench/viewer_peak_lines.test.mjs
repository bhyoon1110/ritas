import test from 'node:test';
import assert from 'node:assert/strict';
import {buildFigure, createState, exportFigure, restoreState}
  from '../../app/xrd_workbench/web/viewer/model.mjs';

const sample = id => ({
  id, name: id,
  measurement: {x: [10, 11, 12], y: [2, 4, 3], axis_kind: 'two_theta', axis_unit: 'deg', intensity_unit: 'cps'},
  phases: [{id: id + '-p', name: 'Phase ' + id, card: id}],
  // Deliberately different observed/reference x and fitted/raw heights.
  observed_peaks: [{no: 1, x: 11.2, x_raw: '11.2', height_raw: '999', page: 1}],
  assignments: [{no: 1, x: 11.2, x_raw: '11.2', page: 1,
    matches: [{phase: 'Phase ' + id, hkl: '100'}, {phase: 'Phase ' + id, hkl: '200'}]}],
  reference_peaks: [{phase: 'Phase ' + id, x: 10.9, intensity: 100, page: 1}],
});
const project = () => ({id: 'lines', samples: [sample('a'), sample('b')]});
const role = (figure, name) => figure.data.filter(t => t.meta?.role === name);

test('peak lines are opt-in and restored independently, rejecting nonboolean saved values', () => {
  const p = project();
  assert.equal(createState(p).peakLines, false);
  assert.equal(restoreState(p, {projectId: p.id, markers: true}).peakLines, false);
  for (const value of [true, false, 'true', 1, null]) {
    const s = restoreState(p, {projectId: p.id, peakLines: value, markers: false, peakbar: false});
    assert.equal(s.peakLines, value === true);
    assert.equal(s.markers, false);
    assert.equal(s.peakbar, false);
  }
});

test('upper lines are independent of symbols and lower sticks in normal/Origin view and image export', () => {
  const p = project(), before = JSON.stringify(p);
  for (const render of [buildFigure, exportFigure])
    for (const markers of [false, true])
      for (const peakbar of [false, true])
        for (const originStyle of [false, true]) {
          const s = createState(p);
          Object.assign(s, {markers, peakbar, originStyle, phaseColors: {'a-p': '#12ab34'}});
          const off = render(p, s);
          s.peakLines = true;
          const on = render(p, s);
          assert.equal(role(off, 'peak-line').length, 0);
          assert.equal(role(on, 'peak-line').length, 2);
          for (const unchanged of ['measurement', 'marker', 'peakbar', 'peakbar-tip'])
            assert.deepEqual(role(on, unchanged), role(off, unchanged));
          assert.equal(role(on, 'peak-line')[0].line.color, '#12ab34');
          if (render === exportFigure) {
            assert.equal(role(on, 'legend-phase')[0].mode, markers ? 'lines+markers' : 'lines');
            assert.equal(role(on, 'legend-phase')[0].line.color, '#12ab34');
          }
        }
  assert.equal(JSON.stringify(p), before);
});

test('lines use observed positions and raw heights, including stack reversal, zero spacing and single view', () => {
  const p = project();
  for (const spacing of [-10, 0, 10]) {
    const s = createState(p);
    s.peakLines = true;
    s.offsets.raw = {spacing, individual: {a: 7}};
    s.xRange = [10.5, 11.5]; s.yRange = [-20, 30];
    const f = buildFigure(p, s);
    for (const line of role(f, 'peak-line')) {
      const measured = role(f, 'measurement').find(t => t.meta.sampleId === line.meta.sampleId);
      const offset = measured.y[1] - 4;
      assert.deepEqual(line.x, [11.2, 11.2, null]); // not reference x or duplicate per hkl
      assert.deepEqual(line.y, [offset, measured.y[1], null]); // not PDF Height, or marker gap
      assert.equal(line.connectgaps, false);
    }
    assert.deepEqual(f.layout.xaxis.range, s.xRange);
    assert.deepEqual(f.layout.yaxis.range, s.yRange);
    s.viewMode = 'single'; s.active = 'b';
    assert.deepEqual(role(buildFigure(p, s), 'peak-line').map(t => t.y), [[0, 4, null]]);
  }
  p.samples[0].measurement.y = [-3, -1, -2];
  const s = createState(p); s.peakLines = true; s.offsets.raw.spacing = 0;
  assert.deepEqual(role(buildFigure(p, s), 'peak-line')[0].y, [0, -1, null]);
});

test('phase hiding, highlight and excluded samples govern lines without inventing missing peaks', () => {
  const p = project(), s = createState(p);
  s.peakLines = true;
  s.highlight = {name: 'Phase a', card: null, db: null};
  assert.deepEqual(role(buildFigure(p, s), 'peak-line').map(t => t.opacity), [1, 0.25]);
  s.hiddenPhases = ['a-p'];
  assert.deepEqual(role(buildFigure(p, s), 'peak-line').map(t => t.meta.sampleId), ['b']);
  s.hiddenPhases = [];
  s.visible = ['a'];
  assert.deepEqual(role(buildFigure(p, s), 'peak-line').map(t => t.meta.sampleId), ['a']);
  s.visible = ['a', 'b'];
  p.samples[1].measurement.intensity_unit = null;
  assert.deepEqual(role(buildFigure(p, s), 'peak-line').map(t => t.meta.sampleId), ['a']);
  for (const invalidate of [
    p => {p.samples[0].observed_peaks = [];},
    p => {p.samples[0].assignments = [];},
    p => {p.samples[0].assignments[0].matches = [];},
    p => {p.samples[0].observed_peaks[0].x = 11.1;},
    p => {p.samples[0].observed_peaks[0].x = p.samples[0].assignments[0].x = 50;},
  ]) {
    const missing = structuredClone(p); invalidate(missing);
    assert.equal(role(buildFigure(missing, s), 'peak-line').length, 0);
  }
});
