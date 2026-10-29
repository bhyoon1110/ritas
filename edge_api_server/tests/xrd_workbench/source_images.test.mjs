import test from 'node:test';
import assert from 'node:assert/strict';
import {phaseImageChoices,phaseImageHTML} from '../../app/xrd_workbench/web/report/source_images.mjs';
import {mergeDraftChanges} from '../../app/xrd_workbench/web/report/report_merge.mjs';

test('report selector can repair an orphan but cannot pick another sample or a quantity image',()=>{
  const orphan={id:'o',role:'ignore',filename:'<원본>.png'},own={id:'a',role:'phase_image',filename:'A.jpg'},other={id:'b',role:'phase_image',filename:'B.jpg'};
  const sample={id:'A',sources:[own]}, project={samples:[sample,{id:'B',sources:[other]}],sources:[own,orphan,other,{id:'q',role:'quantity_image',filename:'A_정량.png'}]};
  assert.deepEqual(phaseImageChoices(project,sample),[own,orphan]);
  const entry={phase_caption:'<test>',phase_image_source_id:'o'};
  const html=phaseImageHTML(project,sample,entry,r=>'/source/'+r.id);
  assert.match(html,/value="o" selected/);
  assert.match(html,/src="\/source\/o"/);
  assert.match(html,/&lt;원본&gt;\.png/);
  assert.doesNotMatch(html,/B.jpg|A_정량/);
  assert.match(phaseImageHTML(project,{id:'C',sources:[]},{phase_caption:''},r=>r.id),/연결된 원본이 없어 뷰어/);
  assert.match(phaseImageHTML(project,sample,{...entry,phase_image_source_id:'missing'},r=>r.id),/선택한 원본을 찾지 못했습니다/);
});

test('draft recovery retains an explicit source choice and a deliberate reset',()=>{
  const base={metadata:{},llm:{},samples:[{sample_id:'A',notes:[],phase_image_source_id:''}]};
  const changed=structuredClone(base);changed.samples[0].phase_image_source_id='orphan';
  const latest={...structuredClone(base),revision:2};
  assert.equal(mergeDraftChanges(latest,changed,base).samples[0].phase_image_source_id,'orphan');
  assert.equal(mergeDraftChanges(changed,base,changed).samples[0].phase_image_source_id,'');
});
