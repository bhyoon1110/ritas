import test from 'node:test';
import assert from 'node:assert/strict';
import {estimateBackground} from '../../app/xrd_workbench/web/viewer/background.mjs';

function spectrum(count = 2001, uneven = false) {
  const x = Array.from({length: count}, (_, i) => 10 + 70 * (i / (count - 1)) ** (uneven ? 1.4 : 1));
  const baseline = v => 30 + v * 0.8;
  const y = x.map(v => baseline(v) + 500 * Math.max(0, 1 - Math.abs(v - 35) / 0.3)
    + 200 * Math.max(0, 1 - Math.abs(v - 60) / 0.25));
  return {x, y, baseline};
}

test('flat, tilted and negative backgrounds stay in raw intensity units', () => {
  for (const slope of [0, 0.8, -0.4]) for (const intercept of [-100, 0, 30]) {
    const x = Array.from({length: 101}, (_, i) => i);
    const y = x.map(v => intercept + slope * v);
    const background = estimateBackground({x, y});
    background.forEach((value, i) => assert.ok(Math.abs(value - y[i]) < 1e-9));
  }
});

test('estimation removes narrow peaks from the guide baseline, without touching RAW or losing X alignment', () => {
  for (const count of [1001, 4001]) for (const uneven of [false, true]) {
    const m = spectrum(count, uneven), before = JSON.stringify(m);
    const background = estimateBackground(m);
    assert.equal(background.length, count);
    for (let i = 0; i < count; i++) {
      assert.ok(Number.isFinite(background[i]));
      assert.ok(background[i] <= m.y[i]);
      assert.ok(Math.abs(background[i] - m.baseline(m.x[i])) < 0.05,
        `count=${count}, uneven=${uneven}, x=${m.x[i]}, bg=${background[i]}`);
    }
    assert.equal(JSON.stringify(m), before);
  }
});

test('broad curved background and noisy/edge peaks remain bounded, not a global zero baseline', () => {
  const m = spectrum();
  m.y = m.x.map((v, i) => 100 + 40 * Math.exp(-(((v - 45) / 12) ** 2))
    + 500 * Math.exp(-(((v - 35) / 0.15) ** 2)) + 2 * Math.sin(i * 1.73));
  const baseline = estimateBackground(m);
  baseline.forEach((v, i) => {
    assert.ok(Number.isFinite(v) && v <= m.y[i]);
    assert.ok(v >= 97);
  });
  assert.ok(baseline[1000] > baseline[100] + 25); // follows the broad background rise
  assert.equal(baseline[0], m.y[0]);
  assert.equal(baseline.at(-1), m.y.at(-1));
});

test('short, zero and negative inputs are safe and caching never alters or outlives replaced RAW', () => {
  for (const y of [[], [4], [-2, 1], [0, 0, 0], [-3, -1, -2]]) {
    const m = {x: y.map((_, i) => i), y};
    const bg = estimateBackground(m);
    assert.equal(bg.length, y.length);
    assert.notEqual(bg, y);
    bg.forEach((v, i) => assert.ok(Number.isFinite(v) && v <= y[i]));
    assert.equal(estimateBackground(m), bg);
    m.y = y.map(v => v + 10);
    const replaced = estimateBackground(m);
    assert.notEqual(replaced, bg);
    replaced.forEach((v, i) => assert.ok(Math.abs(v - bg[i] - 10) < 1e-9));
    m.x = m.x.slice();
    assert.notEqual(estimateBackground(m), replaced);
  }
});

test('100,000-point RAW uses bounded estimation and cached redraws', () => {
  const m = spectrum(100000);
  const start = performance.now(), bg = estimateBackground(m);
  const elapsed = performance.now() - start;
  assert.equal(bg.length, 100000);
  assert.ok(elapsed < 10000, `unbounded calculation: ${elapsed}ms`);
  for (let i = 0; i < 30; i++) assert.equal(estimateBackground(m), bg);
});
