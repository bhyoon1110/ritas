import test from 'node:test';
import assert from 'node:assert/strict';
import {ensureEmail, emailContextKey, mergeEmailFields} from '../../app/xrd_workbench/web/report/email.mjs';
import {mergeDraftChanges} from '../../app/xrd_workbench/web/report/report_merge.mjs';

test('old drafts get mail fields, while authored text is never reset',()=>{
  const doc={};assert.equal(ensureEmail(doc).status,'empty');
  doc.email.body='직접 수정';assert.equal(ensureEmail(doc).body,'직접 수정');
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
