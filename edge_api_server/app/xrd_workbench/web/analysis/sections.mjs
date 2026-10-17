// Report contents grouped by their headings instead of by PDF page: the phase list, one Peak
// List merged across pages, a d-I List per phase and the WPPF quantities. Built only from what the
// parser read (original tokens); this is for checking recognition, not for editing.
import { escapeHTML } from '../shared/dom.mjs';

const pagesOf = (rows) =>
  [...new Set(rows.map((r) => r.page).filter(Number.isInteger))].sort((a, b) => a - b);
const pagesText = (pages) => pages.map((p) => `p.${p}`).join(', ');

function section(id, title, columns, rows, extra = '') {
  const pages = pagesOf(rows);
  const merged =
    pages.length > 1 ? `${pages.length}개 쪽(${pagesText(pages)})을 하나로 합쳤습니다. ` : '';
  return {
    id,
    title,
    columns,
    rows,
    pages,
    note: `${rows.length}행. ${merged}${extra}`.trim(),
  };
}

export function buildSections(report) {
  const sections = [];
  const phases = report?.phases || [];
  if (phases.length)
    sections.push(
      section(
        'phases',
        '상 목록',
        ['상 이름', 'Chemical formula', 'DB', 'Card', 'FOM'],
        phases.map((p) => ({
          page: p.page,
          cells: [p.name, p.formula || '', p.db || '', p.card || '', p.fom_raw || ''],
        })),
      ),
    );
  const observed = [...(report?.observed_peaks || [])].sort((a, b) => a.no - b.no);
  if (observed.length) {
    const assignments = report.assignments || [];
    let unassigned = 0;
    const rows = observed.map((p) => {
      const row = assignments.find((a) => a.no === p.no && a.x === p.x);
      const text =
        row && row.matches.length
          ? row.phase_raw || row.matches.map((m) => `${m.phase}: ${m.hkl}`).join(', ')
          : '';
      if (!text) unassigned += 1;
      return {
        page: p.page,
        cells: [String(p.no), p.x_raw ?? String(p.x), p.height_raw ?? '', text || '배정 없음'],
      };
    });
    sections.push(
      section(
        'peaklist',
        'Peak List (통합)',
        ['No.', '2θ, °', 'Height, cps', '배정 (상: hkl)'],
        rows,
        unassigned ? `배정 없는 피크 ${unassigned}개.` : '',
      ),
    );
  }
  const reference = report?.reference_peaks || [];
  const names = [...new Set([...phases.map((p) => p.name), ...reference.map((r) => r.phase)])];
  for (const name of names) {
    const rows = reference
      .filter((r) => r.phase === name)
      .map((r) => ({
        page: r.page,
        cells: [String(r.no), r.x_raw ?? '', r.d_raw ?? '', r.hkl_raw ?? '', r.intensity_raw ?? ''],
      }));
    if (!rows.length && !phases.length) continue;
    const s = section(
      `di:${name}`,
      `d-I List · ${name}`,
      ['No.', '2θ, °', 'd, Å', 'h k l', 'Norm. I.'],
      rows,
    );
    if (!rows.length)
      s.note = '이 상의 d-I 행이 없습니다. 원본 Report의 d-I List 제목을 확인하세요.';
    sections.push(s);
  }
  const quantities = report?.quantities || [];
  if (quantities.length)
    sections.push(
      section(
        'wppf',
        '정량 (WPPF)',
        ['상', '값', '단위'],
        quantities.map((q) => ({ page: q.page, cells: [q.phase, q.raw ?? '', q.unit ?? ''] })),
      ),
    );
  return sections;
}

export function sectionHTML(s) {
  const head = s.columns.map((c) => `<th>${escapeHTML(c)}</th>`).join('');
  const body = s.rows
    .map(
      (r) =>
        `<tr data-source-page="${escapeHTML(String(r.page ?? ''))}" tabindex="0" title="원본 p.${escapeHTML(String(r.page ?? '?'))} 보기">${r.cells.map((c) => `<td>${escapeHTML(c)}</td>`).join('')}<td class="pageCell">p.${escapeHTML(String(r.page ?? '?'))}</td></tr>`,
    )
    .join('');
  return `<p class="hint">${escapeHTML(s.note)}</p><table><thead><tr>${head}<th>쪽</th></tr></thead><tbody>${body}</tbody></table>`;
}
