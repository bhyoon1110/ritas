import {escapeHTML} from '../shared/dom.mjs';
export function diagnosticsHTML(value){
  return '<h3>Raw 사전 진단</h3><p>TXT 전체를 검사한 결과입니다. PDF 내용 검증은 다음 분석 단계에서 진행합니다. 원본 값은 수정하지 않습니다.</p>'+
    value.files.map(f=>`<section class="uploadSample"><b>${escapeHTML(f.name)} · ${f.ok?'정상':'확인 필요'}</b>${f.ok?`<p>${f.points.toLocaleString()}점 · X ${f.x_range.join(' ~ ')} · 강도 ${f.y_range.join(' ~ ')} · 단위 ${escapeHTML(f.unit)} · 축 ${escapeHTML(f.axis)}</p><details><summary>처음 ${f.preview.length}개 측정점</summary><table><thead><tr><th>원본 행</th><th>X</th><th>Intensity</th></tr></thead><tbody>${f.preview.map(r=>`<tr><td>${r.line}</td><td>${r.x}</td><td>${r.y}</td></tr>`).join('')}</tbody></table></details>`:`<p class="error">${escapeHTML(f.error)}</p><p>${escapeHTML(f.hint)}</p>`}</section>`).join('')+
    (value.errors||[]).map(e=>`<p class="error">${escapeHTML(e)}</p>`).join('');
}
