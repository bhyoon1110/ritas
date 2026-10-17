import { escapeHTML } from '../shared/dom.mjs';

const levelOf = (items) => items.some((i) => i.level === 'blocker' || i.level === 'warning');

// HTML of the 검토 현황 tab: totals, report-wide items and one block per sample in the report.
// The server computes the items (xrd.core.checklist); this only lays them out.
export function checklistHTML({ data, notice, dirty }) {
  const head =
    '<h1>검토 현황</h1><p class="intro">출력하기 전에 아직 확인이 필요한 시료를 모아 보여 줍니다. 값을 새로 만들거나 바꾸지 않으며, 검토 완료는 시료 화면에서 직접 선택합니다.</p>';
  if (!data)
    return (
      head +
      `<p class="hint">${escapeHTML(notice || '저장한 보고서의 점검 결과를 불러오면 표시됩니다. 먼저 저장하세요.')}</p>`
    );
  const s = data.summary;
  const line = `<div class="checkSummary"><b>검토 완료 ${s.reviewed}/${s.samples} 시료</b> · 막힘 ${s.blockers} · 확인 ${s.warnings} · 참고 ${s.infos}</div>`;
  const stale = dirty
    ? '<p class="hint">저장하지 않은 변경이 있습니다. 저장한 버전 기준의 점검입니다.</p>'
    : '';
  const items = (list) =>
    list
      .map((i) => `<div class="checkItem ${escapeHTML(i.level)}">${escapeHTML(i.text)}</div>`)
      .join('');
  const report = data.report.length
    ? `<div class="subHeading">보고서 전체</div>${items(data.report)}`
    : '';
  const rows = data.samples
    .map(
      (sample) =>
        `<div class="checkSample"><div class="checkSampleHead"><b>${escapeHTML(sample.name)}</b><span class="hint">${sample.reviewed ? '검토 완료' : '검토 완료 아님'}</span><button data-goto="${escapeHTML(sample.sample_id)}">이 시료로 이동</button></div>${items(sample.items) || '<div class="hint">확인할 항목 없음</div>'}</div>`,
    )
    .join('');
  return (
    head +
    line +
    stale +
    '<div class="figureActions"><button id="checkNext">다음 확인 필요 시료로 이동</button></div>' +
    report +
    rows
  );
}

// The sample after `currentId` (wrapping around) that has a blocker or a warning; null if none.
export function nextSampleToCheck(data, currentId) {
  const samples = data?.samples || [];
  const start = samples.findIndex((s) => s.sample_id === currentId);
  for (let step = 1; step <= samples.length; step++) {
    const sample = samples[(start + step + samples.length) % samples.length];
    if (levelOf(sample.items)) return sample.sample_id;
  }
  return null;
}

export function precheckText(summary, notice = '', unsaved = false) {
  if (!summary) return notice ? `출력 전 점검: 확인하지 못했습니다. ${notice}` : '';
  const base =
    summary.blockers || summary.warnings
      ? `출력 전 점검: 막힘 ${summary.blockers} · 확인 ${summary.warnings}`
      : '출력 전 점검: 확인할 항목 없음';
  return unsaved ? `${base} (저장한 버전 기준)` : base;
}
