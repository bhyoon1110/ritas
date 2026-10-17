import test from 'node:test';
import assert from 'node:assert/strict';
import { ensureQuantityTable, quantityTableHTML } from '../../app/xrd_workbench/web/report/quantity_table_view.mjs';
import { mergeDraftChanges } from '../../app/xrd_workbench/web/report/report_merge.mjs';

const table = {
  columns: [
    { name: '<i>PhaseA</i>', class_note: '(주요상)' },
    { name: 'PhaseB', class_note: '' },
  ],
  rows: [
    {
      sample_id: 'a',
      name: '<b>시료 A</b>',
      cells: [
        { text: '12.34(5) wt% †', kind: 'value', manual: true },
        { text: '미기재', kind: 'missing', manual: false },
      ],
    },
    {
      sample_id: 'b',
      name: '시료 B',
      cells: [
        { text: '정량 자료 없음', kind: 'none', manual: false },
        { text: '', kind: 'blank', manual: false },
      ],
    },
  ],
  footnotes: ['† 직접 입력한 값 (출처는 시료 쪽 참조)', '값은 원문 표기입니다.'],
  empty_message: '',
};

test('a report saved before the table block opens with the table off', () => {
  const doc = {};
  assert.deepEqual(ensureQuantityTable(doc), { include: false });
  assert.deepEqual(doc.quantity_table, { include: false });
});

test('the tab shows the include switch, the original tokens, states and footnotes', () => {
  const html = quantityTableHTML({ include: true, table, notice: '' });
  assert.ok(html.includes('data-qt="include"') && html.includes('checked'));
  assert.ok(
    html.includes('12.34(5) wt% †') && html.includes('미기재') && html.includes('정량 자료 없음'),
  );
  assert.ok(html.includes('(주요상)') && html.includes('원문 표기'));
  assert.ok(html.includes('qtCell missing') && html.includes('qtCell none'));
});

test('every name and token is escaped', () => {
  const html = quantityTableHTML({ include: false, table, notice: '' });
  assert.ok(!html.includes('<b>시료 A</b>') && html.includes('&lt;b&gt;시료 A&lt;/b&gt;'));
  assert.ok(!html.includes('<i>PhaseA</i>') && html.includes('&lt;i&gt;PhaseA&lt;/i&gt;'));
  assert.ok(!html.includes('checked'));
});

test('without a table the tab explains why, and an empty table shows its message', () => {
  assert.ok(
    quantityTableHTML({ include: true, table: null, notice: '먼저 저장하세요.' }).includes(
      '먼저 저장하세요.',
    ),
  );
  const empty = {
    ...table,
    columns: [],
    rows: [],
    footnotes: [],
    empty_message: '정량 자료가 등록된 시료가 없습니다.',
  };
  assert.ok(
    quantityTableHTML({ include: true, table: empty, notice: '' }).includes(
      '등록된 시료가 없습니다',
    ),
  );
});

test('an unsaved table switch survives an analysis refresh', () => {
  const baseline = {
    metadata: {},
    llm: {},
    summary: '',
    samples: [{ sample_id: 's', notes: [], retained_notes: [] }],
    quantity_table: { include: true },
  };
  const current = structuredClone(baseline);
  current.quantity_table.include = false;
  const out = mergeDraftChanges(structuredClone(baseline), current, baseline);
  assert.equal(out.quantity_table.include, false);
  const latest = structuredClone(baseline);
  assert.equal(
    mergeDraftChanges(latest, structuredClone(baseline), baseline).quantity_table.include,
    true,
  );
});

test('a phase column header links to the viewer with that phase highlighted by name', () => {
  const html = quantityTableHTML({ include: true, table, notice: '', storageId: 'abc 123' });
  assert.ok(
    html.includes('href="/?project=abc%20123&amp;phase=%3Ci%3EPhaseA%3C%2Fi%3E"') ||
      html.includes('href="/?project=abc%20123&phase=%3Ci%3EPhaseA%3C%2Fi%3E"'),
  );
  assert.ok(html.includes('target="_blank"') && html.includes('rel="noopener"'));
  assert.ok(html.includes('phase=PhaseB'));
});

test('without a project id the header stays plain text', () => {
  const html = quantityTableHTML({ include: true, table, notice: '' });
  assert.ok(!html.includes('href='));
});
