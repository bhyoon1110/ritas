const {test}=require('node:test');
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const {JSDOM}=require('jsdom');
const app=path.join(__dirname,'../app'),source=fs.readFileSync(path.join(app,'report_history.js'),'utf8');
function setup(t){
  const dom=new JSDOM(fs.readFileSync(path.join(app,'report_history.html'),'utf8'),{url:'http://edge.test/my/reports',runScripts:'outside-only'});
  t.after(()=>dom.window.close());const w=dom.window,calls=[];
  w.XMLHttpRequest=class{open(method,url){this.method=method;this.url=url;}setRequestHeader(){}send(){calls.push(this);}abort(){this.aborted=true;}reply(data,status=200){this.status=status;this.responseText=typeof data==='string'?data:JSON.stringify(data);this.onload();}};
  w.eval(fs.readFileSync(path.join(app,'report_delivery_progress.js'),'utf8'));w.eval(source);
  const $=id=>w.document.getElementById('history-'+id);
  return{w,calls,$,submit(){ $('filters').dispatchEvent(new w.Event('submit',{bubbles:true,cancelable:true})); }};
}
function item(patch={}){return{reportId:'report-a',requestNumber:'2022T00793',analysisType:'XRD',experimentCode:'A11111',equipmentCode:'XRD-LIMS-TEST',generatedAt:'2026-11-25T17:29:13+09:00',transferStatus:'COMPLETED',mailStatus:'SENT',packageSizeBytes:14438400,localFilesRetained:true,downloadUrl:'/api/v1/reports/report-a/package',...patch};}
function result(items=[item()],page=1,totalCount=1,totalPages=1){return{items,page,totalCount,totalPages,pageSize:20};}
test('history script is ES5 and page uses explicit labels',()=>{
  require('acorn').parse(source,{ecmaVersion:5,allowReserved:true});
  const dom=new JSDOM(fs.readFileSync(path.join(app,'report_history.html'),'utf8'));
  for(const input of dom.window.document.querySelectorAll('input,select'))assert(dom.window.document.querySelector('label[for="'+input.id+'"]'));
  dom.window.close();
});
test('history shows completed, failed, and expired reports without unsafe markup or untrusted URLs',t=>{
  const u=setup(t);assert.equal(u.calls[0].method,'GET');assert.equal(u.calls[0].timeout,15000);
  u.calls[0].reply(result([item(),item({reportId:'other',requestNumber:'<img src=x onerror=alert(1)>',transferStatus:'FAILED',mailStatus:null,lastErrorMessage:'<script>evil()</script>',localFilesRetained:false,downloadUrl:'https://evil.example/'})],1,2));
  assert.equal(u.w.document.querySelectorAll('.report-row').length,2);
  assert.match(u.$('list').textContent,/메일 서버 전달 완료/);assert.match(u.$('list').textContent,/로컬 파일 보관 만료/);
  assert.equal(u.$('list').querySelectorAll('img,script').length,0);
  assert.match(u.$('list').textContent,/<script>evil\(\)<\/script>/);
  assert.equal(u.$('list').querySelectorAll('a[href$="/package"]').length,1);
  assert(!u.$('list').innerHTML.includes('evil.example'));assert.equal(u.$('next').disabled,true);
});
test('filters and pagination retain submitted query while refresh stays on current page',t=>{
  const u=setup(t);u.calls[0].reply(result([item()],1,41,3));
  u.$('query').value='  2022T00793  ';u.$('project').value='XRD';u.$('status').value='FAILED';u.$('from').value='2026-11-01';u.$('to').value='2026-11-30';u.submit();
  let url=new URL(u.calls[1].url,'http://edge.test');assert.equal(url.searchParams.get('q'),'2022T00793');assert.equal(url.searchParams.get('project'),'XRD');assert.equal(url.searchParams.get('transferStatus'),'FAILED');assert.equal(url.searchParams.get('dateFrom'),'2026-11-01');assert.equal(url.searchParams.get('page'),'1');
  u.calls[1].reply(result([item()],1,41,3));u.$('query').value='unsent edit';u.$('next').click();
  url=new URL(u.calls[2].url,'http://edge.test');assert.equal(url.searchParams.get('page'),'2');assert.equal(url.searchParams.get('q'),'2022T00793');
  u.calls[2].reply(result([item()],2,41,3));u.$('refresh').click();url=new URL(u.calls[3].url,'http://edge.test');assert.equal(url.searchParams.get('page'),'2');
  u.calls[3].reply(result([item()],2,41,3));u.$('reset').click();url=new URL(u.calls[4].url,'http://edge.test');assert.equal(url.searchParams.get('page'),'1');assert.equal(url.searchParams.get('q'),'');assert.equal(url.searchParams.get('transferStatus'),'');
});
test('validation and request failures clear private results and recover controls',t=>{
  const u=setup(t);u.calls[0].reply(result());u.$('from').value='2026-12-01';u.$('to').value='2026-11-01';u.submit();assert.equal(u.calls.length,1);assert.equal(u.$('list').querySelectorAll('.report-row').length,0);assert.match(u.$('notice').textContent,/늦을 수 없습니다/);assert.equal(u.$('search').disabled,false);
  u.$('reset').click();u.calls[1].reply(result());u.$('refresh').click();u.calls[2].reply({},401);assert.equal(u.$('list').querySelectorAll('.report-row').length,0);assert.equal(u.$('login').hidden,false);assert.equal(u.$('prev').disabled,true);assert.equal(u.$('next').disabled,true);
  u.$('refresh').click();u.calls[3].reply(result());u.$('refresh').click();u.calls[4].ontimeout();assert.match(u.$('notice').textContent,/지연/);assert.equal(u.$('list').querySelectorAll('.report-row').length,0);assert.equal(u.$('refresh').disabled,false);
});
test('superseded and malformed responses never repopulate stale private data',t=>{
  const u=setup(t);u.$('reset').click();assert.equal(u.calls[0].aborted,true);u.calls[1].reply(result([item({requestNumber:'LATEST'})]));u.calls[0].reply(result([item({requestNumber:'STALE'})]));assert.match(u.$('list').textContent,/LATEST/);assert(!u.$('list').textContent.includes('STALE'));
  u.$('refresh').click();u.calls[2].reply({items:[item(),{}],totalCount:2});assert.equal(u.$('list').querySelectorAll('.report-row').length,0);assert.match(u.$('notice').textContent,/응답/);
});
