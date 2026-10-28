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
