const {test}=require('node:test');
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const {JSDOM}=require('jsdom'),babel=require('@babel/core');
const source=['mail_editor_icons.js','mail_table_editor.js','mail_address_book.js','mail_editor.js'].map(n=>fs.readFileSync(path.join(__dirname,'../app/xrd_workbench/web/shared',n),'utf8')).join('\n');
const programs={native:source,chrome49:babel.transformSync(source,{configFile:false,babelrc:false,presets:[[require('@babel/preset-env'),{targets:{chrome:'49'},forceAllTransforms:true,modules:false}]]}).code};
function setup(t,program){
  const dom=new JSDOM('<div id="host"><label><textarea>본문</textarea></label></div>',{url:'http://rist.test',runScripts:'outside-only',pretendToBeVisual:true});
  t.after(()=>dom.window.close());const w=dom.window,d=w.document;w.eval(program);
  let number='';const pending=[];
  w.XMLHttpRequest=class {
    open(method,url){this.url=url;}
    send(){pending.push(this);}
    abort(){this.aborted=true;}
    resolve(value,status=200){this.status=status;this.responseText=JSON.stringify(value);this.onload();}
  };
  const editor=new w.RistMailEditor({host:d.querySelector('#host'),body:d.querySelector('textarea'),requestNumber:()=>number});
  const field=name=>d.querySelector('[aria-label="'+name+'"]');
  return {w,d,editor,field,pending,request(value){number=value;editor.refreshRecipient();}};
}
const delay=ms=>new Promise(resolve=>setTimeout(resolve,ms));
for(const [mode,program] of Object.entries(programs)){
  test(mode+': To is immutable; copy recipients use the exact fixed domain',t=>{
    const u=setup(t,program),to=u.field('수신자 이메일 주소'),name=u.field('수신자 이름'),cc=u.field('참조 (CC)'),bcc=u.field('비밀참조 (BCC)');
    assert.equal(to.readOnly,true);assert.equal(name.readOnly,true);
    u.editor.load({to:'old@example.com',recipientName:'옛 수신자',cc:'review@RIST.RE.KR',bcc:'secret@rist.re.kr',signature:'서명'});
    assert.equal(to.value,'');assert.equal(name.value,'');assert.equal(cc.value,'review');
    assert.equal(u.field('메일 서명').value,'서명');
    to.readOnly=false;to.value='injected@example.com';name.value='주입';
    assert.equal(u.editor.value().to,'');assert.equal(u.editor.value().recipientName,'');
    cc.value='review; another';bcc.value='secret@RIST.RE.KR';
    assert.equal(u.editor.value().cc,'review@rist.re.kr, another@rist.re.kr');
    assert.equal(u.editor.validate(),'');assert.equal(u.editor.value().signature,'서명');
    for(const invalid of ['x@example.com','x@sub.rist.re.kr','x@rist.re.kr.evil.com','x@rist.re.kr;foreign@example.com']){
      bcc.value=invalid;assert.match(u.editor.validate(),/@rist.re.kr/);assert.ok(u.editor.value().bcc.includes('x@')||u.editor.value().bcc.includes('foreign@'));
    }
    u.editor.load({bcc:'old@example.com'});assert.equal(bcc.value,'old@example.com');assert.match(u.editor.validate(),/@rist.re.kr/);
  });
  test(mode+': changing request immediately clears requester; stale lookup cannot restore it',async t=>{
    const u=setup(t,program);u.request('REQ-1');await delay(330);
    assert.equal(u.pending.length,1);u.request('REQ-2');await delay(330);
    assert.equal(u.pending.length,2);
    assert(u.pending[0].aborted);
    u.pending[1].resolve({recipientName:'새 의뢰자',email:null});await delay(5);
    assert.equal(u.field('수신자 이름').value,'새 의뢰자');
    u.pending[0].resolve({recipientName:'이전 의뢰자',email:'old@example.com'});await delay(5);
    assert.equal(u.field('수신자 이름').value,'새 의뢰자');assert.equal(u.field('수신자 이메일 주소').value,'');
    u.request('');assert.equal(u.field('수신자 이름').value,'');
    assert.equal(u.editor.value().to,'');
  });
}
