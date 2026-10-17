import { escapeHTML } from '../shared/dom.mjs';

// HTML of the 시료 비교 그래프 tab. Pure: it only turns the figure settings, the report's samples,
// the server's similarity hints and the current checkbox selection into markup.
export function figureHTML({ figure, samples, hints, pick }) {
  const included = samples.filter((s) => s.included),
    names = new Map(samples.map((s) => [s.sample_id, s.display_name])),
    grouped = new Set(figure.groups.flatMap((g) => g.samples)),
    range = figure.x_range || ['', ''];
  const chips = (hints.identical || [])
    .map(
      (ids, i) =>
        `<button class="chip" data-figure-chip="${i}" title="${escapeHTML(ids.map((id) => names.get(id)).join(', '))}">상 구성 동일 ${ids.length}개</button>`,
    )
    .join('');
  const sampleRows = included
    .map((s) => {
      const note = hints.notes?.[s.sample_id],
        other =
          !grouped.has(s.sample_id) && figure.groups.length
            ? ' <span class="hint">(기타)</span>'
            : '';
      return `<label class="checkboxLabel figureSample"><input type="checkbox" data-figure-pick="${s.sample_id}" ${pick.has(s.sample_id) ? 'checked' : ''}>${escapeHTML(s.display_name)}${other}${note ? ` <span class="hint">${escapeHTML(note)}</span>` : ''}</label>`;
    })
    .join('');
  const groups = figure.groups
    .map(
      (g, i) =>
        `<div class="figureGroup"><input data-group-title="${i}" value="${escapeHTML(g.title)}" maxlength="120" aria-label="그룹 이름"><span class="hint">${escapeHTML(g.samples.map((id) => names.get(id)).join(', '))}</span><div><button data-group-set="${i}">선택한 시료로 바꾸기</button><button data-group-move="${i}" data-direction="-1" ${i === 0 ? 'disabled' : ''} aria-label="그룹 위로">↑</button><button data-group-move="${i}" data-direction="1" ${i === figure.groups.length - 1 ? 'disabled' : ''} aria-label="그룹 아래로">↓</button><button data-group-delete="${i}">그룹 삭제</button></div></div>`,
    )
    .join('');
  const checked = (value) => (value ? 'checked' : '');
  const selected = (value) => (value ? 'selected' : '');
  return (
    `<h1>시료 비교 그래프</h1><p class="intro">보고서에 포함한 시료의 패턴을 위아래로 쌓은 그림을 첫 장 다음에 넣습니다. 상 기호는 PDF가 관측·배정한 피크 위치에만 표시하며 새 배정은 하지 않습니다. 같은 상은 이름·Card 번호·데이터베이스가 모두 같을 때만 한 줄로 묶습니다.</p>` +
    `<label class="checkboxLabel"><input type="checkbox" data-figure="include" ${checked(figure.include)}>보고서에 그림 포함</label>` +
    `<div class="formGrid"><label>정렬 방식<select data-figure="order_mode"><option value="report" ${selected(figure.order_mode === 'report')}>보고서 시료 순서</option><option value="similarity" ${selected(figure.order_mode === 'similarity')}>상 구성 유사도순</option></select></label>` +
    `<label>Offset 간격 (비우면 자동)<input type="number" min="0" step="any" data-figure="spacing" value="${figure.spacing ?? ''}"></label>` +
    `<label>가로축 최소 (비우면 전체)<input type="number" step="any" data-figure="x_min" value="${range[0]}"></label>` +
    `<label>가로축 최대<input type="number" step="any" data-figure="x_max" value="${range[1]}"></label></div>` +
    `<div class="figureToggles"><label class="checkboxLabel"><input type="checkbox" data-figure="show_markers" ${checked(figure.show_markers)}>상 기호 표시</label><label class="checkboxLabel"><input type="checkbox" data-figure="show_reference" ${checked(figure.show_reference)}>기준 피크 표시</label></div>` +
    `<label>캡션<input data-figure="caption" maxlength="300" value="${escapeHTML(figure.caption)}"></label>` +
    `<div class="figureActions"><button id="figureImport">뷰어 보기 가져오기</button><button id="figureSort">유사한 시료끼리 정렬</button></div>` +
    `<p class="hint">정렬 버튼은 상 구성 유사도 순서를 보고서 시료 순서에 그대로 적용합니다. 이후 첫 장 화면의 위·아래 버튼으로 직접 고칠 수 있습니다.</p>` +
    `<div class="subHeading">그룹 선택</div><p class="hint">시료를 체크해서 그룹을 만들면 그룹마다 그림이 따로 나옵니다. 그룹에 넣지 않은 시료는 '기타' 그림에 들어갑니다. 그룹이 없으면 포함한 모든 시료가 한 그림입니다.</p>` +
    `<div class="figureChips">${chips}</div><div class="figureSamples">${sampleRows}</div>` +
    `<div class="figureActions"><input id="figureGroupName" maxlength="120" placeholder="그룹 이름" aria-label="새 그룹 이름"><button id="figureAddGroup">새 그룹으로 묶기</button></div>${groups}`
  );
}
