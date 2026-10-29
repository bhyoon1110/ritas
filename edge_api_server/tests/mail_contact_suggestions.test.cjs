const {test}=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const {JSDOM}=require('jsdom'),babel=require('@babel/core'),acorn=require('acorn');
const source=['mail_editor_icons.js','mail_table_editor.js','mail_address_book.js','mail_editor.js'].map(n=>fs.readFileSync(path.join(__dirname,'../app/xrd_workbench/web/shared',n),'utf8')).join('\n');
const programs={native:source,chrome49:babel.transformSync(source,{configFile:false,babelrc:false,presets:[[require('@babel/preset-env'),{targets:{chrome:'49'},forceAllTransforms:true,modules:false}]]}).code};
const delay=()=>new Promise(r=>setTimeout(r,180));
const contact=(id,name,email,department='분석실')=>({id,name,email,department});
function setup(t,program){
  const dom=new JSDOM('<div id="host"><label><textarea>본문</textarea></label></div>',{url:'http://rist.test',runScripts:'outside-only'});
  t.after(()=>dom.window.close());const w=dom.window,d=w.document,requests=[];
  const server={private:[contact('one','김연구','kim@rist.re.kr')],shared:[contact('two','김분석','second@rist.re.kr')],fail:{},hold:false};
  w.XMLHttpRequest=class{
    open(method,url){assert.equal(method,'GET');this.url=url;this.scope=url.includes('shared-contacts')?'shared':'private';}
    send(){requests.push(this);if(!server.hold)this.resolve();}
    abort(){this.aborted=true;}
    resolve(){this.status=server.fail[this.scope]||200;this.responseText=JSON.stringify({contacts:server[this.scope],revision:0});this.onload();}
  };
  w.eval(program);const editor=new w.RistMailEditor({host:d.getElementById('host'),body:d.querySelector('textarea')});
  const field=k=>d.querySelector('[aria-label="'+(k==='cc'?'참조 (CC)':'비밀참조 (BCC)')+'"]');
  const popup=k=>field(k).closest('label').querySelector('.mail-contact-popup');
  return {w,d,server,requests,editor,field,popup,
    rows:k=>Array.from(popup(k).querySelectorAll('[role=option]')),
    type(k,text,pos=text.length){const e=field(k);e.focus();e.value=text;e.setSelectionRange(pos,pos);e.dispatchEvent(new w.Event('input',{bubbles:true}));},
    key(k,key,extra={}){const e=new w.KeyboardEvent('keydown',{key,bubbles:true,cancelable:true,...extra});field(k).dispatchEvent(e);return e;}};
}
for(const [mode,program] of Object.entries(programs)){
  test(mode+': one Korean character searches both books, shows identity and selects without touching To/BCC',async t=>{
    acorn.parse(program,{ecmaVersion:5});const u=setup(t,program);
    u.server.shared.push(contact('dup','김중복','KIM@rist.re.kr'),contact('bad','김외부','external@example.com'));
    u.editor.load({cc:'existing@rist.re.kr',bcc:'secret@rist.re.kr'});assert.equal(u.requests.length,0);
    u.type('cc','existing, 김');await delay();assert.equal(u.requests.length,2);assert.equal(u.rows('cc').length,2);
    assert.match(u.popup('cc').textContent,/김연구.*분석실.*kim@rist.re.kr.*내 주소록/s);
    assert.match(u.popup('cc').textContent,/김분석.*공용 주소록/s);assert.equal(u.field('cc').getAttribute('aria-expanded'),'true');
    u.key('cc','ArrowDown');assert.equal(u.rows('cc')[0].getAttribute('aria-selected'),'true');
    assert(u.key('cc','Enter').defaultPrevented);assert.equal(u.editor.value().cc,'existing@rist.re.kr, kim@rist.re.kr');
    assert.equal(u.editor.value().bcc,'secret@rist.re.kr');assert.equal(u.editor.value().to,'');assert(u.popup('cc').hidden);
    u.type('bcc','김');await delay();u.rows('bcc')[1].focus();u.rows('bcc')[1].click();assert.equal(u.editor.value().bcc,'second@rist.re.kr');
    await delay();assert(u.popup('bcc').hidden,'Returning focus from a touch-selected option must not reopen the suggestions');
    assert.equal(u.editor.validate(),'');
  });
  test(mode+': replaces only the current token, supports name/department/email search and removes duplicates',async t=>{
    const u=setup(t,program);u.type('cc','before; 김; after',9);await delay();u.rows('cc')[0].click();
    assert.equal(u.editor.value().cc,'before@rist.re.kr, kim@rist.re.kr, after@rist.re.kr');
    u.type('cc','before, kim, 분석실');await delay();assert.equal(u.rows('cc').length,2);u.rows('cc')[0].click();
    assert.equal(u.editor.value().cc,'before@rist.re.kr, kim@rist.re.kr');
    u.type('cc','SECOND@');await delay();assert.equal(u.rows('cc').length,1);u.key('cc','ArrowUp');u.key('cc','Enter');
    assert.equal(u.editor.value().cc,'second@rist.re.kr');
    u.type('cc','foreign@example.com, 김');await delay();u.rows('cc')[0].click();
    assert.equal(u.field('cc').value,'foreign@example.com, 김');assert.match(u.popup('cc').textContent,/@rist.re.kr 이메일 주소만/);
    const many=Array.from({length:50},(_,i)=>'person'+i).join(', ');u.type('cc',many+', 김');await delay();u.rows('cc')[0].click();
    assert.equal(u.field('cc').value,many+', 김');assert.match(u.popup('cc').textContent,/50명 이하/);
  });
  test(mode+': Korean composition, Escape, Tab and edited query never confirm a stale contact',async t=>{
    const u=setup(t,program);u.type('cc','김');await delay();u.key('cc','ArrowDown');
    u.field('cc').dispatchEvent(new u.w.CompositionEvent('compositionstart',{bubbles:true}));
    assert(!u.key('cc','Enter',{isComposing:true,keyCode:229}).defaultPrevented);assert.equal(u.field('cc').value,'김');
    u.field('cc').dispatchEvent(new u.w.CompositionEvent('compositionend',{bubbles:true}));await delay();assert.equal(u.rows('cc').length,2);assert.equal(u.requests.length,2);
    u.key('cc','Escape');assert(u.popup('cc').hidden);assert.equal(u.field('cc').value,'김');
    u.type('cc','김');await delay();u.key('cc','ArrowDown');u.type('cc','없는이름');u.key('cc','Enter');
    assert.equal(u.field('cc').value,'없는이름');await delay();assert.equal(u.rows('cc').length,0);assert.match(u.popup('cc').textContent,/검색 결과가 없습니다/);
    u.type('cc','김');await delay();u.key('cc','Tab');assert(u.popup('cc').hidden);assert.equal(u.field('cc').value,'김');
    u.type('cc','김');await delay();u.field('bcc').focus();await delay();assert(u.popup('cc').hidden);assert(u.popup('bcc').hidden);
  });
  test(mode+': revalidates selected shared/private addresses and fails closed after revocation or network error',async t=>{
    const u=setup(t,program);u.type('bcc','김');await delay();u.server.shared=[];u.rows('bcc')[1].click();
    assert.equal(u.field('bcc').value,'김');assert.equal(u.rows('bcc').length,0);assert.match(u.popup('bcc').textContent,/공개 범위가 변경/);
    u.type('bcc','김');await delay();u.server.private[0].email='changed@rist.re.kr';u.rows('bcc')[0].click();
    assert.equal(u.field('bcc').value,'김');assert.match(u.popup('bcc').textContent,/주소 또는 공개 범위/);
    u.type('bcc','김');await delay();u.server.fail.private=403;u.rows('bcc')[0].click();
    assert.equal(u.field('bcc').value,'김');assert.match(u.popup('bcc').textContent,/주소를 확인하지 못했습니다/);
    u.type('bcc','김');await delay();assert.equal(u.rows('bcc').length,0);assert.match(u.popup('bcc').textContent,/일부 주소록/);
  });
  test(mode+': stale asynchronous lookups and choices cannot modify a different field or newly loaded/locked report',async t=>{
    const u=setup(t,program);u.server.hold=true;u.type('cc','김');await delay();const old=u.requests.slice();
    u.type('bcc','김');await delay();old.forEach(r=>r.resolve());assert(u.popup('cc').hidden);assert.equal(u.rows('bcc').length,0);
    u.requests.slice(2).forEach(r=>r.resolve());assert.equal(u.rows('bcc').length,2);u.rows('bcc')[0].click();const pending=u.requests.at(-1);
    u.type('bcc','different');pending.resolve();assert.equal(u.field('bcc').value,'different');
    await delay();u.requests.filter(r=>!r.aborted).forEach(r=>r.resolve());
    u.type('bcc','김');await delay();u.rows('bcc')[0].click();const choice=u.requests.at(-1);
    u.editor.load({bcc:'new@rist.re.kr'});choice.resolve();assert.equal(u.editor.value().bcc,'new@rist.re.kr');assert(u.popup('bcc').hidden);
    u.server.hold=false;u.type('cc','김');await delay();u.server.hold=true;u.rows('cc')[0].click();const locking=u.requests.at(-1);
    u.editor.lock(true);locking.resolve();assert.equal(u.field('cc').value,'김');assert(u.popup('cc').hidden);assert(u.field('cc').disabled);
  });
  test(mode+': debounces requests, bounds results and treats stored names as text',async t=>{
    const u=setup(t,program);u.server.private=Array.from({length:100},(_,i)=>contact('id'+i,'김연구'+i,'person'+i+'@rist.re.kr'));
    u.server.shared=[contact('html','김<b>문자</b>','html@rist.re.kr')];
    u.type('cc','ㄱ');u.type('cc','기');u.type('cc','김');await delay();assert.equal(u.requests.length,2);assert.equal(u.rows('cc').length,20);assert.match(u.popup('cc').textContent,/101명 중 20명/);
    u.type('cc','<b>');await delay();assert.equal(u.requests.length,2);assert.equal(u.rows('cc').length,1);assert.equal(u.popup('cc').querySelector('b'),null);
    u.type('cc','person99');await delay();assert.equal(u.rows('cc').length,1);assert.match(u.popup('cc').textContent,/김연구99/);
    u.type('cc','');await delay();assert(u.popup('cc').hidden);
    u.server.private=[];u.type('cc','person');await delay();assert.equal(u.requests.length,4);assert.equal(u.rows('cc').length,0);
  });
}
