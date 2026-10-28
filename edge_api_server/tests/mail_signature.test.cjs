const {test}=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const {JSDOM}=require('jsdom'),babel=require('@babel/core'),acorn=require('acorn');
const source=['mail_editor_icons.js','mail_table_editor.js','mail_address_book.js','mail_editor.js'].map(n=>fs.readFileSync(path.join(__dirname,'../app/xrd_workbench/web/shared',n),'utf8')).join('\n');
const programs={native:source,chrome49:babel.transformSync(source,{configFile:false,babelrc:false,presets:[[require('@babel/preset-env'),{targets:{chrome:'49'},forceAllTransforms:true,modules:false}]]}).code};
const image='data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Y9Z4kkAAAAASUVORK5CYII=';
function setup(t,program){
  const dom=new JSDOM('<div id="host"><label><textarea>본문</textarea></label></div>',{url:'http://rist.test',runScripts:'outside-only'});t.after(()=>dom.window.close());
  const w=dom.window,d=w.document;w.confirm=()=>true;let saved={signature:''},failure=0,delay=false,pending,changes=0;const requests=[],book={revision:0,defaultId:null,items:[]};
  w.XMLHttpRequest=class {
    open(method,url){this.method=method;this.url=url;this.headers={};}setRequestHeader(k,v){this.headers[k]=v;}
    send(raw){const data=raw?JSON.parse(raw):null;requests.push({method:this.method,url:this.url,data,headers:this.headers});
      const finish=()=>{this.status=failure||200;let result=saved;
        if(!failure&&this.url.includes('/signatures')){
          const parts=this.url.split('/signatures')[1].split('/'),id=parts[1],item=book.items.find(i=>i.id===id);
          if(this.method!=='GET'){
            assert.equal(data.revision,book.revision);
            if(this.method==='POST'){const next={id:String(book.revision+1),name:data.name,content:data.content};if(!book.items.length)book.defaultId=next.id;book.items.push(next);}
            else if(parts[2]==='default')book.defaultId=id;
            else if(this.method==='PUT')Object.assign(item,{name:data.name,content:data.content});
            else{book.items=book.items.filter(i=>i.id!==id);if(book.defaultId===id)book.defaultId=null;}
            book.revision++;saved=book.items.find(i=>i.id===book.defaultId)?.content||{signature:''};
          }
          result=this.method==='GET'&&id?{...item,revision:book.revision}:{revision:book.revision,defaultId:book.defaultId,items:book.items.map(i=>({id:i.id,name:i.name,hasImage:!!i.content.signatureImage,isDefault:i.id===book.defaultId}))};
        }else if(!failure&&this.method==='PUT')result=saved=data;
        this.responseText=JSON.stringify(failure?{message:'서명을 저장하지 못했습니다.'}:result);this.onload();};
      if(delay)pending=finish;else finish();
    }
  };
  w.eval(program);const body=d.querySelector('textarea'),editor=new w.RistMailEditor({host:d.querySelector('#host'),body,changed:()=>changes++});
  const field=label=>Array.from(d.querySelectorAll('[aria-label]')).find(e=>e.getAttribute('aria-label')===label);
  return {w,d,editor,body,field,requests,book,changes:()=>changes,saved:()=>saved,setSaved:value=>saved=value,fail:code=>failure=code,
    delay:value=>delay=value,finish:()=>pending(),click:label=>field(label).click(),
    input(label,value,event='input'){const e=field(label);e.value=value;e.dispatchEvent(new w.Event(event,{bubbles:true}));}};
}
for(const [mode,program] of Object.entries(programs)){
  test(mode+': named signatures add/edit/load/set-default/delete without modifying the body',t=>{
    const u=setup(t,program);u.editor.load({signature:'이름',signatureImage:image,signatureImageWidth:'200'});
    u.click('서명 목록 관리');u.input('서명 이름','명함');u.click('현재 서명을 새 항목으로 추가');assert.equal(u.book.items.length,1);
    u.input('서명 이름','영문');u.input('메일 서명','Name');u.click('현재 서명을 새 항목으로 추가');assert.equal(u.book.items.length,2);assert.equal(u.saved().signature,'이름');
    u.input('메일 서명','Revised');u.input('서명 이미지 표시 폭','270','change');u.click('선택한 서명 수정 저장');assert.equal(u.book.items[1].content.signature,'Revised');assert.equal(u.book.items[1].content.signatureImageWidth,'270');
    u.click('선택한 서명 기본값 지정');assert.equal(u.saved().signature,'Revised');
    u.input('저장된 서명 선택',u.book.items[0].id,'change');assert(u.field('선택한 서명 수정 저장').disabled);u.click('선택한 서명 불러오기');assert.equal(u.editor.value().signature,'이름');assert.equal(u.editor.value().signatureImageWidth,'200');assert(!u.field('선택한 서명 수정 저장').disabled);
    u.fail(409);u.input('서명 이름','입력 유지');u.click('선택한 서명 수정 저장');assert.equal(u.field('서명 이름').value,'입력 유지');assert.equal(u.editor.value().signature,'이름');u.fail(0);
    u.click('선택한 저장 서명 삭제');assert.equal(u.book.items.length,1);assert.equal(u.editor.value().signature,'이름');assert.equal(u.body.value,'본문');
  });
  test(mode+': signature paste handles image files and blocks remote image HTML without disturbing plain text',t=>{
    const u=setup(t,program);u.editor.load({signature:'유지',signatureImage:image,signatureImageWidth:'200'});let reads=0;
    u.w.FileReader=class{readAsArrayBuffer(){reads++;this.onerror();}};
    function paste(target,data){const e=new u.w.Event('paste',{bubbles:true,cancelable:true});Object.defineProperty(e,'clipboardData',{value:data});target.dispatchEvent(e);return e;}
    const file=new u.w.File(['png'],'card.png',{type:'image/png'}),text=()=>'';
    assert(paste(u.field('메일 서명'),{items:[{kind:'file',type:'image/png',getAsFile:()=>file}],getData:text}).defaultPrevented);
    assert(paste(u.field('서명 미리보기'),{files:[file],getData:text}).defaultPrevented);assert.equal(reads,2);
    assert(!paste(u.field('메일 서명'),{getData:type=>type==='text/plain'?'텍스트':''}).defaultPrevented);
    assert(paste(u.field('메일 서명'),{getData:type=>type==='text/html'?'<img src="https://invalid/card.png">':''}).defaultPrevented);
    assert.equal(reads,2);assert.equal(u.editor.value().signatureImage,image);assert.equal(u.editor.value().signature,'유지');
  });
  test(mode+': corner drag commits once, supports keyboard, and cancels on Escape/blur/lock/touch cancellation',t=>{
    const u=setup(t,program);u.editor.load({signatureImage:image,signatureImageWidth:'320'});const h=u.field('서명 이미지 모서리 드래그 조절'),picture=u.d.querySelector('.mail-signature-preview img');
    picture.getBoundingClientRect=()=>({width:320,height:160});
    const mouse=(target,type,x,y)=>target.dispatchEvent(new u.w.MouseEvent(type,{bubbles:true,cancelable:true,button:0,clientX:x,clientY:y}));
    function start(){mouse(h,'mousedown',10,10);mouse(u.d,'mousemove',50,30);assert.equal(u.editor.value().signatureImageWidth,'360');}
    start();assert.equal(u.changes(),0);u.d.dispatchEvent(new u.w.KeyboardEvent('keydown',{key:'Escape',bubbles:true}));assert.equal(u.editor.value().signatureImageWidth,'320');mouse(u.d,'mouseup',50,30);assert.equal(u.changes(),0);
    start();mouse(u.d,'mouseup',50,30);assert.equal(u.changes(),1);assert.equal(u.editor.value().signatureImageWidth,'360');
    u.editor.load({signatureImage:image,signatureImageWidth:'320'});start();u.w.dispatchEvent(new u.w.Event('blur'));assert.equal(u.editor.value().signatureImageWidth,'320');
    start();u.editor.lock(true);assert.equal(u.editor.value().signatureImageWidth,'320');u.editor.lock(false);
    h.dispatchEvent(new u.w.KeyboardEvent('keydown',{key:'ArrowRight',shiftKey:true,bubbles:true,cancelable:true}));assert.equal(u.editor.value().signatureImageWidth,'330');
    function touch(target,type,x,y){const e=new u.w.Event(type,{bubbles:true,cancelable:true});Object.defineProperty(e,'changedTouches',{value:[{identifier:7,clientX:x,clientY:y}]});target.dispatchEvent(e);return e;}
    assert(touch(h,'touchstart',10,10).defaultPrevented);assert(touch(u.d,'touchmove',50,30).defaultPrevented);touch(u.d,'touchcancel',50,30);assert.equal(u.editor.value().signatureImageWidth,'330');
  });
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
