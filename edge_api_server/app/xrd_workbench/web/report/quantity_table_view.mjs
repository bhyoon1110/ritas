import { escapeHTML } from '../shared/dom.mjs';

// Reports saved before the quantity table existed have no block: they open with the table off.
export function ensureQuantityTable(doc) {
  if (!doc.quantity_table) doc.quantity_table = { include: false };
  return doc.quantity_table;
}

// A column header opens the viewer with that phase highlighted. The quantity report has no card
// or database, so the link highlights every phase with this name.
function columnName(name, storageId) {
  if (!storageId) return escapeHTML(name);
  const href = `/?project=${encodeURIComponent(storageId)}&phase=${encodeURIComponent(name)}`;
  return `<a href="${escapeHTML(href)}" target="_blank" rel="noopener" title="뷰어에서 이 상을 강조해서 열기">${escapeHTML(name)}</a>`;
}

// HTML of the 정량 요약 표 tab: the include switch and a read-only copy of the table the report
// prints (the server computes it with the same function the page layout uses).
export function quantityTableHTML({ include, table, notice, storageId = '' }) {
  const head = `<h1>정량 요약 표</h1><p class="intro">시료 × 상 정량을 한 표로 모아 비교 그래프 다음 쪽에 넣습니다. 값은 각 시료의 정량 자료에 적힌 원문 표기 그대로이며, 값이 없을 때는 이유(정량 자료 없음, 미기재, 확인 필요)를 표시합니다. 0이나 합계로 채우지 않습니다.</p><label class="checkboxLabel"><input type="checkbox" data-qt="include" ${include ? 'checked' : ''}>보고서에 정량 요약 표 포함</label>`;
  if (!table)
    return (
      head +
      `<p class="hint">${escapeHTML(notice || '저장한 보고서의 표를 불러오면 미리 볼 수 있습니다. 먼저 저장하세요.')}</p>`
    );
  if (!table.columns.length) return head + `<p class="hint">${escapeHTML(table.empty_message)}</p>`;
  const header = table.columns
    .map(
      (c) =>
        `<th>${columnName(c.name, storageId)}${c.class_note ? `<br><span class="hint">${escapeHTML(c.class_note)}</span>` : ''}</th>`,
    )
    .join('');
  const rows = table.rows
    .map(
      (r) =>
        `<tr><td>${escapeHTML(r.name)}</td>${r.cells.map((c) => `<td class="qtCell ${c.kind}">${escapeHTML(c.text)}</td>`).join('')}</tr>`,
    )
    .join('');
  const notes = table.footnotes.map((f) => `<p class="hint">${escapeHTML(f)}</p>`).join('');
  return (
    head +
    `<div class="qtScroll"><table class="qtTable"><thead><tr><th>시료</th>${header}</tr></thead><tbody>${rows}</tbody></table></div>${notes}`
  );
}
