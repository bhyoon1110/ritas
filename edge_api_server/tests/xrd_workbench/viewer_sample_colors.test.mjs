import test from 'node:test';
import assert from 'node:assert/strict';
import { sampleColor, automaticSampleColors } from '../../app/xrd_workbench/web/viewer/sample_colors.mjs';
import { createState, restoreState, buildFigure, exportFigure, legendHTML } from '../../app/xrd_workbench/web/viewer/model.mjs';

function project(count = 24) {
  return { id: 'many-colours', samples: Array.from({ length: count }, (_, i) => ({
    id: 's' + i, name: '시료 ' + (i + 1), phases: [], sources: [],
    measurement: { x: [10, 20, 30], y: [1, 10 + i, 2], axis_kind: 'two_theta', axis_unit: 'deg', intensity_unit: 'cps' },
  })) };
}
const curves = f => f.data.filter(t => t.meta?.role === 'measurement');

test('24 RAW curves, legend and image export use 24 distinct stable colours', () => {
  const p = project(), before = JSON.stringify(p), state = createState(p);
  const colors = curves(buildFigure(p, state)).map(t => t.line.color);
  assert.equal(colors.length, 24);
  assert.equal(new Set(colors).size, 24);
  for (const color of colors) assert.ok(legendHTML(p, state).includes('background:' + color));
  for (const origin of [false, true]) {
    state.originStyle = origin;
    assert.deepEqual(curves(exportFigure(p, state)).map(t => t.line.color), colors);
  }
  assert.equal(JSON.stringify(p), before);
  assert.deepEqual(automaticSampleColors(project(30)), {
    ...state.colors, ...Object.fromEntries(Array.from({ length: 6 }, (_, i) => ['s' + (24 + i), sampleColor(24 + i)])),
  });
});

test('reordering, hiding and single mode preserve sample colours, including missing-state fallback', () => {
  const p = project(), state = createState(p), original = { ...state.colors };
  state.order.reverse();
  state.visible = state.order.filter((_, i) => i % 2 === 0);
  state.colors = {}; // partial state also resolves colours by project index, not visible index
  for (const trace of curves(buildFigure(p, state))) assert.equal(trace.line.color, original[trace.meta.sampleId]);
  state.viewMode = 'single'; state.active = 's23';
  assert.equal(curves(buildFigure(p, state))[0].line.color, original.s23);
  assert.ok(legendHTML(p, state).includes('background:' + original.s23));
});

test('saved colours including intentional duplicates survive reload; explicit automatic assignment replaces them', () => {
  const p = project(), state = createState(p);
  state.colors.s7 = '#123456'; state.colors.s8 = state.colors.s0;
  const restored = restoreState(p, JSON.parse(JSON.stringify(state)));
  assert.deepEqual(restored.colors, state.colors);
  restored.colors = automaticSampleColors(p);
  assert.equal(new Set(Object.values(restored.colors)).size, p.samples.length);
  assert.deepEqual(restoreState(p, restored).colors, restored.colors);
});

test('extended palette has 256 unique hex colours, readable on white', () => {
  const colors = Array.from({ length: 256 }, (_, i) => sampleColor(i));
  assert.equal(new Set(colors).size, 256);
  for (const [i, color] of colors.entries()) {
    assert.match(color, /^#[0-9a-f]{6}$/);
    if (i < 12) continue;
    const linear = [1, 3, 5].map(j => parseInt(color.slice(j, j + 2), 16) / 255)
      .map(v => v <= 0.04045 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4);
    const luminance = linear.reduce((sum, v, j) => sum + v * [0.2126, 0.7152, 0.0722][j], 0);
    assert.ok(1.05 / (luminance + 0.05) >= 3, color);
  }
});
