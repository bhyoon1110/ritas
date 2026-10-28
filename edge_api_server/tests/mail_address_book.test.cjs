const {test}=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const {JSDOM}=require('jsdom'),babel=require('@babel/core'),acorn=require('acorn');
const source=['mail_editor_icons.js','mail_table_editor.js','mail_address_book.js','mail_editor.js'].map(n=>fs.readFileSync(path.join(__dirname,'../app/xrd_workbench/web/shared',n),'utf8')).join('\n');
const programs={native:source,chrome49:babel.transformSync(source,{configFile:false,babelrc:false,presets:[[require('@babel/preset-env'),{targets:{chrome:'49'},forceAllTransforms:true,modules:false}]]}).code};
const clone=v=>JSON.parse(JSON.stringify(v));
function setup(t,source,contacts=[],publicContacts=[]){
  const dom=new JSDOM('<div id="host"><label><textarea>본문</textarea></label></div>',{url:'http://rist.test',runScripts:'outside-only'});
  t.after(()=>dom.window.close());const w=dom.window,d=w.document;w.confirm=()=>true;
  let book={revision:0,contacts:clone(contacts)},shared={revision:0,contacts:clone(publicContacts),canManage:true},changes=0,fail=0;const requests=[];
  w.XMLHttpRequest=class {
    open(method,url){this.method=method;this.url=url;this.headers={};}
    setRequestHeader(k,v){this.headers[k]=v;}
    send(raw){
      const value=raw&&JSON.parse(raw);requests.push({method:this.method,url:this.url,value,headers:this.headers});
      this.status=fail||200;const target=this.url.includes('/shared-contacts')?shared:book;
      if(!fail&&this.method!=='GET'){
        assert.equal(value.revision,target.revision);const id=this.url.split('/').pop();
        if(this.method==='POST')target.contacts.push({...value.contact,id:String(target.revision+100).padStart(32,'0')});
        if(this.method==='PUT')target.contacts=target.contacts.map(c=>c.id===id?{...value.contact,id}:c);
        if(this.method==='DELETE')target.contacts=target.contacts.filter(c=>c.id!==id);
        target.revision++;
      }
      this.responseText=JSON.stringify(fail?{message:'다른 창에서 주소록이 변경되었습니다. 새로고침하세요.'}:target);this.onload();
    }
  };
  w.eval(source);const editor=new w.RistMailEditor({host:d.querySelector('#host'),body:d.querySelector('textarea'),changed:()=>changes++});
  const field=label=>Array.from(d.querySelectorAll('[aria-label]')).find(e=>e.getAttribute('aria-label')===label);
  return {w,d,editor,field,requests,changes:()=>changes,book:()=>book,shared:()=>shared,fail:code=>fail=code,
    select(label,value){const e=field(label);e.value=value;e.dispatchEvent(new w.Event('change',{bubbles:true}));},
    click:label=>field(label).click(),input(label,value){const e=field(label);e.value=value;e.dispatchEvent(new w.Event('input',{bubbles:true}));}};
}
for(const [mode,program] of Object.entries(programs)){
  test(mode+': admin manages shared visibility independently from private contacts',t=>{
    const personal=[{id:'a'.repeat(32),name:'개인',department:'',email:'mine@rist.re.kr'}];
    const u=setup(t,program,personal);u.click('주소록');u.click('공용 주소록');
    assert.equal(u.requests.at(-1).url,'/api/v1/report-mail/shared-contacts');
    u.click('주소록 새 주소');assert.equal(u.field('주소록 공개 범위').value,'admins');
    u.input('주소록 이름','공동연구');u.input('주소록 이메일 아이디','shared');u.select('주소록 공개 범위','projects');u.click('주소록 저장');
    assert.equal(u.shared().contacts.length,0);assert.match(u.d.querySelector('.mail-book-status').textContent,/실험을 하나 이상/);
    u.click('FTIR 주소록 공개');u.click('TEM 주소록 공개');u.click('주소록 저장');
    assert.deepEqual(u.shared().contacts[0].visibility,{mode:'projects',projects:['FTIR','TEM']});
    assert.equal(u.changes(),0);assert.deepEqual(u.book().contacts,personal);
    assert.match(u.d.querySelector('.mail-book-list').textContent,/공개: FTIR · TEM/);
    u.click('공동연구 주소 수정');u.select('주소록 공개 범위','all');u.click('주소록 저장');
    assert.deepEqual(u.shared().contacts[0].visibility,{mode:'all',projects:[]});
    u.click('내 주소록');assert(u.field('개인 주소 수정'));assert.equal(u.field('공동연구 주소 수정'),undefined);
    u.click('공용 주소록');u.click('공동연구 주소 삭제');assert.equal(u.shared().contacts.length,0);assert.deepEqual(u.book().contacts,personal);
  });
  test(mode+': members cannot manage shared contacts and selection rechecks visibility and address changes',t=>{
    const contacts=[{id:'b'.repeat(32),name:'공개',department:'분석실',email:'shared@rist.re.kr'},
      {id:'c'.repeat(32),name:'다른 공개',department:'',email:'second@rist.re.kr'}];
    const u=setup(t,program,[],contacts);u.shared().canManage=false;u.click('주소록');u.click('공용 주소록');
    assert(u.field('주소록 새 주소').hidden);assert(u.field('주소록 새 주소').disabled);assert.equal(u.field('공개 주소 수정'),undefined);assert.equal(u.field('공개 주소 삭제'),undefined);
    u.click('공개 · shared@rist.re.kr 선택');u.click('다른 공개 · second@rist.re.kr 선택');u.shared().contacts.pop();u.click('참조에 추가');
    assert.equal(u.editor.value().cc,'','A partially revoked selection must not be inserted');assert.match(u.d.querySelector('.mail-book-status').textContent,/공개 범위가 변경/);
    u.click('공개 · shared@rist.re.kr 선택');u.shared().contacts[0].email='updated@rist.re.kr';u.click('비밀참조에 추가');assert.equal(u.editor.value().bcc,'');
    u.click('공개 · updated@rist.re.kr 선택');u.click('참조에 추가');assert.equal(u.editor.value().cc,'updated@rist.re.kr');assert.equal(u.editor.value().to,'');
    u.click('공개 · updated@rist.re.kr 선택');u.click('참조에 추가');assert.equal(u.editor.value().cc,'updated@rist.re.kr');
    assert(u.requests.every(r=>r.method==='GET'));assert(u.field('수신자 이메일 주소').readOnly);
  });
  test(mode+': shared read failures clear stale contacts, and reopening refreshes permissions',t=>{
    const u=setup(t,program,[],[{id:'d'.repeat(32),name:'공개',department:'',email:'shared@rist.re.kr'}]);
    u.shared().canManage=false;u.click('주소록');u.click('공용 주소록');u.click('공개 · shared@rist.re.kr 선택');
    u.fail(403);u.click('참조에 추가');assert.equal(u.d.querySelectorAll('.mail-book-item').length,0);assert(u.field('참조에 추가').disabled);assert.equal(u.editor.value().cc,'');
    u.fail(0);u.click('주소록');u.click('주소록');assert(u.field('공개 · shared@rist.re.kr 선택'));
    u.click('주소록');u.shared().contacts=[];u.click('주소록');assert.equal(u.d.querySelectorAll('.mail-book-item').length,0);
  });
  test(mode+': address book CRUD/search applies only copy recipients, preserves signature and current recipients',t=>{
    acorn.parse(program,{ecmaVersion:5});const u=setup(t,program);assert.equal(u.requests.length,0);
    u.editor.load({cc:'existing@rist.re.kr',signature:'RIST 서명'});
    u.click('주소록');assert.equal(u.requests[0].method,'GET');
    u.click('주소록 새 주소');u.input('주소록 이름','김연구');u.input('주소록 부서','분석실');u.input('주소록 이메일 아이디','research');u.click('주소록 저장');
    assert.equal(u.requests[1].headers['X-Requested-With'],'RIST-Mail-Editor');assert.equal(u.book().contacts[0].email,'research@rist.re.kr');
    assert.equal(u.changes(),0,'Managing a private book must not change or regenerate a report');
    u.input('주소록 검색','분석실');u.click('김연구 · research@rist.re.kr 선택');u.click('참조에 추가');
    assert.equal(u.editor.value().cc,'existing@rist.re.kr, research@rist.re.kr');assert.equal(u.editor.value().to,'');
    u.click('김연구 · research@rist.re.kr 선택');u.click('참조에 추가');assert.equal(u.editor.value().cc,'existing@rist.re.kr, research@rist.re.kr');
    u.click('김연구 주소 수정');u.input('주소록 이름','<b>수정</b>');u.input('주소록 이메일 아이디','updated@RIST.RE.KR');u.click('주소록 저장');
    assert.equal(u.book().contacts[0].name,'<b>수정</b>');assert.equal(u.book().contacts[0].email,'updated@RIST.RE.KR');
    assert.equal(u.d.querySelectorAll('.mail-book-item b').length,0);
    u.click('<b>수정</b> · updated@RIST.RE.KR 선택');u.click('비밀참조에 추가');assert.equal(u.editor.value().bcc,'updated@rist.re.kr');
    u.click('<b>수정</b> 주소 삭제');assert.equal(u.book().contacts.length,0);
    assert.equal(u.editor.value().bcc,'updated@rist.re.kr');assert.equal(u.editor.value().signature,'RIST 서명');
    assert.equal(u.field('수신자 이메일 주소').readOnly,true);
  });
  test(mode+': domain errors, conflicts and report locks preserve typed contact and do not add recipients',t=>{
    const u=setup(t,program);u.click('주소록');u.click('주소록 새 주소');u.input('주소록 이름','연구');u.input('주소록 이메일 아이디','bad@example.com');u.click('주소록 저장');
    assert.equal(u.requests.length,1);assert.match(u.d.querySelector('.mail-book-status').textContent,/RIST 이메일/);
    u.input('주소록 이메일 아이디','good');u.fail(409);u.click('주소록 저장');
    assert.equal(u.field('주소록 이름').value,'연구');assert(!u.d.querySelector('.mail-book-form').hidden);
    u.fail(0);u.click('주소록 새로고침');assert.equal(u.field('주소록 이메일 아이디').value,'good');u.click('주소록 저장');
    u.click('연구 · good@rist.re.kr 선택');u.editor.lock(true);assert(u.field('참조에 추가').disabled);assert(u.field('연구 주소 수정').disabled);
    u.click('참조에 추가');assert.equal(u.editor.value().cc,'');u.editor.lock(false);assert(!u.field('참조에 추가').disabled);
    u.input('참조 (CC)','bad@example.com');u.click('참조에 추가');assert.equal(u.editor.value().cc,'bad@example.com');assert.match(u.d.querySelector('.mail-book-status').textContent,/@rist.re.kr/);
  });
  test(mode+': paging bounds rendered rows and selection survives search without selecting hidden contacts',t=>{
    const contacts=Array.from({length:120},(_,i)=>({id:String(i).padStart(32,'0'),name:'연구'+i,department:'부서'+(i%3),email:'person'+i+'@rist.re.kr'}));
    const u=setup(t,program,contacts);u.click('주소록');assert.equal(u.d.querySelectorAll('.mail-book-item').length,50);
    u.click('연구0 · person0@rist.re.kr 선택');u.click('주소록 다음 목록');assert.equal(u.d.querySelectorAll('.mail-book-item').length,50);
    u.click('연구50 · person50@rist.re.kr 선택');u.input('주소록 검색','person119@');assert.equal(u.d.querySelectorAll('.mail-book-item').length,1);
    u.click('참조에 추가');assert.equal(u.editor.value().cc,'person0@rist.re.kr, person50@rist.re.kr');
    assert.equal(u.editor.value().to,'');assert.equal(u.editor.value().bcc,'');
    u.click('연구119 · person119@rist.re.kr 선택');u.click('주소록 선택 해제');assert(u.field('참조에 추가').disabled);
  });
}
