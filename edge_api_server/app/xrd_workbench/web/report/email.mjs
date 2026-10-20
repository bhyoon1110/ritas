// Mail is editable report data, never HTML or an instruction to send anything.
export function mountEmailEditor(fields, navigation) {
  const pane=document.createElement('div');
  pane.id='emailForm';pane.className='formPane';pane.hidden=true;
  pane.innerHTML='<h1>보고서 안내 메일</h1><p class="intro">현재 포함 시료·상 분류·WPPF 정량값·선택한 특이사항과 전체 요약으로 기본 문안을 먼저 채웁니다. 로컬 LLM으로 문안을 다듬고 직접 수정할 수 있으며, 보고서 페이지에는 추가하지 않습니다.</p>'+
    '<div class="generationActions"><button type="button" id="generateEmail">분석 결과로 다시 작성</button></div>'+
    '<p id="emailStatus" class="generationLine" role="status" aria-live="polite"></p>'+
    '<label>이메일 제목<input id="emailSubject" data-email="subject" maxlength="180" autocomplete="off"></label>'+
    '<label>이메일 본문<textarea id="emailBody" data-email="body" rows="16" maxlength="4000"></textarea></label>'+
    '<p class="hint">문안은 서버의 보고서 및 복구 초안에 보존하며, 전달 ZIP에는 별도 메일 파일을 넣지 않습니다. 이 화면에서 실제 이메일을 발송하지 않습니다. 사내 SSO와 보고서 전송 큐 정책은 그대로 적용됩니다.</p>';
  fields.insertBefore(pane,fields.querySelector('#sampleForm'));
  const button=document.createElement('button');
  button.className='sectionNav';button.dataset.tab='email';button.textContent='안내 메일';
  navigation.querySelector('[data-tab="overview"]').insertAdjacentElement('afterend',button);
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
  doc.email=structuredClone(fallback);
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
    if (current[key] !== (baseline || {})[key]) result[key] = current[key];
  }
  return result;
}
