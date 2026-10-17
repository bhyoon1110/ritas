import test from 'node:test';
import assert from 'node:assert/strict';
import {ensureEmail, emailContextKey, mergeEmailFields, initializeEmptyEmail, canAutoDraftEmail} from '../../app/xrd_workbench/web/report/email.mjs';
import {mergeDraftChanges} from '../../app/xrd_workbench/web/report/report_merge.mjs';

test('old drafts get mail fields, while authored text is never reset',()=>{
  const doc={};assert.equal(ensureEmail(doc).status,'empty');
  doc.email.body='직접 수정';assert.equal(ensureEmail(doc).body,'직접 수정');
});
test('blank mail gets an immediate seed regardless of old status, authored partial mail stays intact',()=>{
  const seed={subject:'분석 제목',body:'분석 결과 본문',status:'rules'};
  for(const status of ['empty','manual','llm','fallback']) {
    const doc={email:{subject:'  ',body:'\n',status}};
    assert.equal(initializeEmptyEmail(doc,seed),true);
    assert.deepEqual(doc.email,seed);assert.notEqual(doc.email,seed);
  }
  for(const email of [{subject:'직접 제목',body:''},{subject:'',body:'직접 본문'}]) {
    const doc={email:structuredClone(email)};
    assert.equal(initializeEmptyEmail(doc,seed),false);assert.deepEqual(doc.email,email);
  }
});
test('deferred automatic model request resumes after busy/recovery but never replaces authored mail',()=>{
  const seed={subject:'분석 제목',body:'분석 결과 본문',status:'rules'},doc={email:structuredClone(seed)};
  const ready={busy:false,recovery:false,attempted:false};
  for(const key of Object.keys(ready)) assert.equal(canAutoDraftEmail(doc,seed,{...ready,[key]:true}),false);
  assert.equal(canAutoDraftEmail(doc,seed,ready),true);
  doc.email.status='manual';assert.equal(canAutoDraftEmail(doc,seed,ready),false);
  doc.email.status='rules';doc.email.body='복구된 다른 문안';
  assert.equal(canAutoDraftEmail(doc,seed,ready),false);
});
test('source change warning ignores mail editing and graph appearance',()=>{
  const doc={analysis_revision:0,metadata:{},summary:'',phase_classes:{},samples:[{sample_id:'A',included:true,notes:[]}]};
  const key=emailContextKey(doc);ensureEmail(doc).body='manual';doc.figure={spacing:12};
  assert.equal(emailContextKey(doc),key);
  doc.samples[0].display_name='new';assert.notEqual(emailContextKey(doc),key);
});
test('rebase preserves edited subject without clobbering another saved body',()=>{
  assert.deepEqual(mergeEmailFields({subject:'old',body:'other'},{subject:'mine',body:'saved'},{subject:'old',body:'saved'}),{subject:'mine',body:'other'});
  const base={metadata:{},llm:{},summary:'',samples:[],email:{subject:'old',body:'saved'}};
  const current=structuredClone(base);current.email.subject='mine';
  const latest=structuredClone(base);latest.email.body='other';
  assert.deepEqual(mergeDraftChanges(latest,current,base).email,{subject:'mine',body:'other'});
});
