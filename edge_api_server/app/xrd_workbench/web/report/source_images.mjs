import {escapeHTML} from '../shared/dom.mjs';

export function phaseImageChoices(project, sample) {
  const others=new Set(project.samples.filter(s=>s.id!==sample.id).flatMap(s=>s.sources.map(r=>r.id)));
  const records=new Map(sample.sources.filter(r=>r.role==='phase_image').map(r=>[r.id,r]));
  for(const r of project.sources || []) {
    if(!others.has(r.id) && ['phase_image','ignore'].includes(r.role) && /\.(png|jpe?g)$/i.test(r.filename) && !records.has(r.id)) records.set(r.id,r);
  }
  return [...records.values()];
}

export function phaseImageHTML(project, sample, entry, sourceHref) {
  const choices=phaseImageChoices(project,sample), selected=entry.phase_image_source_id || '';
  const original=sample.sources.find(r=>r.role==='phase_image');
  const record=selected ? choices.find(r=>r.id===selected) : original;
  return `<div class="subHeading">원본 상매칭 이미지</div><label>보고서에 사용할 상매칭 이미지<select data-field="phase_image_source_id" aria-label="보고서에 사용할 상매칭 이미지"><option value="">${escapeHTML(original ? '업로드 연결 사용: '+original.filename : '업로드 연결 없음 · 뷰어 그래프 사용')}</option>${choices.map(r=>`<option value="${escapeHTML(r.id)}" ${r.id===selected?'selected':''}>${escapeHTML(r.filename)}</option>`).join('')}</select></label>` +
    (record ? `<p class="hint">사용할 원본: ${escapeHTML(record.filename)} · 상 동정 결과의 상단 그래프에 반영됩니다.</p><img class="assetPreview" src="${escapeHTML(sourceHref(record))}" alt="상매칭 원본 ${escapeHTML(record.filename)}"><label>캡션<input data-field="phase_caption" value="${escapeHTML(entry.phase_caption)}" maxlength="300" aria-label="상매칭 이미지 캡션"></label>` :
    `<p class="sourceWarning">${selected ? '선택한 원본을 찾지 못했습니다. 다시 선택하세요.' : '연결된 원본이 없어 뷰어 그래프가 표시됩니다. 보존된 미연결 이미지가 있으면 위에서 선택하세요.'}</p>`) +
    '<p class="hint">원본 파일과 분석값은 유지됩니다. PPT 자유편집에서 그림을 교체하거나 숨긴 경우 해당 개체 편집도 확인하세요.</p>';
}
