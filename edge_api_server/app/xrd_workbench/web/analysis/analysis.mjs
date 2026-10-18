import { apiUrl, request as api } from '../shared/api.mjs';
import { $, escapeHTML } from '../shared/dom.mjs';
import { buildSections, sectionHTML } from './sections.mjs';
const esc = escapeHTML,
  copy = (v) => structuredClone(v);
const reviewMain = document.querySelector('main'), reviewTools = document.createElement('div');
reviewTools.className = 'workTabs';
reviewTools.innerHTML = '<button id="reviewNavToggle" aria-expanded="true">시료·표 목록 접기</button><span class="compareLabel">원문 대조</span><button data-compare="both">나란히</button><button data-compare="data">분석 표</button><button data-compare="source">원문</button><span id="compareContext" role="status">표·행을 선택하면 원문 위치를 연결합니다.</span>';
reviewMain.parentNode.insertBefore(reviewTools,reviewMain);
function setCompare(view) {
  reviewMain.dataset.compare = view;
  reviewTools.querySelectorAll('[data-compare]').forEach(b=>b.setAttribute('aria-pressed',String(b.dataset.compare === view)));
}
function setReviewNav(open) {
  reviewMain.classList.toggle('reviewNavClosed',!open);
  $('reviewNavToggle').textContent = open ? '시료·표 목록 접기' : '시료·표 목록 열기';
  $('reviewNavToggle').setAttribute('aria-expanded',String(open));
}
reviewTools.onclick = e => {
  const b = e.target.closest('button');
  if (b?.dataset.compare) setCompare(b.dataset.compare);
  else if (b?.id === 'reviewNavToggle') setReviewNav(reviewMain.classList.contains('reviewNavClosed'));
};
setCompare(window.innerWidth < 900 ? 'data' : 'both');
setReviewNav(window.innerWidth >= 1100);
let mode = 'sections',
  sectionId = null;
let storageId = new URLSearchParams(location.search).get('project'),
  project,
  review,
  effective,
  headers,
  sourceId,
  sampleId,
  displayedSource,
  tableIndex = 0,
  page = 1,
  zoom = 1,
  selected = null,
  dirty = false,
  busy = false,
  imageToken = 0,
  imageUrl;
const sample = () => project.samples.find((s) => s.id === sampleId),
  record = () => sample()?.sources.find((r) => r.id === sourceId),
  report = () => sample()?.reports[record()?.role],
  table = () => report()?.raw_tables[tableIndex];
const entry = () => {
  review.tables[sourceId] ??= {};
  return (review.tables[sourceId][tableIndex] ??= {
    status: 'unreviewed',
    cells: [],
    excluded_rows: [],
  });
};
function status(text, error = false) {
  $('status').textContent = text;
  $('status').classList.toggle('error', error);
}
function changed() {
  dirty = true;
  $('revision').textContent = `검토 버전 ${review.revision} · 미저장 변경`;
  $('save').textContent = '변경 검토 저장';
}
function lock(value) {
  busy = value;
  $('dataFields').disabled = value;
  for (const id of [
    'save',
    'sampleSelect',
    'sourceSelect',
    'search',
    'quantityTab',
    'statusFilter',
    'kindFilter',
    'pageFilter',
  ])
    $(id).disabled = value;
  document.querySelectorAll('.tableButton').forEach((e) => (e.disabled = value));
}
function navigation() {
  window.dispatchEvent(new CustomEvent('xrd-shell', { detail: { project: storageId } }));
}
function label(t, i) {
  const kinds = [
    ...new Set(
      report()
        .logical_tables.filter(
          (l) => l.page === t.page && JSON.stringify(l.bbox) === JSON.stringify(t.bbox),
        )
        .map((l) => l.kind),
    ),
  ];
  return `p.${t.page} · 표 ${i + 1} · ${kinds.join(' / ') || '미인식 표'}`;
}
function renderTables() {
  const q = $('search').value.toLowerCase();
  $('tables').innerHTML =
    (report()?.raw_tables || [])
      .map((t, i) => ({ t, i, label: label(t, i) }))
      .filter((v) => (v.label + JSON.stringify(v.t.rows)).toLowerCase().includes(q))
      .filter((v) => !$('pageFilter').value || v.t.page === Number($('pageFilter').value))
      .filter(
        (v) =>
          !$('statusFilter').value ||
          (review.tables[sourceId]?.[v.i]?.status || 'unreviewed') === $('statusFilter').value,
      )
      .filter(
        (v) =>
          !$('kindFilter').value ||
          ($('kindFilter').value === 'unknown'
            ? v.label.endsWith('미인식 표')
            : report().logical_tables.some(
                (l) =>
                  l.kind === $('kindFilter').value &&
                  l.page === v.t.page &&
                  JSON.stringify(l.bbox) === JSON.stringify(v.t.bbox),
              )),
      )
      .map(
        ({ t, i, label }) =>
          `<button class="tableButton ${tableIndex === i ? 'active' : ''}" data-table="${i}" title="${esc(label)}"><span>${esc(label)}</span><small data-review-status="${review.tables[sourceId]?.[i]?.status || 'unreviewed'}">${t.rows.length}행 · ${esc({ reviewed: '검토 완료', excluded: '제외', unreviewed: '미검토' }[review.tables[sourceId]?.[i]?.status || 'unreviewed'])} · 수정 ${review.tables[sourceId]?.[i]?.cells?.length || 0}셀</small></button>`,
      )
      .join('') || '<p class="hint">표가 없습니다. 원문 페이지 텍스트를 확인하세요.</p>';
}
function renderEffective() {
  const s = effective.find((v) => v.id === sampleId);
  $('effective').innerHTML =
    `<b>저장된 적용값 · 버전 ${review.revision}</b><br>${s.phases.length}상 · 정량 ${s.analysis_review.quantity_source === 'manual' ? '직접 입력' : 'PDF'}<br>${s.quantities.map((q) => `${esc(q.phase)} ${esc(q.raw)} ${esc(q.unit)}`).join('<br>')}<br><small>미저장 수정은 위 결과에 반영되지 않습니다.</small>`;
}
function renderSample() {
  const s = sample();
  const pdfs = s.sources.filter((r) => r.role === 'phase_report' || r.role === 'wppf_report');
  $('sourceSelect').innerHTML = pdfs
    .map((r) => `<option value="${r.id}">${esc(r.filename)}</option>`)
    .join('');
  sourceId = pdfs[0]?.id;
  $('sourceSelect').value = sourceId || '';
  tableIndex = 0;
  selected = null;
  renderEffective();
  refresh();
}
// Two ways to look at a report: by its headings (merged across pages, read-only) or table by table
// as printed on each page (where corrections are made).
function refresh() {
  $('modeSections').classList.toggle('active', mode === 'sections');
  $('modeTables').classList.toggle('active', mode === 'tables');
  $('sectionList').hidden = mode !== 'sections';
  $('tableTools').hidden = mode !== 'tables';
  if (mode === 'sections') renderSections();
  else {
    $('sectionPane').hidden = true;
    $('dataFields').hidden = false;
    renderTable();
  }
}
function renderSections() {
  const sections = buildSections(report());
  if (!sections.some((s) => s.id === sectionId)) sectionId = sections[0]?.id || null;
  $('sectionList').innerHTML =
    sections
      .map(
        (s) =>
          `<button class="tableButton ${s.id === sectionId ? 'active' : ''}" data-section="${esc(s.id)}">${esc(s.title)}<small>${s.rows.length}행${s.pages.length ? ' · ' + s.pages.map((p) => 'p.' + p).join(', ') : ''}</small></button>`,
      )
      .join('') ||
    '<p class="hint">이 Report에서 인식한 소제목이 없습니다. 쪽별 표에서 원문을 확인하세요.</p>';
  $('quantityPane').hidden = true;
  $('dataFields').hidden = true;
  $('sectionPane').hidden = false;
  const current = sections.find((s) => s.id === sectionId);
  $('sectionTitle').textContent = current ? current.title : '인식한 소제목 없음';
  $('sectionGrid').innerHTML = current ? sectionHTML(current) : '';
  if (current?.pages.length) showPage(record(), current.pages[0], false);
  else showPage(record(), 1, false);
}
$('modeSections').onclick = () => {
  mode = 'sections';
  refresh();
};
$('modeTables').onclick = () => {
  mode = 'tables';
  refresh();
};
$('sectionList').onclick = (e) => {
  const b = e.target.closest('[data-section]');
  if (!b || busy) return;
  sectionId = b.dataset.section;
  renderSections();
};
function selectSourceRow(e) {
  const row = e.target.closest('[data-source-page]');
  if (row && Number(row.dataset.sourcePage)) {
    $('sectionGrid').querySelectorAll('[data-source-page]').forEach(r=>r.classList.toggle('selectedSource',r===row));
    if (window.innerWidth < 900) setCompare('source');
    showPage(record(), Number(row.dataset.sourcePage), false);
  }
}
$('sectionGrid').onclick = selectSourceRow;
$('sectionGrid').onkeydown = (e) => {
  const row = e.target.closest('[data-source-page]');
  if (row && e.key === 'Enter') selectSourceRow(e);
};
function renderTable() {
  selected = null;
  $('quantityPane').hidden = true;
  $('tablePane').hidden = false;
  renderTables();
  const t = table();
  $('tableTitle').textContent = t ? label(t, tableIndex) : '추출 표 없음';
  $('tableStatus').disabled = !t;
  $('tableStatus').value = t ? entry().status : 'unreviewed';
  $('grid').innerHTML = '';
  $('cellEditor').textContent = '셀을 선택하여 원문·수정값·사유를 확인하세요.';
  if (t) {
    const e = entry(),
      fixed = headers[sourceId]?.[tableIndex] || [];
    $('grid').innerHTML =
      '<table><thead><tr><th>행 제외</th>' +
      Array.from(
        { length: Math.max(...t.rows.map((r) => r.length)) },
        (_, i) => `<th>열 ${i + 1}</th>`,
      ).join('') +
      '</tr></thead><tbody>' +
      t.rows
        .map(
          (r, ri) =>
            `<tr class="${e.excluded_rows.includes(ri) ? 'excluded' : ''}"><td><input type="checkbox" data-exclude="${ri}" aria-label="${ri + 1}행 제외" ${e.excluded_rows.includes(ri) ? 'checked' : ''} ${fixed.includes(ri) ? 'disabled' : ''}></td>` +
            r
              .map((v, ci) => {
                const c = e.cells.find((c) => c.row === ri && c.col === ci);
                return `<td ${fixed.includes(ri) ? '' : `data-cell="${ri},${ci}" tabindex="0" role="button" aria-label="${ri + 1}행 ${ci + 1}열 셀"`} class="${c ? 'changed' : ''}" title="${fixed.includes(ri) ? '고정 헤더' : '원문과 수정 사유 보기'}">${esc(String(c ? (c.value ?? '') : (v ?? '')))}</td>`;
              })
              .join('') +
            '</tr>',
        )
        .join('') +
      '</tbody></table>';
    page = t.page;
  } else page = 1;
  showPage();
}
function renderCell() {
  if (!selected) return;
  const [row, col] = selected,
    t = table(),
    c = entry().cells.find((c) => c.row === row && c.col === col);
  document
    .querySelectorAll('[data-cell]')
    .forEach((td) => td.classList.toggle('selected', td.dataset.cell === selected.join(',')));
  $('cellEditor').innerHTML =
    `<b>${row + 1}행 · ${col + 1}열</b><div class="originalValue">원문: ${esc(String(t.rows[row][col] ?? '(빈 셀)'))}</div><label>검토값<textarea id="cellValue" aria-label="셀 검토값" maxlength="4000">${esc(String(c ? (c.value ?? '') : (t.rows[row][col] ?? '')))}</textarea></label><label>수정 사유<textarea id="cellReason" aria-label="셀 수정 사유" maxlength="500">${esc(c?.reason || '')}</textarea></label><div class="cellActions"><button id="applyCell">셀 수정 등록</button><button id="resetCell">원문 복원</button></div>`;
  $('applyCell').onclick = () => {
    const value = $('cellValue').value,
      reason = $('cellReason').value;
    if (!reason.trim()) {
      status('셀 수정 사유를 입력하세요.', true);
      return;
    }
    const e = entry();
    e.cells = e.cells.filter((c) => c.row !== row || c.col !== col);
    if (value !== String(t.rows[row][col] ?? '')) e.cells.push({ row, col, value, reason });
    changed();
    const keep = selected;
    renderTable();
    selected = keep;
    renderCell();
    status('셀 수정 등록 · 검토 완료 상태로 저장하면 적용됩니다.');
  };
  $('resetCell').onclick = () => {
    entry().cells = entry().cells.filter((c) => c.row !== row || c.col !== col);
    changed();
    const keep = selected;
    renderTable();
    selected = keep;
    renderCell();
  };
}
async function showPage(source = record(), number = page, highlight = true) {
  const token = ++imageToken;
  displayedSource = source;
  $('originalImage').removeAttribute('src');
  if (!source) {
    $('originalImage').removeAttribute('src');
    $('bbox').hidden = true;
    $('originalTitle').textContent = '연결된 원문 없음';
    $('originalTitle').title = '';
    $('originalLink').removeAttribute('href');
    $('pageText').textContent = '';
    $('pageNumber').value = '';
    $('pageCount').textContent = '/ 0';
    for (const id of ['pageNumber', 'prevPage', 'nextPage']) $(id).disabled = true;
    if (imageUrl) { URL.revokeObjectURL(imageUrl); imageUrl = null; }
    $('compareContext').textContent = '이 시료에는 연결된 원문 PDF가 없습니다.';
    return;
  }
  const pdf = ['phase_report', 'wppf_report'].includes(source.role);
  const count = pdf ? sample().reports[source.role].page_texts.length : 1;
  page = Math.max(1, Math.min(count, number));
  $('pageNumber').value = page;
  $('pageCount').textContent = `/ ${count}`;
  for (const id of ['pageNumber', 'prevPage', 'nextPage']) $(id).disabled = !pdf;
  $('originalTitle').textContent = source.filename;
  $('originalTitle').title = source.filename;
  $('compareContext').textContent = `${sample().name} · ${source.filename} · p.${page}${highlight ? ' · 선택 표 강조' : ''}`;
  $('originalLink').href = apiUrl(
    `/api/files/${storageId}/${source.id}${pdf ? '#page=' + page : ''}`,
  );
  $('bbox').hidden = true;
  const texts = pdf ? sample().reports[source.role].page_texts : [];
  $('pageText').textContent =
    typeof texts[page - 1] === 'string'
      ? texts[page - 1]
      : JSON.stringify(texts[page - 1] || '', null, 2);
  $('pageFrame').style.width = `${zoom * 100}%`;
  try {
    const response = await api(
        pdf
          ? `/api/analysis/${storageId}/pages/${source.id}/${page}`
          : `/api/files/${storageId}/${source.id}`,
      ),
      blob = await response.blob();
    if (token !== imageToken) return;
    const url = URL.createObjectURL(blob);
    if (imageUrl) URL.revokeObjectURL(imageUrl);
    imageUrl = url;
    $('originalImage').src = url;
    const t = table();
    if (pdf && highlight && source.id === sourceId && t?.page === page) {
      const [x0, y0, x1, y1] = t.bbox,
        w = Number(response.headers.get('X-PDF-Width')),
        h = Number(response.headers.get('X-PDF-Height'));
      Object.assign($('bbox').style, {
        left: `${(x0 / w) * 100}%`,
        top: `${(y0 / h) * 100}%`,
        width: `${((x1 - x0) / w) * 100}%`,
        height: `${((y1 - y0) / h) * 100}%`,
      });
      $('bbox').hidden = false;
    }
  } catch (e) {
    if (token === imageToken) status(e.message, true);
  }
}
function manualRows() {
  return (review.manual_quantities[sampleId] ??= []);
}
function renderQuantity() {
  selected = null;
  $('tablePane').hidden = true;
  $('quantityPane').hidden = false;
  $('quantitySource').value = review.quantity_source[sampleId] || 'pdf';
  const s = effective.find((v) => v.id === sampleId);
  $('pdfQuantities').innerHTML =
    '<b>PDF 추출·검토값 (별도 보존)</b><br>' +
    s.pdf_quantities.map((q) => `${esc(q.phase)} ${esc(q.raw)} ${esc(q.unit)}`).join('<br>');
  const sources = sample().sources.filter(
    (r) => r.role === 'wppf_report' || r.role === 'quantity_image',
  );
  $('manualRows').innerHTML = manualRows()
    .map(
      (q, i) =>
        `<div class="manualRow" data-manual="${i}"><div class="manualGrid">${[
          ['phase', '상 이름'],
          ['raw', '원문 숫자'],
          ['unit', '단위'],
          ['page', 'PDF 페이지'],
        ]
          .map(
            ([k, l]) =>
              `<label>${l}<input data-quantity="${k}" aria-label="${l} ${i + 1}" value="${esc(String(q[k] ?? ''))}" ${k === 'page' && sources.find((r) => r.id === q.source_id)?.role === 'quantity_image' ? 'disabled' : ''}></label>`,
          )
          .join(
            '',
          )}<label class="wide">출처<select data-quantity="source_id" aria-label="정량 출처 ${i + 1}">${sources.map((r) => `<option value="${r.id}" ${r.id === q.source_id ? 'selected' : ''}>${esc(r.filename)}</option>`).join('')}</select></label><label class="wide">입력 사유<textarea data-quantity="reason" aria-label="정량 입력 사유 ${i + 1}">${esc(q.reason)}</textarea></label></div><div class="manualActions"><label><input type="checkbox" data-quantity="reviewed" aria-label="정량 원문 확인 ${i + 1}" ${q.reviewed ? 'checked' : ''}>원문 확인</label><button data-quantity-view="${i}">출처 보기</button><button data-quantity-remove="${i}">입력 행 삭제</button></div></div>`,
    )
    .join('');
}
async function openProject(id) {
  if (busy) return;
  lock(true);
  status('원문과 검토 기록을 여는 중…');
  try {
    const value = await (await api(`/api/analysis/${encodeURIComponent(id)}`)).json();
    storageId = id;
    ({ project, review, effective_samples: effective, header_rows: headers } = value);
    dirty = false;
    $('save').textContent = '검토 저장';
    $('sampleSelect').innerHTML = project.samples
      .map((s) => `<option value="${s.id}">${esc(s.name)}</option>`)
      .join('');
    sampleId = project.samples[0].id;
    navigation();
    renderSample();
    $('revision').textContent = `검토 버전 ${review.revision}`;
    history.replaceState(null, '', apiUrl(`/analysis.html?project=${id}`));
    status('원본 옆에서 검토하세요. 검토 완료 표만 적용됩니다.');
  } catch (e) {
    status(e.message, true);
  } finally {
    lock(false);
  }
}
$('save').onclick = async () => {
  if (busy) return;
  lock(true);
  const id = storageId;
  try {
    const value = await (
      await api(`/api/analysis/${id}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ review, expected_revision: review.revision }),
      })
    ).json();
    review = value.review;
    effective = value.effective_samples;
    dirty = false;
    $('save').textContent = '검토 저장';
    $('revision').textContent = `검토 버전 ${review.revision}`;
    renderEffective();
    renderTables();
    localStorage.setItem(`xrd-analysis-updated-${id}`, String(review.revision));
    status('분석 검토를 저장했습니다. 변경된 시료의 보고서 문장을 다시 검토하세요.');
  } catch (e) {
    status(e.message, true);
  } finally {
    lock(false);
  }
};
$('sampleSelect').onchange = () => {
  sampleId = $('sampleSelect').value;
  renderSample();
};
$('sourceSelect').onchange = () => {
  sourceId = $('sourceSelect').value;
  tableIndex = 0;
  sectionId = null;
  refresh();
};
$('search').oninput = renderTables;
$('tables').onclick = (e) => {
  const b = e.target.closest('[data-table]');
  if (b && !busy) {
    tableIndex = Number(b.dataset.table);
    renderTable();
  }
};
$('grid').onclick = (e) => {
  const c = e.target.closest('[data-cell]');
  if (c && !busy) {
    selected = c.dataset.cell.split(',').map(Number);
    renderCell();
  }
};
$('grid').onchange = (e) => {
  if (e.target.dataset.exclude !== undefined) {
    const row = Number(e.target.dataset.exclude),
      v = entry();
    v.excluded_rows = e.target.checked
      ? [...new Set([...v.excluded_rows, row])]
      : v.excluded_rows.filter((r) => r !== row);
    changed();
    renderTable();
  }
};
$('tableStatus').onchange = () => {
  entry().status = $('tableStatus').value;
  changed();
  renderTables();
};
$('quantityTab').onclick = () => {
  $('sectionPane').hidden = true;
  $('dataFields').hidden = false;
  renderQuantity();
};
$('quantitySource').onchange = () => {
  review.quantity_source[sampleId] = $('quantitySource').value;
  changed();
};
$('addQuantity').onclick = () => {
  const s =
    sample().sources.find((r) => r.role === 'quantity_image') ||
    sample().sources.find((r) => r.role === 'wppf_report');
  if (!s) {
    status('이 시료의 정량 이미지 또는 WPPF PDF가 필요합니다.', true);
    return;
  }
  manualRows().push({
    phase: '',
    raw: '',
    unit: 'wt%',
    source_id: s.id,
    page: s.role === 'wppf_report' ? 1 : null,
    reason: '',
    reviewed: false,
  });
  changed();
  renderQuantity();
};
$('manualRows').oninput = (e) => {
  const row = e.target.closest('[data-manual]'),
    key = e.target.dataset.quantity;
  if (!row || !key) return;
  const q = manualRows()[Number(row.dataset.manual)];
  q[key] =
    key === 'reviewed'
      ? e.target.checked
      : key === 'page'
        ? e.target.value
          ? Number(e.target.value)
          : null
        : e.target.value;
  if (key === 'source_id') {
    q.page = sample().sources.find((r) => r.id === q.source_id)?.role === 'wppf_report' ? 1 : null;
    q.reviewed = false;
    renderQuantity();
  } else if (key !== 'reviewed') {
    q.reviewed = false;
    row.querySelector('[data-quantity="reviewed"]').checked = false;
  }
  changed();
};
$('manualRows').onclick = (e) => {
  const b = e.target.closest('button');
  if (!b) return;
  if (b.dataset.quantityRemove !== undefined) {
    manualRows().splice(Number(b.dataset.quantityRemove), 1);
    changed();
    renderQuantity();
  }
  if (b.dataset.quantityView !== undefined) {
    const q = manualRows()[Number(b.dataset.quantityView)];
    showPage(
      sample().sources.find((r) => r.id === q.source_id),
      q.page || 1,
      false,
    );
  }
};
$('prevPage').onclick = () => showPage(displayedSource || record(), page - 1, false);
$('nextPage').onclick = () => showPage(displayedSource || record(), page + 1, false);
$('pageNumber').onchange = () =>
  showPage(displayedSource || record(), Number($('pageNumber').value) || 1, false);
$('zoomIn').onclick = () => {
  zoom = Math.min(4, zoom + 0.25);
  $('pageFrame').style.width = `${zoom * 100}%`;
};
$('zoomOut').onclick = () => {
  zoom = Math.max(0.5, zoom - 0.25);
  $('pageFrame').style.width = `${zoom * 100}%`;
};
$('fit').onclick = () => {
  zoom = 1;
  $('pageFrame').style.width = '100%';
};
// Leaving this step (sidebar, project switch, next button) with an unsaved review asks first.
window.xrdCanReset = () => {
  if (!busy) return true;
  window.alert('분석 자료 저장·처리 중입니다. 완료된 뒤 초기화하세요.');
  return false;
};
window.xrdBeforeLeave = async () =>
  !dirty || confirm('저장하지 않은 검토가 있습니다. 저장하지 않고 이동할까요?');
$('nextStep').onclick = async () => {
  if (await window.xrdBeforeLeave())
    location.href = apiUrl(`/report.html?project=${encodeURIComponent(storageId)}`);
};
window.addEventListener('beforeunload', (e) => {
  if (dirty) {
    e.preventDefault();
    e.returnValue = '';
  }
});
async function start() {
  if (!storageId) {
    location.replace(apiUrl('/?step=upload&fresh=1'));
    return;
  }
  try {
    await openProject(storageId);
  } catch (e) {
    status(e.message, true);
  }
}
start();

$('grid').onkeydown = (e) => {
  if (['Enter', ' '].includes(e.key) && e.target.dataset.cell && !busy) {
    e.preventDefault();
    selected = e.target.dataset.cell.split(',').map(Number);
    renderCell();
    $('cellValue').focus();
  }
};

for (const id of ['statusFilter', 'kindFilter', 'pageFilter']) $(id).onchange = renderTables;
