const {test}=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const {JSDOM}=require('jsdom'),babel=require('@babel/core'),acorn=require('acorn');
const source=['mail_editor_icons.js','mail_table_editor.js','mail_address_book.js','mail_template_context.js','mail_templates.js','mail_editor.js'].map(n=>fs.readFileSync(path.join(__dirname,'../app/xrd_workbench/web/shared',n),'utf8')).join('\n');
const programs={native:source,chrome49:babel.transformSync(source,{configFile:false,babelrc:false,presets:[[require('@babel/preset-env'),{targets:{chrome:'49'},forceAllTransforms:true,modules:false}]]}).code};
function setup(t,program){
  const dom=new JSDOM('<div id="host"><label><input id="subject" value="기존 제목"></label><label><textarea>기존 본문</textarea></label></div>',{url:'http://rist.test',runScripts:'outside-only'});t.after(()=>dom.window.close());
  const w=dom.window,d=w.document;w.confirm=()=>true;w.eval(program);let changes=0,failure=0,definitions={'실험종류':'FTIR','의뢰번호':'REQ-1','실험장비':'장비 A','분석결과':'수정한 결과','공통.온도':'25 °C'};
  const book={revision:0,items:[]};
  w.XMLHttpRequest=class {open(method,url){this.method=method;this.url=url;}setRequestHeader(){}send(raw){
    const value=raw?JSON.parse(raw):null,id=this.url.split('/templates/')[1],item=book.items.find(i=>i.id===id);let result;
    if(failure){this.status=failure;result={message:'충돌: 입력 유지'};}
    else{this.status=200;if(this.method!=='GET'){assert.equal(value.revision,book.revision);if(this.method==='POST')book.items.push({id:String(book.revision+1),content:value.content});else if(this.method==='PUT')item.content=value.content;else book.items=book.items.filter(i=>i.id!==id);book.revision++;}
      result=this.method==='GET'&&id?{...item,revision:book.revision}:{revision:book.revision,items:book.items.map(i=>({id:i.id,name:i.content.name,experiment:i.content.experiment})),selectedId:this.method==='POST'?book.items.at(-1).id:id};}
    this.responseText=JSON.stringify(result);this.onload();}};
  const subject=d.querySelector('#subject'),body=d.querySelector('textarea'),editor=new w.RistMailEditor({host:d.querySelector('#host'),subject,body,experiment:'FTIR',context:()=>definitions,changed:()=>changes++});
  const field=label=>Array.from(d.querySelectorAll('[aria-label]')).find(e=>e.getAttribute('aria-label')===label),click=label=>field(label).click();
  function input(label,value){const e=field(label);if(e.tagName==='DIV')e.textContent=value;else e.value=value;e.dispatchEvent(new w.Event('input',{bubbles:true}));}
  return {w,d,subject,body,editor,field,click,input,book,changes:()=>changes,defs:v=>definitions=v,fail:v=>failure=v,open:()=>d.querySelector('.mail-template-entry button').click()};
}
for(const [mode,program] of Object.entries(programs)){
  test(mode+': template CRUD stays separate; preview uses latest facts and apply preserves recipients/signature',t=>{
    acorn.parse(program,{ecmaVersion:5});const u=setup(t,program);u.editor.load({cc:'cc@rist.re.kr',bcc:'hidden@rist.re.kr',signature:'담당자'});u.open();
    u.input('메일 템플릿 이름','분석 안내');u.input('템플릿 메일 제목','{{실험종류}} 결과 {{의뢰번호}}');u.input('템플릿 메일 본문','장비: {{실험장비}}\n{{분석결과}}');u.click('메일 템플릿 새 이름으로 저장');
    assert.equal(u.book.items.length,1);assert.equal(u.subject.value,'기존 제목');assert.equal(u.body.value,'기존 본문');assert.equal(u.changes(),0);
    u.click('공통항목 반영 미리보기');assert.match(u.d.querySelector('.mail-template-preview').textContent,/수정한 결과/);
    u.defs({'실험종류':'FTIR','의뢰번호':'REQ-2','실험장비':'장비 B','분석결과':'최신 결과'});u.click('미리본 템플릿을 현재 메일에 적용');
    assert.equal(u.subject.value,'FTIR 결과 REQ-2');assert.match(u.body.value,/장비 B\n최신 결과/);assert.equal(u.editor.value().bcc,'hidden@rist.re.kr');assert.equal(u.editor.value().signature,'담당자');assert.equal(u.changes(),1);
    u.input('메일 템플릿 이름','변경 안내');u.click('선택한 메일 템플릿 수정 저장');assert.equal(u.book.items[0].content.name,'변경 안내');
    u.fail(409);u.input('메일 템플릿 이름','유지할 입력');u.click('선택한 메일 템플릿 수정 저장');assert.equal(u.field('메일 템플릿 이름').value,'유지할 입력');u.fail(0);
    u.click('선택한 메일 템플릿 불러오기');assert.equal(u.field('메일 템플릿 이름').value,'변경 안내');u.click('선택한 메일 템플릿 삭제');assert.equal(u.book.items.length,0);assert.equal(u.subject.value,'FTIR 결과 REQ-2');
  });
  test(mode+': tokens are text-only, unknown/cyclic/split/oversized values block application, blanks are visible',t=>{
    const u=setup(t,program),expand=(title,html,defs)=>u.w.RistMailTemplateContext.expand(title,html,defs,u.w.RistMailEditor.clean);
    const value=expand('{{제목}}','<p><b>{{결과}}</b></p><table><tr><td>{{빈칸}}</td></tr></table>',{'제목':'제목','결과':'<img src=x onerror=bad()>\n25°C','빈칸':''});
    assert(value.bodyHtml.includes('&lt;img'));assert(!value.bodyHtml.includes('<img'));assert.deepEqual(Array.from(value.missing),['빈칸']);assert.match(value.body,/25°C/);
    assert.match(expand('표','<table><tr><td>시료</td><td>{{a}}</td></tr></table>',{a:'A'}).body,/시료\tA/);
    for(const [html,defs] of [['{{unknown}}',{}],['{{a}}',{a:'{{b}}',b:'{{a}}'}],['{{a<b>b</b>}}',{ab:'value'}],['{{a}}',{a:'x'.repeat(4001)}]])assert.throws(()=>expand('제목',html,defs));
    assert.throws(()=>expand('{{a}}','본문',{a:'x\nBcc:other'}));u.open();u.input('템플릿 메일 본문','{{unknown}}');u.click('미리본 템플릿을 현재 메일에 적용');assert.equal(u.changes(),0);assert.equal(u.body.value,'기존 본문');
    u.editor.lock(true);assert(u.field('미리본 템플릿을 현재 메일에 적용').disabled);assert.equal(u.field('템플릿 메일 본문').contentEditable,'false');
  });
  test(mode+': four experiment contexts use edited conditions, current summaries and included XRD samples',t=>{
    const u=setup(t,program),api=u.w.RistMailTemplateContext;
    for(const kind of ['FT-IR','RAMAN','TEM']){
      const doc={title:'편집 제목',transfer:{equipmentCode:'EQ1'},sections:[{sectionId:'experiment_conditions',rows:[['공통','온도','30°C'],['공통','실험환경/비고','진공']]},{sectionId:'summary',paragraphs:['편집 분석 결과']},{sectionId:'report_traceability',rows:[['장비명 / 모델','모델A'],['의뢰기관','RIST']]},{sectionId:'slide-1-shape-5',heading:'표지 · 측정 환경',paragraphs:['현장 환경']} ]};
      const v=api.report(doc,kind,[{name:'사용자항목',value:'100 nm'}]);assert.equal(v['분석결과'],'편집 분석 결과');assert.equal(v['공통.온도'],'30°C');assert.equal(v['실험환경'],'현장 환경');assert.equal(v['장비모델'],'모델A');assert.equal(v['사용자항목'],'100 nm');
    }
    const v=api.xrd({metadata:{equipment_name:'XRD-M',conditions:'Cu Kα'},summary:'최신 상동정',samples:[{sample_id:'a',display_name:'A',included:true,notes:[{selected:true,text:'확인 결과'},{selected:false,text:'제외 결과'}]},{sample_id:'b',display_name:'B',included:false}]},{samples:[{id:'a',quantities:[{phase:'ZnO',raw:'12.5',unit:'wt%'}]}]});
    assert.equal(v['시료명'],'A');assert.match(v['핵심관찰'],/ZnO 12.5 wt%/);assert(!v['핵심관찰'].includes('제외 결과'));assert.equal(v['분석결과'],'최신 상동정');
  });
}
