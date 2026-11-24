const {test}=require('node:test'),assert=require('node:assert/strict'),path=require('node:path');
const {JSDOM}=require('jsdom'),esbuild=require('esbuild');
const web=path.resolve(__dirname,'../../app/xrd_workbench/web');
const source=esbuild.buildSync({entryPoints:[path.join(web,'report/ppt_editor.mjs')],bundle:true,format:'iife',globalName:'Ppt',write:false}).outputFiles[0].text;

test('phase classification and literal formula/name editing use distinct controls and exact cells',t=>{
  const dom=new JSDOM('<div id="host"></div>',{runScripts:'outside-only',pretendToBeVisual:true});
  t.after(()=>dom.window.close());const w=dom.window,d=w.document;
  require('../ppt_tools_harness.cjs')(w);w.eval(source);
  const document={canvasEdits:{},pageEdits:{}},linked=[],messages=[];
  const editor=w.Ppt.mountPptEditor({host:d.getElementById('host'),document:()=>document,
    changed(){},linked:target=>linked.push(target),message:text=>messages.push(text),upload(){},editing(){}});
  const id='shape-'+ 'a'.repeat(20),direct={tab:'classes',field:'ppt_cell',objectId:id,row:1,col:1};
  const classification={tab:'classes',field:'phase_class',sampleId:'A',phase:{name:'Phase A',card:'1',db:'ICDD'},cell:{...direct,col:0}};
  const table={id,kind:'table',name:'상동정',x:.1,y:.2,w:.8,h:.3,cells:[
    {row:1,col:0,x:0,y:0,w:.5,h:1,text:'[주요상] Phase A'},
    {row:1,col:1,x:.5,y:0,w:.5,h:1,text:'Fe2O3'}]};
  editor.load();editor.accept({canvasEditing:true,pageEditing:true,ratio:16/9,pages:[{
    id:'page1',title:'상동정 결과',objects:[table],targets:[
      {x:.1,y:.2,w:.4,h:.3,target:classification},{x:.5,y:.2,w:.4,h:.3,target:direct}]}]});
  d.querySelectorAll('.edit-region')[0].click();
  assert.equal(linked[0],classification);assert.equal(d.activeElement.className,'');
  const nameButton=d.getElementById('ppt-cell-direct');assert.equal(nameButton.hidden,false);nameButton.click();
  assert.equal(d.activeElement.value,'[주요상] Phase A');
  d.activeElement.value='표에서만 수정한 상명';d.activeElement.dispatchEvent(new w.Event('input'));
  d.activeElement.dispatchEvent(new w.KeyboardEvent('keydown',{keyCode:13,bubbles:true}));
  assert.equal(document.canvasEdits.page1[0].tableCells[0].col,0);
  assert.equal(document.canvasEdits.page1[0].tableCells[0].text,'표에서만 수정한 상명');
  d.getElementById('canvas-toggle').click();
  d.querySelectorAll('.edit-region')[1].click();
  assert.equal(d.activeElement.value,'Fe2O3');assert.equal(linked.length,1);
  d.activeElement.value='Fe3O4';d.activeElement.dispatchEvent(new w.Event('input'));
  assert.equal(document.canvasEdits.page1[0].tableCells.find(c=>c.col===1).text,'Fe3O4');
  assert(messages.some(text=>text.includes('원자료와 상 분류 값은 바뀌지 않')));
});
