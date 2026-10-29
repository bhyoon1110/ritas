const {test}=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const {JSDOM}=require('jsdom'),acorn=require('acorn');
const source=['report_editor_icons.js','report_ppt_templates.js'].map(f=>fs.readFileSync(path.join(__dirname,'../app',f),'utf8')).join('\n');
const id='shape-'+'a'.repeat(20),table='shape-'+'b'.repeat(20),key='a'.repeat(64);
function setup(t){const dom=new JSDOM('<div id="host"></div>',{url:'http://localhost',runScripts:'outside-only'});t.after(()=>dom.window.close());dom.window.eval(source);return dom.window;}
const plain=v=>JSON.parse(JSON.stringify(v));
function layout(page='new'){return {canvasEditing:true,ratio:16/9,pages:[{id:page,templateKey:key,objects:[
  {id,kind:'text',text:'새 분석결과',x:.1,y:.1,w:.4,h:.2},
  {id:table,kind:'table',cells:[{row:0,col:0,text:'새 시료'},{row:1,col:0,text:'현재 값'}]}
]}]};}
test('ES5 capture excludes results, graph assets, hidden/added objects and page manipulation',t=>{
  acorn.parse(source,{ecmaVersion:5});const w=setup(t),api=w.RistPptTemplates;
  const page=layout('old');page.pages.push({id:'added-1',objects:[]});
  const state={old:[{id,text:'이전 분석결과',fontSize:24,x:.2,asset:'secret'},
    {id:table,tableCells:[{row:0,col:0,text:'이전 값',bold:true},{row:1,col:0,text:'다른 값'}]},
    {id:'object-extra',kind:'text',text:'이전 시료'}]};
  const data=plain(api.capture(page,state,'FTIR','양식'));
  assert.deepEqual(data.pages,[{key,objects:[{id,kind:'text',fontSize:24,x:.2,y:.1,w:.4,h:.2},
    {id:table,kind:'table',tableCells:[{row:0,col:0,bold:true}]}]}]);
  assert(!JSON.stringify(data).includes('이전'));
  assert.throws(()=>api.capture(page,{old:[{id,text:'내용만 수정'}]},'FTIR','양식'),/서식/);
});
test('mapping skips incompatible pages/cells and preserves target text, graph assets and inserted objects',t=>{
  const w=setup(t),api=w.RistPptTemplates,sourceLayout=layout('old');
  const template=api.capture(sourceLayout,{old:[{id,color:'#123456'},
    {id:table,tableCells:[{row:0,col:0,bold:true},{row:9,col:0,bold:true}]}]},'FTIR','양식');
  const target=layout();target.pages.push({id:'extra',templateKey:'b'.repeat(64),objects:[]});
  const plan=plain(api.plan(template,target,'FTIR'));
  assert.equal(plan.pages,1);assert.equal(plan.objects,2);assert.equal(plan.skipped,1);assert.equal(plan.cellsSkipped,1);
  const current={new:[{id,text:'현재 보고서',asset:'current-image'},
    {id:table,tableCells:[{row:0,col:0,text:'현재 표 값'},{row:1,col:0,text:'유지',color:'#334455'}]},
    {id:'object-added',text:'현재 추가 개체'}],dormant:[{id,text:'유지'}]};
  const result=plain(api.merge(current,plan.edits));
  assert.equal(result.new[0].text,'현재 보고서');assert.equal(result.new[0].asset,'current-image');assert.equal(result.new[0].color,'#123456');
  assert.deepEqual(result.new[1].tableCells,[{row:0,col:0,text:'현재 표 값',bold:true},{row:1,col:0,text:'유지',color:'#334455'}]);
  assert.deepEqual(result.new[2],current.new[2]);assert.deepEqual(result.dormant,current.dormant);
  assert(api.plan(template,target,'XRD').error);target.ratio=4/3;assert(api.plan(template,target,'FTIR').error);
});
test('repeated layout profiles pair in order, not by previous sample IDs',t=>{
  const api=setup(t).RistPptTemplates,source=layout('old1');source.pages.push({...source.pages[0],id:'old2'});
  const template=api.capture(source,{old2:[{id,bold:true}]},'FTIR','순서 양식');
  const target=layout('new1');target.pages.push({...target.pages[0],id:'new2'},{...target.pages[0],id:'new3'});
  const plan=plain(api.plan(template,target,'FTIR'));assert.deepEqual(Object.keys(plan.edits),['new2']);assert.equal(plan.skipped,1);
});
test('manager lists privately, saves, previews, applies and handles concurrent errors without losing input',t=>{
  const w=setup(t),api=w.RistPptTemplates,d=w.document,requests=[];w.confirm=()=>true;
  w.XMLHttpRequest=function(){this.open=(method,url)=>{this.method=method;this.url=url;};this.setRequestHeader=()=>{};
    this.send=body=>{this.body=body&&JSON.parse(body);requests.push(this);};};
  const respond=(status,data)=>{const r=requests.shift();r.status=status;r.responseText=JSON.stringify(data);r.onload();return r;};
  let edits;const manager=new api.Manager({host:d.getElementById('host'),experiment:'FTIR',layout:()=>layout(),state:()=>({new:[{id,bold:true}]}),apply:v=>edits=plain(v)});
  manager.lock(false);d.querySelector('summary').click();assert.equal(requests[0].url,'/api/v1/report-ppt/templates');respond(200,{revision:0,items:[]});
  const input=d.querySelector('input'),button=label=>d.querySelector('[aria-label="'+label+'"]');input.value='내 양식';
  button('현재 PPT 서식을 새 템플릿으로 저장').click();const data=requests[0].body.content;assert.equal(data.experiment,'FTIR');
  respond(201,{revision:1,items:[{id:'one',name:'내 양식',experiment:'FTIR'}],selectedId:'one'});respond(200,{revision:1,content:data});
  assert(d.body.textContent.includes('1개 페이지 · 1개 개체'));
  const apply=[...d.querySelectorAll('button')].find(b=>b.textContent==='양식 적용');manager.lock(true);assert(apply.disabled);manager.lock(false);apply.click();assert.equal(edits.new[0].bold,true);
  input.value='보존할 입력';button('선택한 템플릿을 현재 PPT 서식으로 덮어쓰기').click();respond(409,{message:'다른 창에서 변경'});
  assert.equal(input.value,'보존할 입력');assert(d.body.textContent.includes('다른 창에서 변경'));
});

function external(){return {version:2,name:'외부 양식',experiment:'FTIR',ratio:16/9,source:'d'.repeat(64),bindings:[
  {key,slide:0,background:true,objects:[{source:'slot-0',target:id},{source:'slot-1',target:table}]}]};}
function sourceDocument(){return {ratio:16/9,pages:[{index:0,backgroundDigest:'e'.repeat(64),omitted:1,objects:[
  {id:'slot-0',kind:'text',label:'이전 분석 문장',style:{x:.2,y:.3,w:.6,h:.2,fontSize:30},cells:[]},
  {id:'slot-1',kind:'table',label:'표',style:{x:.1,y:.6,w:.7,h:.3},cells:[{row:0,col:0,bold:true,text:'NEVER COPY'},{row:8,col:0,bold:true}]}
]}]};}
test('external mapping repeats for new samples, preserves values and reports mismatches',t=>{
  const api=setup(t).RistPptTemplates,target=layout();target.pages.push({...target.pages[0],id:'other-sample'});
  const result=plain(api.externalPlan(external(),sourceDocument(),target,'FTIR'));
  assert.equal(result.pages,2);assert.equal(result.backgrounds.length,2);assert.equal(result.cellsSkipped,2);
  assert(!JSON.stringify(result.edits).includes('NEVER COPY'));
  const state={new:[{id,text:'CURRENT ANALYSIS'},{id:table,tableCells:[{row:0,col:0,text:'CURRENT VALUE'}]}]};
  const merged=plain(api.merge(state,result.edits));assert.equal(merged.new[0].text,'CURRENT ANALYSIS');assert.equal(merged.new[1].tableCells[0].text,'CURRENT VALUE');
  assert(api.externalPlan(external(),sourceDocument(),{...target,ratio:4/3},'FTIR').error);
  assert(api.externalPlan(external(),sourceDocument(),target,'TEM').error);
});
function externalManager(t){
  const w=setup(t),d=w.document,requests=[],applied=[];w.confirm=()=>true;
  w.XMLHttpRequest=function(){this.open=(method,url)=>{this.method=method;this.url=url;};this.setRequestHeader=()=>{};this.send=body=>{this.body=body;requests.push(this);};};
  const respond=(data,status=200)=>{const r=requests.shift();r.status=status;r.responseText=JSON.stringify(data);r.onload();return r;};
  let state={},uploaded;
  const manager=new w.RistPptTemplates.Manager({host:d.getElementById('host'),experiment:'FTIR',layout:()=>layout(),state:()=>state,
    apply:v=>applied.push(plain(v)),upload:(file,done)=>{uploaded=done;}});
  manager.lock(false);d.querySelector('summary').click();respond({revision:1,items:[{id:'ext',name:'외부 양식',experiment:'FTIR'}]});
  const select=d.querySelector('[aria-label="PPT 템플릿 선택"]');select.value='ext';select.onchange();respond({revision:1,content:external(),sourceDocument:sourceDocument()});
  return {w,d,manager,requests,respond,applied,setState:s=>state=s,uploaded:()=>uploaded};
}
test('external mapping editor saves bindings without replacing them with current report data',t=>{
  const h=externalManager(t),d=h.d;
  assert.equal(d.querySelector('.ppt-template-links select').value,id);
  const select=d.querySelector('.ppt-template-links select');select.value='';select.onchange();
  const apply=[...d.querySelectorAll('button')].find(b=>b.textContent==='양식 적용');assert(apply.disabled);
  d.querySelector('[aria-label="PPTX 양식 이름·연결 저장"]').click();
  const body=JSON.parse(h.requests[0].body);assert.equal(body.content.version,2);assert.equal(body.content.bindings[0].objects.length,1);
  assert(!JSON.stringify(body).includes('새 분석결과'));
  h.respond({message:'다른 창에서 변경'},409);assert(d.body.textContent.includes('다른 창에서 변경'));
  assert.equal(d.querySelector('.ppt-template-links select').value,'');
});
test('backgrounds apply as one patch and abort if report changes during upload',t=>{
  const h=externalManager(t),apply=[...h.d.querySelectorAll('button')].find(b=>b.textContent==='양식 적용');apply.click();
  const request=h.requests.shift();assert.equal(request.responseType,'blob');request.status=200;request.response=new h.w.Blob(['png'],{type:'image/png'});request.onload();
  h.setState({new:[{id,text:'changed meanwhile'}]});h.uploaded()(null,{asset:'f'.repeat(64)});
  assert.equal(h.applied.length,0);assert(h.d.body.textContent.includes('보고서가 변경'));
  apply.click();const next=h.requests.shift();next.status=200;next.response=request.response;next.onload();h.uploaded()(null,{asset:'f'.repeat(64)});
  assert.equal(h.applied.length,1);assert.equal(h.applied[0].new.at(-1).layer,'back');assert.equal(h.applied[0].new.at(-1).asset,'f'.repeat(64));
});

test('native default removes overrides and template backgrounds without copying or discarding content',t=>{
  const api=setup(t).RistPptTemplates;
  const state={new:[{id,text:'현재 제목',x:.4,y:.5,fontSize:45,hidden:true,layer:'front'},
    {id:table,tableCells:[{row:0,col:0,text:'현재 표 값',bold:true},{row:1,col:0,fill:'#112233'}]},
    {id:'shape-'+'c'.repeat(20),kind:'image',asset:'a'.repeat(64),x:.2},
    {id:'object-template-background',kind:'image',asset:'b'.repeat(64)},
    {id:'object-added',kind:'text',text:'내 추가 개체',x:.1,fontSize:30}],
    dormant:[{id,color:'#123456',text:'숨겨진 페이지의 내용'}],empty:[{id,kind:'text',bold:true}]};
  const before=plain(state),restored=plain(api.nativeDefaults(state));
  assert.deepEqual(restored,{new:[{id,text:'현재 제목'},
    {id:table,tableCells:[{row:0,col:0,text:'현재 표 값'}]},
    {id:'shape-'+'c'.repeat(20),kind:'image',asset:'a'.repeat(64)},state.new[4]],
    dormant:[{id,text:'숨겨진 페이지의 내용'}]});
  assert.deepEqual(state,before);
  assert.equal(api.nativeDefaultError(state,'2026-10-29'),'');
  for(const patch of [{id,text:''},{id,asset:'a'.repeat(64)},{id,tableCells:[{row:0,col:0,text:'보존'}]}])
    assert.match(api.nativeDefaultError({dormant:[patch]},'legacy'),/이전 기본 양식/);
  assert.equal(api.nativeDefaultError({new:[{id,fontSize:25},state.new[4]]},'legacy'),'');
});

test('XRD manager selects read-only default visually once and uses native apply without mappings',t=>{
  const w=setup(t),d=w.document,requests=[],applied=[];
  w.XMLHttpRequest=function(){this.open=(method,url)=>{this.method=method;this.url=url;};this.setRequestHeader=()=>{};this.send=body=>{this.body=body;requests.push(this);};};
  const respond=data=>{const r=requests.shift();r.status=200;r.responseText=JSON.stringify(data);r.onload();};
  const builtin={id:'builtin-xrd-20261029',name:'XRD 기본 양식 (2026-10-29)',experiment:'XRD',builtin:true,default:true,readOnly:true};
  const descriptor={version:3,name:builtin.name,experiment:'XRD',nativeTemplate:'2026-10-29'};
  const custom={id:'personal',name:'내 기존 양식',experiment:'XRD'},items=[custom,builtin];
  let version='2026-10-29',state={new:[{id,text:'현재 보고서',color:'#123456'}]};
  const manager=new w.RistPptTemplates.Manager({host:d.getElementById('host'),experiment:'XRD',layout:()=>layout(),state:()=>state,
    apply:()=>assert.fail('native default must not apply positional patches'),
    nativeError:()=>w.RistPptTemplates.nativeDefaultError(state,version),applyNative:v=>applied.push(v)});
  manager.lock(false);d.querySelector('summary').click();respond({revision:7,items});
  assert.equal(requests[0].url,'/api/v1/report-ppt/templates/'+builtin.id);
  respond({...builtin,content:descriptor,revision:7});
  const select=d.querySelector('[aria-label="PPT 템플릿 선택"]');
  assert.equal(select.value,builtin.id);assert.match(select.selectedOptions[0].textContent,/기본 제공/);
  assert.deepEqual(applied,[]);assert.equal(state.new[0].color,'#123456');
  for(const label of ['선택한 템플릿을 현재 PPT 서식으로 덮어쓰기','선택한 PPT 템플릿 삭제']){
    const button=d.querySelector('[aria-label="'+label+'"]');assert(button.disabled);button.onclick();
  }
  assert.equal(requests.length,0);assert(d.querySelector('.ppt-template-mapping').hidden);
  assert(!d.querySelector('.ppt-template-preview').hasAttribute('src'));
  const apply=[...d.querySelectorAll('button')].find(b=>b.textContent==='양식 적용');
  manager.lock(true);assert(apply.disabled);manager.lock(false);apply.click();assert.deepEqual(applied,['2026-10-29']);
  version='legacy';manager.refresh();assert(apply.disabled);assert.match(d.body.textContent,/이전 기본 양식/);
  apply.onclick();assert.equal(applied.length,1);version='2026-10-29';manager.refresh();
  select.value=custom.id;select.onchange();respond({revision:7,content:{version:1,name:custom.name,experiment:'XRD',ratio:16/9,pages:[{key,objects:[{id,kind:'text',bold:true}]}]}});
  d.querySelector('[aria-label="PPT 템플릿 목록 새로고침"]').click();respond({revision:7,items});
  assert.equal(requests[0].url,'/api/v1/report-ppt/templates/personal');
  assert.equal(select.value,'personal');
});
