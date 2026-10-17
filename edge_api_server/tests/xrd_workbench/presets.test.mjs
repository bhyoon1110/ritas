import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { applyPreset, presetOptionsHTML, settingsFromDoc } from '../../app/xrd_workbench/web/report/presets.mjs';
import { mergeDraftChanges } from '../../app/xrd_workbench/web/report/report_merge.mjs';

const cases = JSON.parse(readFileSync(new URL('./cases/preset_cases.json', import.meta.url)));

test('extracting and applying match the shared cases the Python side is tested against', () => {
  for (const c of cases) {
    if (c.document) assert.deepEqual(settingsFromDoc(c.document), c.settings, c.name);
    else {
      const target = structuredClone(c.target);
      applyPreset(target, c.apply);
      assert.deepEqual(target, c.expected, c.name);
    }
  }
});

test('the percent typed in the tab wins over the rule last applied, and bad text is ignored', () => {
  const doc = cases[0].document;
  assert.equal(settingsFromDoc(doc, '3.5').phase_classes.trace_percent, 3.5);
  assert.equal(settingsFromDoc(doc, '').phase_classes.trace_percent, 4);
  assert.equal(settingsFromDoc(doc, 'abc').phase_classes.trace_percent, 4);
  assert.equal(settingsFromDoc(doc, '101').phase_classes.trace_percent, 4);
  assert.equal(settingsFromDoc(cases[1].document, '').phase_classes.trace_percent, null);
});

test('settings never carry groups, labels, cover information or samples', () => {
  const taken = settingsFromDoc(cases[0].document);
  assert.deepEqual(Object.keys(taken).sort(), ['figure', 'phase_classes', 'quantity_table']);
  assert.ok(!('groups' in taken.figure) && !('labels' in taken.phase_classes));
});

test('applying works on a report saved before figures and tables existed', () => {
  const doc = { phase_classes: { criteria_text: '', rule: null, labels: [], retained: [] } };
  applyPreset(doc, cases[2].apply);
  assert.equal(doc.figure.spacing, 12.5);
  assert.deepEqual(doc.figure.groups, []);
  assert.deepEqual(doc.quantity_table, { include: false });
});

test('applying does not change the classes or the rule the author already applied', () => {
  const target = structuredClone(cases[2].target);
  applyPreset(target, cases[2].apply);
  assert.deepEqual(target.phase_classes.labels, cases[2].target.phase_classes.labels);
  assert.deepEqual(target.phase_classes.rule, cases[2].target.phase_classes.rule);
});

test('the preset list is escaped, marks the selection and has an empty state', () => {
  const html = presetOptionsHTML(
    [
      { id: 'a', name: '<b>설정</b>' },
      { id: 'b', name: '두 번째' },
    ],
    'b',
  );
  assert.ok(!html.includes('<b>설정</b>') && html.includes('&lt;b&gt;설정&lt;/b&gt;'));
  assert.ok(html.includes('<option value="b" selected>'));
  assert.ok(presetOptionsHTML([], '').includes('저장된 프리셋 없음'));
});

test('unsaved criteria text from a preset survives an analysis refresh', () => {
  const baseline = {
    metadata: {},
    llm: {},
    summary: '',
    samples: [{ sample_id: 's', notes: [], retained_notes: [] }],
    phase_classes: { criteria_text: '', rule: null, labels: [], retained: [] },
  };
  const current = structuredClone(baseline);
  applyPreset(current, cases[2].apply);
  const out = mergeDraftChanges(structuredClone(baseline), current, baseline);
  assert.equal(out.phase_classes.criteria_text, '새 기준');
});
