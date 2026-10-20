import test from 'node:test';
import assert from 'node:assert/strict';
import { applyViewerLink, createState, selectedSamples } from '../../app/xrd_workbench/web/viewer/model.mjs';

const project = {
  id: 'links',
  samples: ['a', '한글 + &/#?'].map(id => ({
    id, name: 'Same name', phases: [],
    measurement: { x: [10,20,30], y: [1,9,2], axis_kind: 'two_theta', axis_unit: 'deg', intensity_unit: 'cps' },
  })),
};

test('single link selects stable ID, reveals hidden sample, resets zoom but never source/style', () => {
  const before = JSON.stringify(project);
  const state = createState(project);
  state.visible = [];
  state.xRange = [10, 11]; state.yRange = [1, 2];
  state.colors.a = '#123456'; state.markers = true;
  const preserved = JSON.stringify([state.colors, state.offsets, state.hiddenPhases]);
  const id = project.samples[1].id;
  assert.deepEqual(applyViewerLink(project, state, '#view=single&sample='+encodeURIComponent(id)), {applied: true});
  assert.equal(state.active, id);
  assert.equal(state.viewMode, 'single');
  assert.deepEqual(state.visible, [id]);
  assert.deepEqual(selectedSamples(project, state).samples.map(s=>s.id), [id]);
  assert.equal(state.xRange, null); assert.equal(state.yRange, null);
  assert.equal(JSON.stringify([state.colors, state.offsets, state.hiddenPhases]), preserved);
  assert.equal(state.markers, true);
  assert.equal(JSON.stringify(project), before);
});

test('summary multi link overrides hidden/single state and retains authored sample order', () => {
  const state = createState(project);
  state.viewMode = 'single'; state.visible = [];
  state.order.reverse(); state.xRange = [20, 21];
  assert.equal(applyViewerLink(project, state, '#view=multi').applied, true);
  assert.equal(state.viewMode, 'multi');
  assert.deepEqual(selectedSamples(project, state).samples.map(s=>s.id), state.order);
  assert.equal(state.xRange, null);
});

test('missing/invalid/removed sample does not silently select a different sample or mutate state', () => {
  const state = createState(project), before = JSON.stringify(state);
  for (const hash of ['', '#unrelated', '#view=other', '#view=single', '#view=single&sample=removed', '#view=single&sample=%ZZ']) {
    const result = applyViewerLink(project, state, hash);
    assert.equal(result.applied, false);
    assert.equal(!!result.error, hash.includes('view=single'));
    assert.equal(JSON.stringify(state), before);
  }
});
