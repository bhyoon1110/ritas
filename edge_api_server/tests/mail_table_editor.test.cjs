const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path');
const {JSDOM}=require('jsdom');
const babel=require('@babel/core');
const source=['mail_editor_icons.js','mail_table_editor.js','mail_address_book.js','mail_editor.js'].map(n=>fs.readFileSync(path.join(__dirname,'../app/xrd_workbench/web/shared',n),'utf8')).join('\n');
const programs={native:source,fallback:babel.transformSync(source,{configFile:false,babelrc:false,presets:[[require('@babel/preset-env'),{targets:{chrome:'49'},forceAllTransforms:true,modules:false}]]}).code};
function setup(t,mode){
  const dom=new JSDOM('<div id="toolbar"></div><div id="rich" contenteditable="true"><table><tbody><tr><td><p>A</p></td><td>B</td></tr><tr><td>C</td><td>D</td></tr></tbody></table></div>',{runScripts:'outside-only',pretendToBeVisual:true});
  t.after(()=>dom.window.close());const w=dom.window,d=w.document;w.eval(programs[mode]);let count=0,enabled=true,inserted='',note='';
  const node=(tag,parent,text)=>{const e=d.createElement(tag);if(text!=null)e.textContent=text;if(parent)parent.appendChild(e);return e;};
  const button=(parent,text,label,fn,icon)=>{const b=node('button',parent,text);b.type='button';b.title=label;b.setAttribute('aria-label',label);b.onclick=fn;if(icon)w.RistMailIcons(b,icon,label);return b;};
  const rich=d.querySelector('#rich'),tables=new w.RistMailTables({rich,toolbar:d.querySelector('#toolbar'),node,button,enabled:()=>enabled,changed:()=>count++,onShow(){},remember(){},restore(){},outsideTable(){},insert:html=>inserted=html,note:text=>note=text});
  const find=label=>d.querySelector('[aria-label="'+label+'"]');
  const click=(cell,shift=false)=>cell.dispatchEvent(new w.MouseEvent('click',{bubbles:true,shiftKey:shift}));
  return {w,d,rich,tables,find,click,count:()=>count,inserted:()=>inserted,note:()=>note,lock(){enabled=false;tables.lock();}};
}
for(const mode of Object.keys(programs)){
  test(mode+': every mail action uses a named decorative icon, preserving grid and resize handles',t=>{
    const u=setup(t,mode),host=u.d.createElement('div');u.d.body.appendChild(host);host.innerHTML='<label><textarea>본문</textarea></label>';
    let changed=0;const editor=new u.w.RistMailEditor({host,body:host.querySelector('textarea'),changed:()=>changed++,value:{bodyHtml:'<p>본문</p><table><tr><td>A</td></tr></table>'}});
    const buttons=[...host.querySelectorAll('.mail-toolbar button')].filter(b=>!b.closest('.mail-table-grid'));
    assert(buttons.length>=30);
    for(const b of buttons){assert(b.classList.contains('mail-icon-button'),b.title);assert.equal(b.textContent,'');assert.equal(b.title,b.getAttribute('aria-label'));assert.equal(b.querySelector('svg').getAttribute('aria-hidden'),'true');assert.equal(b.querySelector('svg').getAttribute('focusable'),'false');}
    assert.equal(host.querySelectorAll('.mail-table-grid button').length,48);assert.equal(host.querySelectorAll('.mail-table-grid .mail-icon-button').length,0);
    assert.equal(host.querySelectorAll('.mail-resize-handle').length,3);assert.equal(host.querySelectorAll('.mail-resize-handle.mail-icon-button').length,0);
    const remove=host.querySelector('[aria-label="선택한 메일 표 삭제"]');assert(remove.classList.contains('mail-icon-danger'));
    const save=host.querySelector('[aria-label="내 기본 서명 저장"]');save.focus();assert.equal(host.querySelector('.mail-tool-hint').textContent,save.title);
    const image=host.querySelector('[aria-label="메일 이미지 삽입"]');image.dispatchEvent(new u.w.MouseEvent('mouseover',{bubbles:true}));assert.equal(host.querySelector('.mail-tool-hint').textContent,image.title);assert.equal(changed,0);
    const disabledBefore=buttons.map(b=>b.disabled);
    editor.lock(true);assert(buttons.every(b=>b.disabled));editor.lock(false);assert.deepEqual(buttons.map(b=>b.disabled),disabledBefore);assert(!editor.value().bodyHtml.includes('<svg'));
  });
  test(mode+': text mode icon, tooltip and accessible name follow state without losing rich content',t=>{
    const u=setup(t,mode),host=u.d.createElement('div');u.d.body.appendChild(host);host.innerHTML='<label><textarea>본문</textarea></label>';
    const body=host.querySelector('textarea');let changed=0;u.w.confirm=()=>true;
    const editor=new u.w.RistMailEditor({host,body,changed:()=>changed++,value:{bodyHtml:'<p><b>본문</b></p><table><tr><td>A</td></tr></table>'}});
    const toggle=host.querySelector('[aria-label="메일 본문 일반 텍스트 전환"]'),rich=host.querySelector('.mail-rich'),before=editor.value().bodyHtml;
    toggle.click();assert.equal(toggle.dataset.mailIcon,'rich-text');assert.equal(toggle.title,'메일 본문 서식 편집 전환');assert.equal(toggle.getAttribute('aria-label'),toggle.title);assert.equal(toggle.getAttribute('aria-pressed'),'true');assert(rich.hidden);assert(!body.hidden);assert(toggle.querySelector('svg'));
    toggle.click();assert.equal(toggle.dataset.mailIcon,'plain-text');assert.equal(toggle.title,'메일 본문 일반 텍스트 전환');assert.equal(toggle.getAttribute('aria-pressed'),'false');assert(!rich.hidden);assert(body.hidden);assert.equal(editor.value().bodyHtml,before);assert.equal(changed,0);
    editor.lock(true);toggle.click();assert(!rich.hidden);assert.equal(toggle.dataset.mailIcon,'plain-text');
  });
  test(mode+': grid and numeric insert are bounded; keyboard grid navigation works',t=>{
    const u=setup(t,mode);u.find('메일 표 삽입 설정').click();const first=u.find('1행 1열 표 삽입');first.focus();first.dispatchEvent(new u.w.KeyboardEvent('keydown',{key:'ArrowRight',bubbles:true}));
    assert.equal(u.d.activeElement.getAttribute('aria-label'),'1행 2열 표 삽입');u.find('3행 4열 표 삽입').click();assert.equal((u.inserted().match(/<tr>/g)||[]).length,3);assert.equal((u.inserted().match(/<td>/g)||[]).length,12);
    u.find('메일 표 삽입 설정').click();u.find('메일 표 행 수').value=21;u.find('메일 표 삽입').click();assert.match(u.note(),/20행/);
  });
  test(mode+': shift and touch-style range selection apply both alignments to all cells',t=>{
    const u=setup(t,mode),cells=u.rich.querySelectorAll('td');u.click(cells[0]);u.click(cells[3],true);assert.equal(u.rich.querySelectorAll('[data-mail-selected]').length,4);
    u.find('메일 글자 가운데 정렬').click();u.find('메일 셀 가운데 정렬').click();
    for(const c of cells){assert.equal(c.style.textAlign,'center');assert.equal(c.style.verticalAlign,'middle');}
    assert.equal(cells[0].querySelector('p').style.textAlign,'center');
    u.tables.reset();u.click(cells[0]);u.find('여러 셀 범위 선택').click();u.click(cells[2]);assert.equal(u.rich.querySelectorAll('[data-mail-selected]').length,2);
    const stored=u.w.RistMailEditor.clean(u.rich.innerHTML);assert(!stored.includes('data-mail-selected'));assert.match(stored,/vertical-align: middle/);
  });
  test(mode+': selected rows/columns edit as a group and whole-table delete stays scoped',t=>{
    const u=setup(t,mode);u.click(u.rich.querySelector('td'));u.find('위 행 추가').click();assert.equal(u.rich.querySelectorAll('tr').length,3);
    u.find('왼쪽 열 추가').click();assert.equal(u.rich.querySelectorAll('tr')[0].cells.length,3);
    let rows=u.rich.querySelectorAll('tr');u.click(rows[0].cells[0]);u.click(rows[1].cells[1],true);u.find('선택 행 삭제').click();assert.equal(u.rich.querySelectorAll('tr').length,1);
    rows=u.rich.querySelectorAll('tr');u.click(rows[0].cells[0]);u.click(rows[0].cells[1],true);u.find('선택 열 삭제').click();assert.equal(u.rich.querySelectorAll('td').length,1);
    u.find('선택한 메일 표 삭제').click();assert.equal(u.rich.querySelectorAll('table').length,0);
  });
  test(mode+': restricted CSS round-trips alignment, background and geometry, not tracking CSS',t=>{
    const u=setup(t,mode),value='<p style="text-align:right;position:fixed">본문</p><table style="width:60%"><tr style="height:80px"><td width="65%" style="text-align:center;vertical-align:middle;background-color:rgb(232,242,250);background-image:url(https://invalid);padding:999px" onclick="evil()">A</td><td style="width:35%">B</td></tr></table>';
    const html=u.w.RistMailEditor.clean(value);assert(!/invalid|onclick|999|position\s*:\s*fixed/.test(html));
    const simple=u.w.RistMailEditor.clean('<table><tr><td>default</td></tr></table>');assert.equal(u.w.RistMailEditor.clean(simple),simple);
    const out=u.d.createElement('div');out.innerHTML=html;const cell=out.querySelector('td');assert.equal(cell.style.width,'65%');assert.equal(cell.style.textAlign,'center');assert.equal(cell.style.verticalAlign,'middle');assert.equal(cell.getAttribute('bgcolor'),'#e8f2fa');assert.equal(out.querySelector('tr').style.height,'80px');
    assert.equal(out.querySelector('table').style.width,'60%');assert.equal(out.querySelector('table').getAttribute('width'),'60%');assert.equal(u.w.RistMailEditor.clean(html),html);
    for(const width of ['20%','33.333%','100%']){out.innerHTML=u.w.RistMailEditor.clean('<table width="'+width+'"><tr><td>A</td></tr></table>');assert.equal(out.querySelector('table').style.width,width);}
    for(const width of ['19%','101%','9000px','expression(x)','calc(50% + 1px)']){out.innerHTML=u.w.RistMailEditor.clean('<table style="width:'+width+';height:9999px"><tr><td>A</td></tr></table>');assert.equal(out.querySelector('table').style.width,'100%');assert.equal(out.querySelector('table').style.height,'');}
    const bad=u.w.RistMailEditor.clean('<table><tr style="height:9999px"><td style="width:1%;text-align:expression(x);vertical-align:expression(x);background-color:red">A</td></tr></table>');assert(!/9999|expression|width: 1%|color: red/.test(bad));
  });
  test(mode+': locked editor cannot change table, reset removes stale selection',t=>{
    const u=setup(t,mode);u.click(u.rich.querySelector('td'));u.lock();const before=u.rich.innerHTML;u.find('메일 글자 오른쪽 정렬').click();u.find('아래 행 추가').click();assert.equal(u.rich.innerHTML,before);assert.equal(u.count(),0);u.tables.reset();assert.equal(u.rich.querySelectorAll('[data-mail-selected]').length,0);
  });
  test(mode+': whole-table numeric sizing, limits, row proportions, reset and keyboard',t=>{
    const u=setup(t,mode),table=u.rich.querySelector('table'),rows=table.rows;
    rows[0].style.height='40px';rows[1].style.height='80px';u.click(rows[0].cells[0]);
    const width=u.find('메일 표 전체 너비'),height=u.find('메일 표 전체 높이');
    width.value=60;width.onchange();assert.equal(table.style.width,'60%');assert.equal(table.getAttribute('width'),'60%');
    height.value=240;height.onchange();assert.equal(rows[0].style.height,'80px');assert.equal(rows[1].style.height,'160px');
    height.value=700;height.onchange();assert.equal(rows[0].style.height,'300px');assert.equal(rows[1].style.height,'400px');
    rows[0].getBoundingClientRect=()=>({height:10});rows[1].getBoundingClientRect=()=>({height:1000});height.value=500;height.onchange();assert.equal(rows[0].style.height,'100px');assert.equal(rows[1].style.height,'400px');
    for(const value of [0,19,101]){width.value=value;width.onchange();assert.equal(table.style.width,'60%');}
    height.value=801;height.onchange();assert.equal(rows[1].style.height,'400px');
    u.find('표 전체 크기 드래그 조절').dispatchEvent(new u.w.KeyboardEvent('keydown',{key:'ArrowLeft',bubbles:true}));assert.equal(table.style.width,'59%');
    u.find('메일 표 전체 너비 초기화').click();u.find('메일 표 전체 높이 자동').click();assert.equal(table.style.width,'100%');
    for(const r of rows){assert.equal(r.style.height,'');assert.equal(r.getAttribute('height'),null);}
    u.lock();width.value=40;width.onchange();height.value=100;height.onchange();assert.equal(table.style.width,'100%');assert.equal(rows[0].style.height,'');
  });
  test(mode+': whole-table corner scales both axes, preserves columns, rolls back all attributes',t=>{
    const u=setup(t,mode),table=u.rich.querySelector('table'),rect=(width,height)=>({left:0,top:0,right:width,bottom:height,width,height});
    table.getBoundingClientRect=()=>rect(200,100);u.rich.getBoundingClientRect=()=>rect(400,300);
    for(const r of table.rows){r.getBoundingClientRect=()=>rect(200,50);r.cells[0].getBoundingClientRect=()=>rect(119.999,50);r.cells[1].getBoundingClientRect=()=>rect(80.001,50);r.cells[0].style.width='60%';r.cells[1].style.width='40%';}
    u.click(table.rows[0].cells[0]);const handle=u.find('표 전체 크기 드래그 조절');
    const mouse=(target,name,x,y)=>target.dispatchEvent(new u.w.MouseEvent(name,{bubbles:true,cancelable:true,button:0,clientX:x,clientY:y}));
    mouse(handle,'mousedown',200,100);mouse(u.d,'mousemove',240,140);mouse(u.d,'mouseup',240,140);
    assert.equal(table.style.width,'60%');assert.equal(table.rows[0].style.height,'70px');assert.equal(table.rows[1].style.height,'70px');assert.equal(table.rows[0].cells[0].style.width,'60%');assert.equal(u.count(),1);
    const before=table.outerHTML;
    for(const cancel of ['Escape','blur','lock']){
      mouse(handle,'mousedown',200,100);mouse(u.d,'mousemove',1000,10000);assert.equal(table.style.width,'100%');assert.equal(table.rows[0].style.height,'400px');
      if(cancel==='Escape')u.d.dispatchEvent(new u.w.KeyboardEvent('keydown',{key:cancel,bubbles:true}));else if(cancel==='blur')u.w.dispatchEvent(new u.w.Event('blur'));else u.lock();
      assert.equal(table.outerHTML,before);assert.equal(u.count(),1);
    }
  });
  test(mode+': mouse fallback drag commits once; Escape, blur and locking roll back',t=>{
    const u=setup(t,mode),table=u.rich.querySelector('table'),first=table.querySelector('td');
    const rect=(width,height=40)=>({left:0,top:0,right:width,bottom:height,width,height});
    table.getBoundingClientRect=()=>rect(200);u.rich.getBoundingClientRect=()=>rect(220,300);
    for(const c of table.querySelectorAll('td'))c.getBoundingClientRect=()=>rect(100);
    for(const r of table.rows)r.getBoundingClientRect=()=>rect(200);
    u.click(first);const handle=u.find('선택 열 너비 드래그 조절');
    const mouse=(target,name,x)=>target.dispatchEvent(new u.w.MouseEvent(name,{bubbles:true,cancelable:true,button:0,clientX:x,clientY:20}));
    mouse(handle,'mousedown',100);mouse(u.d,'mousemove',120);assert.equal(u.count(),0);mouse(u.d,'mouseup',120);assert.equal(u.count(),1);assert.equal(first.style.width,'60%');
    mouse(handle,'mousedown',100);mouse(u.d,'mousemove',110);u.d.dispatchEvent(new u.w.KeyboardEvent('keydown',{key:'Escape',bubbles:true}));assert.equal(first.style.width,'60%');assert.equal(u.count(),1);
    mouse(handle,'mousedown',100);mouse(u.d,'mousemove',110);u.w.dispatchEvent(new u.w.Event('blur'));assert.equal(first.style.width,'60%');
    mouse(handle,'mousedown',100);mouse(u.d,'mousemove',110);u.lock();assert.equal(first.style.width,'60%');assert.equal(u.count(),1);
  });
}
