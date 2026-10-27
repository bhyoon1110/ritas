import { apiUrl, request as api } from '../shared/api.mjs';
import '../shared/pptx_password.js';
import { startExport, downloadJob, mountReportPlatform, transferValues } from '../shared/platform.mjs';
import { $, escapeHTML } from '../shared/dom.mjs';
import { restoreState, viewerStorageKey } from '../viewer/model.mjs';
import { mergeDraftChanges } from './report_merge.mjs';
import { ensureEmail, emailContextKey, mountEmailEditor, initializeEmptyEmail, canAutoDraftEmail } from './email.mjs';
import { createLivePreview } from './live_preview.mjs';
import { mountPptEditor } from './ppt_editor.mjs';
import { createDraftAutosave } from './draft_autosave.mjs';
import { figureHTML } from './figure_view.mjs';
import { phaseClassesHTML } from './phase_classes_view.mjs';
import { ensureQuantityTable, quantityTableHTML } from './quantity_table_view.mjs';
import { checklistHTML, nextSampleToCheck, precheckText } from './checklist.mjs';
import { applyPreset, presetOptionsHTML, settingsFromDoc } from './presets.mjs';
import {
  applyTrace,
  distinctPhases,
  ensurePhaseClasses,
  setLabel,
  setRowLabel,
  setSampleLabel,
  visibleClassSamples,
} from './phase_classes.mjs';
import {
  addGroup,
  applyOrder,
  ensureFigure,
  figureFromViewer,
  moveGroup,
  removeGroup,
  renameGroup,
  setGroupMembers,
} from './figure.mjs';
let storageId = new URLSearchParams(location.search).get('project'),
  project,
  doc,
  baseline,
  dirty = false,
  busy = false,
  tab = 'overview',
  figureHints = { identical: [], notes: {} },
  figurePick = new Set(),
  presets = [],
  ckData = null,
  ckCursor = null,
  ckNotice = '',
  ckSeq = 0,
  qtData = null,
  qtNotice = '',
  qtSeq = 0,
  pcData = null,
  pcNotice = '',
  pcSeq = 0,
  pcPercent = '';
let composing = false, previewPosition = {index:0,offset:0}, linkedTimer = 0;
const previewFrame = $('reportPreview'), reportMain = document.querySelector('main');
const pptHost=document.createElement('div');pptHost.className='ppt-editor';previewFrame.parentNode.insertBefore(pptHost,previewFrame);previewFrame.hidden=true;
const pptEditor=mountPptEditor({host:pptHost,document:()=>doc,changed:markDirty,linked:linkedField,message:status,
  editing:value=>{composing=value;if(value)livePreview.invalidate();else if(doc&&!busy)livePreview.schedule();},
  upload:(file,done)=>{
    lock(true);
    api(`/api/reports/${storageId}/editor-images`,{method:'POST',headers:{'Content-Type':file.type,'X-Requested-With':'RIST-Report-Editor'},body:file})
      .then(r=>r.json()).then(value=>{lock(false);done(null,value);},error=>{lock(false);done(error);});
  }});
const pptxPassword = new window.RistPptxPassword({host:document.querySelector('#savePane .saveActions'),
  changed:()=>{if(doc){doc.pptxProtection=pptxPassword.value();markDirty();status('PPT 수정 비밀번호 설정을 변경했습니다. 저장·내보내기 시 적용됩니다.');}},
  request:(password,done)=>{
    lock(true);
    api(`/api/reports/${storageId}/pptx-password`,{method:'POST',
      headers:{'Content-Type':'application/json','X-Requested-With':'RIST-Report-Editor'},
      body:JSON.stringify({password})}).then(r=>r.json()).then(value=>{
        lock(false);done(null,value);
      },error=>{lock(false);done(error);});
  }});
const mailEditor=mountEmailEditor($('editorFields'),$('reportNav'),()=>{
  if(!doc||busy)return;
  const email=ensureEmail(doc);email.delivery=mailEditor.value();email.body=$('emailBody').value;
  email.status='manual';updateEmailStatus();markDirty();
});
let recoveryDraft=null;
let emailBaselineKey='', emailStale=false, emailAttempted=false, emailDefault=null;
const recoveryBar=document.createElement('div');recoveryBar.className='draftRecovery';
recoveryBar.innerHTML='<span id="draftStatus" role="status">초안 확인 중…</span><button id="restoreDraft" hidden>초안 복구</button><button id="discardDraft" hidden>복구 초안 버리기</button>';
reportMain.parentNode.insertBefore(recoveryBar,reportMain);
const draftSaver=createDraftAutosave({
  snapshot:()=>doc&&dirty&&!composing?{document:structuredClone(doc),baseline:structuredClone(baseline)}:null,
  write:async(value,revision)=>(await api(`/api/reports/${storageId}/draft`,{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({...value,expected_revision:revision})})).json(),
  clear:async revision=>(await api(`/api/reports/${storageId}/draft/clear`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({expected_revision:revision})})).json(),
  status:text=>{$('draftStatus').textContent=text;}
});
async function loadRecovery(){
  try{const value=await(await api(`/api/reports/${storageId}/draft`)).json();
    recoveryDraft=value.document?value:null;draftSaver.initialize(value.revision||0,!recoveryDraft);
    $('restoreDraft').hidden=$('discardDraft').hidden=!recoveryDraft;
    $('draftStatus').textContent=recoveryDraft?'복구 가능한 초안이 있습니다. 복구 또는 버리기를 선택하세요.':'입력 후 초안을 자동 임시저장합니다. 최종 저장·전송과 별도입니다.';
  }catch(e){$('draftStatus').textContent='초안 확인 실패: '+e.message;}
}
$('restoreDraft').onclick=()=>{
  if(!recoveryDraft||busy)return;
  if((dirty||doc.revision!==recoveryDraft.baseline.revision||doc.analysis_revision!==recoveryDraft.baseline.analysis_revision)&&!confirm('현재 보고서와 초안의 기준이 다릅니다. 초안에서 수정한 항목을 우선 복구하며 같은 항목의 최신 내용과 충돌할 수 있습니다. 복구 후 반드시 검토하세요. 계속할까요?'))return;
  doc=mergeDraftChanges(doc,recoveryDraft.document,recoveryDraft.baseline);
  initializeEmptyEmail(doc,emailDefault);
  draftSaver.initialize(recoveryDraft.revision);recoveryDraft=null;
  $('restoreDraft').hidden=$('discardDraft').hidden=true;render();markDirty();
  status('초안을 복구했습니다. 미저장 변경을 검토한 뒤 최종 저장하세요.');
};
$('discardDraft').onclick=async()=>{
  if(busy||!confirm('복구 초안만 버릴까요? 최종 저장된 보고서와 원자료는 유지합니다.'))return;
  try{await draftSaver.clear();recoveryDraft=null;$('restoreDraft').hidden=$('discardDraft').hidden=true;if(dirty)draftSaver.schedule();renderEmail();maybeAutoEmail();}
  catch(e){status(e.message,true);}
};
// Isolate draft scripts while allowing explicit source-PDF links to open normally.
previewFrame.setAttribute('sandbox','allow-scripts allow-popups allow-popups-to-escape-sandbox');
const livePreview = createLivePreview({
  delay: Number(navigator.deviceMemory || 0) <= 4 ? 1200 : 900,
  render: async () => {
    if (!doc) throw new Error('프로젝트가 열리지 않았습니다.');
    try {return await (await api(`/api/reports/${storageId}/draft-preview`, {method:'POST',
      headers:{'Content-Type':'application/json'}, body:JSON.stringify({document:doc,format:'editor'})})).json();}
    catch(error){pptEditor.failed();throw error;}
  },
  ready: value => {
    pptEditor.accept(value);
    $('previewStatus').textContent = dirty ? '미저장 초안 반영 · 저장은 별도' : `저장 버전 ${doc.revision} 기준`;
  },
  status: text => { $('previewStatus').textContent = text; }
});
const reportTools = document.createElement('div');
reportTools.className = 'workTabs';
reportTools.innerHTML = '<button data-work-view="both">입력·미리보기</button><button data-work-view="editor">입력 화면</button><button data-work-view="preview">미리보기 화면</button><button id="draftRefresh">미리보기 다시 생성</button><span>입력을 멈추면 자동 반영 · 미리보기 클릭 시 입력 항목으로 이동</span>';
reportMain.parentNode.insertBefore(reportTools,reportMain);
function setWorkView(view) {
  reportMain.dataset.workView = view;
  reportTools.querySelectorAll('[data-work-view]').forEach(b=>b.setAttribute('aria-pressed',String(b.dataset.workView===view)));
}
reportTools.onclick = e => {
  const b = e.target.closest('button');
  if (b?.dataset.workView) setWorkView(b.dataset.workView);
  else if (b?.id === 'draftRefresh' && !busy) updatePreview();
};
setWorkView(window.innerWidth < 900 ? 'editor' : 'both');
function linkedField(target) {
  if (!doc || busy || !target || typeof target.tab !== 'string') return;
  if (!['overview','figure','classes'].includes(target.tab) && !doc.samples.some(s=>s.sample_id===target.tab)) return;
  if (target.field === 'transfer') {setStep('save');}
  else {setStep('report'); showTab(target.tab);}
  if (reportMain.dataset.workView === 'preview') setWorkView('editor');
  let element;
  const overviewIds={title:'title',client:'client',requester:'requester',received_date:'receivedDate',analysis_period:'analysisPeriod',report_number:'reportNumber',author:'author',date:'reportDate',summary:'summary',transfer:'ristEquipment',purpose:'trace-purpose',equipment_name:'trace-equipment_name',measured_date:'trace-measured_date',conditions:'trace-conditions',preparation:'trace-preparation',environment:'trace-environment',reviewer:'trace-reviewer'};
  if (target.tab === 'overview' && overviewIds[target.field]) element=$(overviewIds[target.field]);
  else if (target.tab === 'figure') element=$('figureForm').querySelector('input,select,textarea');
  else if (target.tab === 'classes') element=$('classForm').querySelector('input,select,textarea');
  else if (target.field === 'note' || target.field === 'retained_note') {
    const key = target.field === 'note' ? 'noteText' : 'retainedText';
    element=[...$('sampleForm').querySelectorAll('textarea')].find(e=>e.dataset[key]===target.id);
  }
  else element=[...$('sampleForm').querySelectorAll('[data-field]')].find(e=>e.dataset.field===target.field)
    || $('sampleForm').querySelector(target.field==='notes' ? '[data-note-text], [data-field="manual_note"]' : 'input');
  if (!element) element=$('title');
  document.querySelectorAll('.fieldLinked').forEach(e=>e.classList.remove('fieldLinked'));
  element.classList.add('fieldLinked'); element.focus(); element.scrollIntoView({block:'center'});
  clearTimeout(linkedTimer); linkedTimer=setTimeout(()=>element.classList.remove('fieldLinked'),2000);
}
window.addEventListener('message', e => {
  if (e.source !== previewFrame.contentWindow || !e.data) return;
  if (e.data.type === 'xrd-preview-edit') linkedField(e.data.target);
  if (e.data.type === 'xrd-preview-position') {
    const p=e.data.position;
    if(p && Number.isInteger(p.index) && p.index>=0 && Number.isFinite(p.offset) && p.offset>=0 && p.offset<=1)
      previewPosition={index:p.index,offset:p.offset,key:typeof p.key==='string'?p.key.slice(0,200):''};
  }
  if (e.data.type === 'xrd-preview-ready') previewFrame.contentWindow.postMessage({type:'xrd-preview-restore',position:previewPosition},'*');
});
window.addEventListener('pagehide',()=>{livePreview.invalidate();clearTimeout(linkedTimer);});
window.addEventListener('pageshow',e=>{if(e.persisted && doc && !busy)livePreview.schedule(true);});
$('editorFields').addEventListener('compositionstart',()=>{composing=true;livePreview.invalidate();draftSaver.pause();});
$('editorFields').addEventListener('compositionend',()=>{composing=false;if(doc&&!busy){livePreview.schedule();if(dirty)draftSaver.schedule();}});
document.addEventListener('visibilitychange',()=>{if(document.hidden&&!composing)draftSaver.flush();});
window.addEventListener('pagehide',()=>{if(!composing)draftSaver.flush();});
$('editorFields').addEventListener('focusin',e=>{
  const d=e.target.dataset, field=d.meta || d.field || (d.noteText?'note':d.retainedText?'retained_note':e.target.id==='summary'?'summary':null);
  if (field) previewFrame.contentWindow.postMessage({type:'xrd-preview-locate',target:{tab,field,id:d.noteText||d.retainedText}},'*');
  if (field) pptEditor.locate({tab,field,id:d.noteText||d.retainedText});
});
function status(text, error = false) {
  $('status').textContent = text;
  $('status').classList.toggle('error', error);
}
function markDirty() {
  dirty = true;
  updateEmailStatus();
  if(!composing)draftSaver.schedule();
  renderPrecheck();
  syncShell();
  $('revision').textContent = `버전 ${doc.revision} · 미저장 변경`;
  if (!composing && !busy) livePreview.schedule();
  else livePreview.invalidate();
}
function lock(value) {
  mailEditor.lock(value);
  pptxPassword.lock(value);
  busy = value;
  pptEditor.lock(value);
  if (value) livePreview.invalidate();
  else if (doc) livePreview.schedule();
  $('editorFields').disabled = value;
  for (const id of [
    'save',
    'draftRefresh',
    'preview',
    'pdfExport',
    'pptxExport',
    'zipExport',
    'viewerExport',
    'refreshAnalysis',
    'includeViewerState',
    'presetSelect',
    'presetApply',
    'presetName',
    'presetSave',
    'presetDelete',
  ])
    $(id).disabled = value;
  if (!value) maybeAutoEmail();
}
function showTab(value) {
  if (!doc) return;
  tab = value;
  if (!['overview', 'checks', 'figure', 'classes', 'qtable', 'llm', 'email'].includes(value))
    ckCursor = value;
  for (const id of ['overviewForm', 'sampleForm', 'figureForm', 'classForm', 'qtableForm', 'emailForm'])
    $(id).hidden = true;
  document
    .querySelectorAll('[data-tab]')
    .forEach((e) => e.classList.toggle('active', e.dataset.tab === tab));
  if (tab === 'overview') $('overviewForm').hidden = false;
  else if (tab === 'email') {
    $('emailForm').hidden = false;
    renderEmail();
    maybeAutoEmail();
  }
  else if (tab === 'classes') {
    $('classForm').hidden = false;
    renderClasses();
    loadClassData();
  } else if (tab === 'figure') {
    // 그래프·정량 표: the comparison figure and the quantity table settings on one page.
    $('figureForm').hidden = false;
    $('qtableForm').hidden = false;
    renderFigure();
    loadFigureHints();
    renderQuantityTable();
    loadQuantityTable();
  } else {
    $('sampleForm').hidden = false;
    renderSample();
  }
}
function render() {
  pptEditor.load();
  ensureEmail(doc);
  pptxPassword.set(doc.pptxProtection);
  for (const [id, key] of [['ristExperiment','experiment_code'], ['ristEquipment','equipment_code']]) {
    if ($(id)) $(id).value = doc.metadata[key] || (key === 'experiment_code' ? 'XRD' : '');
  }
  ensureFigure(doc);
  ensureQuantityTable(doc);
  ensurePhaseClasses(doc);
  // The threshold box starts with the percent the report's rule was applied with (5 by default).
  if (pcPercent === '' && doc.phase_classes.rule)
    pcPercent = String(doc.phase_classes.rule.trace_max_percent);
  renderViewerSummary();
  syncShell();
  const affected = doc.samples.filter((s) => s.included && s.analysis_review_required);
  $('analysisBanner').hidden = !affected.length;
  $('analysisBanner').textContent = affected.length
    ? `분석값 변경 · 재검토 필요: ${affected.map((s) => s.display_name).join(', ')}. 보존된 사용자 문장과 원본 이미지를 확인하고 시료별 자료·문장 검토 완료를 선택한 뒤 저장하세요.`
    : '';
  for (const [id, key] of [
    ['title', 'title'],
    ['client', 'client'],
    ['requester', 'requester'],
    ['receivedDate', 'received_date'],
    ['analysisPeriod', 'analysis_period'],
    ['reportNumber', 'report_number'],
    ['author', 'author'],
    ['reportDate', 'date'],
  ])
    $(id).value = doc.metadata[key] ?? '';
  for (const key of ['purpose','equipment_name','measured_date','conditions','preparation','environment','reviewer']) {
    const input = $('trace-' + key);
    if (input) input.value = doc.metadata[key] || '';
  }
  $('summary').value = doc.summary;
  $('provider').value = doc.llm.provider;
  $('baseUrl').value = doc.llm.base_url;
  $('model').value = doc.llm.model;
  $('sampleNav').innerHTML = doc.samples
    .map(
      (s) =>
        `<button class="sectionNav" data-tab="${s.sample_id}">${escapeHTML(s.display_name)}</button>`,
    )
    .join('');
  $('sampleOverview').innerHTML = doc.samples
    .map(
      (s, i) =>
        `<div class="sampleOverviewRow"><span class="sampleName">${escapeHTML(s.display_name)}</span><label><input type="checkbox" data-included="${s.sample_id}" ${s.included ? 'checked' : ''}>본문 포함</label><label><input type="checkbox" data-summary="${s.sample_id}" ${s.summary_included ? 'checked' : ''}>요약 포함</label><button data-move="${s.sample_id}" data-direction="-1" ${i === 0 ? 'disabled' : ''} aria-label="${escapeHTML(s.display_name)} 앞 순서">↑</button><button data-move="${s.sample_id}" data-direction="1" ${i === doc.samples.length - 1 ? 'disabled' : ''} aria-label="${escapeHTML(s.display_name)} 뒤 순서">↓</button></div>`,
    )
    .join('');
  $('revision').textContent = `저장 버전 ${doc.revision}${dirty ? ' · 미저장 변경' : ''}`;
  showTab(tab);
}
function renderSample() {
  const entry = doc.samples.find((s) => s.sample_id === tab),
    sample = project.samples.find((s) => s.id === tab);
  if (!entry || !sample) return;
  const sourceHref = (r) =>
    apiUrl(`/api/files/${encodeURIComponent(storageId)}/${encodeURIComponent(r.id)}`);
  const imageBlock = (role, flag, caption) => {
    const record = sample.sources.find((r) => r.role === role),
      label = role === 'phase_image' ? '상매칭 이미지' : '정량 이미지';
    return `<div class="subHeading">원본 ${label}</div>${record ? `<label class="checkboxLabel"><input type="checkbox" data-field="${flag}" ${entry[flag] ? 'checked' : ''}>본문에 포함</label><img class="assetPreview" src="${sourceHref(record)}" alt="${escapeHTML(label)}"><label>캡션<input data-field="${caption}" value="${escapeHTML(entry[caption])}" maxlength="300" aria-label="${label} 캡션"></label>` : '<p class="hint">해당 원본 이미지가 등록되어 있지 않습니다.</p>'}`;
  };
  const notes = entry.notes
    .map(
      (n, i) =>
        `<div class="noteItem"><div class="noteHeader"><label><input type="checkbox" data-note-selected="${n.id}" ${n.selected ? 'checked' : ''}>${escapeHTML(n.title)}</label><div><button data-note-move="${n.id}" data-direction="-1" ${i === 0 ? 'disabled' : ''}>↑</button><button data-note-move="${n.id}" data-direction="1" ${i === entry.notes.length - 1 ? 'disabled' : ''}>↓</button></div></div><textarea rows="4" data-note-text="${n.id}" maxlength="10000" aria-label="${escapeHTML(n.title)} 문장">${escapeHTML(n.text)}</textarea><div class="noteEvidence">${n.edited ? '사용자 수정 · ' : n.origin === 'llm' ? '모델 작성 · 수치·상 이름 근거 대조 확인 · ' : '규칙 기반 · '}${n.evidence.map((e) => escapeHTML(e.filename) + (e.page ? ' p.' + e.page : '')).join(' / ')}</div></div>`,
    )
    .join('');
  $('sampleForm').innerHTML =
    `<h1>${escapeHTML(entry.display_name)}</h1><p class="intro">원본 이미지와 특이사항을 검토합니다. 검토 문장과 근거는 편집 데이터에 보존합니다. 보고서 요약의 특이사항은 상 분류의 미량상·추정상 여부로만 생성되며, 아래 문장이나 LLM 출력으로 대체하지 않습니다. 등록 시료명: ${escapeHTML(sample.name)} · PDF 원문은 하단 링크에서 확인하세요.</p><label>보고서 표시 시료명<input data-field="display_name" value="${escapeHTML(entry.display_name)}" maxlength="200" aria-label="보고서 표시 시료명"></label>${imageBlock('phase_image', 'include_phase_image', 'phase_caption')}${imageBlock('quantity_image', 'include_quantity_image', 'quantity_caption')}<div class="subHeading">시료별 검토 문장 (보존용)</div><div class="generationLine">${escapeHTML(entry.generation.message || '규칙 기반 후보입니다.')}</div><div class="generationActions"><button id="generateNotes">후보 다시 생성</button><label class="checkboxLabel"><input type="checkbox" data-field="reviewed" ${entry.reviewed ? 'checked' : ''}>자료·문장 검토 완료</label></div>${notes || '<p class="hint">근거 후보가 없습니다. 아래에 직접 작성하거나 원본 Report 연결을 확인하세요.</p>'}<label class="subHeading">추가 직접 작성 문장<textarea rows="5" data-field="manual_note" maxlength="10000" aria-label="추가 직접 작성 문장">${escapeHTML(entry.manual_note)}</textarea></label><div class="sourceLinks">${sample.sources
      .filter((r) => ['phase_report', 'wppf_report', 'measurement'].includes(r.role))
      .map(
        (r) =>
          `<a target="_blank" rel="noopener" href="${sourceHref(r)}">↗ ${escapeHTML(r.filename)}</a>`,
      )
      .join('')}`;
  $('sampleForm').insertAdjacentHTML(
    'beforeend',
    (sample.warnings || [])
      .map((w) => `<div class="sourceWarning">${escapeHTML(w)}</div>`)
      .join(''),
  );
  if (entry.retained_notes?.length)
    $('sampleForm').insertAdjacentHTML(
      'beforeend',
      '<div class="subHeading">보존된 이전 사용자 문장</div>' +
        entry.retained_notes
          .map(
            (n) =>
              `<div class="noteItem"><div class="retainedLabel">이전 분석 후보에서 작성한 문장 · 현재 분석값과 함께 재검토하세요.</div><label><input type="checkbox" data-retained-selected="${n.id}" ${n.selected ? 'checked' : ''}>${escapeHTML(n.title)}</label><textarea rows="4" data-retained-text="${n.id}" aria-label="보존 문장 ${escapeHTML(n.title)}">${escapeHTML(n.text)}</textarea></div>`,
          )
          .join(''),
    );
}
async function openProject(id) {
  if (busy) return;
  lock(true);
  status('등록 자료와 보고서를 여는 중…');
  try {
    const value = await (await api(`/api/reports/${encodeURIComponent(id)}`)).json();
    storageId = id;
    project = value.project;
    doc = value.document;
    ensureEmail(doc);
    emailBaselineKey=emailContextKey(doc);
    emailStale=!!value.email_stale;
    emailAttempted=false;
    emailDefault=value.email_default || null;
    baseline = structuredClone(doc);
    dirty = value.analysis_update_required;
    await loadRecovery();
    const emailInitialized=initializeEmptyEmail(doc,emailDefault);
    if(emailInitialized)dirty=true;
    tab = 'overview';
    qtData = null;
    qtNotice = '';
    qtSeq++;
    ckData = null;
    ckSeq++;
    pcData = null;
    pcNotice = '';
    pcSeq++;
    render();
    renderEmail();
    if(emailInitialized)markDirty();
    loadChecklist();
    status(
      value.analysis_update_required
        ? '분석값이 변경되어 보고서 후보를 갱신했습니다. 검토 후 저장하세요.'
        : emailInitialized ? '분석 결과로 안내 메일 기본 문안을 채웠습니다. 검토 후 저장하세요.'
        : '원본 자료를 유지하며 보고서를 편집할 수 있습니다.',
    );
    setStep(step);
    if (new URLSearchParams(location.search).get('settings')) openSettings();
  } catch (e) {
    status(e.message, true);
  } finally {
    lock(false);
  }
}
async function save() {
  if (!doc) throw new Error('프로젝트가 열리지 않았습니다.');
  if(recoveryDraft)throw new Error('복구 가능한 초안이 있습니다. 먼저 복구 또는 버리기를 선택하세요.');
  await draftSaver.flush();
  syncTransfer();
  const result = await (
    await api(`/api/reports/${storageId}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ document: doc, expected_revision: doc.revision }),
    })
  ).json();
  doc = result;
  baseline = structuredClone(doc);
  dirty = false;
  try{await draftSaver.clear();}catch{/* Do not misreport a successful final save as failed. */}
  render();
  loadChecklist();
  status(`보고서 버전 ${doc.revision}을 저장했습니다.`);
  return doc;
}

function updateEmailStatus() {
  if (!doc) return;
  const email=ensureEmail(doc);
  const stale=emailStale || (emailBaselineKey && emailContextKey(doc)!==emailBaselineKey);
  $('emailStatus').textContent=(recoveryDraft ? '복구 초안이 있어 LLM 자동 작성을 대기합니다. 위에서 복구 또는 버리기를 선택하세요. ' : '') + (stale && (email.subject||email.body)
    ? '분석·보고서 입력이 변경되었습니다. 현재 메일을 대조하거나 다시 작성하세요. '
    : '') + (email.status==='manual' ? '직접 수정한 문안입니다. 저장·전송 시 유지합니다.' : email.message || '분석 결과로 메일 초안을 작성할 수 있습니다.');
}
function renderEmail() {
  const email=ensureEmail(doc);
  $('emailSubject').value=email.subject;
  $('emailBody').value=email.body;
  mailEditor.load(email.delivery);
  updateEmailStatus();
}
function maybeAutoEmail() {
  if (doc && tab==='email' && canAutoDraftEmail(doc,emailDefault,{busy,recovery:!!recoveryDraft,attempted:emailAttempted})) generateEmail(false);
}
async function generateEmail(confirmReplace=true) {
  if (!doc || busy) return;
  const email=ensureEmail(doc);
  if (recoveryDraft) {status('먼저 복구 초안을 복구 또는 버린 뒤 메일을 작성하세요.',true);return;}
  if (confirmReplace && (email.subject||email.body) && !confirm('현재 이메일 제목·본문을 분석 결과로 다시 작성할까요? 직접 수정한 문안도 교체됩니다.')) return;
  // Even an HTTP failure must not leave a previously empty mail blank.
  if(initializeEmptyEmail(doc,emailDefault)){renderEmail();markDirty();}
  emailAttempted=true;
  lock(true);
  $('emailStatus').textContent='분석 결과로 로컬 LLM이 문안을 다듬는 중… 현재 문안은 유지합니다.';
  try {
    syncTransfer();
    const result=await (await api(`/api/reports/${storageId}/email-draft`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({document:doc})})).json();
    doc.email={...result,delivery:{...(email.delivery||{}),bodyHtml:''}};
    emailBaselineKey=emailContextKey(doc);
    emailStale=false;
    renderEmail();
    markDirty();
    status('이메일 초안을 작성했습니다. 내용 확인 후 저장하세요. 실제 메일은 발송하지 않았습니다.');
  } catch(e) {
    $('emailStatus').textContent='메일 작성 실패: '+e.message+' 기존 문안은 유지했습니다.';
    status(e.message,true);
  } finally {lock(false);}
}
function updatePreview() {
  if (doc) livePreview.schedule(true);
}
async function action(kind) {
  if (busy || !doc) return;
  if (kind === 'preview') { updatePreview(); return; }
  lock(true);
  try {
    syncTransfer();
    if (dirty || !doc.revision) await save();
    if (kind === 'save') return;
    const sending = kind === 'send';
    if (sending) kind = 'zip';
    const label = { pdf: 'PDF', pptx: 'PPTX', zip: '전달 ZIP' }[kind];
    status(
      kind === 'zip'
        ? 'PDF·PPTX·뷰어·원본 PDF/TXT를 ZIP으로 묶는 중…'
        : `${label} 보고서를 만드는 중…`,
    );
    const job = await startExport(storageId, doc.revision, kind, {
      viewer_state:deliveryView(), analysis_revision:project.analysis_revision || 0
    }, text => status(text));
    if (sending) {
      await api(`/api/jobs/${job.job_id}/send`, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(transferValues())});
      status('MariaDB 전송 큐에 등록했습니다. 실제 LIMS 전송은 Spring Boot가 처리합니다.');
    } else {
      downloadJob(job);
      status(`저장 버전 ${doc.revision}의 ${label} 다운로드를 시작했습니다.`);
    }
    updatePreview();
  } catch (e) {
    status(e.message, true);
  } finally {
    lock(false);
  }
}
const numberOrNull = (value) => (String(value).trim() === '' ? null : Number(value));
function syncTransfer() {
  if (!doc) return;
  const transfer = transferValues();
  for (const [field, key] of [['experimentCode','experiment_code'], ['equipmentCode','equipment_code'], ['operatorId','operator_id']]) {
    if (doc.metadata[key] !== transfer[field]) {
      doc.metadata[key] = transfer[field];
      markDirty();
    }
  }
}
mountReportPlatform(() => action('send'), syncTransfer);
async function loadFigureHints() {
  figureHints = { identical: [], notes: {} };
  if (!doc?.revision || dirty) return;
  try {
    figureHints = await (
      await api(`/api/reports/${storageId}/figure/similarity?revision=${doc.revision}`)
    ).json();
    if (tab === 'figure') renderFigure();
  } catch {
    // Hints are optional: the figure tab works without them.
  }
}
function renderFigure() {
  $('figureForm').innerHTML = figureHTML({
    figure: ensureFigure(doc),
    samples: doc.samples,
    hints: figureHints,
    pick: figurePick,
  });
}
function figureInput(element) {
  const figure = ensureFigure(doc),
    key = element.dataset.figure;
  if (key === 'x_min' || key === 'x_max') {
    const lo = numberOrNull($('figureForm').querySelector('[data-figure="x_min"]').value),
      hi = numberOrNull($('figureForm').querySelector('[data-figure="x_max"]').value);
    figure.x_range = lo === null || hi === null ? null : [lo, hi];
  } else if (key === 'spacing') figure.spacing = numberOrNull(element.value);
  else figure[key] = element.type === 'checkbox' ? element.checked : element.value;
  markDirty();
}
function figureClick(button) {
  const figure = ensureFigure(doc),
    d = button.dataset,
    picked = [...figurePick].filter((id) => doc.samples.some((s) => s.sample_id === id));
  try {
    if (button.id === 'figureAddGroup') {
      addGroup(figure, $('figureGroupName').value, picked);
      figurePick.clear();
    } else if (d.groupSet !== undefined) setGroupMembers(figure, Number(d.groupSet), picked);
    else if (d.groupDelete !== undefined) removeGroup(figure, Number(d.groupDelete));
    else if (d.groupMove !== undefined) moveGroup(figure, Number(d.groupMove), Number(d.direction));
    else if (d.figureChip !== undefined) {
      figurePick = new Set(figureHints.identical[Number(d.figureChip)]);
      renderFigure();
      return;
    }
  } catch (e) {
    status(e.message, true);
    return;
  }
  markDirty();
  renderFigure();
}
async function sortBySimilarity() {
  lock(true);
  try {
    if (dirty || !doc.revision) await save();
    const value = await (
      await api(`/api/reports/${storageId}/figure/similarity?revision=${doc.revision}`)
    ).json();
    figureHints = value;
    applyOrder(doc, value.order);
    markDirty();
    render();
    status('상 구성 유사도 순서를 보고서 시료 순서에 적용했습니다. 확인 후 저장하세요.');
  } catch (e) {
    status(e.message, true);
  } finally {
    lock(false);
  }
}
function importViewer() {
  const view = deliveryView();
  if (!view.projectId) {
    status(
      '이 브라우저에 저장된 뷰어 보기가 없습니다. 뷰어에서 보기를 조정한 뒤 다시 시도하세요.',
      true,
    );
    return;
  }
  doc.figure = figureFromViewer(ensureFigure(doc), view);
  markDirty();
  renderFigure();
  status(
    '뷰어의 현재 보기(정규화, 간격, 가로축 범위, 표시 설정)를 가져왔습니다. 저장하면 반영됩니다.',
  );
}
async function fetchClassData(percent) {
  const query = percent === undefined ? '' : `&percent=${encodeURIComponent(percent)}`;
  return await (
    await api(`/api/reports/${storageId}/phase-classes?revision=${doc.revision}${query}`)
  ).json();
}
async function loadClassData() {
  const seq = ++pcSeq;
  if (!doc?.revision) {
    pcData = null;
    pcNotice = '저장한 보고서의 상 목록을 불러옵니다. 먼저 저장하세요.';
  } else {
    try {
      const data = await fetchClassData();
      if (seq !== pcSeq) return;
      pcData = data;
      pcNotice = '';
    } catch (e) {
      if (seq !== pcSeq) return;
      pcData = null;
      pcNotice = e.message;
    }
  }
  if (tab === 'classes') renderClasses();
}
function renderClasses() {
  const focused = document.activeElement?.id;
  $('classForm').innerHTML = phaseClassesHTML({
    pc: ensurePhaseClasses(doc),
    samples: doc.samples.filter((s) => s.included),
    data: pcData,
    percent: pcPercent,
    notice: pcNotice,
  });
  if (focused === 'pcPercent') $('pcPercent').focus();
}
function classInput(element) {
  const pc = ensurePhaseClasses(doc),
    d = element.dataset;
  const sample = (id) => visibleClassSamples(pcData, doc.samples).find((v) => v.sample_id === id);
  if (d.pc !== undefined) pc[d.pc] = element.value;
  else if (d.pcSampleId !== undefined) {
    const found = sample(d.pcSampleId),
      item = found?.phases[Number(d.pcPhaseIndex)];
    if (!item) return;
    setLabel(pc, found.sample_id, item.phase, element.value);
  } else if (d.pcSampleAll !== undefined) {
    if (element.value === '__none') return;
    const found = sample(d.pcSampleAll);
    if (!found) return;
    setSampleLabel(
      pc,
      found.sample_id,
      found.phases.map((p) => p.phase),
      element.value,
    );
  }
  markDirty();
  if (d.pc === undefined) renderClasses();
}
async function applyTraceRule() {
  const percent = Number(pcPercent);
  if (pcPercent.trim() === '' || !Number.isFinite(percent) || percent < 0 || percent > 100) {
    status('미량상 기준 비율을 0 이상 100 이하의 숫자로 입력하세요.', true);
    return;
  }
  lock(true);
  try {
    if (dirty || !doc.revision) await save();
    const data = await fetchClassData(percent);
    pcSeq++;
    applyTrace(ensurePhaseClasses(doc), visibleClassSamples(data, doc.samples), percent);
    pcData = data;
    pcNotice = '';
    markDirty();
    renderClasses();
    status(
      '미량상 규칙을 적용했습니다. 판정할 수 없는 상은 주요상(기본)으로 두었고 직접 지정한 분류는 유지했습니다. 확인 후 저장하세요.',
    );
  } catch (e) {
    status(e.message, true);
  } finally {
    lock(false);
  }
}
function applyRowClass() {
  const visible = visibleClassSamples(pcData, doc.samples),
    phase = distinctPhases(visible)[Number($('pcRowPhase').value)];
  if (!phase) return;
  const ids = visible
    .filter((s) =>
      s.phases.some(
        (p) =>
          p.phase.name === phase.name && p.phase.card === phase.card && p.phase.db === phase.db,
      ),
    )
    .map((s) => s.sample_id);
  setRowLabel(ensurePhaseClasses(doc), phase, ids, $('pcRowClass').value);
  markDirty();
  renderClasses();
}
async function loadQuantityTable() {
  const seq = ++qtSeq;
  if (!doc?.revision) {
    qtData = null;
    qtNotice = '저장한 보고서의 표를 불러오면 미리 볼 수 있습니다. 먼저 저장하세요.';
  } else {
    try {
      const data = await (
        await api(`/api/reports/${storageId}/quantity-table?revision=${doc.revision}`)
      ).json();
      if (seq !== qtSeq) return;
      qtData = data;
      qtNotice = '';
    } catch (e) {
      if (seq !== qtSeq) return;
      qtData = null;
      qtNotice = e.message;
    }
  }
  if (tab === 'figure') renderQuantityTable();
}
function renderQuantityTable() {
  $('qtableForm').innerHTML = quantityTableHTML({
    include: ensureQuantityTable(doc).include,
    table: qtData,
    notice: qtNotice,
    storageId,
  });
}
function renderPresets(selectedId = '') {
  $('presetSelect').innerHTML = presetOptionsHTML(presets, selectedId);
}
async function loadPresets(selectedId = '') {
  try {
    presets = await (await api('/api/presets')).json();
  } catch (e) {
    presets = [];
    status(e.message, true);
  }
  renderPresets(selectedId);
}
function selectedPreset() {
  return presets.find((p) => p.id === $('presetSelect').value) || null;
}
$('presetApply').onclick = () => {
  const preset = selectedPreset();
  if (!doc || !preset) {
    status('적용할 프리셋을 선택하세요.', true);
    return;
  }
  applyPreset(doc, preset.settings);
  if (preset.settings.phase_classes.trace_percent !== null)
    pcPercent = String(preset.settings.phase_classes.trace_percent);
  markDirty();
  render();
  status(
    `프리셋 "${preset.name}"을 적용했습니다(미저장). 그래프·정량 표·분류 기준 문구만 바뀌었고 분류는 자동으로 채우지 않았습니다. 확인 후 저장하세요.`,
  );
};
$('presetSave').onclick = async () => {
  if (busy || !doc) return;
  const name = $('presetName').value.trim();
  if (!name) {
    status('프리셋 이름을 입력하세요.', true);
    return;
  }
  lock(true);
  try {
    const body = (overwrite) =>
      JSON.stringify({ name, settings: settingsFromDoc(doc, pcPercent), overwrite });
    const send = (overwrite) =>
      api('/api/presets', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: body(overwrite),
      });
    let saved;
    try {
      saved = await (await send(false)).json();
    } catch (e) {
      if (e.status !== 409) throw e;
      if (!confirm(`"${name}" 프리셋이 이미 있습니다. 덮어쓸까요?`)) return;
      saved = await (await send(true)).json();
    }
    await loadPresets(saved.id);
    $('presetName').value = '';
    status(`프리셋 "${saved.name}"을 저장했습니다.`);
  } catch (e) {
    status(e.message, true);
  } finally {
    lock(false);
  }
};
$('presetDelete').onclick = async () => {
  const preset = selectedPreset();
  if (busy || !preset) {
    status('삭제할 프리셋을 선택하세요.', true);
    return;
  }
  if (!confirm(`프리셋 "${preset.name}"을 삭제할까요?`)) return;
  lock(true);
  try {
    await api(`/api/presets/${encodeURIComponent(preset.id)}`, { method: 'DELETE' });
    await loadPresets();
    status(`프리셋 "${preset.name}"을 삭제했습니다.`);
  } catch (e) {
    status(e.message, true);
  } finally {
    lock(false);
  }
};
async function loadChecklist() {
  const seq = ++ckSeq;
  if (!doc?.revision) {
    ckData = null;
    ckNotice = '저장한 보고서의 점검 결과를 불러오면 표시됩니다. 먼저 저장하세요.';
  } else {
    try {
      const data = await (
        await api(`/api/reports/${storageId}/checklist?revision=${doc.revision}`)
      ).json();
      if (seq !== ckSeq) return;
      ckData = data;
      ckNotice = '';
    } catch (e) {
      if (seq !== ckSeq) return;
      ckData = null;
      ckNotice = e.message;
    }
  }
  renderPrecheck();
  if (step === 'save') renderChecklist();
}
function renderChecklist() {
  $('checksForm').innerHTML = checklistHTML({ data: ckData, notice: ckNotice, dirty });
}
function renderPrecheck() {
  const failed = !ckData && !!doc?.revision && !!ckNotice;
  $('precheckText').textContent = precheckText(ckData?.summary, failed ? ckNotice : '', dirty);
  $('precheck').classList.toggle('blocked', !!ckData?.summary.blockers || failed);
  if (ckData)
    window.dispatchEvent(
      new CustomEvent('xrd-shell', {
        detail: {
          issues: ckData.summary.blockers + ckData.summary.warnings,
          blocked: ckData.summary.blockers > 0,
        },
      }),
    );
}
$('reportNav').onclick = (e) => {
  const target = e.target.closest('[data-tab]');
  if (target) showTab(target.dataset.tab);
};
$('editorFields').oninput = (e) => {
  if (!doc || busy) return;
  const element = e.target,
    d = element.dataset;
  if (d.qt === 'include') {
    ensureQuantityTable(doc).include = element.checked;
    markDirty();
    return;
  }
  if (element.id === 'pcPercent') {
    pcPercent = element.value;
    return;
  }
  if (d.pc !== undefined || d.pcSampleId !== undefined || d.pcSampleAll !== undefined) {
    classInput(element);
    return;
  }
  if (d.figure !== undefined) {
    figureInput(element);
    return;
  }
  if (d.figurePick !== undefined) {
    if (element.checked) figurePick.add(d.figurePick);
    else figurePick.delete(d.figurePick);
    return;
  }
  if (d.groupTitle !== undefined) {
    try {
      renameGroup(ensureFigure(doc), Number(d.groupTitle), element.value);
      markDirty();
    } catch {
      // An empty name is not applied; the previous name stays in the document.
    }
    return;
  }
  if (d.email) {
    const email=ensureEmail(doc);
    email[d.email]=element.value;
    email.status='manual';
  }
  else if (d.meta) doc.metadata[d.meta] = element.value;
  else if (element.id === 'summary') doc.summary = element.value;
  else if (['provider', 'baseUrl', 'model'].includes(element.id))
    doc.llm[{ provider: 'provider', baseUrl: 'base_url', model: 'model' }[element.id]] =
      element.value;
  else if (d.included || d.summary) {
    const entry = doc.samples.find((s) => s.sample_id === (d.included || d.summary));
    entry[d.included ? 'included' : 'summary_included'] = element.checked;
    if (d.included) renderViewerSummary();
  } else {
    const entry = doc.samples.find((s) => s.sample_id === tab);
    if (!entry) return;
    if (d.field) entry[d.field] = element.type === 'checkbox' ? element.checked : element.value;
    if (d.noteText) {
      const note = entry.notes.find((n) => n.id === d.noteText);
      note.text = element.value;
      note.edited = true;
      note.origin = 'user';
    }
    if (d.noteSelected) {
      const note = entry.notes.find((n) => n.id === d.noteSelected);
      note.selected = element.checked;
      note.selection_edited = true;
    }
    if (d.retainedText)
      entry.retained_notes.find((n) => n.id === d.retainedText).text = element.value;
    if (d.retainedSelected)
      entry.retained_notes.find((n) => n.id === d.retainedSelected).selected = element.checked;
  }
  markDirty();
};
$('editorFields').onchange = (e) => {
  if (e.target.id === 'provider') {
    const type = e.target.value;
    doc.llm.base_url = type === 'lmstudio' ? 'http://127.0.0.1:1234/v1' : 'http://127.0.0.1:11434';
    $('baseUrl').value = doc.llm.base_url;
    markDirty();
  }
};
$('editorFields').onclick = async (e) => {
  const button = e.target.closest('button');
  if (!button || busy) return;
  if (button.id === 'generateEmail') {await generateEmail();return;}
  const d = button.dataset;
  if (d.goto !== undefined) {
    showTab(d.goto);
    return;
  }
  if (button.id === 'checkNext') {
    const target = nextSampleToCheck(ckData, ckCursor);
    if (target) showTab(target);
    else status('확인이 필요한 시료가 없습니다.');
    return;
  }
  if (button.id === 'pcApply') {
    await applyTraceRule();
    return;
  }
  if (button.id === 'pcRowApply') {
    applyRowClass();
    return;
  }
  if (button.id === 'figureSort') {
    await sortBySimilarity();
    return;
  }
  if (button.id === 'figureImport') {
    importViewer();
    return;
  }
  if (
    button.id === 'figureAddGroup' ||
    d.groupSet !== undefined ||
    d.groupDelete !== undefined ||
    d.groupMove !== undefined ||
    d.figureChip !== undefined
  ) {
    figureClick(button);
    return;
  }
  if (d.move || d.noteMove) {
    const entry = doc.samples.find((s) => s.sample_id === tab),
      list = d.move ? doc.samples : entry.notes;
    const index = list.findIndex((s) => (s.sample_id || s.id) === (d.move || d.noteMove)),
      other = index + Number(d.direction);
    if (other >= 0 && other < list.length) {
      [list[index], list[other]] = [list[other], list[index]];
      if (d.noteMove) entry.note_order_edited = true;
      markDirty();
      render();
    }
    return;
  }
  if (button.id === 'generateNotes') {
    const targetId = tab;
    lock(true);
    try {
      if (dirty || !doc.revision) await save();
      status('근거 후보를 생성하는 중…');
      doc = await (
        await api(`/api/reports/${storageId}/notes/${targetId}`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ expected_revision: doc.revision }),
        })
      ).json();
      dirty = false;
      render();
      status(doc.samples.find((s) => s.sample_id === targetId).generation.message);
    } catch (e) {
      status(e.message, true);
    } finally {
      lock(false);
    }
  }
};
for (const [id, kind] of [
  ['save', 'save'],
  ['preview', 'preview'],
  ['pdfExport', 'pdf'],
  ['pptxExport', 'pptx'],
  ['zipExport', 'zip'],
])
  $(id).onclick = () => action(kind);
// ---- steps ③ 보고서 편집 / ④ 보고서 저장 share this page and its unsaved state.
let step = new URLSearchParams(location.search).get('step') === 'save' ? 'save' : 'report',
  leaving = false;
function syncShell() {
  window.dispatchEvent(
    new CustomEvent('xrd-shell', { detail: { project: storageId, dirty, step } }),
  );
}
function setStep(value) {
  step = value;
  const saving = value === 'save';
  $('reportNav').hidden = $('editor').hidden = saving;
  $('savePane').hidden = !saving;
  $('previewPane').hidden = false;
  $('nextStep').hidden = saving;
  history.replaceState(
    null,
    '',
    apiUrl(`/report.html?project=${encodeURIComponent(storageId)}${saving ? '&step=save' : ''}`),
  );
  syncShell();
  if (saving) {
    renderChecklist();
    loadChecklist();
    renderViewerSummary();
  }
}
window.xrdLocalStep = (value) => {
  if (value !== 'report' && value !== 'save') return false;
  setStep(value);
  return true;
};
window.xrdCanReset = () => {
  if (!busy) return true;
  window.alert('저장·보고서 처리 중입니다. 완료된 뒤 초기화하세요.');
  return false;
};
window.xrdBeforeLeave = async () => {
  if (!dirty) return true;
  if (confirm('저장하지 않은 변경이 있습니다. 저장하고 이동할까요?')) {
    lock(true);
    try {
      await save();
      return true;
    } catch (e) {
      status(e.message, true);
      return false;
    } finally {
      lock(false);
    }
  }
  leaving = confirm('저장하지 않고 이동할까요? 변경 내용은 사라집니다.');
  return leaving;
};
// Project removal can fail after the leave confirmation (network/revision conflict).
window.xrdLeaveCanceled = () => { leaving = false; };
$('nextStep').onclick = () => setStep('save');
function openSettings() {
  $('settingsDialog').showModal();
}
window.addEventListener('xrd-settings', openSettings);
$('settingsClose').onclick = () => $('settingsDialog').close();
$('checksForm').onclick = (event) => {
  const button = event.target.closest('button');
  if (!button) return;
  if (button.dataset.goto !== undefined) {
    setStep('report');
    showTab(button.dataset.goto);
  } else if (button.id === 'checkNext') {
    const target = nextSampleToCheck(ckData, ckCursor);
    if (!target) {
      status('확인이 필요한 시료가 없습니다.');
      return;
    }
    setStep('report');
    showTab(target);
  }
};
$('viewerExport').onclick = async () => {
  if (busy || !project) return;
  lock(true);
  try {
    const response = await api('/api/export/html', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        project: {
          id: project.id,
          storage_id: storageId,
          analysis_revision: project.analysis_revision || 0,
        },
        state: deliveryView(),
      }),
    });
    const url = URL.createObjectURL(await response.blob()),
      a = document.createElement('a');
    a.href = url;
    a.download = 'XRD-그래프-뷰어.html';
    a.click();
    setTimeout(() => URL.revokeObjectURL(url), 3000);
    status('현재 뷰어 보기를 담은 뷰어 HTML을 저장했습니다.');
  } catch (e) {
    status(e.message, true);
  } finally {
    lock(false);
  }
};
window.addEventListener('beforeunload', (event) => {
  if (dirty && !leaving) {
    event.preventDefault();
    event.returnValue = '';
  }
});
async function start() {
  if (!storageId) {
    location.replace(apiUrl('/?step=upload&fresh=1'));
    return;
  }
  try {
    await openProject(storageId);
    await loadPresets();
  } catch (e) {
    status(e.message, true);
  }
}
start();
function deliveryView() {
  if (!project || !$('includeViewerState').checked) return {};
  try {
    const saved = JSON.parse(localStorage.getItem(viewerStorageKey(project)) || 'null');
    return saved?.projectId === project.id ? restoreState(project, saved) : {};
  } catch {
    return {};
  }
}
function renderViewerSummary() {
  if (!project) return;
  const view = deliveryView();
  const included = new Set(doc.samples.filter((s) => s.included).map((s) => s.sample_id));
  $('viewerSummary').textContent = view.projectId
    ? `${view.viewMode === 'single' ? '시료 하나씩 보기' : '여러 시료 비교'} · ${view.offsets.raw.spacing === 0 ? '겹쳐 보기' : view.offsets.raw.spacing > 0 ? '첫 시료 위' : '반대 순서'} · 표시 ${view.visible.filter((id) => included.has(id)).length}시료 · 결정상 마커 ${view.markers ? '켜짐' : '꺼짐'} · 기준 피크 ${view.peakbar ? '켜짐' : '꺼짐'}`
    : '초기 보기로 전달';
}
$('includeViewerState').onchange = renderViewerSummary;
window.addEventListener('storage', (e) => {
  if (project && e.key === viewerStorageKey(project)) renderViewerSummary();
});
$('refreshAnalysis').onclick = async () => {
  if (busy || !doc) return;
  lock(true);
  try {
    const value = await (await api(`/api/reports/${storageId}`)).json();
    const old = doc;
    doc = mergeDraftChanges(value.document, old, baseline);
    baseline = structuredClone(value.document);
    project = value.project;
    dirty = true;
    pcData = null;
    qtData = null;
    ckData = null;
    render();
    loadChecklist();
    $('reportPreview').removeAttribute('srcdoc');
    $('reportPreview').src = 'about:blank';
    status('분석값을 갱신하고 미저장 작성 내용을 보존했습니다. 해당 시료를 검토한 뒤 저장하세요.');
  } catch (e) {
    status(e.message, true);
  } finally {
    lock(false);
  }
};
window.addEventListener('storage', (e) => {
  if (e.key === `xrd-analysis-updated-${storageId}`) {
    livePreview.invalidate();
    $('analysisBanner').hidden = false;
    $('analysisBanner').textContent =
      '분석 검토값이 변경되었습니다. 분석값 갱신을 누르면 미저장 작성 내용도 보존합니다.';
    $('reportPreview').removeAttribute('srcdoc');
    $('reportPreview').src = 'about:blank';
    ckData = null;
    loadChecklist();
  }
});
