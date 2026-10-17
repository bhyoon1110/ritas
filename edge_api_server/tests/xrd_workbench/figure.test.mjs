import test from 'node:test';
import assert from 'node:assert/strict';
import {
  addGroup,
  applyOrder,
  defaultFigure,
  ensureFigure,
  figureFromViewer,
  moveGroup,
  removeGroup,
  renameGroup,
  setGroupMembers,
} from '../../app/xrd_workbench/web/report/figure.mjs';
import { figureHTML } from '../../app/xrd_workbench/web/report/figure_view.mjs';
import { mergeDraftChanges } from '../../app/xrd_workbench/web/report/report_merge.mjs';

const titles = (figure) => figure.groups.map((g) => g.title);

test('old reports without a figure block get the figure off', () => {
  const doc = {};
  assert.equal(ensureFigure(doc).include, false);
  assert.deepEqual(doc.figure, defaultFigure());
});

test('addGroup needs a name and members and moves samples out of other groups', () => {
  const figure = defaultFigure();
  assert.throws(() => addGroup(figure, '  ', ['a']), /그룹 이름/);
  assert.throws(() => addGroup(figure, 'g', []), /시료/);
  addGroup(figure, 'g1', ['a', 'b']);
  addGroup(figure, 'g2', ['b', 'c']);
  assert.deepEqual(figure.groups, [
    { title: 'g1', samples: ['a'] },
    { title: 'g2', samples: ['b', 'c'] },
  ]);
  addGroup(figure, 'g3', ['a']);
  assert.deepEqual(titles(figure), ['g2', 'g3']);
});

test('rename, remove and move keep the other groups intact', () => {
  const figure = defaultFigure();
  addGroup(figure, 'g1', ['a']);
  addGroup(figure, 'g2', ['b']);
  renameGroup(figure, 0, ' new ');
  assert.equal(figure.groups[0].title, 'new');
  assert.throws(() => renameGroup(figure, 0, ''), /그룹 이름/);
  moveGroup(figure, 0, 1);
  assert.deepEqual(titles(figure), ['g2', 'new']);
  moveGroup(figure, 0, -1);
  assert.deepEqual(titles(figure), ['g2', 'new']);
  removeGroup(figure, 0);
  assert.deepEqual(titles(figure), ['new']);
});

test('setGroupMembers moves samples between groups and drops a group left empty', () => {
  const figure = defaultFigure();
  addGroup(figure, 'g1', ['a', 'b']);
  addGroup(figure, 'g2', ['c']);
  setGroupMembers(figure, 1, ['b', 'c']);
  assert.deepEqual(figure.groups, [
    { title: 'g1', samples: ['a'] },
    { title: 'g2', samples: ['b', 'c'] },
  ]);
  setGroupMembers(figure, 0, []);
  assert.deepEqual(figure.groups, [{ title: 'g2', samples: ['b', 'c'] }]);
});

test('figureFromViewer copies the view and keeps groups, caption and inclusion', () => {
  const base = {
    ...defaultFigure(),
    include: true,
    caption: 'c',
    groups: [{ title: 'g', samples: ['a'] }],
  };
  const out = figureFromViewer(base, {
    intensity: 'normalized',
    offsets: { raw: { spacing: 7 }, normalized: { spacing: 15 } },
    xRange: [20, 60],
    markers: true,
    peakbar: false,
  });
  // Normalization was removed from the viewer: the raw spacing is taken and the figure stays raw.
  assert.equal(out.normalize, false);
  assert.equal(out.spacing, 7);
  assert.deepEqual(out.x_range, [20, 60]);
  assert.equal(out.show_markers, true);
  assert.equal(out.show_reference, false);
  assert.equal(out.include, true);
  assert.equal(out.caption, 'c');
  assert.deepEqual(out.groups, base.groups);
  const raw = figureFromViewer(base, {
    intensity: 'raw',
    offsets: { raw: { spacing: -7 } },
    xRange: null,
  });
  assert.equal(raw.normalize, false);
  assert.equal(raw.spacing, 7);
  assert.equal(raw.x_range, null);
  assert.equal(base.normalize, false);
});

test('applyOrder writes the order into included slots and returns to report order mode', () => {
  const doc = {
    figure: { ...defaultFigure(), order_mode: 'similarity' },
    samples: [
      { sample_id: 'a', included: true },
      { sample_id: 'x', included: false },
      { sample_id: 'b', included: true },
      { sample_id: 'c', included: true },
    ],
  };
  applyOrder(doc, ['c', 'a', 'b']);
  assert.deepEqual(
    doc.samples.map((s) => s.sample_id),
    ['c', 'x', 'a', 'b'],
  );
  assert.equal(doc.figure.order_mode, 'report');
});

test('unsaved figure edits survive an analysis refresh', () => {
  const sample = { sample_id: 's', notes: [], retained_notes: [] };
  const baseline = {
    metadata: {},
    llm: {},
    summary: '',
    samples: [sample],
    figure: defaultFigure(),
  };
  const current = structuredClone(baseline);
  current.figure.include = true;
  current.figure.groups = [{ title: 'g', samples: ['s'] }];
  const latest = structuredClone(baseline);
  const out = mergeDraftChanges(latest, current, baseline);
  assert.equal(out.figure.include, true);
  assert.deepEqual(out.figure.groups, [{ title: 'g', samples: ['s'] }]);
  latest.figure.caption = 'server';
  assert.equal(
    mergeDraftChanges(latest, structuredClone(baseline), baseline).figure.caption,
    'server',
  );
});

test('setGroupMembers works on groups that share a title', () => {
  const figure = defaultFigure();
  addGroup(figure, 'same', ['a']);
  addGroup(figure, 'same', ['b']);
  setGroupMembers(figure, 1, ['b', 'c']);
  assert.deepEqual(figure.groups, [
    { title: 'same', samples: ['a'] },
    { title: 'same', samples: ['b', 'c'] },
  ]);
});

test('figure tab markup escapes names and shows hints, groups and the picked samples', () => {
  const figure = { ...defaultFigure(), include: true, groups: [{ title: '<g>', samples: ['a'] }] };
  const html = figureHTML({
    figure,
    samples: [
      { sample_id: 'a', display_name: '<b>A</b>', included: true },
      { sample_id: 'b', display_name: 'B', included: true },
      { sample_id: 'x', display_name: 'X', included: false },
    ],
    hints: { identical: [['a', 'b']], notes: { b: '+PhaseB' } },
    pick: new Set(['b']),
  });
  assert.ok(!html.includes('<b>A</b>') && html.includes('&lt;b&gt;A&lt;/b&gt;'));
  assert.ok(html.includes('상 구성 동일 2개') && html.includes('+PhaseB'));
  assert.ok(html.includes('data-figure-pick="b" checked'));
  assert.ok(!html.includes('data-figure-pick="x"'));
  assert.ok(html.includes('value="&lt;g&gt;"'));
});
