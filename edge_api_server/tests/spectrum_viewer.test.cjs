const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {execFileSync} = require('node:child_process');
const {JSDOM} = require('jsdom');
const acorn = require('acorn');
const clone = x => JSON.parse(JSON.stringify(x));
const tick = () => new Promise(resolve => setTimeout(resolve, 20));
const html = execFileSync(process.env.RIST_TEST_PYTHON || 'python3', ['-c',
  "from edge_api_server.tests.test_spectrum_viewer import sample_figure; from app.report import spectrum_viewer as v; v.get_plotlyjs=lambda:''; print(v.viewer_html(title='Test report',figure=sample_figure(),slides=['data:image/jpeg;base64,one','data:image/jpeg;base64,two']))"], {encoding:'utf8'});
const program = fs.readFileSync(path.join(__dirname, '../app/report/spectrum_viewer.js'), 'utf8');
const analysisHtml = execFileSync(process.env.RIST_TEST_PYTHON || 'python3', ['-c',
  "from edge_api_server.tests.test_spectrum_viewer import sample_figure; from app.report import spectrum_viewer as v; v.get_plotlyjs=lambda:''; print(v.viewer_html(title='Edited report',figure=sample_figure(),slides=['data:image/jpeg;base64,one'],analysis={'status':'generated','sections':[{'heading':'해석','paragraphs':['1700 cm-1 피크와 관심 영역을 검토했습니다.'],'source':'llm'}]}))"], {encoding:'utf8'});
const clientPrograms=JSON.parse(execFileSync(process.env.RIST_TEST_PYTHON || 'python3',['-c',
  "import json; from app import ftir_web,raman_web; print(json.dumps([ftir_web._UPLOAD_SCRIPT,raman_web._UPLOAD_SCRIPT]))"],{encoding:'utf8'}));
function setup(t, empty=false, source=html, hash='') {
  const dom=new JSDOM(source,{runScripts:'outside-only',pretendToBeVisual:true,url:'http://localhost/preview'+hash});
  t.after(()=>dom.window.close());
  const w=dom.window,d=w.document,q=id=>d.getElementById(id),gd=q('report-graph'),handlers={};
  let calls=0,config;
  if(empty){const p=JSON.parse(q('report-data').textContent);p.figure.data=[];q('report-data').textContent=JSON.stringify(p);}
  gd.on=(name,fn)=>{handlers[name]=fn;};
  const render=async(target,data,layout,c)=>{calls++;target.data=data;target.layout=layout;config=c;await tick();};
  w.Plotly={newPlot:render,react:render,relayout:async(target,patch)=>Object.assign(target.layout,patch)};
  w.eval(program);
  const published=clone(JSON.parse(q('report-data').textContent).figure);
  return {w,d,q,gd,handlers,published,calls:()=>calls,config:()=>config,
    open:async()=>{q('graph-tab').click();await tick();},
    change:async value=>{q('view-mode').value=value;q('view-mode').dispatchEvent(new w.Event('change'));await tick();}};
}
test('viewer program is ES5, has no editor or network API',()=>{
  acorn.parse(program,{ecmaVersion:5});
  assert(!/fetch\(|XMLHttpRequest|contenteditable|_ristPeakEditor/.test(program));
});
test('recipient HTML uses custom and additional preset ratios, not a narrow editor canvas ratio',async t=>{
  for(const [size,expected] of [[{width:1100,height:820,ratio:'custom'},820], [{width:900,ratio:'3:4'},1200], [{width:1200,ratio:'21:9'},1200*9/21]]) {
    const doc=new JSDOM(html).window.document;
    const payload=JSON.parse(doc.getElementById('report-data').textContent);
    payload.figure.layout.meta.size=size;
    payload.figure.layout.width=340;payload.figure.layout.height=420;
    doc.getElementById('report-data').textContent=JSON.stringify(payload);
    const h=setup(t,false,doc.documentElement.outerHTML);await h.open();
    assert.equal(h.gd.layout.width,size.width);assert(Math.abs(h.gd.layout.height-expected)<1e-6);
    assert.equal(h.gd.layout.margin.autoexpand,false);assert.equal(h.gd.layout.margin.r,24);
    assert.equal(h.gd.layout.margin.b,70);
    await h.change('stack');assert(Math.abs(h.gd.layout.height-expected)<1e-6);
  }
});
test('sized HTML keeps legend inside without changing graph size, across resize and view modes',async t=>{
  const source=new JSDOM(html),node=source.window.document.getElementById('report-data');
  const payload=JSON.parse(node.textContent);
  payload.figure.layout.meta.size={width:1000,ratio:'4:3'};
  payload.figure.layout.legend={x:1.02,y:1,font:{size:28},orientation:'v'};
  payload.figure.layout.margin={l:60,r:260,t:80,b:150,autoexpand:true};
  node.textContent=JSON.stringify(payload);
  const h=setup(t,false,source.serialize());source.window.close();
  let width=374;Object.defineProperty(h.gd.parentNode,'clientWidth',{get:()=>width});
  await h.open();
  for(const viewport of [374,1300]) for(const mode of ['original','overlay','stack']) {
    width=viewport;await h.change(mode);
    assert.equal(h.gd.layout.margin.r,24);assert.equal(h.gd.layout.margin.b,70);
    assert.equal(h.gd.layout.margin.autoexpand,false);assert.equal(h.gd.layout.legend.orientation,'v');
    assert.equal(h.gd.layout.legend.font.size,28);
    assert.equal(h.gd.layout.legend.x,0.02);assert.equal(h.gd.layout.legend.y,0.98);
    assert.equal(h.gd.layout.width,viewport===374?374:1000);
    assert.equal(h.gd.layout.height,viewport===374?420:750);
  }
  assert.deepEqual(h.published,payload.figure,'published snapshot remains untouched');
});
test('standalone graph HTML opens directly without slides, broken image or server calls',async t=>{
  const standalone=html.replace(/("slides":)\[[^\]]*\]/,'$1[]').replace('"graphOnly":false','"graphOnly":true');
  const h=setup(t,false,standalone);await tick();
  assert.equal(h.q('slides-panel').hidden,true);assert.equal(h.q('graph-panel').hidden,false);
  assert.equal(h.calls(),1);assert.equal(h.q('slide-image').getAttribute('src'),null);
  assert.equal(h.config().editable,false);assert.equal(h.config().scrollZoom,true);
  await h.change('stack');assert.equal(h.gd.layout.yaxis.showticklabels,false);
  h.q('reset-view').click();await tick();
  assert.deepEqual(clone(h.gd.data).map(t=>t.y),h.published.data.map(t=>t.y));
});
for (const [index,kind] of ['ftir','raman'].entries()) test(kind+' client snapshot keeps displayed coordinates, text and current signal offset',()=>{
  const source=clientPrograms[index].match(/function compactReportFigurePayload\(figure, forViewer\) \{[\s\S]*?\n  \}/)[0];
  const fn=new Function('gd',source+';return compactReportFigurePayload;')({_ristFtirSignalMode:'transmittance'});
  const input={data:[{x:[1,2],y:[17,18],text:['a','b'],customdata:[[1],[2]],meta:{rist_sample_parent:true,
    ftir_signal_toggle:{absorbance_y:[999]},rist_ftir_stack_offset_absorbance:3,rist_ftir_stack_offset_transmittance:7,
    ...(kind==='raman'?{rist_raman_stack_offset:9}:{})}}],layout:{title:{text:'Fixed'}}};
  const before=clone(input),snapshot=fn(input,true),compact=fn(input,false);
  assert.deepEqual(snapshot.data[0].x,[1,2]);assert.deepEqual(snapshot.data[0].y,[17,18]);
  assert.deepEqual(snapshot.data[0].text,['a','b']);assert.equal(snapshot.data[0].meta.rist_view_offset,kind==='raman'?9:7);
  assert.equal(snapshot.data[0].meta.ftir_signal_toggle,undefined);assert.equal(compact.data[0].x,undefined);
  assert.deepEqual(input,before);
});
test('real PDF page list shows one page at a time and graph is lazy',async t=>{
  const h=setup(t);assert.equal(h.calls(),0);
  assert.equal(h.q('page-select').options.length,2);assert.equal(h.q('previous').disabled,true);
  h.q('next').click();assert.equal(h.q('page-count').textContent,'2 / 2');assert.equal(h.q('next').disabled,true);
  assert(h.q('slide-image').src.endsWith('two'));h.q('previous').click();assert(h.q('slide-image').src.endsWith('one'));
  await h.open();assert.equal(h.calls(),1);assert.equal(h.config().editable,false);assert.equal(h.config().scrollZoom,true);
  assert(Object.values(h.config().edits).every(v=>v===false));
  assert.deepEqual(clone(h.gd.data).map(t=>t.y),h.published.data.map(t=>t.y));
});
test('PPT opens first; analysis stays frozen across graph display changes and has no edit actions',async t=>{
  const h=setup(t,false,analysisHtml);
  assert.equal(h.q('analysis-panel').hidden,true);
  assert.equal(h.q('slides-panel').hidden,false);
  assert.equal(h.q('slides-tab').getAttribute('aria-pressed'),'true');
  assert.equal(h.calls(),0);
  const prose=h.q('analysis-panel').textContent;
  assert(prose.includes('1700 cm-1'));
  await h.open();await h.change('stack');
  h.q('analysis-tab').click();
  assert.equal(h.q('analysis-panel').hidden,false);
  assert.equal(h.q('graph-panel').hidden,true);
  assert.equal(h.q('analysis-panel').textContent,prose);
  assert.equal(h.q('analysis-panel').querySelectorAll('button,input,textarea,[contenteditable]').length,0);
  h.q('slides-tab').click();
  assert.equal(h.q('analysis-panel').hidden,true);assert.equal(h.q('slides-panel').hidden,false);
});
test('refresh restores the requested PPT page and clamps it when the deck shrinks',t=>{
  const h=setup(t,false,html,'#view=slides&page=1');assert.equal(h.q('page-count').textContent,'2 / 2');
  const shorter=setup(t,false,analysisHtml,'#view=analysis&page=199');
  assert.equal(shorter.q('page-count').textContent,'1 / 1');assert.equal(shorter.q('analysis-panel').hidden,false);
  const invalid=setup(t,false,html,'#view=invalid&page=-1');
  assert.equal(invalid.q('page-count').textContent,'1 / 2');assert.equal(invalid.q('slides-panel').hidden,false);
});
for (const [index,kind] of ['ftir','raman'].entries()) test(kind+' hidden samples retain stable groups for edited report metadata',()=>{
  const source=clientPrograms[index].match(/function filterReportAnalysisPayload\(payload\) \{[\s\S]*?\n  \}/)[0];
  const fn=new Function('visibleReportSampleGroups',source+';return filterReportAnalysisPayload;')(()=>['sample:2']);
  const payload={samples:[{label:'Hidden 1'},{label:'Hidden 2'},{label:'Renamed',metadata:{Resolution:'4'}}]};
  const result=fn(payload);
  assert.equal(result.samples.length,1);assert.equal(result.samples[0].sample_group,'sample:2');
  assert.equal(result.samples[0].metadata.Resolution,'4');assert.equal(result.sample,'Renamed');
});
test('overlay/stack shift only Y display and attached peaks/ratios, restore is exact',async t=>{
  const h=setup(t);await h.open();await h.change('overlay');
  assert.deepEqual(clone(h.gd.data[0].y),[1,3,2]);assert.deepEqual(clone(h.gd.data[1].y),[3]);
  assert.equal(h.gd.layout.annotations[0].y,4);assert.equal(h.gd.layout.annotations[1].y,5);
  assert.equal(h.gd.layout.annotations[1].text,'Ratio: 2.5');assert.equal(h.gd.layout.shapes[0].y0,3);
  await h.change('stack');
  assert.deepEqual(clone(h.gd.data[0].line),{width:3.5,dash:'dash',color:'#123456'});
  const delta=h.gd.data[0].y[1]-13;
  assert.equal(h.gd.data[1].y[0],13+delta);assert.equal(h.gd.layout.annotations[0].y,14+delta);
  assert.deepEqual(clone(h.gd.data[0].x),[4000,1700,400]);
  h.q('reset-view').click();await tick();
  assert.deepEqual(clone(h.gd.data).map(t=>t.y),h.published.data.map(t=>t.y));
  assert.deepEqual(clone(h.gd.layout.annotations),h.published.layout.annotations);
  assert.deepEqual(clone(h.gd.layout.shapes),h.published.layout.shapes);
  assert.deepEqual(clone(h.gd.layout.xaxis.range),[4000,400]);assert.deepEqual(clone(h.gd.layout.yaxis.range),[0,16]);
});
test('sample visibility includes linked peaks and labels without changing issued data',async t=>{
  const h=setup(t);await h.open();assert.equal(h.handlers.plotly_legendclick({curveNumber:0}),false);await tick();
  assert.equal(h.gd.data[0].visible,'legendonly');assert.equal(h.gd.data[1].visible,'legendonly');
  assert.equal(h.gd.layout.annotations[0].visible,false);assert.equal(h.gd.layout.annotations[1].visible,false);
  assert.equal(h.gd.layout.shapes[0].visible,false);assert.equal(h.gd.data[2].visible,true);
  assert.deepEqual(JSON.parse(h.q('report-data').textContent).figure,h.published);
});
test('rapid mode changes serialize Plotly work and end with the requested display',async t=>{
  const h=setup(t);await h.open();
  for(const mode of ['overlay','stack','original']) {h.q('view-mode').value=mode;h.q('view-mode').dispatchEvent(new h.w.Event('change'));}
  await tick();await tick();await tick();
  assert.deepEqual(clone(h.gd.data).map(t=>t.y),h.published.data.map(t=>t.y));
});
test('legacy compact requests explicitly show a static fallback, no fabricated coordinates',async t=>{
  const h=setup(t,true);await h.open();assert.equal(h.calls(),0);assert.equal(h.q('view-mode').disabled,true);
  assert.equal(h.q('graph-fallback').hidden,false);assert(h.q('graph-message').textContent.includes('이전 요청'));
});
test('automatic mobile layout keeps the existing legend-below placement',async t=>{
  const source=new JSDOM(html),node=source.window.document.getElementById('report-data');
  const payload=JSON.parse(node.textContent);payload.figure.layout.meta.size={width:null,ratio:'auto'};
  node.textContent=JSON.stringify(payload);
  const h=setup(t,false,source.serialize());source.window.close();
  Object.defineProperty(h.gd.parentNode,'clientWidth',{get:()=>374});
  await h.open();
  assert.equal(h.gd.layout.width,374);assert(h.gd.layout.height>=600);
  assert.equal(h.gd.layout.legend.orientation,'h');assert(h.gd.layout.legend.y<0);
  assert(h.gd.layout.width-h.gd.layout.margin.l-h.gd.layout.margin.r>=300);
  assert.equal(h.gd.style.marginTop,'36px');
});

test('report stack hides all Y scale marks, overlay restores labels, original display remains stacked',async t=>{
  const h=setup(t);await h.open();
  const data=clone(h.gd.data), annotations=clone(h.gd.layout.annotations);
  for(const mode of ['original','overlay','stack','overlay','original']) {
    await h.change(mode);
    const y=h.gd.layout.yaxis;
    if(mode==='overlay') {assert.equal(y.showticklabels,true);assert.equal(y.ticks,'inside');assert.equal(y.minor.ticklen,4);}
    else {assert.equal(y.showticklabels,false);assert.equal(y.ticks,'');assert.equal(y.ticklen,0);
      assert.equal(y.minor.ticks,'');assert.equal(y.minor.ticklen,0);assert.equal(y.showgrid,false);assert.equal(y.minor.showgrid,false);}
  }
  assert.deepEqual(clone(h.gd.data),data);assert.deepEqual(clone(h.gd.layout.annotations),annotations);
});

test('report overlay restores saved normal display and keeps edited tick intervals',async t=>{
  const source=new JSDOM(html);
  const node=source.window.document.getElementById('report-data'),payload=JSON.parse(node.textContent);
  payload.figure.layout.yaxis={showticklabels:false,ticks:'',ticklen:0,showgrid:false,range:[0,16],dtick:2,
    minor:{ticks:'',ticklen:0,showgrid:false,dtick:0.5}};
  payload.figure.layout.meta.yAxisDisplay={ticks:{exists:true,value:'outside'},ticklen:{exists:true,value:5},
    showgrid:{exists:true,value:true},minorTicks:{exists:true,value:'inside'},minorTicklen:{exists:true,value:3},
    minorShowgrid:{exists:true,value:true}};
  node.textContent=JSON.stringify(payload);
  const h=setup(t,false,source.serialize());source.window.close();await h.open();await h.change('overlay');
  const y=h.gd.layout.yaxis;
  assert.equal(y.showticklabels,true);assert.equal(y.ticks,'outside');assert.equal(y.ticklen,5);
  assert.equal(y.showgrid,true);assert.equal(y.minor.ticks,'inside');assert.equal(y.minor.ticklen,3);
  assert.equal(y.minor.showgrid,true);assert.equal(y.dtick,2);assert.equal(y.minor.dtick,0.5);
  await h.change('stack');assert.equal(h.gd.layout.yaxis.minor.ticks,'');
});
