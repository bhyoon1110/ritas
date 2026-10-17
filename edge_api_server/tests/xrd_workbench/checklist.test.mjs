import test from 'node:test';
import assert from 'node:assert/strict';
import { checklistHTML, nextSampleToCheck, precheckText } from '../../app/xrd_workbench/web/report/checklist.mjs';

const item = (level, code, text) => ({ level, code, text });
const data = {
  summary: { samples: 3, reviewed: 1, blockers: 1, warnings: 2, infos: 1 },
  report: [item('warning', 'figure_empty', '비교 그래프에 그릴 수 있는 시료가 없습니다.')],
  samples: [
    { sample_id: 'a', name: '<b>시료 A</b>', reviewed: true, items: [] },
    {
      sample_id: 'b',
      name: '시료 B',
      reviewed: false,
      items: [
        item('blocker', 'analysis_review_required', '분석값이 바뀌었습니다.'),
        item('info', 'no_notes', '특이사항이 없습니다.'),
      ],
    },
    {
      sample_id: 'c',
      name: '시료 C',
      reviewed: false,
      items: [item('warning', 'not_reviewed', '검토 완료를 선택하지 않았습니다.')],
    },
  ],
};

test('the tab shows the totals, report items, every sample and its items with level classes', () => {
  const html = checklistHTML({ data, notice: '', dirty: false });
  assert.ok(
    html.includes('검토 완료 1/3 시료') && html.includes('막힘 1') && html.includes('확인 2'),
  );
  assert.ok(html.includes('비교 그래프에 그릴 수 있는 시료가 없습니다.'));
  assert.ok(
    html.includes('class="checkItem blocker"') && html.includes('class="checkItem warning"'),
  );
  assert.ok(html.includes('class="checkItem info"'));
  assert.ok(html.includes('확인할 항목 없음'));
  assert.ok(html.includes('data-goto="b"') && html.includes('id="checkNext"'));
});

test('names and texts are escaped', () => {
  const html = checklistHTML({ data, notice: '', dirty: false });
  assert.ok(!html.includes('<b>시료 A</b>') && html.includes('&lt;b&gt;시료 A&lt;/b&gt;'));
});

test('unsaved changes and missing data are explained instead of hidden', () => {
  assert.ok(checklistHTML({ data, notice: '', dirty: true }).includes('저장한 버전 기준'));
  assert.ok(!checklistHTML({ data, notice: '', dirty: false }).includes('저장한 버전 기준'));
  const none = checklistHTML({ data: null, notice: '먼저 저장하세요.', dirty: false });
  assert.ok(none.includes('먼저 저장하세요.') && !none.includes('checkNext'));
});

test('the next sample to check skips clean ones and wraps around', () => {
  assert.equal(nextSampleToCheck(data, 'a'), 'b');
  assert.equal(nextSampleToCheck(data, 'b'), 'c');
  assert.equal(nextSampleToCheck(data, 'c'), 'b');
  assert.equal(nextSampleToCheck(data, null), 'b');
  const clean = { ...data, samples: [data.samples[0]] };
  assert.equal(nextSampleToCheck(clean, 'a'), null);
  const onlyInfo = {
    samples: [{ sample_id: 'x', items: [item('info', 'no_notes', 't')] }],
  };
  assert.equal(nextSampleToCheck(onlyInfo, null), null);
  assert.equal(nextSampleToCheck(null, 'a'), null);
});

test('the pre-export line counts blockers and warnings and says when nothing is left', () => {
  assert.equal(precheckText(data.summary), '출력 전 점검: 막힘 1 · 확인 2');
  assert.equal(
    precheckText({ samples: 1, reviewed: 1, blockers: 0, warnings: 0, infos: 3 }),
    '출력 전 점검: 확인할 항목 없음',
  );
  assert.equal(precheckText(null), '');
});

test('when the checklist cannot be loaded the line says so instead of staying blank or clean', () => {
  const text = precheckText(null, '분석 검토값이 변경되었습니다.');
  assert.ok(
    text.startsWith('출력 전 점검: 확인하지 못했습니다') &&
      text.includes('분석 검토값이 변경되었습니다.'),
  );
  assert.equal(precheckText(null, ''), '');
  assert.equal(precheckText(undefined), '');
  // A loaded summary always wins over an old notice.
  assert.equal(precheckText(data.summary, '오래된 안내'), '출력 전 점검: 막힘 1 · 확인 2');
});

test('with unsaved changes the pre-export line says it describes the saved version', () => {
  assert.equal(
    precheckText(data.summary, '', true),
    '출력 전 점검: 막힘 1 · 확인 2 (저장한 버전 기준)',
  );
  assert.equal(precheckText(data.summary, '', false), '출력 전 점검: 막힘 1 · 확인 2');
  assert.equal(precheckText(null, '', true), '');
});
