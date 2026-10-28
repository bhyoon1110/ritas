import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import {
  applyTrace,
  defaultPhaseClasses,
  distinctPhases,
  ensurePhaseClasses,
  labelOf,
  setLabel,
  setRowLabel,
  setSampleLabel,
  visibleClassSamples,
} from '../../app/xrd_workbench/web/report/phase_classes.mjs';
import { phaseClassesHTML } from '../../app/xrd_workbench/web/report/phase_classes_view.mjs';
import { mergeDraftChanges } from '../../app/xrd_workbench/web/report/report_merge.mjs';

const A = { name: 'PhaseA', card: 'c1', db: 'DB' };
const A2 = { name: 'PhaseA', card: 'c2', db: 'DB' };
const B = { name: 'PhaseB', card: 'c1', db: 'DB' };

test('a report without a phase_classes block opens unclassified', () => {
  const doc = {};
  assert.deepEqual(ensurePhaseClasses(doc), defaultPhaseClasses());
  assert.deepEqual(doc.phase_classes.labels, []);
});

test('setLabel makes a direct choice, changes it, and removes it for explicit 미분류', () => {
  const pc = defaultPhaseClasses();
  setLabel(pc, 's1', A, 'major');
  assert.deepEqual(pc.labels, [{ sample_id: 's1', phase: A, class: 'major', origin: 'manual' }]);
  setLabel(pc, 's1', A, 'presumed');
  assert.equal(pc.labels.length, 1);
  assert.equal(labelOf(pc, 's1', A).class, 'presumed');
  setLabel(pc, 's1', A, '');
  assert.deepEqual(pc.labels, []);
  assert.throws(() => setLabel(pc, 's1', A, 'unknown'), /알 수 없는/);
});

test('trace rule can update default major without erasing other defaults or direct choices', () => {
  const pc = defaultPhaseClasses();
  pc.labels = [A, B, A2].map(phase => ({sample_id:'s1', phase, class:'major', origin:'default'}));
  setLabel(pc, 's1', A2, 'major');
  applyTrace(pc, [{sample_id:'s1', phases:[
    {phase:A,status:'trace'}, {phase:B,status:'undecided'}, {phase:A2,status:'trace'},
  ]}], 2);
  assert.equal(labelOf(pc,'s1',A).class,'trace');
  assert.equal(labelOf(pc,'s1',B).origin,'default');
  assert.equal(labelOf(pc,'s1',A2).class,'major');
});

test('a direct choice replaces a rule label and becomes direct', () => {
  const pc = defaultPhaseClasses();
  pc.labels.push({ sample_id: 's1', phase: A, class: 'trace', origin: 'rule' });
  setLabel(pc, 's1', A, 'major');
  assert.equal(labelOf(pc, 's1', A).origin, 'manual');
});

test('same name with another card or database is another phase', () => {
  const pc = defaultPhaseClasses();
  setLabel(pc, 's1', A, 'major');
  assert.equal(labelOf(pc, 's1', A2), null);
  assert.equal(labelOf(pc, 's1', { ...A, db: 'other' }), null);
});

test('row and sample bulk choices cover exactly the listed samples and phases', () => {
  const pc = defaultPhaseClasses();
  setRowLabel(pc, A, ['s1', 's2'], 'trace');
  setSampleLabel(pc, 's3', [A, B], 'major');
  assert.deepEqual(
    pc.labels.map((l) => [l.sample_id, l.phase.name, l.class]),
    [
      ['s1', 'PhaseA', 'trace'],
      ['s2', 'PhaseA', 'trace'],
      ['s3', 'PhaseA', 'major'],
      ['s3', 'PhaseB', 'major'],
    ],
  );
});

test('applyTrace matches the shared cases that the Python rule is tested against', () => {
  const cases = JSON.parse(
    readFileSync(new URL('./cases/phase_class_cases.json', import.meta.url)),
  );
  for (const c of cases) {
    const pc = { ...defaultPhaseClasses(), labels: structuredClone(c.labels) };
    applyTrace(pc, c.evaluation, c.percent);
    assert.deepEqual(pc.labels, c.expected.labels, c.name);
    assert.deepEqual(pc.rule, c.expected.rule, c.name);
  }
});

test('applyTrace reads the verdict from the API shape and rejects a bad percent or no verdict', () => {
  const pc = defaultPhaseClasses();
  const api = [
    { sample_id: 's1', phases: [{ phase: A, quantity: null, trace: { status: 'trace' } }] },
  ];
  applyTrace(pc, api, 2);
  assert.equal(labelOf(pc, 's1', A).class, 'trace');
  for (const bad of [-1, 101, NaN, '2', null])
    assert.throws(() => applyTrace(pc, api, bad), /0 이상 100 이하/);
  assert.throws(
    () => applyTrace(pc, [{ sample_id: 's1', phases: [{ phase: A, trace: null }] }], 2),
    /판정 결과/,
  );
});

test('distinctPhases keeps first-appearance order and separates cards', () => {
  const samples = [
    { sample_id: 's1', phases: [{ phase: A }, { phase: B }] },
    { sample_id: 's2', phases: [{ phase: B }, { phase: A2 }] },
  ];
  assert.deepEqual(distinctPhases(samples), [A, B, A2]);
});

const samples = [{ sample_id: 's1', display_name: '<b>S1</b>', included: true }];
const data = {
  samples: [
    {
      sample_id: 's1',
      phases: [
        { phase: { ...A, name: '<i>A</i>' }, quantity: '12.34(5) wt%', trace: null },
        {
          phase: B,
          quantity: null,
          trace: { status: 'undecided', reason: '동점 (두 번째로 강한 피크가 둘 이상)', peaks: [] },
        },
      ],
    },
  ],
};

test('the tab shows quantity tokens, reasons, origins and escapes every name', () => {
  const pc = defaultPhaseClasses();
  setLabel(pc, 's1', B, 'presumed');
  pc.retained.push({
    sample_id: 's1',
    phase: { name: 'Gone', card: '', db: '' },
    class: 'trace',
    origin: 'rule',
  });
  const html = phaseClassesHTML({ pc, samples, data, percent: '2' });
  assert.ok(!html.includes('<b>S1</b>') && html.includes('&lt;b&gt;S1&lt;/b&gt;'));
  assert.ok(!html.includes('<i>A</i>') && html.includes('&lt;i&gt;A&lt;/i&gt;'));
  assert.ok(html.includes('12.34(5) wt%') && html.includes('정량 값 없음'));
  assert.ok(html.includes('규칙 판정 불가: 동점'));
  assert.ok(html.includes('<option value="presumed" selected>'));
  assert.ok(html.includes('직접') && html.includes('보존된 이전 분류') && html.includes('Gone'));
  assert.ok(html.includes('placeholder="예: 2"') && html.includes('value="2"'));
});

test('without evaluation data the tab still renders the criteria box and threshold input', () => {
  const html = phaseClassesHTML({ pc: defaultPhaseClasses(), samples, data: null, percent: '' });
  assert.ok(html.includes('data-pc="criteria_text"') && html.includes('id="pcApply"'));
});

test('bulk selector defaults to 주요상 with no fixed suffix, without mutating stored classifications', () => {
  const pc = defaultPhaseClasses();
  setLabel(pc, 's1', B, 'presumed');
  const before = structuredClone(pc);
  for (const evaluated of [null, data]) {
    const html = phaseClassesHTML({pc, samples, data: evaluated, percent: ''});
    const bulk = html.match(/<select id="pcRowClass"[^>]*>([\s\S]*?)<\/select>/)[1];
    assert.ok(bulk.includes('<option value="major" selected>주요상</option>'));
    assert.ok(!html.includes('(고정)'));
    assert.ok(!bulk.includes('<option value="" selected>'));
    if(evaluated) {
      const sampleBulk=html.match(/<select data-pc-sample-all="[^"]*"[^>]*>([\s\S]*?)<\/select>/)[1];
      assert.ok(sampleBulk.includes('<option value="major" selected>주요상</option>'));
      assert.ok(html.includes('data-pc-sample-apply="s1"'));
    }
    assert.deepEqual(pc, before);
  }
});

test('unsaved classification edits survive an analysis refresh', () => {
  const sample = { sample_id: 's1', notes: [], retained_notes: [] };
  const baseline = {
    metadata: {},
    llm: {},
    summary: '',
    samples: [sample],
    phase_classes: defaultPhaseClasses(),
  };
  const current = structuredClone(baseline);
  setLabel(current.phase_classes, 's1', A, 'major');
  const out = mergeDraftChanges(structuredClone(baseline), current, baseline);
  assert.equal(out.phase_classes.labels.length, 1);
  const latest = structuredClone(baseline);
  latest.phase_classes.criteria_text = 'server';
  assert.equal(
    mergeDraftChanges(latest, structuredClone(baseline), baseline).phase_classes.criteria_text,
    'server',
  );
});

test('a direct choice drops the percent of a rule label; the rule stores it on each label', () => {
  const pc = defaultPhaseClasses();
  applyTrace(pc, [{ sample_id: 's1', phases: [{ phase: A, status: 'trace' }] }], 3);
  assert.equal(labelOf(pc, 's1', A).percent, 3);
  setLabel(pc, 's1', A, 'major');
  assert.equal('percent' in labelOf(pc, 's1', A), false);
});

test('samples the saved report evaluated but the editor excluded are not offered', () => {
  const data = {
    samples: [
      { sample_id: 'a', phases: [{ phase: A }] },
      { sample_id: 'b', phases: [{ phase: B }] },
    ],
  };
  const visible = visibleClassSamples(data, [
    { sample_id: 'a', included: false },
    { sample_id: 'b', included: true },
  ]);
  assert.deepEqual(
    visible.map((s) => s.sample_id),
    ['b'],
  );
  assert.deepEqual(visibleClassSamples(null, []), []);
});

test('the tab addresses selects by sample id so excluded samples cannot shift them', () => {
  const data = {
    samples: [
      { sample_id: 'a', phases: [{ phase: A, quantity: null, trace: null }] },
      { sample_id: 'b', phases: [{ phase: B, quantity: null, trace: null }] },
    ],
  };
  const html = phaseClassesHTML({
    pc: defaultPhaseClasses(),
    samples: [
      { sample_id: 'a', display_name: 'A', included: false },
      { sample_id: 'b', display_name: 'B', included: true },
    ],
    data,
    percent: '',
  });
  assert.ok(html.includes('data-pc-sample-id="b"') && html.includes('data-pc-phase-index="0"'));
  assert.ok(!html.includes('data-pc-sample-id="a"'));
});

test('without phase data the tab says why instead of staying silent', () => {
  const html = phaseClassesHTML({
    pc: defaultPhaseClasses(),
    samples,
    data: null,
    percent: '',
    notice: '저장한 뒤 상 목록을 불러옵니다.',
  });
  assert.ok(html.includes('저장한 뒤 상 목록을 불러옵니다.'));
});
