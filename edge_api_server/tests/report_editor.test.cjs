const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const {JSDOM}=require('jsdom');
const acorn=require('acorn');
const {execFileSync}=require('node:child_process');
const clone=x=>JSON.parse(JSON.stringify(x));
const script=['xrd_workbench/web/shared/pptx_password.js','file_save.js','report_editor_icons.js','report_page_sidebar.js','report_cell_editor.js','report_direct_editor.js','report_canvas_editor.js','report_ppt_templates.js','report_saved_graphs.js','report_condition_options.js','xrd_workbench/web/shared/mail_editor_icons.js','xrd_workbench/web/shared/mail_table_editor.js','xrd_workbench/web/shared/mail_address_book.js','xrd_workbench/web/shared/mail_template_context.js','xrd_workbench/web/shared/mail_templates.js','xrd_workbench/web/shared/mail_editor.js','report_editor.js'].map(name=>fs.readFileSync(path.join(__dirname,'../app',name),'utf8')).join('\n');
const template=fs.readFileSync(path.join(__dirname,'../app/report_editor.html'),'utf8');
function setup(t,kind='ftir',identity='',conditionScenario=null) {
  const prefix='/api/v1/'+kind+'/report/jobs',posts=[],gets=[],timers=new Map(); let timerId=0;
  let doc={title:'Report',completionDate:'2026-10-15',variables:[],transfer:{requestNumber:'',limsExperimentCode:'',equipmentCode:'',operatorId:''},
    sections:[{sectionId:'summary',heading:'분석 결과 요약',paragraphs:['AI 초안'],bullets:[],columns:null,rows:null,source:'llm'},
      {sectionId:'experiment_conditions',heading:'실험조건 및 실험환경',paragraphs:[],bullets:[],columns:['대상','항목','값'],rows:[['공통','온도','']],source:'rule'}],auxiliary:{}};
  if(conditionScenario)doc.sections[1].rows=conditionScenario.rows;
  const dom=new JSDOM(template.replace('__CONFIG__',JSON.stringify({prefix,jobId:'original',conditionOptions:conditionScenario&&conditionScenario.config})).replace('__SCRIPT__',''),
    {url:'http://localhost'+prefix+'/original/edit',runScripts:'outside-only'});
  t.after(()=>dom.window.close());const w=dom.window,d=w.document,q=id=>d.getElementById(id);
  let pending=null,delay=false,fail=false,revisions=0,items=[],lookupError=false,pollError=false,rateLimit=false,running=false,heldPath='',heldFinish=null;
  w.setTimeout=(fn,ms)=>{timers.set(++timerId,{fn,ms});return timerId;};w.clearTimeout=id=>timers.delete(id);
  w.XMLHttpRequest=class {
    open(method,url){this.method=method;this.url=url;this.headers={};}
    abort(){}
    setRequestHeader(k,v){this.headers[k]=v;}
    send(raw){
      const body=raw&&JSON.parse(raw);if(body) posts.push({url:this.url,body,headers:this.headers});
      if(this.method==='GET') gets.push({url:this.url,headers:this.headers});
      let result;
      if(this.url.startsWith('/api/v1/report-mail/recipient?')) result={recipientName:'의뢰자',email:null,recipientPolicy:'lims'};
      else if(this.url.endsWith('/editor-data')) result={document:doc,job:{jobId:this.url.includes('/revision/')?'revision':'original',status:'completed',parentJobId:'original'},authenticatedOperatorId:identity};
      else if(this.url.endsWith('/edit-layout')) {
        let pages=[
          {id:'cover',title:'표지',regions:[{refs:['title'],x:.1,y:.2,w:.8,h:.2},{refs:['transfer/operatorId'],x:.1,y:.5,w:.3,h:.2}]},
          {id:'summary',title:'요약',regions:[{refs:['section/summary/paragraphs/0'],x:.1,y:.3,w:.7,h:.4}]},
          {id:'conditions',title:'조건',regions:[{refs:['section/experiment_conditions/rows/0/2'],x:.5,y:.3,w:.3,h:.2,empty:true}]}];
        if(conditionScenario&&conditionScenario.regions)pages[2].regions=conditionScenario.regions;
        const edits=doc.pageEdits||{added:[],hidden:[]};
        for(const item of edits.added) pages.splice(pages.findIndex(p=>p.id===item.after)+1,0,{id:item.id,title:item.title,
          regions:[{refs:['page/'+item.id+'/title'],x:.1,y:.1,w:.8,h:.1},{refs:['page/'+item.id+'/body'],x:.1,y:.3,w:.8,h:.5}]});
        if(edits.order&&edits.order.length)pages.sort((a,b)=>edits.order.indexOf(a.id)-edits.order.indexOf(b.id));
        result={ratio:16/9,pageEditing:true,canvasEditing:true,pages:pages.filter(p=>!edits.hidden.includes(p.id))};
      }
      else if(this.url.endsWith('/pptx-password')) result={protection:{hashData:'verifier-only',saltData:'salt'}};
      else if(this.url.endsWith('/revisions')) {
        revisions++;
        doc=clone(body);doc.sections.forEach(s=>{s.source='user';s.columns=s.rows?['대상','항목','값']:null;});
        result={jobId:revisions===1?'revision':'revision'+revisions,status:'queued'};
      } else if(this.url.endsWith('/send')) result={queued:true};
      else if(this.url.startsWith('/api/v1/requests?')) result={items};
      else result={jobId:this.url.split('/').pop(),status:running?'running':'completed',transfer:doc.transfer,parentJobId:revisions===1?'original':'revision'};
      const finish=()=>{this.status=fail?403:lookupError&&this.url.startsWith('/api/v1/requests?')?503:pollError&&/\/revision\d*$/.test(this.url)?503:rateLimit&&this.url.endsWith('/revisions')?429:200;
        this.responseText=JSON.stringify(this.status>=400?{message:'SSO 인증이 필요합니다.'}:result);this.onload();};
      if(heldPath&&this.url.includes(heldPath))heldFinish=finish;
      else if(delay&&this.url.endsWith('/revisions')) pending=finish;else finish();
    }
  };
  w.eval(fs.readFileSync(path.join(__dirname,'../../common/rist_common/plotting/file_progress.js'),'utf8'));
  w.eval(script);
  return {w,d,q,posts,gets,prefix,timers,field:label=>d.querySelector('[aria-label="'+label+'"]'),
    tick(){for(const [id,entry] of timers)if(entry.ms===300){timers.delete(id);entry.fn();}const entry=timers.entries().next().value;assert(entry,'expected a scheduled operation');timers.delete(entry[0]);entry[1].fn();},
    lookup(value,error=false){items=value;lookupError=error;},pollError(value){pollError=value;},rateLimit(value){rateLimit=value;},running(value){running=value;},
    hold(path){heldPath=path;},release(){heldPath='';heldFinish();},
    input(label,value){const e=this.field(label);e.value=value;e.dispatchEvent(new w.Event('input',{bubbles:true}));},
    delay(){delay=true;},finish(){delay=false;pending();},fail(){fail=true;}};
}
test('analyst editor is ES5 and renders text without HTML interpretation',t=>{
  acorn.parse(script,{ecmaVersion:5});const h=setup(t);
  assert.equal(h.field('실험조건 및 실험환경 1행 값').value,'');
  assert.equal(h.field('분석 결과 요약 · 문단 1').value,'AI 초안');
  assert.equal(h.q('slide-image').getAttribute('src'),h.prefix+'/original/edit-slides/0');
  assert(h.field('분석 결과 요약 · 문단 1').closest('details').hidden);
});
for(const kind of ['ftir','raman','tem'])test(kind+' preview progress waits for the page image, ignores stale loads, and recovers after failure',t=>{
  const h=setup(t,kind),image=h.q('slide-image'),bar=()=>h.q('page-status').closest('.rist-task-progress');
  assert(bar().classList.contains('is-running'));image.onload();assert(!bar().classList.contains('is-running'));
  h.hold('/edit-layout');h.q('reload-preview').click();assert(bar().classList.contains('is-running'));
  h.release();assert(bar().classList.contains('is-running'));
  const oldLoad=image.onload;
  h.q('edit-next').click();oldLoad();assert(bar().classList.contains('is-running'));
  image.onerror();assert(!bar().classList.contains('is-running'));assert.match(h.q('page-status').textContent,/불러오지 못/);
  h.q('reload-preview').click();assert(bar().classList.contains('is-running'));image.onload();assert(!bar().classList.contains('is-running'));
  h.delay();h.input('분석 결과 요약 · 문단 1','편집 유지');h.tick();assert(bar().classList.contains('is-running'));
  h.fail();h.finish();assert(!bar().classList.contains('is-running'));assert.equal(h.field('분석 결과 요약 · 문단 1').value,'편집 유지');
});
for(const kind of ['ftir','raman','tem']) test(kind+' password is submitted explicitly and only verifier enters saved revisions',t=>{
  const h=setup(t,kind),panel=h.d.querySelector('.pptx-password-panel');
  const apply=[...panel.querySelectorAll('button')].find(b=>b.textContent==='설정 / 변경');
  const clear=[...panel.querySelectorAll('button')].find(b=>b.textContent==='해제');
  h.input('새 수정 비밀번호','private-secret');h.input('비밀번호 확인','mismatch');
  assert.equal(h.posts.length,0);apply.click();assert.equal(h.posts.length,0);
  h.input('비밀번호 확인','private-secret');apply.click();
  assert.equal(h.posts.length,1);assert(h.posts[0].url.endsWith('/pptx-password'));
  assert.equal(h.field('새 수정 비밀번호').value,'');assert.equal(h.field('비밀번호 확인').value,'');
  h.tick();const revision=h.posts.find(p=>p.url.endsWith('/revisions'));
  assert.equal(revision.body.pptxProtection.hashData,'verifier-only');
  assert(!JSON.stringify(revision.body).includes('private-secret'));
  assert(!JSON.stringify({...h.w.localStorage}).includes('private-secret'));
  clear.click();h.tick();assert.deepEqual(h.posts[h.posts.length-1].body.pptxProtection,{});
});
for(const kind of ['ftir','raman']) test(kind+' condition choices autosave only the selected row and keep source/PPT bindings',t=>{
  const label=kind==='ftir'?'Type':'Excitation Wavelength',value=kind==='ftir'?'ATR method':'532 nm';
  const h=setup(t,kind,'',{config:{storageKey:'options-'+kind,fields:[{field:'choice',label,defaults:[value]}]},
    rows:[['공통',label,'직접 입력'],['sample A',label,'RAW 값'],['공통','온도','25°C']]});
  const picks=[1,2,3].map(i=>h.field('실험조건 및 실험환경 '+i+'행 값'));
  assert(picks[0].list);assert(picks[1].list);assert(!picks[2].list);assert.equal(h.posts.length,0);
  assert.equal(picks[0].tagName,'INPUT');assert.equal(picks[1].tagName,'INPUT');assert.equal(picks[2].tagName,'TEXTAREA');
  assert.equal(picks[0].parentNode.querySelectorAll('input,textarea,select').length,1);
  assert.equal(h.d.querySelectorAll('.report-condition-picker').length,0);assert(!picks[1].closest('details').hidden);
  const manage=h.d.querySelector('.report-condition-options>button');manage.click();
  const preference=h.field(label+' 선택지 1');preference.value='새 선택지';preference.dispatchEvent(new h.w.Event('input',{bubbles:true}));
  assert.equal(h.posts.length,0);assert.equal(h.timers.size,0);
  [...h.q('report-condition-manager').querySelectorAll('button')].find(b=>b.textContent==='선택지 저장').click();
  assert.equal(h.field('실험조건 및 실험환경 1행 값').value,'직접 입력');assert.equal(h.posts.length,0);
  assert.deepEqual([...picks[0].list.options].map(o=>o.value),['새 선택지']);
  picks[0].value='새 선택지';picks[0].dispatchEvent(new h.w.Event('input',{bubbles:true}));assert.equal(h.timers.size,1);h.tick();
  assert.deepEqual(h.posts[0].body.sections[1].rows,[['공통',label,'새 선택지'],['sample A',label,'RAW 값'],['공통','온도','25°C']]);
  assert(h.q('slide-image').getAttribute('src').includes('/revision/'));
  h.q('edit-page').value='2';h.q('edit-page').dispatchEvent(new h.w.Event('change'));
  h.q('slide-image').dispatchEvent(new h.w.Event('load'));
  h.d.querySelector('.edit-region').click();
  const inline=h.d.querySelector('.ppt-cell-input');
  assert.equal(inline.value,'새 선택지');assert.equal(inline.list,picks[0].list);
  h.input('실험조건 및 실험환경 1행 항목','자유 조건');assert(!picks[0].list);assert.equal(h.field('실험조건 및 실험환경 1행 값').value,'새 선택지');
  assert(!inline.list);
  h.input('실험조건 및 실험환경 1행 항목',label);assert(picks[0].list);
  assert.equal(inline.list,picks[0].list);
  inline.blur();
  h.input('실험조건 및 실험환경 1행 값','목록에 없는 직접 입력');h.tick();
  assert.equal(h.posts[h.posts.length-1].body.sections[1].rows[0][2],'목록에 없는 직접 입력');
  h.input('실험조건 및 실험환경 1행 값','');h.tick();
  assert.equal(h.posts[h.posts.length-1].body.sections[1].rows[0][2],'');
});
test('multiline imported conditions and notes remain lossless textareas',t=>{
  const h=setup(t,'ftir','',{config:{storageKey:'choices',fields:[{field:'model',label:'장비모델',defaults:[]}]},
    rows:[['공통','장비모델','Model A\nModel B'],['공통','실험환경/비고','온도 25°C\n습도 50%']]});
  const model=h.field('실험조건 및 실험환경 1행 값');assert.equal(model.tagName,'TEXTAREA');assert.equal(model.value,'Model A\nModel B');
  h.input('보고서 제목','Revised');h.tick();assert.deepEqual(h.posts[0].body.sections[1].rows,[['공통','장비모델','Model A\nModel B'],['공통','실험환경/비고','온도 25°C\n습도 50%']]);
});
for(const kind of ['ftir','raman','tem']) test(kind+' page and tool visibility toggles are adjacent and independent',t=>{
  const h=setup(t,kind),toggle=h.q('canvas-toggle'),sidebar=h.q('page-sidebar-toggle'),tools=h.q('canvas-controls');
  assert.equal(sidebar.nextElementSibling,toggle);assert.equal(toggle.parentNode,h.q('preview-panel-toggles'));
  assert.equal(tools.parentNode,h.q('canvas-tools'));assert(!h.q('canvas-tools').contains(toggle));assert(!toggle.hidden);
  assert(tools.hidden);assert(!h.q('page-sidebar').hidden);
  toggle.click();assert(!tools.hidden);assert(!h.q('page-sidebar').hidden);assert.equal(toggle.getAttribute('aria-expanded'),'true');
  sidebar.click();assert(h.q('page-sidebar').hidden);assert(!tools.hidden);assert(!toggle.hidden);
  toggle.click();assert(tools.hidden);assert(h.q('page-sidebar').hidden);assert.equal(toggle.getAttribute('aria-expanded'),'false');
  sidebar.click();assert(!h.q('page-sidebar').hidden);assert(tools.hidden);assert.equal(h.posts.length,0);
});
test('TEM shares the icon canvas tools and autosaves inserted objects',t=>{
  const h=setup(t,'tem');loaded(h);h.q('canvas-toggle').click();
  h.field('글자 삽입').click();h.tick();
  assert.equal(h.posts[0].body.canvasEdits.cover[0].kind,'text');
  assert.match(h.q('analysis-help').textContent,/원본 분석 데이터는 유지/);
});
for(const kind of ['ftir','raman','tem'])test(kind+' mail IME waits until Korean composition finishes',t=>{
  const h=setup(t,kind),rich=h.d.querySelector('.mail-rich');
  rich.dispatchEvent(new h.w.Event('compositionstart',{bubbles:true}));
  rich.textContent='분석';rich.dispatchEvent(new h.w.Event('input',{bubbles:true}));
  assert.equal(h.timers.size,0);
  rich.dispatchEvent(new h.w.Event('compositionend',{bubbles:true}));
  assert.equal(h.timers.size,1);
});
for(const kind of ['ftir','raman','tem'])test(kind+' mail recipients, rich body and signature autosave without losing the original prose',t=>{
  const h=setup(t,kind);
  assert(h.field('수신자 이름').readOnly);assert(h.field('수신자 이메일 주소').readOnly);
  h.input('수신자 이름','의뢰자');h.input('수신자 이메일 주소','to@example.com');h.input('참조 (CC)','cc');h.input('비밀참조 (BCC)','hidden@rist.re.kr');h.input('메일 서명','윤병훈\nRIST');
  const rich=h.field('메일 본문 서식 편집');rich.innerHTML='<strong>분석 결과</strong>';
  rich.dispatchEvent(new h.w.Event('input',{bubbles:true}));h.tick();
  const saved=h.posts[0].body;
  assert.equal(saved.auxiliary.email_body,'분석 결과');assert.equal(saved.deliveryEmail.bodyHtml,'<strong>분석 결과</strong>');
  assert.equal(saved.deliveryEmail.to,'');assert.equal(saved.deliveryEmail.recipientName,'');assert.equal(saved.deliveryEmail.cc,'cc@rist.re.kr');
  assert.equal(saved.deliveryEmail.bcc,'hidden@rist.re.kr');assert.equal(saved.deliveryEmail.signature,'윤병훈\nRIST');
  assert.equal(h.field('메일 서명').value,'윤병훈\nRIST');
  assert.equal(h.w.RistMailEditor.clean('<img src=x onerror=x()><script>x()</script><b style="color:red">ok</b>'),'<b>ok</b>');
  h.input('메일 본문','새 본문');h.tick();assert.equal(h.posts[1].body.deliveryEmail.bodyHtml,'');
});
for(const kind of ['ftir','raman','tem']) test(kind+' page selection help is collapsed below the slide, outside the thumbnails',t=>{
  const h=setup(t,kind),help=h.q('page-selection-help'),sidebar=h.q('page-sidebar');
  assert.equal(help.tagName,'DETAILS');assert(!help.hidden);assert(!help.open);
  assert.equal(h.q('slide-stage').nextElementSibling,help);assert(!sidebar.contains(help));
  assert(!sidebar.textContent.includes('Shift+클릭'));assert.equal(h.q('page-actions').querySelectorAll('button').length,5);
  assert.equal(help.querySelector('summary').textContent,'페이지 선택·순서 안내');
  assert.deepEqual([...help.querySelectorAll('li')].map(li=>li.textContent),['Shift+클릭: 범위 선택','Ctrl/Cmd+클릭: 개별 선택·해제','목록에서 Ctrl/Cmd+A: 전체 선택','끌어서 순서 변경 · Alt+↑/↓']);
  help.open=true;h.q('page-sidebar-toggle').click();assert(sidebar.hidden);assert(!help.hidden);assert(help.open);
  h.q('page-sidebar-toggle').click();assert(!sidebar.hidden);assert(help.open);assert.equal(h.posts.length,0);
});
for(const kind of ['ftir','raman','tem']) test(kind+' mail draft uses wide resizable fields and keeps autosaving long text',t=>{
  const h=setup(t,kind),subject=h.field('메일 제목'),body=h.field('메일 본문');
  assert.equal(subject.tagName,'TEXTAREA');assert.equal(subject.rows,2);assert.equal(body.rows,12);
  assert(h.q('mail-editor').contains(subject));assert(h.q('mail-editor').contains(body));
  assert(!h.q('editor').contains(body));assert(subject.closest('details').open);
  assert.equal(subject.className,'mail-subject');assert.equal(body.className,'mail-body');
  const title='FTIR 및 Raman 분석 결과 보고서 · 시료와 실험 조건을 포함한 긴 안내 제목';
  const content=('실험 조건과 피크 해석 결과를 확인해 주세요.\n').repeat(45);
  h.input('메일 제목',title);h.input('메일 본문',content);body.focus();h.delay();h.tick();
  h.input('메일 본문',content+'추가 안내');h.finish();
  assert.equal(h.d.activeElement,body);assert.equal(body.value,content+'추가 안내');h.tick();
  assert.equal(h.posts[1].body.auxiliary.email_subject,title);
  assert.equal(h.posts[1].body.auxiliary.email_body,content+'추가 안내');
  assert.equal(h.q('download').getAttribute('aria-disabled'),'false');
});
for(const kind of ['ftir','raman','tem']) test(kind+' saves blank fields and edited AI prose into new revision',t=>{
  const h=setup(t,kind);
  h.input('의뢰번호','REQ-1');h.input('실험코드','LIMS-01');h.input('실험장비','LAB1');h.input('실험자','OP1');
  h.input('실험조건 및 실험환경 1행 값','23°C');
  h.input('분석 결과 요약 · 문단 1','분석자 수정 <script>bad()</script>');
  assert.equal(h.q('download').getAttribute('aria-disabled'),'true');assert(h.q('send').disabled);
  h.delay();h.q('save').click();assert(!h.field('의뢰번호').disabled);assert(h.q('save').disabled);h.finish();
  const body=h.posts[0].body;
  assert.equal(body.transfer.requestNumber,'REQ-1');assert.equal(body.sections[1].rows[0][2],'23°C');
  assert.equal(body.sections[0].paragraphs[0],'분석자 수정 <script>bad()</script>');
  assert.equal(h.q('slide-image').getAttribute('src'),h.prefix+'/revision/edit-slides/0');
  assert(h.q('recipient-preview').href.endsWith('/revision/preview'));
  assert(h.q('download').href.endsWith('/revision/download'));
  assert.equal(h.w.location.pathname,h.prefix+'/revision/edit');
  assert.equal(JSON.parse(h.w.localStorage.getItem('rist-report-revision')).previousJobId,'original');
  assert.equal(h.q('download').getAttribute('aria-disabled'),'false');
  assert.equal(h.d.querySelectorAll('script').length,2);
  h.q('send').click();assert(h.posts[1].url.endsWith('/revision/send'));
  assert.equal(h.posts[1].body.experimentCode,'LIMS-01');
  assert.equal(h.posts[1].headers['X-Requested-With'],'RIST-Report-Editor');
});
test('SSO identity requires save before sending and is locked',t=>{
  const h=setup(t,'ftir','SSO-001');assert.equal(h.field('실험자').value,'SSO-001');
  assert(h.field('실험자').readOnly);assert(h.q('send').disabled);
  assert.equal(h.q('download').getAttribute('aria-disabled'),'true');
});
for(const kind of ['ftir','raman','tem'])test(kind+' package save chooses a name and only the finalized current revision',t=>{
  const h=setup(t,kind);let options,calls=0;
  h.w.RistFileSave.open=value=>{options=value;calls++;return Promise.resolve({status:'cancelled'});};
  h.q('download').click();assert.equal(calls,1);assert.equal(options.extension,'.zip');assert(options.url.endsWith('/original/download'));assert(options.ready());
  const old=options;h.input('보고서 제목','새 보고서');assert(!old.ready());h.q('download').click();assert.equal(calls,1);
  h.tick();h.q('download').click();assert.equal(calls,2);assert(options.url.endsWith('/revision/download'));assert.match(options.name,/새 보고서/);assert(!old.ready());
});
test('failed save retains unsaved edits and blocks stale download/send',t=>{
  const h=setup(t);h.input('분석 결과 요약 · 문단 1','보존할 문장');h.fail();h.q('save').click();
  assert.equal(h.field('분석 결과 요약 · 문단 1').value,'보존할 문장');assert(!h.q('save').disabled);
  assert(h.q('send').disabled);assert.equal(h.q('download').getAttribute('aria-disabled'),'true');
  assert.match(h.q('status').textContent,/SSO 인증이 필요합니다/);assert.equal(h.timers.size,0);
});
for(const kind of ['ftir','raman']) test(kind+' debounces edits and autosaves without clicking; keeps focus and newer edits',t=>{
  const h=setup(t,kind);h.input('보고서 제목','첫 입력');h.input('보고서 제목','저장할 제목');
  assert.equal(h.posts.length,0);assert.equal(h.timers.size,1);
  assert.equal([...h.timers.values()][0].ms,650);
  const field=h.field('보고서 제목');field.focus();h.delay();h.tick();
  h.input('보고서 제목','생성 중 추가 수정');assert.equal(h.posts.length,1);assert.equal(h.timers.size,0);
  h.finish();assert.equal(h.field('보고서 제목'),field);assert.equal(h.d.activeElement,field);
  assert.equal(field.value,'생성 중 추가 수정');assert(h.q('send').disabled);
  assert.equal(h.q('download').getAttribute('aria-disabled'),'true');assert.equal(h.timers.size,1);
  h.tick();assert.equal(h.posts.length,2);assert(h.posts[1].url.endsWith('/revision/revisions'));
  assert.equal(h.posts[1].body.title,'생성 중 추가 수정');assert(!h.q('send').disabled);
  assert(h.q('download').href.endsWith('/revision2/download'));assert.equal(h.timers.size,0);
});
test('IME composition and reverting unsaved changes do not create revisions',t=>{
  const h=setup(t),field=h.field('보고서 제목');h.input('보고서 제목','입');
  field.dispatchEvent(new h.w.Event('compositionstart'));h.input('보고서 제목','입력');assert.equal(h.timers.size,0);
  field.dispatchEvent(new h.w.Event('compositionend'));assert.equal(h.timers.size,1);
  h.input('보고서 제목','Report');assert.equal(h.timers.size,0);assert.equal(h.posts.length,0);
  assert(!h.q('send').disabled);
});
test('quick completion polling backs off for long jobs and hidden tabs without duplicate revisions',t=>{
  const h=setup(t);Object.defineProperty(h.d,'hidden',{configurable:true,value:false});
  h.running(true);h.input('보고서 제목','완료 기다리기');h.tick();
  for(let i=0;i<6;i++){assert.equal([...h.timers.values()][0].ms,500);h.tick();}
  assert.equal([...h.timers.values()][0].ms,1500);
  assert.equal(h.posts.length,1);assert(h.q('send').disabled);
  h.running(false);h.tick();assert.equal(h.timers.size,0);assert(!h.q('send').disabled);
  Object.defineProperty(h.d,'hidden',{configurable:true,value:true});
  h.running(true);h.input('보고서 제목','숨긴 창');h.tick();
  assert.equal([...h.timers.values()][0].ms,1500);
  h.running(false);h.tick();assert.equal(h.posts.length,2);
});
test('poll retry resumes the same revision instead of creating duplicate work',t=>{
  const h=setup(t);h.input('보고서 제목','수정');h.pollError(true);h.tick();
  assert.equal(h.posts.length,1);assert.equal(h.timers.size,0);assert(h.q('send').disabled);
  h.pollError(false);h.q('save').click();assert.equal(h.posts.length,1);assert(!h.q('send').disabled);
});
test('global rendering busy response retries with a bound and preserves inputs',t=>{
  const h=setup(t);h.rateLimit(true);h.input('보고서 제목','유지');
  for(let i=0;i<6;i++) h.tick();
  assert.equal(h.posts.length,6);assert.equal(h.timers.size,0);assert(h.q('send').disabled);
  assert.equal(h.field('보고서 제목').value,'유지');
});
for(const kind of ['ftir','raman','tem']) test(kind+' lookup is experiment-scoped, manual entry works, SSO stays unchanged',t=>{
  const h=setup(t,kind,'SSO-001');h.lookup([{requestNumber:'REQ-9',testMethodCode:'CODE-9',sampleName:'<b>시료</b>',operatorId:'NOT-SSO'}]);
  h.input('실험장비','CURRENT-LAB');h.q('request-load').click();
  assert(h.gets.some(g=>g.url.endsWith('experimentType='+encodeURIComponent(kind==='ftir'?'FT-IR':kind.toUpperCase()))&&g.headers['X-Request-Id']));
  assert.equal(h.q('request-results').querySelectorAll('b').length,0);assert.equal(h.q('request-select'),null);
  assert.equal(h.q('request-search').getAttribute('role'),'combobox');h.q('request-result-0').click();
  assert.equal(h.field('의뢰번호').value,'REQ-9');assert.equal(h.field('실험코드').value,'CODE-9');
  assert.equal(h.field('실험장비').value,'CURRENT-LAB');assert.equal(h.field('실험자').value,'SSO-001');
  assert.equal(h.timers.size,2);h.tick();assert.equal(h.posts[0].body.transfer.requestNumber,'REQ-9');
  assert.equal(h.field('수신자 이름').value,'의뢰자');
  h.input('의뢰번호','MANUAL-1');assert.equal(h.q('request-search').value,'');assert.equal(h.field('실험코드').value,'');
});
test('request pagination, local filtering, equipment selection and lookup failures',t=>{
  const h=setup(t);h.lookup(Array.from({length:200},(_,i)=>({requestNumber:'REQ-'+i,experimentCode:'CODE',equipmentCode:'LAB'})));
  h.q('request-load').click();assert(!h.q('request-next').disabled);assert(h.q('request-previous').disabled);
  h.input('의뢰 검색·선택','REQ-199');assert.equal(h.q('request-results').querySelectorAll('[role=option]').length,1);assert.equal(h.posts.length,0);
  h.q('request-result-199').click();
  assert.equal(h.field('실험장비').value,'LAB');h.lookup([]);h.q('request-next').click();
  assert(h.gets.some(g=>g.url.includes('page=2&')));assert(h.q('request-next').disabled);assert(!h.q('request-previous').disabled);
  h.lookup([],true);h.q('request-load').click();assert.match(h.d.body.textContent,/조회 실패/);
  assert.equal(h.field('의뢰번호').value,'REQ-199');assert(!h.field('의뢰번호').disabled);
});
test('request combobox filters locally without changing delivery; arrows/Enter explicitly choose exact duplicate-number rows',t=>{
  const h=setup(t);h.lookup([{requestNumber:'SAME',experimentCode:'FIRST',sampleName:'같은 시료'},
    {requestNumber:'SAME',experimentCode:'SECOND',sampleName:'같은 시료',equipmentCode:'LAB2'}]);h.q('request-load').click();
  const search=h.q('request-search'),list=h.q('request-results'),keys=key=>search.dispatchEvent(new h.w.KeyboardEvent('keydown',{key,bubbles:true,cancelable:true}));
  const before=h.gets.length;h.input('의뢰 검색·선택','같은 시료');assert.equal(h.gets.length,before);assert.equal(h.posts.length,0);assert.equal(h.field('의뢰번호').value,'');
  keys('Enter');assert.equal(h.field('의뢰번호').value,'');
  keys('ArrowDown');assert.equal(search.getAttribute('aria-activedescendant'),'request-result-0');keys('ArrowDown');keys('Enter');
  assert.equal(h.field('의뢰번호').value,'SAME');assert.equal(h.field('실험코드').value,'SECOND');assert.equal(h.field('실험장비').value,'LAB2');assert(list.hidden);
  h.tick();const posts=h.posts.length;h.input('의뢰 검색·선택','no match');assert.match(list.textContent,/일치하는 의뢰가 없습니다/);
  keys('ArrowDown');keys('Enter');keys('Escape');assert(list.hidden);assert.equal(h.posts.length,posts);assert.equal(h.field('실험코드').value,'SECOND');
  h.input('의뢰 검색·선택','');keys('ArrowUp');assert.equal(search.getAttribute('aria-activedescendant'),'request-result-1');keys('Tab');assert(list.hidden);
});
test('request combobox guards Korean composition, closes on outside/scroll, and retains manually changed routing codes',t=>{
  const h=setup(t);h.lookup([{requestNumber:'REQ-1',testMethodCode:'CODE'}]);h.q('request-load').click();const search=h.q('request-search'),list=h.q('request-results');
  search.dispatchEvent(new h.w.Event('compositionstart'));h.input('의뢰 검색·선택','REQ');
  search.dispatchEvent(new h.w.KeyboardEvent('keydown',{key:'ArrowDown'}));search.dispatchEvent(new h.w.KeyboardEvent('keydown',{key:'Enter',isComposing:true}));assert.equal(h.field('의뢰번호').value,'');
  search.dispatchEvent(new h.w.Event('compositionend'));h.d.body.dispatchEvent(new h.w.MouseEvent('mousedown',{bubbles:true}));assert(list.hidden);
  h.q('request-toggle').click();assert(!list.hidden);list.dispatchEvent(new h.w.Event('scroll'));assert(!list.hidden);
  h.w.dispatchEvent(new h.w.Event('scroll'));assert(list.hidden);h.q('request-toggle').click();h.q('request-result-0').click();
  h.input('실험코드','MANUAL-CODE');h.input('의뢰번호','MANUAL-REQ');assert.equal(h.field('실험코드').value,'MANUAL-CODE');
  assert.equal(search.value,'');assert(list.hidden);
});
test('request fetch and delivery lock picker and stale options, but lookup failure permits direct entry',t=>{
  const h=setup(t,'ftir','SSO');h.lookup([{requestNumber:'REQ',experimentCode:'CODE',equipmentCode:'LAB'}]);h.q('request-load').click();
  const old=h.q('request-result-0');h.hold('/api/v1/requests?');h.q('request-load').click();
  assert(h.q('request-search').disabled);assert(h.q('request-toggle').disabled);assert(h.q('request-results').hidden);old.click();assert.equal(h.field('의뢰번호').value,'');
  h.release();assert(!h.q('request-search').disabled);h.q('request-result-0').click();h.tick();h.hold('/send');h.q('send').click();
  assert(h.q('request-search').disabled);assert(h.q('request-results').hidden);assert(h.q('request-toggle').disabled);h.release();
  assert(!h.q('request-search').disabled);h.lookup([],true);h.q('request-load').click();assert.match(h.q('request-info').textContent,/조회 실패/);
  h.input('의뢰 검색·선택','REQ');assert.match(h.q('request-info').textContent,/조회 실패/);assert.equal(h.field('의뢰번호').value,'REQ');
  h.input('의뢰번호','MANUAL');assert.equal(h.field('의뢰번호').value,'MANUAL');assert(!h.field('의뢰번호').disabled);
});
for(const kind of ['ftir','raman','tem'])test(kind+' common item creation uses a single input row with an accessible plus icon',t=>{
  const h=setup(t,kind),input=h.field('새 공통 항목 이름'),add=h.q('variable-add');
  assert(input.parentNode.classList.contains('variable-add-row'));assert.equal(input.nextElementSibling,add);
  assert.equal(add.type,'button');assert.equal(add.getAttribute('aria-label'),'공통 항목 추가');assert.equal(add.title,'공통 항목 추가');
  assert.equal(add.textContent,'');assert.equal(add.querySelector('svg').getAttribute('aria-hidden'),'true');
  assert.equal(add.querySelector('path').getAttribute('d'),'M12 5v14 M5 12h14');
  h.input('새 공통 항목 이름','실험실 온도');add.click();assert.equal(input.value,'');assert(h.field('실험실 온도 · 값'));
  h.tick();assert.deepEqual(h.posts[0].body.variables,[{name:'실험실 온도',value:'',unit:''}]);
  h.input('새 공통 항목 이름','실험실 온도');add.click();assert.match(h.q('status').textContent,/중복/);assert.equal(h.timers.size,0);
});
test('direct preview preserves selected page when autosave completes',t=>{
  const h=setup(t);h.q('edit-next').click();h.q('edit-next').click();assert(h.q('edit-next').disabled);
  h.input('보고서 제목','새 제목');h.tick();assert(h.q('slide-image').src.endsWith('/revision/edit-slides/2'));
  assert.equal(h.q('edit-page').value,'2');
});
function loaded(h){h.q('slide-image').dispatchEvent(new h.w.Event('load'));}
for(const kind of ['ftir','raman','tem']) test(kind+' adds, edits, deletes and restores PPT pages without losing source fields',t=>{
  const h=setup(t,kind);assert(!h.q('page-actions').hidden);assert(!h.q('page-add').disabled);
  h.q('edit-next').click();h.q('page-add').click();
  assert(h.q('page-add').disabled);assert(h.q('page-delete').disabled);assert(h.q('send').disabled);
  h.q('reload-preview').click();assert(h.q('page-add').disabled,'stale layout must not unlock structural edits');
  h.input('추가 페이지 제목','<b>검토 의견</b>');h.input('추가 페이지 본문','추가 해석과 실험 조건');h.tick();
  const added=h.posts[0].body.pageEdits.added[0];assert.equal(added.after,'summary');
  assert.equal(h.q('edit-page').options.length,4);assert.equal(h.q('edit-page').value,'2');
  assert.equal(h.q('edit-page').selectedOptions[0].textContent,'3. <b>검토 의견</b>');
  loaded(h);h.field('PPT 편집 · 추가 페이지 본문').click();h.input('PPT 수정 · 추가 페이지 본문','PPT에서 직접 수정');h.tick();
  assert.equal(h.posts[1].body.pageEdits.added[0].body,'PPT에서 직접 수정');
  h.w.confirm=()=>false;h.q('page-delete').click();assert.equal(h.timers.size,0);
  h.w.confirm=()=>true;h.q('page-delete').click();assert(h.field('추가 페이지 제목').closest('details').hidden);
  assert(!h.q('page-restore').disabled);h.tick();
  assert.deepEqual(h.posts[2].body.pageEdits.hidden,[added.id]);assert.equal(h.q('edit-page').options.length,3);
  h.q('page-restore').click();h.tick();assert.equal(h.q('edit-page').options.length,4);
  assert.equal(h.q('edit-page').value,'2');assert.equal(h.field('추가 페이지 본문').value,'PPT에서 직접 수정');
  assert.equal(h.field('분석 결과 요약 · 문단 1').value,'AI 초안');
  h.q('edit-previous').click();h.q('page-delete').click();h.tick();
  assert.equal(h.posts[4].body.pageEdits.hidden[0],'summary');
  assert.equal(h.q('edit-page').value,'1');assert.match(h.q('edit-page').selectedOptions[0].textContent,/검토 의견/);
});
test('last page cannot be deleted; unsaved deletion can be undone without rendering',t=>{
  const h=setup(t);h.w.confirm=()=>true;
  h.q('page-delete').click();h.q('page-restore').click();assert.equal(h.timers.size,0);assert(!h.q('page-delete').disabled);
  h.q('page-delete').click();h.tick();h.q('page-delete').click();h.tick();
  assert.equal(h.q('edit-page').options.length,1);assert(h.q('page-delete').disabled);assert(!h.q('page-add').disabled);
});
for(const kind of ['ftir','raman','tem'])test(kind+' sidebar moves by controls and keyboard, preserves IDs, edits and restore order',t=>{
  const h=setup(t,kind),ids=()=>[...h.d.querySelectorAll('.page-thumb')].map(b=>b.dataset.pageId);
  assert.deepEqual(ids(),['cover','summary','conditions']);assert(!h.q('page-sidebar').hidden);assert(h.q('page-up').disabled);
  h.q('page-down').click();assert(h.q('page-down').disabled);assert(h.q('send').disabled);h.tick();
  assert.deepEqual(ids(),['summary','cover','conditions']);assert.equal(h.q('edit-page').value,'1');
  assert.deepEqual(h.posts[0].body.pageEdits.order,ids());
  h.d.querySelector('.page-thumb[data-page-id="cover"]').dispatchEvent(new h.w.KeyboardEvent('keydown',{keyCode:40,altKey:true,bubbles:true}));h.tick();
  assert.deepEqual(ids(),['summary','conditions','cover']);assert.equal(h.q('edit-page').value,'2');
  h.q('page-add').click();h.input('추가 페이지 제목','마지막 검토');h.tick();
  const added=ids()[3];assert(added.startsWith('added-'));assert.equal(h.q('edit-page').value,'3');
  h.q('page-up').click();h.tick();assert.deepEqual(ids(),['summary','conditions',added,'cover']);
  h.w.confirm=()=>true;h.q('page-delete').click();h.tick();assert.deepEqual(ids(),['summary','conditions','cover']);
  h.q('page-restore').click();h.tick();assert.deepEqual(ids(),['summary','conditions',added,'cover']);
  assert.equal(h.q('edit-page').value,'2');assert.equal(h.field('추가 페이지 제목').value,'마지막 검토');
  h.input('보고서 제목','순서 유지');h.tick();assert.deepEqual(ids(),['summary','conditions',added,'cover']);
  assert.equal(h.field('분석 결과 요약 · 문단 1').value,'AI 초안');
  h.q('page-sidebar-close').click();assert(h.q('page-sidebar').hidden);h.q('page-sidebar-toggle').click();assert(!h.q('page-sidebar').hidden);
});
test('sidebar failure retains pending order and reload cannot unlock stale structural controls',t=>{
  const h=setup(t);h.fail();h.q('page-down').click();h.tick();
  assert(h.q('page-up').disabled);assert(h.q('page-down').disabled);assert(h.q('send').disabled);
  assert.deepEqual(h.posts[0].body.pageEdits.order,['summary','cover','conditions']);
  h.q('reload-preview').click();assert(h.q('page-down').disabled);
});
for(const kind of ['ftir','raman','tem']){
  test(kind+' multi-page selection survives autosave, moves as one group and deletes/restores together',t=>{
    const h=setup(t,kind),ids=()=>[...h.d.querySelectorAll('.page-thumb')].map(b=>b.dataset.pageId);
    const selected=()=>[...h.d.querySelectorAll('.page-thumb[aria-pressed="true"]')].map(b=>b.dataset.pageId);
    const click=(id,keys={})=>h.d.querySelector('[data-page-id="'+id+'"]').dispatchEvent(new h.w.MouseEvent('click',{bubbles:true,...keys}));
    click('cover');click('summary',{shiftKey:true});assert.deepEqual(selected(),['cover','summary']);assert.equal(h.posts.length,0);
    h.input('보고서 제목','여러 페이지 유지');h.tick();assert.deepEqual(selected(),['cover','summary']);
    h.q('page-down').click();h.tick();assert.deepEqual(ids(),['conditions','cover','summary']);assert.deepEqual(selected(),['cover','summary']);
    assert.deepEqual(h.posts[1].body.pageEdits.order,ids());
    h.d.querySelector('[data-page-id="summary"]').dispatchEvent(new h.w.KeyboardEvent('keydown',{keyCode:38,altKey:true,bubbles:true}));h.tick();
    assert.deepEqual(ids(),['cover','summary','conditions']);assert.deepEqual(selected(),['cover','summary']);
    h.w.confirm=()=>false;h.q('page-delete').click();assert.equal(h.timers.size,0);
    let confirmations=0;h.w.confirm=message=>{confirmations++;assert.match(message,/2개 페이지/);return true;};
    h.q('page-delete').click();assert(h.q('page-delete').disabled);h.tick();assert.equal(confirmations,1);
    assert.deepEqual(ids(),['conditions']);assert.deepEqual(h.posts[3].body.pageEdits.hidden,['cover','summary']);assert(h.q('page-delete').disabled);
    h.q('page-restore').click();h.tick();assert.deepEqual(ids(),['cover','summary','conditions']);assert.deepEqual(selected(),['cover','summary']);
    assert.deepEqual(h.posts[4].body.pageEdits.hidden,[]);assert.equal(h.field('분석 결과 요약 · 문단 1').value,'AI 초안');
  });
  test(kind+' Ctrl+A only affects thumbnails; all-page deletion is guarded and Ctrl toggle may select none',t=>{
    const h=setup(t,kind),key=target=>{const e=new h.w.KeyboardEvent('keydown',{key:'a',keyCode:65,ctrlKey:true,bubbles:true,cancelable:true});target.dispatchEvent(e);return e;};
    const thumb=()=>h.d.querySelector('.page-thumb');
    assert.equal(key(thumb()).defaultPrevented,true);assert.equal(h.d.querySelectorAll('[aria-pressed="true"].page-thumb').length,3);
    assert(h.q('page-delete').disabled);assert(h.q('page-up').disabled);assert(h.q('page-down').disabled);assert.equal(h.posts.length,0);
    const input=h.field('보고서 제목');input.focus();assert.equal(key(input).defaultPrevented,false);
    loaded(h);h.field('PPT 편집 · 보고서 제목').click();assert.equal(key(h.field('PPT 수정 · 보고서 제목')).defaultPrevented,false);
    thumb().click();thumb().dispatchEvent(new h.w.MouseEvent('click',{ctrlKey:true,bubbles:true}));
    assert.equal(h.d.querySelectorAll('[aria-pressed="true"].page-thumb').length,0);assert(h.q('page-delete').disabled);
    h.input('보고서 제목','빈 선택 유지');h.tick();assert.equal(h.d.querySelectorAll('[aria-pressed="true"].page-thumb').length,0);
  });
  test(kind+' noncontiguous selected pages drag together in original order and undo before saving restores them',t=>{
    const h=setup(t,kind),thumb=id=>h.d.querySelector('[data-page-id="'+id+'"]');
    thumb('conditions').dispatchEvent(new h.w.MouseEvent('click',{ctrlKey:true,bubbles:true}));
    thumb('cover').dispatchEvent(new h.w.MouseEvent('dragstart',{bubbles:true,cancelable:true}));
    thumb('summary').getBoundingClientRect=()=>({top:100,bottom:200});
    thumb('summary').dispatchEvent(new h.w.MouseEvent('drop',{bubbles:true,cancelable:true,clientY:190}));h.tick();
    assert.deepEqual(h.posts[0].body.pageEdits.order,['summary','cover','conditions']);
    assert.deepEqual([...h.d.querySelectorAll('.page-thumb[aria-pressed="true"]')].map(b=>b.dataset.pageId),['cover','conditions']);
    h.w.confirm=()=>true;h.q('page-delete').click();h.q('page-restore').click();assert.equal(h.timers.size,0);assert.equal(h.posts.length,1);
  });
}
test('TEM sends the same bounded page schema as spectrum editors',t=>{
  const h=setup(t,'tem');assert(!h.q('page-actions').hidden);assert(!h.q('page-add').disabled);
  h.input('보고서 제목','TEM 수정');h.tick();assert.deepEqual(h.posts[0].body.pageEdits,{hidden:[],order:[],added:[]});
});
test('actual slide regions edit their source, insert named fields and retain typing across refresh',t=>{
  const h=setup(t);h.input('새 공통 항목 이름','레이저');h.q('variable-add').click();
  h.input('레이저 · 값','532');h.input('레이저 · 단위','nm');
  h.q('edit-next').click();loaded(h);h.field('PPT 편집 · 분석 결과 요약 · 문단 1').click();
  h.input('PPT 수정 · 분석 결과 요약 · 문단 1','측정 조건: ');
  const input=h.field('PPT 수정 · 분석 결과 요약 · 문단 1');input.focus();input.setSelectionRange(input.value.length,input.value.length);
  h.q('insert-variable').value='레이저';h.q('insert-variable-button').click();
  assert.equal(input.value,'측정 조건: {{레이저}}');assert.equal(h.field('분석 결과 요약 · 문단 1').value,input.value);
  h.delay();h.tick();h.input('PPT 수정 · 분석 결과 요약 · 문단 1',input.value+' 추가');h.finish();
  assert.equal(h.d.activeElement,input);assert(!h.q('inline-editor').hidden);assert.match(input.value,/추가$/);
  h.tick();assert.deepEqual(h.posts[1].body.variables,[{name:'레이저',value:'532',unit:'nm'}]);
  assert.match(h.posts[1].body.sections[0].paragraphs[0],/\{\{레이저\}\} 추가$/);
  h.q('inline-close').click();assert(h.q('inline-editor').hidden);
});
test('blank cells and shared fields are editable, SSO identifier stays read-only',t=>{
  const h=setup(t,'raman','SSO');loaded(h);h.field('PPT 편집 · 실험자').click();
  assert(h.field('PPT 수정 · 실험자').readOnly);
  h.q('edit-next').click();h.q('edit-next').click();loaded(h);
  h.field('PPT 편집 · 실험조건 및 실험환경 1행 값').click();
  assert(h.q('inline-editor').hidden);assert(h.d.activeElement.classList.contains('ppt-cell-input'));
  h.input('PPT 수정 · 실험조건 및 실험환경 1행 값','25°C');
  assert.equal(h.field('실험조건 및 실험환경 1행 값').value,'25°C');assert.equal(h.timers.size,0);
  h.d.activeElement.blur();h.tick();
  assert.equal(h.posts[0].body.sections[1].rows[0][2],'25°C');
});
for(const kind of ['ftir','raman','tem'])test(kind+' in-place source cells preserve focus, defer rendering, escape restores and Enter autosaves',t=>{
  const h=setup(t,kind),label='PPT 수정 · 실험조건 및 실험환경 1행 값';
  h.q('edit-next').click();h.q('edit-next').click();loaded(h);
  const click=()=>h.field('PPT 편집 · 실험조건 및 실험환경 1행 값').click();
  const key=(code,extra={})=>h.d.activeElement.dispatchEvent(new h.w.KeyboardEvent('keydown',{keyCode:code,bubbles:true,cancelable:true,...extra}));
  click();let input=h.d.activeElement;assert.equal(input.parentNode.style.left,'50%');assert.equal(input.parentNode.style.top,'30%');
  input.dispatchEvent(new h.w.Event('compositionstart'));h.input(label,'한글');key(13);
  assert.equal(h.d.activeElement,input);assert.equal(h.posts.length,0);assert.equal(h.timers.size,0);
  input.dispatchEvent(new h.w.Event('compositionend'));key(13,{shiftKey:true});assert.equal(h.d.activeElement,input);
  h.input(label,'<script>문자 그대로</script>\n25°C');assert.equal(h.d.querySelectorAll('.ppt-cell-editor script').length,0);
  key(27);assert.equal(h.field('실험조건 및 실험환경 1행 값').value,'');assert.equal(h.timers.size,0);
  click();h.input(label,'25°C');key(13);h.tick();loaded(h);
  assert.equal(h.posts[0].body.sections[1].rows[0][2],'25°C');assert.deepEqual(h.posts[0].body.canvasEdits,{});
  click();h.input(label,'');h.d.activeElement.blur();h.tick();assert.equal(h.posts[1].body.sections[1].rows[0][2],'');
});
test('pending source revision cannot steal focus or drop later in-cell edits',t=>{
  const h=setup(t);h.q('edit-next').click();h.q('edit-next').click();loaded(h);
  h.input('보고서 제목','새 제목');h.delay();h.tick();
  h.field('PPT 편집 · 실험조건 및 실험환경 1행 값').click();const input=h.d.activeElement;
  h.input('PPT 수정 · 실험조건 및 실험환경 1행 값','27°C');h.finish();loaded(h);
  assert.equal(h.d.activeElement,input);assert.equal(input.value,'27°C');assert.equal(h.timers.size,0);
  input.blur();h.tick();assert.equal(h.posts[1].body.sections[1].rows[0][2],'27°C');
});
test('equal merged source cells edit together; distinct source values retain separate fields',t=>{
  for(const values of [['Sample A','Sample A'],['Sample A','Sample B']]){
    const refs=[0,1].map(r=>'section/experiment_conditions/rows/'+r+'/0');
    const h=setup(t,'ftir','',{config:{fields:[]},rows:values.map(v=>[v,'온도','25°C']),regions:[{refs,cell:true,x:.1,y:.2,w:.3,h:.4}]});
    h.q('edit-next').click();h.q('edit-next').click();loaded(h);h.d.querySelector('.edit-region').click();
    if(values[0]===values[1]){
      assert.equal(h.d.querySelectorAll('.ppt-cell-input').length,1);const input=h.d.activeElement;
      input.value='변경 시료';input.dispatchEvent(new h.w.Event('input',{bubbles:true}));input.blur();h.tick();
      assert.deepEqual(h.posts[0].body.sections[1].rows.map(r=>r[0]),['변경 시료','변경 시료']);
    }else{assert(!h.q('inline-editor').hidden);assert.equal(h.q('inline-fields').querySelectorAll('textarea').length,2);assert.equal(h.posts.length,0);}
  }
});
test('date and authenticated read-only cells retain their input policies in place',t=>{
  const h=setup(t,'ftir','SSO',{config:{fields:[]},rows:[['공통','온도','']],regions:[
    {refs:['completionDate'],cell:true,x:.1,y:.2,w:.3,h:.1},{refs:['transfer/operatorId'],cell:true,x:.5,y:.2,w:.3,h:.1}]});
  h.q('edit-next').click();h.q('edit-next').click();loaded(h);
  h.field('PPT 편집 · 완료일').click();assert.equal(h.d.activeElement.type,'date');assert.equal(h.d.activeElement.value,'2026-10-15');h.d.activeElement.blur();
  h.field('PPT 편집 · 실험자').click();assert(h.d.activeElement.readOnly);h.d.activeElement.blur();assert.equal(h.posts.length,0);
});
test('regions wait for page image, errors hide them, and duplicate variable names are refused',t=>{
  const h=setup(t);assert(h.q('edit-regions').hidden);loaded(h);assert(!h.q('edit-regions').hidden);
  h.q('slide-image').dispatchEvent(new h.w.Event('error'));assert(h.q('edit-regions').hidden);
  h.input('새 공통 항목 이름','의뢰번호');h.q('variable-add').click();assert.equal(h.posts.length,0);
  assert.match(h.q('status').textContent,/중복/);
  h.input('새 공통 항목 이름','<b>항목</b>');h.q('variable-add').click();assert.equal(h.d.querySelectorAll('b').length,0);
});
test('renamed common condition labels and variable choices stay synchronized',t=>{
  const h=setup(t);h.q('edit-next').click();loaded(h);h.field('PPT 편집 · 분석 결과 요약 · 문단 1').click();
  h.input('실험조건 및 실험환경 1행 항목','측정 온도');
  assert.equal(h.field('실험조건 및 실험환경 1행 값').parentNode.firstChild.nodeValue,'측정 온도');
  assert([...h.q('insert-variable').options].some(o=>o.value==='공통.측정 온도'));
  assert(![...h.q('insert-variable').options].some(o=>o.value==='공통.온도'));
});
test('removing a variable used by another variable cannot break its links',t=>{
  const h=setup(t);
  for(const name of ['A','B']) {h.input('새 공통 항목 이름',name);h.q('variable-add').click();}
  h.input('A · 값','23');h.input('B · 값','{{ A }}');
  h.field('A · 값').closest('.variable-row').querySelector('button').click();
  assert(h.field('A · 값'));assert.match(h.q('status').textContent,/사용 중/);
});
test('empty transfer fields allow report review but not sending',t=>{
  const h=setup(t);h.q('send').click();assert.equal(h.posts.length,0);
  assert.match(h.q('status').textContent,/의뢰번호/);
});

const clientPrograms=JSON.parse(execFileSync(process.env.RIST_TEST_PYTHON || 'python3',['-c',
  'import json; from app import ftir_web,raman_web; print(json.dumps([ftir_web._UPLOAD_SCRIPT,raman_web._UPLOAD_SCRIPT]))'],{encoding:'utf8'}));
for(const [index,kind] of ['ftir','raman'].entries()) test(kind+' report generation saves workspace then opens editor only after completion',async()=>{
  const code=clientPrograms[index].replace(/<\/?script\b[^>]*>/g,''),ast=acorn.parse(code,{ecmaVersion:'latest'});
  let target;
  function visit(node){if(!node||typeof node!=='object')return;if(node.type==='FunctionDeclaration'&&node.id.name==='createReport')target=node;Object.values(node).forEach(v=>Array.isArray(v)?v.forEach(visit):visit(v));}
  visit(ast);assert(target);
  const events=[], job={jobId:'review-ready',status:'completed',editUrl:'/api/v1/'+kind+'/report/jobs/review-ready/edit'};
  const deps={files:[{}],reportTransferFormState:()=>({}),updatePersistentReportDownload:()=>{},updateReportSendAvailability:()=>{},latestAnalysisPayload:{},
    loading:{},status:{},setBusy:()=>{},setMessage:()=>{},setReportProgress:()=>{},gd:{},currentFigurePayload:()=>({}),reportSnapshotSignature:()=>'',
    reportAnalysisPayload:()=>({}),compactReportFigurePayload:()=>({}),reportFilesForVisibleSamples:()=>[{}],captureReportFigureImage:async()=>'',
    reportImageFile:()=>({}),Blob:globalThis.Blob,uploadReportFilesWithSession:async()=>({jobId:'building'}),
    pollReportJob:async()=>{events.push('completed');return job;},setReportDownloadLink:()=>{},saveWorkspaceNow:async()=>events.push('saved'),
    window:{location:{assign:url=>events.push(url)},RistSpectrumWorkflow:{imageFile:()=>({})}}};
  const run=()=>new Function(...Object.keys(deps),'var lastReportJob,lastReportSnapshot;'+code.slice(target.start,target.end)+';return createReport();')(...Object.values(deps));
  await run();assert.deepEqual(events,['completed','saved',job.editUrl]);
  events.length=0;deps.pollReportJob=async()=>{throw new Error('render failed');};await run();assert.deepEqual(events,[]);
});
for(const [index,kind] of ['ftir','raman'].entries()) test(kind+' original workspace switches download/send to the confirmed revision',async()=>{
  const source=clientPrograms[index].match(/window.addEventListener\("storage", function\(event\) \{[\s\S]*?\n  \}\);/)[0];
  const fields={},select={value:'old'},calls=[];let handler;
  const job={jobId:'new',parentJobId:'old',status:'completed',transfer:{requestNumber:'UPDATED',operatorId:'SSO'}};
  const state=new Function('window','fetchJson','requestSelect','setReportTransferValue','updatePersistentReportDownload','updateReportSendAvailability','scheduleWorkspaceSave','setSuccessMessage','setMessage',
    'var lastReportJob={jobId:"old"},REPORT_OPTION_STORAGE_KEY="conditions";'+source+';return ()=>lastReportJob;')(
      {addEventListener:(name,fn)=>handler=fn},async url=>{calls.push(url);return job;},select,
      (key,value)=>fields[key]=value,j=>calls.push(j.jobId),()=>{},()=>{},()=>{},message=>{throw Error(message);});
  handler({key:'rist-report-revision',newValue:JSON.stringify({previousJobId:'unrelated',job})});
  assert.equal(calls.length,0);
  handler({key:'rist-report-revision',newValue:JSON.stringify({previousJobId:'old',job})});
  await new Promise(resolve=>setImmediate(resolve));
  assert.equal(state().jobId,'new');assert.equal(fields.requestNumber,'UPDATED');assert.equal(select.value,'');
  assert.equal(calls[0],'/api/v1/'+kind+'/report/jobs/new');
});
