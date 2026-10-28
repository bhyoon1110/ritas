const {test}=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const {JSDOM}=require('jsdom'),babel=require('@babel/core'),acorn=require('acorn');
const source=['mail_editor_icons.js','mail_table_editor.js','mail_address_book.js','mail_editor.js'].map(n=>fs.readFileSync(path.join(__dirname,'../app/xrd_workbench/web/shared',n),'utf8')).join('\n');
const programs={native:source,chrome49:babel.transformSync(source,{configFile:false,babelrc:false,presets:[[require('@babel/preset-env'),{targets:{chrome:'49'},forceAllTransforms:true,modules:false}]]}).code};
const image='data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Y9Z4kkAAAAASUVORK5CYII=';
function setup(t,program){
  const dom=new JSDOM('<div id="host"><label><textarea>본문</textarea></label></div>',{url:'http://rist.test',runScripts:'outside-only'});t.after(()=>dom.window.close());
  const w=dom.window,d=w.document;w.confirm=()=>true;let saved={signature:''},failure=0,delay=false,pending,changes=0;const requests=[];
  w.XMLHttpRequest=class {
    open(method,url){this.method=method;this.url=url;this.headers={};}setRequestHeader(k,v){this.headers[k]=v;}
    send(raw){const data=raw?JSON.parse(raw):null;requests.push({method:this.method,data,headers:this.headers});
      const finish=()=>{this.status=failure||200;if(!failure&&this.method==='PUT')saved=data;this.responseText=JSON.stringify(failure?{message:'서명을 저장하지 못했습니다.'}:saved);this.onload();};
      if(delay)pending=finish;else finish();
    }
  };
  w.eval(program);const body=d.querySelector('textarea'),editor=new w.RistMailEditor({host:d.querySelector('#host'),body,changed:()=>changes++});
  const field=label=>Array.from(d.querySelectorAll('[aria-label]')).find(e=>e.getAttribute('aria-label')===label);
  return {w,d,editor,body,field,requests,changes:()=>changes,saved:()=>saved,setSaved:value=>saved=value,fail:code=>failure=code,
    delay:value=>delay=value,finish:()=>pending(),click:label=>field(label).click(),
    input(label,value,event='input'){const e=field(label);e.value=value;e.dispatchEvent(new w.Event(event,{bubbles:true}));}};
}
for(const [mode,program] of Object.entries(programs)){
  test(mode+': text/image/width save together; resize, load and remove do not alter the body',t=>{
    acorn.parse(program,{ecmaVersion:5});const u=setup(t,program);
    u.editor.load({signature:'담당자 <b>RIST</b>',signatureImage:image,signatureImageWidth:'320'});
    assert.equal(u.d.querySelector('.mail-signature-preview p').textContent,'담당자 <b>RIST</b>');assert.equal(u.d.querySelector('.mail-signature-preview b'),null);
    u.input('서명 이미지 표시 폭','260','change');assert.equal(u.d.querySelector('.mail-signature-preview img').width,260);
    u.input('서명 이미지 크기 조절','180');assert.equal(u.field('서명 이미지 표시 폭').value,'180');assert.equal(u.editor.value().signatureImageWidth,'180');
    u.click('내 기본 서명 저장');assert.deepEqual(u.saved(),{signature:'담당자 <b>RIST</b>',signatureImage:image,signatureImageWidth:'180'});
    assert.equal(u.requests.at(-1).headers['X-Requested-With'],'RIST-Mail-Editor');
    u.input('메일 서명','다른 서명');u.click('서명 이미지 삭제');assert.equal(u.editor.value().signatureImage,'');assert.equal(u.d.querySelector('.mail-signature-preview img').getAttribute('src'),null);
    u.click('내 기본 서명 불러오기');assert.equal(u.editor.value().signature,'담당자 <b>RIST</b>');assert.equal(u.editor.value().signatureImage,image);assert.equal(u.editor.value().signatureImageWidth,'180');
    assert.equal(u.body.value,'본문');assert.equal(u.d.querySelector('.mail-rich img'),null);assert.equal(u.editor.value().to,'');assert.equal(u.editor.validate(),'');
    u.input('서명 이미지 표시 폭','2000','change');assert.equal(u.editor.value().signatureImageWidth,'180');
    u.setSaved({signature:'기존 텍스트 서명'});u.click('내 기본 서명 불러오기');assert.equal(u.editor.value().signature,'기존 텍스트 서명');assert.equal(u.editor.value().signatureImage,'');
  });
  test(mode+': failures, report replacement and locks never overwrite the current signature',t=>{
    const u=setup(t,program);u.editor.load({signature:'유지',signatureImage:image,signatureImageWidth:'220'});
    u.fail(422);u.click('내 기본 서명 저장');assert.equal(u.editor.value().signature,'유지');assert.match(u.d.querySelector('.mail-signature').textContent,/서명을 저장하지 못했습니다/);
    u.fail(0);u.delay(true);u.setSaved({signature:'서버 서명',signatureImage:image,signatureImageWidth:'300'});u.click('내 기본 서명 불러오기');
    assert(u.field('메일 서명').disabled);u.editor.load({signature:'새 보고서'});u.finish();assert.equal(u.editor.value().signature,'새 보고서');assert.equal(u.editor.value().signatureImage,'');
    assert.match(u.d.querySelector('.mail-signature').textContent,/편집 상태가 바뀌었습니다/);
    u.editor.lock(true);for(const label of ['메일 서명','내 기본 서명 저장','내 기본 서명 불러오기','서명 이미지 추가·교체','서명 이미지 표시 폭','서명 이미지 크기 조절'])assert(u.field(label).disabled,label);
    u.editor.lock(false);assert(!u.field('내 기본 서명 저장').disabled);
  });
  test(mode+': image-count limits and invalid preferences preserve the existing signature',t=>{
    const u=setup(t,program),html=Array.from({length:4},()=>'<img src="'+image+'">').join('');
    u.editor.load({signature:'현재',bodyHtml:html});u.setSaved({signature:'서버',signatureImage:image,signatureImageWidth:'320'});u.click('내 기본 서명 불러오기');
    assert.equal(u.editor.value().signature,'현재');assert.equal(u.editor.value().signatureImage,'');assert.match(u.d.querySelector('.mail-signature').textContent,/최대 4장/);
    u.setSaved({signature:'잘못된 이미지',signatureImage:'https://remote.invalid/img.png',signatureImageWidth:'320'});u.click('내 기본 서명 불러오기');assert.equal(u.editor.value().signature,'현재');
    u.editor.load({signature:'현재',signatureImage:image,signatureImageWidth:'320',bodyHtml:html});assert.match(u.editor.validate(),/최대 4장/);
  });
}
