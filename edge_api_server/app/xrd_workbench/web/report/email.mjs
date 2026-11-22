import '../shared/mail_editor_icons.js';
import '../shared/mail_table_editor.js';
import '../shared/mail_address_book.js';
import '../shared/mail_template_context.js';
import '../shared/mail_templates.js';
import '../shared/mail_editor.js';
// Mail is author-only draft data, never an instruction to send anything.
export function mountEmailEditor(fields, navigation, changed, requestNumber, context) {
  const pane=document.createElement('div');
  pane.id='emailForm';pane.className='formPane';
  pane.innerHTML='<h2>보고서 안내 메일</h2><p class="intro">의뢰자와 분석 결과를 확인하고 제목·본문을 수정하세요. 메일 템플릿과 서명을 적용할 수 있습니다.</p>'+
    '<div class="generationActions"><button type="button" id="generateEmail">분석 결과로 다시 작성</button></div>'+
    '<p id="emailStatus" class="generationLine" role="status" aria-live="polite"></p>'+
    '<label>이메일 제목<input id="emailSubject" data-email="subject" maxlength="180" autocomplete="off"></label>'+
    '<label>이메일 본문<textarea id="emailBody" data-email="body" rows="16" maxlength="4000"></textarea></label>'+
    '<p class="hint">입력 내용은 초안에 자동 저장됩니다. 아래에서 최종 내용을 저장하거나 보고서 전송 큐에 등록하세요.</p>';
  fields.appendChild(pane);
  const button=document.createElement('button');
  button.className='sectionNav';button.dataset.tab='email';button.textContent='의뢰·메일 →';
  button.title='보고서 저장·전송에서 의뢰 조회와 안내 메일 편집';
  navigation.querySelector('[data-tab="overview"]').insertAdjacentElement('afterend',button);
  return new window.RistMailEditor({host:pane,body:pane.querySelector('#emailBody'),
    before:pane.querySelector('#emailSubject').parentNode,changed,requestNumber,
    subject:pane.querySelector('#emailSubject'),experiment:'XRD',context});
}

export function ensureEmail(doc) {
  if (!doc.email) doc.email = {subject:'',body:'',status:'empty',source_fingerprint:'',model_used:'',message:''};
  return doc.email;
}

export function initializeEmptyEmail(doc, fallback) {
  const email=ensureEmail(doc);
  // A blank draft may have an old/manual status. Content, not status, decides initialization.
  // Keep any authored subject/body, including a deliberately partial mail.
  if ([email.subject,email.body].some(v=>String(v||'').trim()) || !fallback?.subject || !fallback?.body) return false;
  doc.email={...structuredClone(fallback),...(email.delivery?{delivery:structuredClone(email.delivery)}:{})};
  return true;
}

export function canAutoDraftEmail(doc, seed, {busy, recovery, attempted}) {
  const email=ensureEmail(doc);
  return !busy && !recovery && !attempted && !!seed && email.status==='rules'
    && email.subject===seed.subject && email.body===seed.body;
}

export function emailContextKey(doc) {
  return JSON.stringify([doc.analysis_revision, doc.metadata, doc.summary, doc.phase_classes,
    doc.samples.filter(s=>s.included).map(s=>[s.sample_id,s.display_name,s.manual_note,
      s.notes.filter(n=>n.selected).map(n=>n.text),
      (s.retained_notes||[]).filter(n=>n.selected).map(n=>n.text),s.analysis_review_required])]);
}

export function mergeEmailFields(latest, current, baseline) {
  const result = {...(latest || {})};
  for (const key of Object.keys(current || {})) {
    if (JSON.stringify(current[key]) !== JSON.stringify((baseline || {})[key])) result[key] = current[key];
  }
  return result;
}
