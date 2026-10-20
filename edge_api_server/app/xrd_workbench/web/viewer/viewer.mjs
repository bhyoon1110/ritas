import {
  createState,
  restoreState,
  applyViewerLink,
  buildFigure,
  phaseMarkerHint,
  exportFigure,
  maxValue,
  minValue,
  viewWarnings,
  viewerStorageKey,
  legendHTML,
  spacingLimit,
  phaseGroups,
  phaseStyles,
  groupCaption,
  groupHidden,
  setGroupHidden,
  setGroupColor,
  setGroupSymbol,
  SYMBOL_CHOICES,
  SYMBOL_GLYPHS,
  phaseMatches,
  phaseRef,
  sourceHref,
} from './model.mjs';
import { apiUrl, request, chunkBytes, waitJob } from '../shared/api.mjs';
import { $, escapeHTML } from '../shared/dom.mjs';
import { classifyUploadFilename, summarizeUpload, uploadSummaryHTML } from './upload_summary.mjs';
import { transferUpload } from '../shared/resumable_upload.mjs';
import { diagnosticsHTML } from './raw_diagnostics.mjs';

const offline = !!window.XRD_EMBEDDED;
let activeUpload=null,uploadBusy=false,uploadPaused=false,uploadReady=false,resumeMappings={},resumeJob=null;
function invalidateRawCheck(){uploadReady=false;$('uploadComplete').disabled=true;$('rawDiagnostics').textContent='';}
function lockUpload(busy){
  uploadBusy=busy;
  for(const id of ['uploadSubmit','uploadClose','fileInput','resumeUpload','refreshUploads','newUpload','cancelUpload'])$(id).disabled=busy;
  $('cancelUpload').disabled=busy||!activeUpload||!!resumeJob;
  $('uploadPause').disabled=!busy||!!resumeJob;
  $('uploadComplete').disabled=busy||!uploadReady;
  $('mappingTable').querySelectorAll('input,select').forEach(e=>{e.disabled=busy;});
}
async function refreshUploads(){
  if(offline)return;
  try{const list=await(await request('/api/upload-sessions')).json();
    $('resumeUpload').innerHTML='<option value="">새 업로드</option>'+list.map(s=>`<option value="${escapeHTML(s.upload_id)}">${escapeHTML(s.filenames.join(', ')||'파일 선택 대기')} · ${new Date(s.updated_at*1000).toLocaleString()}</option>`).join('');
    if(activeUpload)$('resumeUpload').value=activeUpload;
  }catch(e){$('resumeNotice').textContent='이어서 전송 목록 확인 실패: '+e.message;}
}
function applyResumeMappings(){
  $('mappingTable').querySelectorAll('[data-file]').forEach(row=>{const m=resumeMappings[pendingFiles[Number(row.dataset.file)].name];if(!m)return;
    for(const [selector,key] of [['.sampleMapping','sample'],['.roleMapping','role'],['.axisMapping','axis_kind'],['.unitMapping','intensity_unit']]){const e=row.querySelector(selector);if(e)e.value=m[key]||'';}
  });refreshUploadSummary();
}
$('refreshUploads').onclick=refreshUploads;
$('resumeUpload').onchange=async()=>{
  activeUpload=$('resumeUpload').value||null;resumeMappings={};resumeJob=null;invalidateRawCheck();
  if(!activeUpload){$('resumeNotice').textContent='새 업로드를 시작합니다.';lockUpload(false);return;}
  lockUpload(true);
  try{const value=await(await request(`/api/upload-sessions/${activeUpload}`)).json();resumeMappings=value.mappings||{};resumeJob=value.job;
    $('resumeNotice').textContent=resumeJob?'서버 분석 작업이 있습니다. 업로드·Raw 진단 버튼으로 처리 상태를 확인하세요.':`같은 원본 파일 전체를 다시 선택하세요: ${value.files.map(f=>f.name).join(', ')}. 전송된 내용 대조 후 이어갑니다.`;
    applyResumeMappings();
  }catch(e){$('resumeNotice').textContent=e.message;}finally{lockUpload(false);}
};
$('newUpload').onclick=()=>{activeUpload=null;resumeMappings={};resumeJob=null;invalidateRawCheck();$('resumeUpload').value='';$('resumeNotice').textContent='새 업로드입니다. 기존 중단 작업의 파일은 보존됩니다.';lockUpload(false);};
$('cancelUpload').onclick=async()=>{
  if(!activeUpload||uploadBusy||!confirm('이 업로드 작업을 취소할까요? 원자료와 이미 생성된 보고서는 삭제하지 않습니다.'))return;
  try{await request(`/api/upload-sessions/${activeUpload}/cancel`,{method:'POST'});$('newUpload').click();await refreshUploads();$('resumeNotice').textContent='작업을 취소했습니다. 전송된 임시 파일은 서버에 보존됩니다.';}catch(e){$('uploadError').textContent=e.message;}
};
$('uploadPause').onclick=()=>{uploadPaused=true;$('uploadPause').disabled=true;$('uploadError').textContent='현재 조각 전송/진단이 끝난 뒤 멈춥니다. 이어서 전송할 수 있습니다.';};
$('uploadDialog').addEventListener('cancel',e=>{if(uploadBusy)e.preventDefault();});
window.addEventListener('beforeunload',e=>{if(uploadBusy){e.preventDefault();e.returnValue='';}});
window.xrdCanReset = () => {
  if (!uploadBusy) return true;
  window.alert('파일 전송·진단 중입니다. 업로드 일시정지 후 초기화하세요. 중단된 전송은 이어서 진행할 수 있습니다.');
  return false;
};
let project,
  state,
  figure,
  pendingFiles = [],
  renderVersion = 0,
  picking = false, // Single Profile: the sample picker is open
  viewRevision = 0; // bumped when a double-click resets the ranges
const config = {
  responsive: true,
  scrollZoom: true,
  displayModeBar: false,
  // Every double-click autoranges, so it always returns to the original range (see relayout).
  doubleClick: 'autosize',
  displaylogo: false,
  showSendToCloud: false,
  modeBarButtonsToRemove: ['sendDataToCloud', 'sendChartToCloud', 'select2d', 'lasso2d'],
  toImageButtonOptions: { format: 'png', filename: 'XRD-Multi-Profile', scale: 2 },
};
const storageKey = () => viewerStorageKey(project, offline ? window.XRD_EMBEDDED.snapshotId : null);
function message(text, error = false) {
  $('status').textContent = text;
  $('status').classList.toggle('error', error);
}
function persist() {
  try {
    localStorage.setItem(storageKey(), JSON.stringify(state));
  } catch {
    /* File URLs/private browsing may disallow storage. */
  }
}
function updateControls() {
  const single = state.viewMode === 'single';
  $('viewMulti').classList.toggle('active', !single);
  $('viewSingle').classList.toggle('active', single);
  $('quickOverlay').setAttribute('aria-pressed', String(!single && state.offsets.raw.spacing === 0));
  $('quickStack').setAttribute('aria-pressed', String(!single && state.offsets.raw.spacing !== 0));
  for (const key of ['markers', 'peakLines', 'peakbar', 'unknownMarkers', 'originStyle']) $(key).checked = !!state[key];
  $('phaseMarkerHint').textContent = phaseMarkerHint(project, state, figure);
  const lineCount = figure.data.filter(t => t.meta?.role === 'peak-line')
    .reduce((count, t) => count + t.x.length / 3, 0);
  $('peakLineHint').textContent = !state.peakLines
    ? '상단 그래프에 결정상 색상의 세로선을 표시합니다. 마커·하단 막대와 별도로 켜고 끕니다.'
    : lineCount
      ? `결정상 피크 선 ${lineCount}개 표시 · 시료 기준선에서 실제 측정 높이까지 표시합니다.`
      : '표시할 피크 선이 없습니다. 관측 피크·상 배정 자료와 시료·결정상 표시 선택을 확인하세요.';
  $('unknownColor').value = state.unknownColor;
  $('markerSize').value = String(state.markerSize);
  $('markerSizeValue').textContent = String(state.markerSize);
  const hasMarkers = figure.data.some(t => ['marker', 'peakbar-tip', 'unknown'].includes(t.meta?.role) && t.x.length);
  $('markerSize').disabled = !hasMarkers;
  $('markerSizeHint').textContent = hasMarkers
    ? '결정상·기준 피크 마커와 Unknown Peak에 함께 적용됩니다.'
    : '표시된 마커가 없습니다. 피크 자료와 마커 표시 옵션을 확인하세요.';
  const offset = state.offsets.raw,
    limit = spacingLimit(project, state);
  // Spacing between samples makes no sense for one sample: the control is hidden.
  $('offsetSection').hidden = single;
  // One sample needs no colour/order/offset list (its colour is in the legend row).
  $('samplesSection').hidden = single;
  $('spacing').min = String(-limit);
  $('spacing').max = String(limit);
  $('spacing').value = String(offset.spacing);
  $('activeSampleRow').hidden = !single;
  const range = state.xRange || figure.fullX;
  $('xMin').value = Number(range[0].toFixed(6));
  $('xMax').value = Number(range[1].toFixed(6));
  const lit = state.highlight;
  $('highlightStatus').hidden = $('highlightClear').hidden = !lit;
  if (lit)
    $('highlightStatus').textContent =
      `강조: ${lit.name}${lit.card !== null ? ' · Card ' + lit.card : ''}`;
  const spacing = offset.spacing;
  $('modeStatus').textContent = single
    ? '시료 하나씩 보기'
    : `여러 시료 비교 · ${spacing === 0 ? '겹쳐 보기' : spacing > 0 ? '첫 시료 위' : '반대 순서'}`;
  $('legend').innerHTML = legendHTML(project, state);
  renderPicker();
}
// Single Profile starts by choosing the data: the picker lists every registered sample.
function renderPicker() {
  $('singlePicker').hidden = !(picking && state.viewMode === 'single');
  $('singlePickerList').innerHTML = project.samples
    .map(
      (s) =>
        `<button data-pick="${escapeHTML(s.id)}" class="${s.id === state.active ? 'active' : ''}">${escapeHTML(s.name)}</button>`,
    )
    .join('');
}

function renderPanels() {
  $('sampleList').innerHTML = state.order
    .map((id, index) => {
      const s = project.samples.find((s) => s.id === id),
        offset = state.offsets.raw.individual[id] || 0;
      return `<div class="sampleRow"><div class="sampleTop"><input type="checkbox" data-visible="${id}" ${state.visible.includes(id) ? 'checked' : ''} aria-label="${escapeHTML(s.name)} 표시"><input class="colorInput" type="color" data-color="${id}" value="${state.colors[id]}" aria-label="${escapeHTML(s.name)} 색상"><label>${escapeHTML(s.name)}</label><button data-move="${id}" data-direction="-1" ${index === 0 ? 'disabled' : ''} title="아래 순서로">↓</button><button data-move="${id}" data-direction="1" ${index === state.order.length - 1 ? 'disabled' : ''} title="위 순서로">↑</button></div>${state.viewMode === 'single' ? '' : `<label class="sampleOffset">개별 조절 <input type="number" step="any" data-offset="${id}" value="${offset}" aria-label="${escapeHTML(s.name)} 개별 조절"></label>`}</div>`;
    })
    .join('');
  const visible = project.samples.filter((s) => state.visible.includes(s.id));
  if (state.viewMode === 'single') {
    $('activeSample').innerHTML = project.samples
      .map((s) => `<option value="${s.id}">${escapeHTML(s.name)}</option>`)
      .join('');
    if (!project.samples.some((s) => s.id === state.active)) state.active = project.samples[0]?.id;
  } else {
    if (!visible.some((s) => s.id === state.active)) state.active = visible[0]?.id || null;
    $('activeSample').innerHTML = visible
      .map((s) => `<option value="${s.id}">${escapeHTML(s.name)}</option>`)
      .join('');
  }
  $('activeSample').value = state.active || '';
  // Crystal phases: one row per phase group (the same compound across samples is one row).
  const styles = phaseStyles(project, state);
  $('phaseList').innerHTML =
    phaseGroups(project, state)
      .map((g, i) => {
        const style = styles.get(g.ids[0]),
          lit = state.highlight && phaseMatches(state.highlight, g.members[0].phase);
        return `<div class="phaseRow"><div class="phaseTop"><span class="phaseSymbol" style="color:${style.color}">${SYMBOL_GLYPHS[style.symbol] || '●'}</span><button class="phaseName linkLike ${lit ? 'lit' : ''}" data-highlight="${i}" title="이 상을 모든 시료에서 강조">${escapeHTML(groupCaption(g))}</button><input class="colorInput" type="color" data-phase-color="${i}" value="${style.color}" aria-label="${escapeHTML(g.name)} 색상"><select class="symbolSelect" data-phase-symbol="${i}" aria-label="${escapeHTML(g.name)} 마커 모양">${SYMBOL_CHOICES.map((v) => `<option value="${v}" ${v === style.symbol ? 'selected' : ''}>${SYMBOL_GLYPHS[v]}</option>`).join('')}</select></div>${g.members.length > 1 ? `<div class="phaseMeta">${g.members.length}개 시료에 공통</div>` : ''}</div>`;
      })
      .join('') ||
    '<div class="empty">상 목록이 없습니다. 상매칭 Report를 연결하세요.<br>PDF에 없는 상·피크는 생성하지 않습니다.</div>';
  // Review notes belong to the authoring app, not the delivered HTML viewer.
  // Keep the project facts and the model's measurement exclusion rules unchanged.
  const warnings = offline ? [] : viewWarnings(project, figure.warnings);
  $('warningSection').hidden = !warnings.length;
  $('warningList').innerHTML = warnings
    .map((w) => `<div class="sourceWarning">${escapeHTML(w)}</div>`)
    .join('');
}

async function draw(refreshPanel = true) {
  if (!project) return;
  const version = ++renderVersion;
  const height = $('plotViewport').clientHeight;
  figure = buildFigure(project, state, { height });
  // A new view revision makes Plotly apply the ranges given here again (it would otherwise keep
  // the axis state of a double-click: the raw data extent instead of whole degrees).
  figure.layout.uirevision = `${project.id}-${viewRevision}`;
  const overflow = figure.layout.height > height + 2;
  $('plot').style.height = figure.layout.height ? `${figure.layout.height}px` : '100%';
  if (figure.active) state.active = figure.active;
  persist();
  dropStaleSourceCard();
  await Plotly.react($('plot'), figure.data, figure.layout, { ...config, scrollZoom: !overflow });
  if (version !== renderVersion) return;
  updateControls();
  if (refreshPanel) renderPanels();
  const count = figure.data.filter((t) => t.meta?.role === 'measurement').length;
  const points = figure.data
    .filter((t) => t.meta?.role === 'measurement')
    .reduce((sum, t) => sum + t.x.length, 0);
  message(
    `${project.samples.length} 시료 중 ${count} 표시 · ${points.toLocaleString()} 측정점 · 원자료 유지${overflow ? ' · 기준 피크 ' + figure.barRows + '행: 휠로 스크롤' : ''}`,
  );
  const warnings = offline ? [] : viewWarnings(project, figure.warnings);
  $('warnings').hidden = !warnings.length;
  $('warnings').textContent = warnings.length ? `확인 사항 ${warnings.length}` : '';
}

let viewerLinkDone = false;
function seedViewerLink() {
  const result = applyViewerLink(project, state, location.hash);
  if (result.applied) {
    picking = false;
    hideSourceCard();
    viewRevision += 1;
  }
  return result;
}
window.addEventListener('hashchange', () => {
  if (!project || !state) return;
  const result = seedViewerLink();
  if (result.applied) draw().catch(e => message(e.message, true));
  else if (result.error) message(result.error, true);
});
function loadProject(value, saved) {
  project = value;
  try {
    const local = JSON.parse(localStorage.getItem(storageKey()) || 'null');
    if (local?.projectId === project.id) saved = local;
  } catch {
    /* ignore invalid local state */
  }
  state = restoreState(project, saved);
  const selection = viewerLinkDone ? {} : seedViewerLink();
  viewerLinkDone = true;
  if (!offline) {
    $('emptyState').hidden = true;
    $('nextStep').hidden = !project.storage_id;
    window.dispatchEvent(new CustomEvent('xrd-shell', { detail: { project: project.storage_id } }));
  }
  hideSourceCard();
  return draw().then(() => {
    applyUrlHighlight();
    if (selection.error) message(selection.error, true);
  });
}

function download(blob, name) {
  const url = URL.createObjectURL(blob),
    a = document.createElement('a');
  a.href = url;
  a.download = name;
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 2000);
}
// Step ① 데이터 업로드 lives on this page as the registration dialog.
function openUpload() {
  refreshUploads();
  if (!$('uploadDialog').open) $('uploadDialog').show();
  window.dispatchEvent(new CustomEvent('xrd-shell', { detail: { step: 'upload' } }));
}
$('uploadDialog').addEventListener('close', () => {
  window.dispatchEvent(new CustomEvent('xrd-shell', { detail: { step: 'viewer' } }));
  if (project?.storage_id)
    history.replaceState(null, '', apiUrl(`/?project=${encodeURIComponent(project.storage_id)}`));
});
if (!offline)
  window.xrdLocalStep = (step) => {
    if (step === 'upload') {
      openUpload();
      return true;
    }
    if (step === 'viewer' && project) {
      if ($('uploadDialog').open) $('uploadDialog').close();
      return true;
    }
    return false;
  };

function mappingRows() {
  const roles = {
    measurement: 'TXT 측정',
    phase_report: '상매칭 Report',
    wppf_report: '정량(WPPF) Report',
    phase_image: '상매칭 이미지',
    quantity_image: '정량 이미지',
    ignore: '제외 (파일 보존)',
  };
  $('mappingTable').innerHTML =
    '<table><thead><tr><th>원본 파일</th><th>시료명</th><th>역할</th><th>TXT 축 / 단위</th></tr></thead><tbody>' +
    pendingFiles
      .map((file, i) => {
        const guess = classifyUploadFilename(file.name);
        return `<tr data-file="${i}"><td class="fileName">${escapeHTML(file.name)}</td><td><input class="sampleMapping" value="${escapeHTML(guess.sample)}" aria-label="${escapeHTML(file.name)} 시료명"></td><td><select class="roleMapping" aria-label="${escapeHTML(file.name)} 역할">${Object.entries(
          roles,
        )
          .map(
            ([role, label]) =>
              `<option value="${role}" ${role === guess.role ? 'selected' : ''}>${label}</option>`,
          )
          .join(
            '',
          )}</select></td><td>${guess.role === 'measurement' ? '<select class="axisMapping" aria-label="TXT 좌표 종류"><option value="">PDF/미확인</option><option value="two_theta">2θ (deg)</option></select> <input class="unitMapping" placeholder="TXT/PDF" aria-label="TXT 강도 단위">' : '—'}</td></tr>`;
      })
      .join('') +
    '</tbody></table>';
  refreshUploadSummary();
}

function readMappings() {
  const mappings = Object.create(null);
  document.querySelectorAll('[data-file]').forEach(row => {
    const file = pendingFiles[Number(row.dataset.file)];
    mappings[file.name] = {sample:row.querySelector('.sampleMapping').value,
      role:row.querySelector('.roleMapping').value, axis_kind:row.querySelector('.axisMapping')?.value || '',
      intensity_unit:row.querySelector('.unitMapping')?.value || ''};
  });
  return mappings;
}
function refreshUploadSummary() {
  const result = summarizeUpload(pendingFiles, readMappings());
  $('uploadSummary').innerHTML = uploadSummaryHTML(result);
  return result;
}
$('mappingTable').addEventListener('input', refreshUploadSummary);
$('mappingTable').addEventListener('change', refreshUploadSummary);
$('mappingTable').addEventListener('input',invalidateRawCheck);
$('mappingTable').addEventListener('change',invalidateRawCheck);
function setOptions(open) {
  $('sidePanel').hidden = !open;
  $('toggleOptions').setAttribute('aria-expanded', String(open));
  $('toggleOptions').textContent = open ? '보기 옵션 접기' : '보기 옵션 열기';
  if (project) draw(false);
}
$('toggleOptions').onclick = () => setOptions($('sidePanel').hidden);
if (window.innerWidth < 1100) setOptions(false);
$('quickImageExport').onclick = () => $('imageExport').click();
$('resetView').onclick = () => {
  if (!state) return;
  state.xRange = state.yRange = null; viewRevision++; draw(false);
};
for (const [id, stack] of [['quickOverlay',false],['quickStack',true]]) $(id).onclick = () => {
  if (!state) return;
  state.viewMode = 'multi'; picking = false; state.yRange = null;
  state.offsets.raw.spacing = stack ? (createState(project).offsets.raw.spacing || 1) : 0;
  state.offsets.raw.individual = {};
  draw();
};
// Advanced phase styling remains available without crowding the common tools.
const phaseSection = $('phaseList').parentElement, advanced = document.createElement('details');
advanced.className = 'advancedOptions';
advanced.id = 'phaseMarkerDetails';
advanced.innerHTML = '<summary>결정상 마커 상세 설정</summary>';
const markerOption = $('markers').closest('label');
markerOption.parentNode.insertBefore(advanced, markerOption.nextSibling);
advanced.appendChild(phaseSection);

for (const [id, mode] of [
  ['viewMulti', 'multi'],
  ['viewSingle', 'single'],
])
  $(id).onclick = () => {
    if (!state) return;
    state.viewMode = mode;
    state.yRange = null;
    // Single Profile: choose the sample first (a project with one sample needs no choice).
    picking = mode === 'single' && project.samples.length > 1;
    draw();
  };
function setToggle(key, value) {
  if (!state) return;
  state[key] = value;
  draw();
}
for (const key of ['markers', 'peakLines', 'peakbar', 'unknownMarkers', 'originStyle'])
  $(key).onchange = () => setToggle(key, $(key).checked);
$('unknownColor').onchange = () => {
  state.unknownColor = $('unknownColor').value;
  draw(false);
};
$('markerSize').oninput = () => {
  if (!state) return;
  state.markerSize = Number($('markerSize').value);
  $('markerSizeValue').textContent = String(state.markerSize);
  draw(false);
};
$('singlePickerList').onclick = (event) => {
  const b = event.target.closest('[data-pick]');
  if (!b) return;
  state.active = b.dataset.pick;
  picking = false;
  state.yRange = null;
  draw();
};
// The legend over the plot: a checkbox per sample and per phase group, plus the display toggles.
$('legend').onchange = (event) => {
  const d = event.target.dataset;
  if (!state) return;
  if (d.legendSample) {
    state.visible = event.target.checked
      ? [...new Set([...state.visible, d.legendSample])]
      : state.visible.filter((id) => id !== d.legendSample);
    state.yRange = null;
    draw();
  } else if (d.legendGroup !== undefined) {
    setGroupHidden(
      state,
      phaseGroups(project, state)[Number(d.legendGroup)],
      !event.target.checked,
    );
    draw();
  } else if (d.legendToggle) setToggle(d.legendToggle, event.target.checked);
};
$('activeSample').onchange = () => {
  state.active = $('activeSample').value;
  state.yRange = null;
  draw();
};
$('warnings').onclick = () => { setOptions(true); $('warningSection').scrollIntoView({ block: 'nearest' }); };
$('sidePanel').onchange = (event) => {
  const e = event.target,
    d = e.dataset;
  if (!state) return;
  if (d.visible) {
    state.visible = e.checked
      ? [...new Set([...state.visible, d.visible])]
      : state.visible.filter((id) => id !== d.visible);
    state.yRange = null;
  }
  if (d.color) state.colors[d.color] = e.value;
  if (d.offset) {
    if (e.value.trim() === '' || !Number.isFinite(e.valueAsNumber)) {
      message('개별 조절은 유효한 숫자를 입력하세요.', true);
      return;
    }
    state.offsets.raw.individual[d.offset] = e.valueAsNumber;
    state.yRange = null;
  }
  // Colour and symbol belong to the phase group: every member gets the choice.
  const group = (index) => phaseGroups(project, state)[Number(index)];
  if (d.phaseColor) setGroupColor(state, group(d.phaseColor), e.value);
  if (d.phaseSymbol) setGroupSymbol(state, group(d.phaseSymbol), e.value);
  if (Object.keys(d).length) draw();
};
$('sampleList').onclick = (event) => {
  const b = event.target.closest('[data-move]');
  if (!b) return;
  const i = state.order.indexOf(b.dataset.move),
    j = i + Number(b.dataset.direction);
  if (j < 0 || j >= state.order.length) return;
  [state.order[i], state.order[j]] = [state.order[j], state.order[i]];
  state.yRange = null;
  draw();
};
let sliderFrame = null;
function spacingChange(value) {
  if (!state || !Number.isFinite(value)) {
    message('시료 간 세로 간격은 숫자여야 합니다. 0은 겹쳐 보기입니다.', true);
    return;
  }
  state.offsets.raw.spacing = value;
  state.yRange = null;
  persist();
  if (sliderFrame) cancelAnimationFrame(sliderFrame);
  sliderFrame = requestAnimationFrame(() => {
    draw(false);
  });
}
$('spacing').oninput = () => spacingChange(Number($('spacing').value));
$('offsetReset').onclick = () => {
  if (!state) return;
  state.offsets.raw = createState(project).offsets.raw;
  state.yRange = null;
  draw();
};
$('offsetOverlay').onclick = () => spacingChange(0);
$('applyRange').onclick = () => {
  if (!state) return;
  const a = $('xMin').valueAsNumber,
    b = $('xMax').valueAsNumber;
  if (!Number.isFinite(a) || !Number.isFinite(b) || a >= b) {
    message('가로축 시작값은 끝값보다 작아야 합니다.', true);
    return;
  }
  state.xRange = [a, b];
  draw(false);
};
// Right-button drag moves the view (left drag still zooms into a box).
let panning = null;
$('plot').addEventListener('contextmenu', (event) => event.preventDefault());
$('plot').addEventListener(
  'mousedown',
  (event) => {
    if (event.button !== 2 || !state) return;
    const layout = $('plot')._fullLayout;
    if (!layout?.xaxis) return;
    // Capture phase: keep Plotly's own drag (box zoom) from also handling the right button.
    event.preventDefault();
    event.stopPropagation();
    panning = {
      x: event.clientX,
      y: event.clientY,
      xr: [...layout.xaxis.range],
      yr: [...layout.yaxis.range],
      xl: layout.xaxis._length,
      yl: layout.yaxis._length,
    };
  },
  true,
);
window.addEventListener('mousemove', (event) => {
  if (!panning) return;
  const dx = ((event.clientX - panning.x) * (panning.xr[1] - panning.xr[0])) / panning.xl,
    dy = ((event.clientY - panning.y) * (panning.yr[1] - panning.yr[0])) / panning.yl;
  state.xRange = [panning.xr[0] - dx, panning.xr[1] - dx];
  state.yRange = [panning.yr[0] + dy, panning.yr[1] + dy];
  Plotly.relayout($('plot'), {
    'xaxis.range': state.xRange,
    'yaxis.range': state.yRange,
  });
});
window.addEventListener('mouseup', () => {
  if (!panning) return;
  panning = null;
  persist();
  updateControls();
});
$('emptyUpload').onclick = openUpload;
$('nextStep').onclick = () => {
  if (project)
    location.href = apiUrl(`/analysis.html?project=${encodeURIComponent(project.storage_id)}`);
};
$('uploadClose').onclick = () => {
  $('uploadDialog').close();
};
$('fileInput').onchange = () => {
  pendingFiles = Array.from($('fileInput').files);
  mappingRows();
  applyResumeMappings();invalidateRawCheck();
  $('uploadError').textContent = '';
};
$('uploadSubmit').onclick = async () => {
  if(uploadBusy)return;
  if(resumeJob){lockUpload(true);try{await finishUpload(resumeJob);}catch(e){$('uploadError').textContent=e.message;}finally{lockUpload(false);}return;}
  if (!pendingFiles.length) {
    $('uploadError').textContent = '등록할 파일을 선택하세요.';
    return;
  }
  if (refreshUploadSummary().errors.length) {
    $('uploadError').textContent = '시료별 파일 연결의 오류를 먼저 확인하세요.'; return;
  }
  const mappings = readMappings();
  uploadPaused=false;invalidateRawCheck();lockUpload(true);
  $('uploadError').textContent = 'TXT와 PDF를 읽는 중…';
  try {
    const result=await transferUpload(pendingFiles,mappings,{request,chunkSize:chunkBytes(),uploadId:activeUpload,
      onSession:id=>{activeUpload=id;},notice:text=>{$('uploadError').textContent=text;},canceled:()=>uploadPaused});
    if(result.job){resumeJob=result.job;await finishUpload(result.job);return;}
    $('rawDiagnostics').innerHTML=diagnosticsHTML(result.diagnostics);uploadReady=result.diagnostics.ok;
    $('uploadError').textContent=uploadReady?'Raw 진단 완료. 결과 확인 후 아래 「진단 확인·분석 시작」을 누르세요.':'Raw 진단에 확인할 항목이 있습니다. 파일 내용이 달라지면 「새 작업」으로 등록하세요.';
  } catch (error) {
    $('uploadError').textContent = error.message;
  } finally {
    lockUpload(false);refreshUploads();
  }
};
async function finishUpload(job){
  resumeJob=job;$('uploadPause').disabled=true;
  const done=await waitJob(job,text=>{$('uploadError').textContent=text;});
  await loadProject(await(await request(`/api/projects/${done.storage_id}`)).json());
  activeUpload=null;resumeJob=null;invalidateRawCheck();$('uploadDialog').close();
}
$('uploadComplete').onclick=async()=>{
  if(!activeUpload||!uploadReady||uploadBusy)return;
  lockUpload(true);$('uploadPause').disabled=true;
  try{const job=await(await request(`/api/upload-sessions/${activeUpload}/complete`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(readMappings())})).json();await finishUpload(job);}
  catch(e){$('uploadError').textContent=e.message;}finally{lockUpload(false);refreshUploads();}
};
$('htmlExport').onclick = async () => {
  if (!project) return;
  $('htmlExport').disabled = true;
  try {
    const response = await request('/api/export/html', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        project: {
          id: project.id,
          storage_id: project.storage_id,
          analysis_revision: project.analysis_revision || 0,
        },
        state,
      }),
    });
    download(await response.blob(), 'XRD-그래프-뷰어.html');
    message('현재 데이터·보기 상태를 포함한 독립 HTML을 저장했습니다.');
  } catch (error) {
    message(error.message, true);
  } finally {
    $('htmlExport').disabled = false;
  }
};
// Export the current view style, not a separate export checkbox.
$('imageExport').onclick = async () => {
  if (!project || $('imageExport').disabled) return;
  $('imageExport').disabled = $('quickImageExport').disabled = true;
  try {
    const origin = state.originStyle;
    const exported = exportFigure(project, state);
    const format = $('imageFormat').value;
    const url = await Plotly.toImage(
      { data: exported.data, layout: exported.layout },
      { format, width: 1200, height: exported.layout.height || 900, scale: 2 },
    );
    const blob = await (await fetch(url)).blob();
    download(
      blob,
      `XRD-그래프${origin ? '-Origin' : ''}.${format === 'jpeg' ? 'jpg' : 'png'}`,
    );
    message('범례를 포함한 현재 이미지를 저장했습니다.');
  } catch (error) {
    message(error.message, true);
  } finally {
    $('imageExport').disabled = $('quickImageExport').disabled = false;
  }
};

// ---- phase highlight, source card, named views
function sameRef(a, b) {
  return (
    !!a &&
    !!b &&
    a.name === b.name &&
    (a.card ?? null) === (b.card ?? null) &&
    (a.db ?? null) === (b.db ?? null)
  );
}
function toggleHighlight(ref) {
  state.highlight = sameRef(state.highlight, ref) ? null : ref;
  hideSourceCard();
  draw();
}
$('highlightClear').onclick = () => {
  if (!state) return;
  state.highlight = null;
  draw();
};
$('sidePanel').onclick = (event) => {
  const button = event.target.closest('[data-highlight]');
  if (!button || !state) return;
  const group = phaseGroups(project, state)[Number(button.dataset.highlight)];
  if (group) toggleHighlight(phaseRef(group.members[0].phase));
};
// The card describes one drawn phase: close it when that phase is no longer shown.
function dropStaleSourceCard() {
  if ($('sourceCard').hidden) return;
  const id = $('sourceCard').dataset.phase;
  const owner = project.samples.find((s) => s.phases.some((p) => p.id === id));
  const shown =
    owner &&
    !state.hiddenPhases.includes(id) &&
    state.visible.includes(owner.id) &&
    (state.phaseTarget === 'all' || owner.id === state.active);
  if (!shown) hideSourceCard();
}
function hideSourceCard() {
  $('sourceCard').hidden = true;
}
function showSourceCard(trace, point) {
  const sample = project.samples.find((s) => s.id === trace.meta.sampleId),
    phase = sample?.phases.find((p) => p.id === trace.meta.phaseId);
  if (!sample || !phase) return;
  const link = (label, role, page) => {
    const href = sourceHref(project, sample, role, page, offline, apiUrl(''));
    return href
      ? `<a href="${escapeHTML(href)}" target="_blank" rel="noopener">${escapeHTML(label)} p.${page} ↗</a>`
      : `<span class="muted">${escapeHTML(label)}${Number.isInteger(page) ? ' p.' + page : ''}</span>`;
  };
  const data = point.customdata || [];
  const body =
    trace.meta.role === 'marker'
      ? `관측 피크 #${escapeHTML(String(data[0]))}<br>${link('상매칭 배정', 'phase_report', data[1])}<br>${link('Peak List', 'phase_report', data[2])}`
      : `기준 피크 · hkl ${escapeHTML(String(data[1] ?? ''))}<br>${link('d-I List', 'phase_report', data[0])}`;
  $('sourceCard').innerHTML =
    `<button class="cardClose" aria-label="근거 카드 닫기">×</button><b>${escapeHTML(sample.name)} / ${escapeHTML(phase.name)}</b><div class="muted">Card ${escapeHTML(phase.card || '')}</div><div>${body}</div><button class="cardHighlight">이 상 강조</button>`;
  $('sourceCard').hidden = false;
  $('sourceCard').dataset.phase = phase.id;
}
$('sourceCard').onclick = (event) => {
  if (event.target.closest('.cardClose')) hideSourceCard();
  else if (event.target.closest('.cardHighlight')) {
    const phase = project.samples
      .flatMap((s) => s.phases)
      .find((p) => p.id === $('sourceCard').dataset.phase);
    if (phase) toggleHighlight(phaseRef(phase));
  }
};
let urlHighlightDone = false;
function applyUrlHighlight() {
  // The address only seeds the first view; later loads (analysis updates, uploads) keep the user's choice.
  if (urlHighlightDone) return;
  urlHighlightDone = true;
  const name = new URLSearchParams(location.search).get('phase');
  if (!name || !state) return;
  const ref = { name, card: null, db: null };
  if (project.samples.some((s) => s.phases.some((p) => phaseMatches(ref, p)))) {
    state.highlight = ref;
    draw();
  } else message(`주소의 상 "${name}"을 이 프로젝트에서 찾지 못했습니다.`, true);
}
async function start() {
  // Initialise once before event bindings. Subsequent rendering uses react.
  await Plotly.newPlot(
    $('plot'),
    [],
    {
      paper_bgcolor: '#fff',
      plot_bgcolor: '#fff',
      xaxis: { visible: false },
      yaxis: { visible: false },
      margin: { l: 70, r: 18, t: 14, b: 48 },
    },
    config,
  );
  $('plot').on('plotly_relayout', (event) => {
    if (!state) return;
    for (const axis of ['xaxis', 'xaxis2']) {
      if (event[axis + '.autorange']) state.xRange = null;
      // Plotly emits either an array or indexed keys depending on the interaction/API.
      const range = event[axis + '.range'] || [event[axis + '.range[0]'], event[axis + '.range[1]']];
      if (Array.isArray(range) && range.length === 2 && range.every(Number.isFinite))
        state.xRange = range.slice();
    }
    if (event['yaxis.autorange']) state.yRange = null;
    // A double-click on the graph makes Plotly autorange: go back to the original 2θ range (the
    // data range in whole degrees) and the automatic height instead of the raw data extent.
    if (event['xaxis.autorange'] || event['xaxis2.autorange']) {
      state.xRange = state.yRange = null;
      viewRevision += 1;
      setTimeout(() => draw(false), 0);
      return;
    }
    const yRange = event['yaxis.range'] || [event['yaxis.range[0]'], event['yaxis.range[1]']];
    if (Array.isArray(yRange) && yRange.length === 2 && yRange.every(Number.isFinite))
      state.yRange = yRange.slice();
    updateControls();
    persist();
  });
  // Plotly legend interaction follows the same state as the sample list.
  $('plot').on('plotly_legendclick', (event) => {
    const trace = figure.data[event.curveNumber];
    if (trace?.meta?.role === 'measurement') {
      state.visible = state.visible.filter((id) => id !== trace.meta.sampleId);
      state.yRange = null;
      draw();
    }
    return false;
  });
  $('plot').on('plotly_click', (event) => {
    const point = event.points?.[0],
      trace = point && figure?.data[point.curveNumber];
    if (trace?.meta?.role === 'marker' || trace?.meta?.role === 'peakbar')
      showSourceCard(trace, point);
  });
  if (offline) {
    $('htmlExport').parentElement.hidden = true;
    await loadProject(window.XRD_EMBEDDED.project, window.XRD_EMBEDDED.state);
  } else {
    const id = new URLSearchParams(location.search).get('project');
    if (id) {
      let value;
      try {
        value = await (await request(`/api/projects/${encodeURIComponent(id)}`)).json();
      } catch {
        throw new Error('등록 프로젝트를 열지 못했습니다.');
      }
      await loadProject(value);
    } else {
      $('emptyState').hidden = false;
      message('새 작업입니다. 파일을 선택하여 시작하세요. 저장된 프로젝트는 상단 목록에서 다시 열 수 있습니다.');
    }
    if (new URLSearchParams(location.search).get('step') === 'upload') openUpload();
  }
}
window.addEventListener('storage', async (e) => {
  if (offline || !project || e.key !== `xrd-analysis-updated-${project.storage_id}`) return;
  const id = project.storage_id;
  try {
    let value;
    try {
      value = await (await request(`/api/projects/${id}`)).json();
    } catch {
      throw new Error('분석 검토값 갱신 실패');
    }
    if (project.storage_id === id) await loadProject(value, state);
  } catch (error) {
    message(error.message, true);
  }
});
window.addEventListener('resize', () => {
  if (project) draw(false);
});
start().catch((error) => message(error.message, true));
