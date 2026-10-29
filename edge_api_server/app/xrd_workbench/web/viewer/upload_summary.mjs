import {escapeHTML} from '../shared/dom.mjs';

const extensions = {measurement:['txt'],phase_report:['pdf'],wppf_report:['pdf'],phase_image:['png','jpg','jpeg'],quantity_image:['png','jpg','jpeg']};

export function classifyUploadFilename(name) {
  const filename = String(name).replace(/\\/g, '/').split('/').pop().trim().normalize('NFC');
  const ext = filename.split('.').pop().toLowerCase(), stem = filename.slice(0, -ext.length - 1);
  const rules = [
    [/_정량(?:\(WPPF\))?_Report$/i, 'wppf_report'],
    [/_상매칭_Report$/i, 'phase_report'],
    [/_상매칭$/i, 'phase_image'],
    [/_정량$/i, 'quantity_image'],
  ];
  for (const [pattern, role] of rules) {
    if (extensions[role].includes(ext) && pattern.test(stem))
      return {sample:stem.replace(pattern, '').trim(), role};
  }
  const phase = stem.match(/^(.+?)[_\s-]+상\s*매칭(?:[_\s-]*이미지)?$/i);
  if (phase && extensions.phase_image.includes(ext)) return {sample:phase[1].trim(),role:'phase_image'};
  return {sample:stem, role:ext === 'txt' ? 'measurement' : 'ignore'};
}

export function summarizeUpload(files, mappings) {
  const groups = new Map(), names = new Set(), errors = [], ignoredImages = []; let ignored = 0;
  files.forEach(file => {
    const filename = file.name.normalize('NFC');
    if (names.has(filename)) errors.push(`${file.name}: 같은 파일명 중복`);
    names.add(filename);
    const m = mappings[file.name] || {}, name = (m.sample || '').normalize('NFC').trim();
    if (m.role === 'ignore') {
      ignored++;
      if (/\.(png|jpe?g)$/i.test(file.name)) ignoredImages.push(file.name);
      return;
    }
    if (!name || name.length > 200) {errors.push(`${file.name}: 시료명 확인 필요`); return;}
    if (!extensions[m.role]?.includes(file.name.split('.').pop().toLowerCase())) errors.push(`${file.name}: 역할·확장자 불일치`);
    if (!groups.has(name)) groups.set(name,{name, roles:Object.create(null), warnings:[]});
    const g = groups.get(name);
    (g.roles[m.role] ||= []).push(file.name);
  });
  for (const g of groups.values()) {
    for (const [role, files] of Object.entries(g.roles)) if (files.length > 1) errors.push(`${g.name}: ${role} 역할 중복 — 대표 파일을 선택하세요`);
    if (!g.roles.measurement) errors.push(`${g.name}: TXT 없음 — 이 시료는 그래프로 만들 수 없습니다`);
    if (!g.roles.phase_report) g.warnings.push('상매칭 PDF 없음 · 상 목록을 확인할 수 없음');
    if (!g.roles.phase_image) g.warnings.push('상매칭 이미지 미연결 · 보고서에는 뷰어 그래프가 표시됩니다. 원본 이미지의 시료명·역할을 확인하세요.');
  }
  if (files.length && !groups.size) errors.push('분석할 TXT 시료를 연결하세요.');
  return {groups:[...groups.values()],errors,ignored,ignoredImages};
}
export function uploadSummaryHTML(result) {
  const badge = (g,key,label,optional=false) => `<span class="uploadBadge ${g.roles[key] ? 'ready' : optional ? '' : 'missing'}">${escapeHTML(label)} ${g.roles[key] ? '✓' : optional ? '선택 없음' : '없음'}</span>`;
  return `<p><b>${result.groups.length}개 시료</b> · 제외 파일 ${result.ignored}개 · 아래는 파일 연결 확인이며 내용 검증은 서버 처리 후 완료됩니다.</p>` +
    ((result.ignoredImages || []).length ? `<p class="sourceWarning">보고서에 연결되지 않은 이미지: ${result.ignoredImages.map(escapeHTML).join(', ')}. 사용할 이미지는 아래에서 시료명과 ‘상매칭 이미지’ 역할을 지정하세요.</p>` : '') +
    result.groups.map(g=>`<section class="uploadSample"><b>${escapeHTML(g.name)}</b><div>${badge(g,'measurement','TXT')}${badge(g,'phase_report','상매칭 PDF')}${badge(g,'wppf_report','WPPF',true)}${badge(g,'phase_image','상매칭 이미지',true)}${badge(g,'quantity_image','정량 이미지',true)}</div>${g.warnings.map(w=>`<small>${escapeHTML(w)}</small>`).join('')}</section>`).join('') +
    (result.errors.length ? `<ul class="error">${result.errors.map(e=>`<li>${escapeHTML(e)}</li>`).join('')}</ul>` : '');
}
