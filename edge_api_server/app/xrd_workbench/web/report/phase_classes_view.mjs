import { escapeHTML } from '../shared/dom.mjs';
import { CLASS_NAMES, distinctPhases, labelOf, visibleClassSamples } from './phase_classes.mjs';

// Bulk selection starts at 주요상; existing rows pass their saved value explicitly.
// '' still means unclassified. Choosing an option alone does not apply a bulk change.
const classOptions = (value = 'major') =>
  [
    ['', '미분류 (검토 필요)'],
    ['trace', CLASS_NAMES.trace],
    ['presumed', CLASS_NAMES.presumed],
    ['major', CLASS_NAMES.major],
  ]
    .map(([k, v]) => `<option value="${k}" ${k === value ? 'selected' : ''}>${v}</option>`)
    .join('');
const phaseLabel = (p) =>
  escapeHTML(p.name) + (p.card ? ` <span class="hint">· ${escapeHTML(p.card)}</span>` : '');
function traceInfo(item) {
  const trace = item.trace;
  if (!trace) return '';
  if (trace.status === 'undecided')
    return `<span class="hint">규칙 판정 불가: ${escapeHTML(trace.reason)}</span>`;
  const ratios = trace.peaks.map((p) => `${p.role} ${p.ratio.toFixed(2)}%`).join(' / ');
  return `<span class="hint">${trace.status === 'trace' ? '규칙: 미량상 조건 충족' : '규칙: 조건 미충족'} (${escapeHTML(ratios)})</span>`;
}
// HTML of the 상 분류 tab: criteria text, the author's trace threshold, bulk choices and one
// section per included sample. `data` is the server's phase-classes response (phases, quantity
// token, optional rule verdict); `percent` is the text in the threshold box.
export function phaseClassesHTML({ pc, samples, data, percent, notice = '' }) {
  const names = new Map(samples.map((s) => [s.sample_id, s.display_name]));
  const evaluated = visibleClassSamples(data, samples);
  const distinct = distinctPhases(evaluated);
  const sections = evaluated
    .map((sample) => {
      const rows = sample.phases
        .map((item, j) => {
          const label = labelOf(pc, sample.sample_id, item.phase);
          const origin = label
            ? label.origin === 'rule'
              ? `규칙 (${label.percent}%)`
              : '직접'
            : '';
          return `<div class="pcRow"><span class="pcPhase">${phaseLabel(item.phase)}</span><span class="hint">${escapeHTML(item.quantity || '정량 값 없음')}</span><select data-pc-sample-id="${escapeHTML(sample.sample_id)}" data-pc-phase-index="${j}" aria-label="${escapeHTML(item.phase.name)} 분류">${classOptions(label?.class || '')}</select><span class="pcOrigin">${origin}</span>${traceInfo(item)}</div>`;
        })
        .join('');
      return `<div class="pcSample"><div class="subHeading">${escapeHTML(names.get(sample.sample_id))}</div><label class="pcBulk">이 시료의 모든 상<select data-pc-sample-all="${escapeHTML(sample.sample_id)}" aria-label="${escapeHTML(names.get(sample.sample_id))} 전체 분류"><option value="__none">선택…</option>${classOptions('')}</select></label>${rows || '<p class="hint">이 시료에는 상매칭 Report의 상이 없습니다.</p>'}</div>`;
    })
    .join('');
  const empty = evaluated.length
    ? ''
    : `<p class="hint">${escapeHTML(notice || '저장한 보고서의 상 목록을 불러오면 시료별 상이 표시됩니다. 먼저 저장하세요.')}</p>`;
  const retained = pc.retained.length
    ? `<div class="subHeading">보존된 이전 분류</div><p class="hint">분석값이 바뀌어 사라진 상의 분류입니다. 보고서에는 나오지 않으며 지우지 않고 보존합니다.</p>${pc.retained
        .map(
          (l) =>
            `<div class="pcRow"><span>${escapeHTML(names.get(l.sample_id) || l.sample_id)} · ${phaseLabel(l.phase)}</span><span class="hint">${CLASS_NAMES[l.class]}</span></div>`,
        )
        .join('')}`
    : '';
  const rule = pc.rule
    ? `<p class="hint">마지막으로 적용한 기준: 미량상 = 최강·차강 피크의 관측 Height가 시료 최대 Height의 ${pc.rule.trace_max_percent}% 이하</p>`
    : '';
  return (
    `<h1>상 분류</h1><p class="intro">시료별 상을 주요상, 미량상, 추정상으로 직접 분류합니다. 미지정 상은 미분류(검토 필요)로 보존됩니다. 상 이름·Card 번호·데이터베이스가 같은 경우만 연동합니다.</p>` +
    `<label>분류 기준 문구 (작성자가 직접 작성)<textarea rows="3" maxlength="1500" data-pc="criteria_text">${escapeHTML(pc.criteria_text)}</textarea></label>` +
    `<div class="subHeading">미량상 규칙 (선택 적용)</div><p class="hint">상의 d-I List 최강·차강 피크에 연결된 관측 Height를 시료 최대 Height와 비교합니다. WPPF 중량분율(wt%) 기준이 아닙니다. 적용 버튼을 눌러야 분류하며 판정 불가한 상은 미분류로 보존합니다. 직접 지정한 분류는 덮어쓰지 않습니다.</p>` +
    `<div class="figureActions"><label>기준 비율 (%)<input id="pcPercent" type="number" min="0" max="100" step="any" placeholder="예: 2" value="${escapeHTML(percent)}"></label><button id="pcApply">미량상 규칙 적용</button></div>${rule}` +
    `<div class="subHeading">같은 상을 모든 시료에 일괄 지정</div><div class="figureActions"><select id="pcRowPhase" aria-label="일괄 지정할 상">${distinct.map((p, k) => `<option value="${k}">${escapeHTML(p.name)}${p.card ? ' · ' + escapeHTML(p.card) : ''}</option>`).join('')}</select><select id="pcRowClass" aria-label="일괄 분류">${classOptions()}</select><button id="pcRowApply">모든 시료에 적용</button></div>` +
    empty +
    sections +
    retained
  );
}
