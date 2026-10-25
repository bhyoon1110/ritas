const {test}=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const {JSDOM}=require('jsdom'),acorn=require('acorn');
const source=fs.readFileSync(path.join(__dirname,'../app/report_saved_graphs.js'),'utf8');
function setup(t){
  const dom=new JSDOM('<div id="canvas-tools"></div><div id="slide-stage"></div>',{url:'http://localhost/edit',runScripts:'outside-only'});
  t.after(()=>dom.window.close());const w=dom.window,d=w.document,messages=[],inserts=[],errors=[];
  const parent={postMessage(data,origin){messages.push({data,origin});}};Object.defineProperty(w,'parent',{value:parent});
  let pageId='page1',job='job1',available=true;w.eval(source);
  new w.RistSavedGraphs({job:()=>job,message:(...args)=>errors.push(args),canvas:{canInsertGraph:()=>available,pageId:()=>pageId,insertGraph:(...args)=>inserts.push(args)}});
  const send=(data,origin=w.location.origin,source=parent)=>w.dispatchEvent(new w.MessageEvent('message',{data,origin,source}));
  const catalog={type:'rist-saved-graphs',jobId:'job1',graphs:[{id:'g1',title:'그래프 <b>1</b>'},{id:'g2',title:'그래프 2'}]};send(catalog);
  const stage=d.getElementById('slide-stage');stage.getBoundingClientRect=()=>({left:10,top:20,width:1000,height:500});
  function drop(data,type='application/x-rist-saved-graph'){
    const e=new w.MouseEvent('drop',{bubbles:true,cancelable:true,clientX:510,clientY:270});
    Object.defineProperty(e,'dataTransfer',{value:{types:[type],getData:()=>JSON.stringify(data)}});stage.dispatchEvent(e);return e;
  }
  return {w,d,send,catalog,messages,inserts,errors,drop,page:id=>pageId=id,job:id=>job=id,available:v=>available=v};
}
test('saved graph bridge is ES5, uses text labels and native drop position or accessible button',t=>{
  acorn.parse(source,{ecmaVersion:5});const h=setup(t);assert.equal(h.d.querySelectorAll('#report-saved-graphs button').length,2);assert(!h.d.querySelector('b'));
  h.drop({id:'g1',jobId:'job1'});const request=h.messages[0].data;assert.equal(request.id,'g1');assert.equal(request.jobId,'job1');
  h.send({type:'rist-saved-graph-data',jobId:'job1',id:'g1',requestId:request.requestId,image:'blob'});
  assert.equal(h.inserts.length,1);assert.deepEqual(JSON.parse(JSON.stringify(h.inserts[0][1])),{x:.5,y:.5});h.inserts[0][2](null);
  h.d.querySelectorAll('#report-saved-graphs button')[1].click();assert.equal(h.messages[1].data.id,'g2');
});
test('graph drop never handles page reordering or foreign/unknown IDs, forged messages or old jobs',t=>{
  const h=setup(t);h.drop({id:'g1',jobId:'job1'},'application/x-rist-page');h.drop({id:'unknown',jobId:'job1'});h.drop({id:'g1',jobId:'old'});
  h.send({type:'rist-insert-saved-graph',jobId:'job1',id:'g1'},'https://evil.example');
  h.send({type:'rist-insert-saved-graph',jobId:'job1',id:'g1'},h.w.location.origin,h.w);assert.equal(h.messages.length,0);
  h.available(false);h.drop({id:'g1',jobId:'job1'});assert.equal(h.messages.length,0);assert(h.errors.at(-1)[1]);
});
test('duplicate requests/replies, changed pages and upload errors do not add a graph',t=>{
  const h=setup(t);h.drop({id:'g1',jobId:'job1'});h.drop({id:'g1',jobId:'job1'});assert.equal(h.messages.length,1);
  const reply={type:'rist-saved-graph-data',jobId:'job1',id:'g1',requestId:h.messages[0].data.requestId,image:'blob'};
  h.send({...reply,requestId:'wrong'});assert.equal(h.inserts.length,0);h.page('page2');h.send(reply);assert.equal(h.inserts.length,0);
  h.send(reply);assert.equal(h.inserts.length,0);assert.match(h.errors.at(-1)[0],/페이지가 변경/);
  h.drop({id:'g1',jobId:'job1'});h.send({...reply,requestId:h.messages[1].data.requestId,error:'이미지 실패'});assert.equal(h.inserts.length,0);assert.equal(h.errors.at(-1)[0],'이미지 실패');
});
