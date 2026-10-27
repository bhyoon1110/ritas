import test from 'node:test';
import assert from 'node:assert/strict';
import {
  barCaption,
  buildFigure,
  createState,
  exportFigure,
  groupCaption,
  legendHTML,
  phaseGroupKey,
  groupHidden,
  phaseGroups,
  phaseStyles,
  restoreState,
  setGroupColor,
  setGroupHidden,
} from '../../app/xrd_workbench/web/viewer/model.mjs';

const sample = (id, name, x0, x1, phases) => ({
  id,
  name,
  measurement: {
    x: [x0, (x0 + x1) / 2, x1],
    y: [1, 5, 2],
    axis_kind: 'two_theta',
    axis_unit: 'deg',
    intensity_unit: 'cps',
  },
  phases: phases.map((p, i) => ({ id: `${id}-p${i}`, source: id + '.pdf', db: 'DB', ...p })),
  reference_peaks: phases.flatMap((p) => [
    { phase: p.name, x: 20, hkl_raw: '1 0 0', intensity: 100, intensity_raw: '100', page: 3 },
  ]),
  observed_peaks: [
    { no: 1, x: 20, x_raw: '20', page: 2, height_raw: '5' },
    { no: 2, x: 25, x_raw: '25', page: 2, height_raw: '2' },
  ],
  assignments: [
    {
      no: 1,
      x: 20,
      x_raw: '20',
      page: 2,
      matches: phases.map((p) => ({ phase: p.name, hkl: '1 0 0' })),
    },
    { no: 2, x: 25, x_raw: '25', page: 2, matches: [] },
  ],
  sources: [],
});
const project = {
  id: 'pe',
  samples: [
    sample('a', 'A', 10.4, 79.2, [
      { name: 'PhaseA', formula: 'Xa0.08 Yb0.01 Zc0.01 O2 Qd0.91', card: '1' },
      { name: 'PhaseB', formula: 'Ca C O3', card: '2' },
    ]),
    sample('b', 'B', 12, 80.6, [
      { name: 'PhaseA', formula: 'Xa0.08 Yb0.01 Zc0.01 O2 Qd0.91', card: '1' },
      { name: 'PhaseC', formula: 'Li O', card: '3' },
    ]),
    sample('c', 'C', 15, 70, [{ name: 'PhaseNoFormula', formula: '', card: '4' }]),
  ],
};
const traces = (f, role) => f.data.filter((t) => t.meta?.role === role);

test('only matching phase identity shares a group, never an element set', () => {
  assert.notEqual(
    phaseGroupKey({ name: 'x', formula: 'Xa0.08 Yb0.01 Zc0.01 O2 Qd0.91' }),
    phaseGroupKey({ name: 'y', formula: 'Xa0.08 Yb0.01 Zc0.01 O2 Qd0.9' }),
  );
  assert.notEqual(
    phaseGroupKey({ name: 'x', card: '1', formula: 'Ca C O3' }),
    phaseGroupKey({ name: 'x', card: '2', formula: 'Ca C O3' }),
  );
  assert.notEqual(
    phaseGroupKey({ name: 'PhaseR', formula: '' }),
    phaseGroupKey({ name: ' phaser ', formula: '' }),
  );
  const groups = phaseGroups(project, createState(project));
  assert.equal(groups.length, 4, 'A and B share one group; B, C, and the formula-less phase stay');
  assert.deepEqual(groups[0].ids, ['a-p0', 'b-p0']);
  assert.deepEqual(
    groups.map((g) => g.members.length),
    [2, 1, 1, 1],
  );
});

test('a group has one colour and symbol in every sample, and shows its first card', () => {
  const styles = phaseStyles(project, createState(project));
  assert.deepEqual(styles.get('a-p0'), styles.get('b-p0'));
  assert.notDeepEqual(styles.get('a-p0'), styles.get('a-p1'));
  const state = createState(project);
  state.phaseColors['b-p0'] = '#112233';
  assert.equal(phaseStyles(project, state).get('a-p0').color, '#112233');
});

test('single profile lists only the chosen sample, multi lists the visible ones', () => {
  const state = createState(project);
  state.viewMode = 'single';
  state.active = 'b';
  assert.deepEqual(
    phaseGroups(project, state).map((g) => g.ids),
    [['b-p0'], ['b-p1']],
  );
  state.viewMode = 'multi';
  state.visible = ['c'];
  assert.deepEqual(
    phaseGroups(project, state).map((g) => g.ids),
    [['c-p0']],
  );
});

test('captions: legend is name, formula, # card; peak bar is formula, name, # card; no sample name', () => {
  const [g] = phaseGroups(project, createState(project));
  assert.equal(groupCaption(g), 'PhaseA · Xa0.08 Yb0.01 Zc0.01 O2 Qd0.91 · #1');
  assert.equal(barCaption(g), 'Xa0.08 Yb0.01 Zc0.01 O2 Qd0.91 · PhaseA · #1');
  const [, , , none] = phaseGroups(project, createState(project));
  assert.equal(groupCaption(none), 'PhaseNoFormula · #4');
  const state = createState(project);
  const figure = buildFigure(project, state);
  const labels = figure.layout.annotations.map((a) => a.text);
  assert.ok(labels.every((t) => !t.startsWith('A · ') && !t.startsWith('B · ')));
  assert.ok(labels.some((t) => t.startsWith('Xa0.08 Yb0.01 Zc0.01 O2 Qd0.91 · PhaseA · #1')));
});

test('the peak bar has one row per visible group, not one per sample phase', () => {
  const state = createState(project);
  state.visible = ['a', 'b'];
  assert.equal(traces(buildFigure(project, state), 'peakbar').length, 3);
  setGroupHidden(state, phaseGroups(project, state)[0], true);
  assert.deepEqual(state.hiddenPhases.sort(), ['a-p0', 'b-p0']);
  assert.equal(traces(buildFigure(project, state), 'peakbar').length, 2);
  setGroupHidden(state, phaseGroups(project, state)[0], false);
  assert.deepEqual(state.hiddenPhases, []);
});

test('phase markers of a shared group appear in every sample that has the phase', () => {
  const state = createState(project);
  state.markers = true;
  state.visible = ['a', 'b'];
  const markers = traces(buildFigure(project, state), 'marker').filter(
    (t) => t.meta.phaseId === 'a-p0' || t.meta.phaseId === 'b-p0',
  );
  assert.equal(markers.length, 2);
  assert.equal(markers[0].marker.color, markers[1].marker.color);
  assert.equal(markers[0].marker.symbol, markers[1].marker.symbol);
});

test('the default 2θ range is the data range rounded out to whole degrees', () => {
  const figure = buildFigure(project, createState(project));
  assert.deepEqual(figure.fullX, [10, 81]);
  assert.deepEqual(figure.layout.xaxis.range, [10, 81]);
});

test('unknown peak markers are "?" glyphs whose colour and size come from the state', () => {
  const state = createState(project);
  state.unknownMarkers = true;
  state.unknownColor = '#aa0000';
  state.markerSize = 10;
  const [t] = traces(buildFigure(project, state), 'unknown');
  assert.equal(t.mode, 'text');
  assert.deepEqual(t.text, ['?']);
  assert.equal(t.textfont.color, '#aa0000');
  assert.ok(t.textfont.size >= 10);
  assert.match(t.hovertext[0], /Unknown peak/);
  assert.equal(
    restoreState(project, { projectId: 'pe', unknownColor: '#00ff00' }).unknownColor,
    '#00ff00',
  );
  assert.equal(
    restoreState(project, { projectId: 'pe', unknownColor: 'red' }).unknownColor,
    '#000000',
  );
});

test('the legend lists each sample followed by its own phase checkboxes', () => {
  const html = legendHTML(project, createState(project));
  assert.ok(html.indexOf('data-legend-sample="a"') < html.indexOf('data-legend-phase="a-p0"'));
  assert.ok(html.indexOf('data-legend-phase="a-p1"') < html.indexOf('data-legend-sample="b"'));
  assert.equal((html.match(/data-legend-phase=/g) || []).length, 5);
  assert.ok(html.includes('PhaseA · Xa0.08 Yb0.01 Zc0.01 O2 Qd0.91 · #1'));
  assert.ok(!html.includes('Chemical formula'));
  assert.ok(!html.includes('S/M:ABC') && !html.includes('P42/mnm'));
});

test('image export: the legend names the samples, and the phases only while Phase marker is on', () => {
  const state = createState(project);
  state.visible = ['a', 'b'];
  let figure = exportFigure(project, state, { origin: false });
  const named = (f) => f.data.filter((t) => t.showlegend).sort((a,b)=>a.legendrank-b.legendrank).map((t) => t.name);
  assert.deepEqual(named(figure), ['A', 'B']);
  state.markers = true;
  figure = exportFigure(project, state, { origin: false });
  assert.deepEqual(named(figure), [
    'A',
    'Xa0.08 Yb0.01 Zc0.01 O2 Qd0.91 (PhaseA)',
    'Ca C O3 (PhaseB)',
    'B',
    'Xa0.08 Yb0.01 Zc0.01 O2 Qd0.91 (PhaseA)',
    'Li O (PhaseC)',
  ]);
  assert.ok(figure.layout.showlegend);
});

test('Origin style: boxed black axes with inward ticks, bold titles, coloured reference bars', () => {
  const state = createState(project);
  state.markers = true;
  state.phaseColors['a-p0'] = '#cc0000';
  state.originStyle = true;
  const origin = exportFigure(project, state);
  state.originStyle = false;
  const plain = exportFigure(project, state);
  assert.equal(origin.layout.xaxis2?.ticks ?? origin.layout.xaxis.ticks, 'inside');
  assert.equal(origin.layout.yaxis.linecolor, '#000000');
  assert.equal(origin.layout.yaxis.title.text, 'Intensity, cps');
  assert.ok(origin.layout.yaxis.title.font.size >= 16);
  assert.deepEqual(traces(origin, 'peakbar'), traces(plain, 'peakbar'));
  assert.equal(traces(origin, 'peakbar')[0].line.color, '#cc0000');
  assert.equal(origin.layout.showlegend, true);
});

test('Origin preserves default/custom phase colours and highlight dimming in live and exported figures', () => {
  for (const custom of [false, true]) for (const highlight of [null, { name: 'PhaseA', card: '1', db: 'DB' }]) {
    const state = createState(project);
    state.markers = state.unknownMarkers = true;
    state.highlight = highlight;
    if (custom) {
      state.phaseColors['a-p0'] = '#12ab34';
      state.phaseColors['a-p1'] = '#bd4c87';
      state.colors.a = '#9753ab';
      state.unknownColor = '#ef8901';
    }
    for (const render of [buildFigure, exportFigure]) {
      state.originStyle = false;
      const plain = render(project, state);
      state.originStyle = true;
      const origin = render(project, state);
      for (const role of ['marker', 'peakbar', 'peakbar-tip', 'unknown', 'legend-phase', 'legend-unknown'])
        assert.deepEqual(traces(origin, role), traces(plain, role), role);
      assert.deepEqual(traces(origin, 'measurement').map(t => t.line.color),
        traces(plain, 'measurement').map(t => t.line.color));
      assert.deepEqual(origin.layout.annotations.map(a => a.font.color),
        plain.layout.annotations.map(a => a.font.color));
      assert.ok(traces(origin, 'peakbar-tip').length > 0);
      if (highlight) {
        assert.ok(traces(origin, 'peakbar').some(t => t.opacity < 1));
        assert.ok(traces(origin, 'peakbar').some(t => t.opacity === 1 && t.line.width === 2.2));
      }
      if (custom) assert.equal(traces(origin, 'peakbar-tip')[0].marker.color, '#12ab34');
    }
  }
});

test('one marker size controls measurement peaks and reference tips, including Origin exports', () => {
  const state = createState(project);
  state.markers = state.unknownMarkers = true;
  for (const origin of [false, true]) for (const size of [3, 8, 20]) {
    state.originStyle = origin;
    state.markerSize = size;
    for (const figure of [buildFigure(project, state), exportFigure(project, state)]) {
      const markers = figure.data.filter(t => ['marker','peakbar-tip'].includes(t.meta?.role));
      assert.ok(markers.length > 0);
      assert.ok(markers.every(t => t.marker.size === size));
      assert.ok(traces(figure,'unknown').every(t => t.textfont.size === size * 1.8));
    }
  }
});

test('Origin view and export share styles without modifying data, zoom or offsets', () => {
  const before = JSON.stringify(project), state = createState(project);
  state.xRange = [15, 60]; state.yRange = [-10, 50]; state.markers = true;
  const plain = buildFigure(project,state,{height:700});
  state.originStyle = true;
  const live = buildFigure(project,state,{height:700}), output = exportFigure(project,state,{height:700});
  for (const key of ['xaxis','yaxis','xaxis2','yaxis2','font','margin','height','annotations'])
    assert.deepEqual(live.layout[key],output.layout[key]);
  for (const t of live.data) {
    const previous = plain.data.find(p=>p.uid===t.uid);
    assert.deepEqual(t.x, previous.x); assert.deepEqual(t.y, previous.y);
    assert.deepEqual(t.customdata, previous.customdata);
  }
  assert.deepEqual(live.layout.xaxis.range,[15,60]);
  assert.deepEqual(live.layout.yaxis.range,[-10,50]);
  assert.equal(live.layout.yaxis.showticklabels,false);
  state.offsets.raw.spacing=0;
  assert.equal(buildFigure(project,state).layout.yaxis.showticklabels,true);
  assert.equal(buildFigure(project,state).layout.yaxis.ticks,'inside');
  state.originStyle=false;
  assert.equal(buildFigure(project,state).layout.xaxis.linecolor,plain.layout.xaxis.linecolor);
  assert.equal(JSON.stringify(project),before);
});

test('Origin setting is persisted, defaults off and rejects non-boolean local settings', () => {
  assert.equal(createState(project).originStyle,false);
  assert.equal(restoreState(project,{projectId:project.id,originStyle:true}).originStyle,true);
  assert.equal(restoreState(project,{projectId:project.id,originStyle:'true'}).originStyle,false);
});

test('in single profile the legend lists only the chosen sample', () => {
  const state = createState(project);
  state.viewMode = 'single';
  state.active = 'b';
  const html = legendHTML(project, state);
  assert.ok(html.includes('data-legend-sample="b"'));
  assert.ok(!html.includes('data-legend-sample="a"') && !html.includes('data-legend-sample="c"'));
});

const twin = () => {
  const p = structuredClone(project);
  // Two phases of one sample with the same formula (two structures of one compound).
  p.samples[0].phases = [
    { id: 'a-p0', name: 'PolyA', formula: 'Ti O2', card: '11', db: 'DB', source: 'a.pdf' },
    { id: 'a-p1', name: 'PolyB', formula: 'Ti O2', card: '12', db: 'DB', source: 'a.pdf' },
  ];
  p.samples[0].reference_peaks = [
    { phase: 'PolyA', x: 20, hkl_raw: '1', intensity: 100, intensity_raw: '100', page: 3 },
    { phase: 'PolyB', x: 30, hkl_raw: '2', intensity: 100, intensity_raw: '100', page: 3 },
  ];
  p.samples[1].phases = [
    { id: 'b-p0', name: 'PolyA', formula: 'Ti O2', card: '11', db: 'DB', source: 'b.pdf' },
  ];
  return p;
};

test('two phases of one sample are never merged, even with the same formula', () => {
  const p = twin();
  const state = createState(p);
  state.visible = ['a'];
  assert.equal(phaseGroups(p, state).length, 2);
  assert.equal(traces(buildFigure(p, state), 'peakbar').length, 2);
  const styles = phaseStyles(p, state);
  assert.notDeepEqual(styles.get('a-p0'), styles.get('a-p1'));
  // The first of each sample still pairs up across samples.
  state.visible = ['a', 'b'];
  const groups = phaseGroups(p, state);
  assert.equal(groups.length, 2);
  assert.deepEqual(groups[0].allIds, ['a-p0', 'b-p0']);
});

test('hiding or recolouring a group reaches its members in samples that are hidden now', () => {
  const state = createState(project);
  state.visible = ['b'];
  const [g] = phaseGroups(project, state);
  setGroupHidden(state, g, true);
  assert.deepEqual(state.hiddenPhases.sort(), ['a-p0', 'b-p0']);
  state.visible = ['a', 'b'];
  assert.ok(groupHidden(state, phaseGroups(project, state)[0]));
  setGroupHidden(state, phaseGroups(project, state)[0], false);
  assert.deepEqual(state.hiddenPhases, []);
  setGroupColor(state, g, '#112233');
  assert.equal(phaseStyles(project, state).get('a-p0').color, '#112233');
  setGroupColor(state, g, '#445566');
  assert.equal(phaseStyles(project, state).get('b-p0').color, '#445566');
});

test('an old state that hid one member of a shared group opens with the whole group hidden', () => {
  const restored = restoreState(project, { projectId: 'pe', hiddenPhases: ['b-p0'] });
  assert.deepEqual(restored.hiddenPhases.sort(), ['a-p0', 'b-p0']);
  assert.deepEqual(
    restoreState(project, { projectId: 'pe', hiddenPhases: ['a-p1'] }).hiddenPhases,
    ['a-p1'],
  );
});

test('each sample lists its own Unknown Peak after its phases', () => {
  const state = createState(project);
  state.unknownMarkers = true;
  const html = legendHTML(project, state);
  assert.ok(!html.includes('legendToggles'));
  assert.ok(!html.includes('data-legend-toggle="markers"'));
  assert.ok(!html.includes('data-legend-toggle="peakbar"'));
  assert.equal((html.match(/data-legend-unknown="[abc]" checked/g)||[]).length, 3);
  assert.ok(
    html.indexOf('data-legend-phase="a-p1"') < html.indexOf('data-legend-unknown="a"'),
  );
  assert.ok(html.includes('Unknown Peak'));
  assert.ok(!html.includes('알 수 없는'));
  state.unknownMarkers = false;
  assert.ok(!/data-legend-unknown="[abc]" checked/.test(legendHTML(project, state)));
});

test('markers are filled, each phase has its own colour and shape (square, triangle, star, diamond…)', () => {
  const state = createState(project);
  const styles = [
    ...new Set(phaseGroups(project, state).map((g) => phaseStyles(project, state).get(g.ids[0]))),
  ];
  assert.deepEqual(
    styles.map((s) => s.color),
    ['#8261c9', '#df9b29', '#199c78', '#c44d76'],
  );
  assert.deepEqual(
    styles.map((s) => s.symbol),
    ['square', 'triangle-up', 'star', 'diamond'],
  );
});

test('the last Peak Bar row reaches the bottom of the box', () => {
  const figure = buildFigure(project, createState(project));
  assert.equal(figure.layout.yaxis2.range[0], 0);
  assert.equal(figure.layout.yaxis2.range[1], 4);
});

test('the exported legend names Unknown Peak while its markers are on', () => {
  const state = createState(project);
  state.unknownMarkers = true;
  const names = exportFigure(project, state, { origin: false })
    .data.filter((t) => t.showlegend)
    .map((t) => t.name);
  assert.ok(names.includes('? Unknown Peak'));
  state.unknownMarkers = false;
  assert.ok(
    !exportFigure(project, state, { origin: false }).data.some((t) => /Unknown Peak/.test(t.name)),
  );
});

test('the Unknown Peak row appears only when a shown sample has unknown peaks', () => {
  const none = structuredClone(project);
  for (const s of none.samples)
    s.assignments = s.assignments.map((a) => ({ ...a, matches: [{ phase: 'x', hkl: '1' }] }));
  assert.ok(!legendHTML(none, createState(none)).includes('Unknown Peak'));
  assert.ok(legendHTML(project, createState(project)).includes('Unknown Peak'));
});

test('Unknown Peak is unticked by default, in the state, the legend and the page', async () => {
  const state = createState(project);
  assert.equal(state.unknownMarkers, false);
  assert.ok(!/data-legend-unknown="[abc]" checked/.test(legendHTML(project, state)));
  const { readFile } = await import('node:fs/promises');
  const page = await readFile(new URL('../../app/xrd_workbench/web/index.html', import.meta.url), 'utf8');
  assert.match(page, /<input id="unknownMarkers" type="checkbox"(?![^>]*checked)/);
});

test('the hover on a curve shows the 2θ and one intensity only: no raw/shown split, offset or TXT row', () => {
  const t = traces(buildFigure(project, createState(project)), 'measurement')[0];
  assert.match(t.hovertemplate, /2θ: %\{x:\.6f\}°/);
  assert.match(t.hovertemplate, /강도: %\{customdata\[0\]:\.6g\} cps/);
  for (const word of ['원강도', '표시:', 'Offset', 'TXT'])
    assert.ok(!t.hovertemplate.includes(word), word);
});

test('the marker hover shows the peak without "PDF" and stops after Height', () => {
  const state = createState(project);
  state.markers = true;
  state.unknownMarkers = true;
  const figure = buildFigure(project, state);
  const marker = traces(figure, 'marker')[0];
  const unknown = traces(figure, 'unknown')[0];
  for (const text of [marker.text[0], unknown.hovertext[0]]) {
    assert.match(text, /2θ: /);
    assert.match(text, /Height: /);
    assert.ok(!text.includes('PDF'), text);
    assert.ok(
      !text.includes('Peak List') && !text.includes('배정 p.') && !text.includes('표식 높이'),
    );
    assert.ok(text.trimEnd().split('<br>').at(-1).startsWith('Height: '), text);
  }
});
