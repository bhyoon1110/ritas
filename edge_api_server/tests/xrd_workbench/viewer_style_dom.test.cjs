// Mounted controls and export plumbing. Real Plotly rendering is verified separately in Chrome.
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {JSDOM} = require('jsdom');
const esbuild = require('esbuild');
const babel = require('@babel/core');
const root = path.resolve(__dirname,'../../app/xrd_workbench/web');
const native = esbuild.buildSync({entryPoints:[path.join(root,'viewer/viewer.mjs')],bundle:true,write:false,format:'iife',logLevel:'silent'}).outputFiles[0].text;
const legacy = babel.transformSync(native,{configFile:false,babelrc:false,presets:[[require('@babel/preset-env'),{targets:{chrome:'49'},forceAllTransforms:true,modules:false}]]}).code;
const flush = async()=>{for(let i=0;i<8;i++)await new Promise(setImmediate);};
const fixture = {
  id:'style-test',samples:[{id:'a',name:'QA sample',measurement:{x:[10,20,30],y:[1,9,2],axis_kind:'two_theta',axis_unit:'deg',intensity_unit:'cps'},
    phases:[{id:'p1',name:'Phase A',card:'1',formula:'Fe O',db:'QA'}],
    assignments:[{no:1,x:20,x_raw:'20',page:1,matches:[{phase:'Phase A',hkl:'100'}]}],
    observed_peaks:[{no:1,x:20,x_raw:'20',height_raw:'9',page:1}],
    reference_peaks:[{phase:'Phase A',x:20,intensity:100,intensity_raw:'100',hkl_raw:'100',page:1}],sources:[]}],sources:[],warnings:[],
};
function setup(t, program, saved={}, project=fixture) {
  const dom=new JSDOM(fs.readFileSync(path.join(root,'index.html'),'utf8'),{url:'http://rist.test/viewer.html',runScripts:'outside-only',pretendToBeVisual:true});
  t.after(()=>dom.window.close());
  const w=dom.window,d=w.document, renders=[],exports=[],downloads=[],events={};
  w.XRD_EMBEDDED={project,state:{projectId:project.id,...saved},snapshotId:'qa'};
  w.Plotly={newPlot:async node=>{node.on=(key,callback)=>{events[key]=callback;};},react:async(node,data,layout)=>{renders.push(JSON.parse(JSON.stringify({data,layout})));},
    toImage:async figure=>{exports.push(JSON.parse(JSON.stringify(figure)));return 'data:image/png;base64,QA';}};
  w.fetch=async()=>({blob:async()=>new w.Blob(['QA'])});
  w.URL.createObjectURL=()=> 'blob:qa';w.URL.revokeObjectURL=()=>{};
  w.HTMLAnchorElement.prototype.click=function(){downloads.push(this.download);};
  w.eval(program);
  return {w,d,renders,exports,downloads,events,setSize:async size=>{d.getElementById('markerSize').value=String(size);d.getElementById('markerSize').dispatchEvent(new w.Event('input'));await flush();}};
}
for(const [mode, program] of Object.entries({native,chrome49:legacy})) {
  test(`${mode}: phase toggle changes upper markers only and explains missing data, hiding and zoom`,async t=>{
    const ui=setup(t,program);await flush();
    const hint=()=>ui.d.getElementById('phaseMarkerHint').textContent;
    const phaseCount=()=>ui.renders.at(-1).data.filter(t=>t.meta.role==='marker').reduce((n,t)=>n+t.x.length,0);
    const bars=()=>JSON.stringify(ui.renders.at(-1).data.filter(t=>['peakbar','peakbar-tip'].includes(t.meta.role)));
    const reference=bars();assert.match(hint(),/하단 기준 피크 막대와는 별도/);
    for(const origin of [false,true]){
      if(origin){ui.d.getElementById('originStyle').click();await flush();}
      ui.d.getElementById('markers').click();await flush();
      assert.equal(phaseCount(),1);assert.match(hint(),/마커 1개 표시/);assert.equal(bars(),reference);
      ui.events.plotly_relayout({'xaxis.range':[21,29]});await flush();
      assert.match(hint(),/확대 범위 밖/);
      ui.events.plotly_relayout({'xaxis.range':[10,30]});await flush();
      assert.match(hint(),/마커 1개 표시/);
      ui.d.getElementById('markers').click();await flush();
      assert.equal(phaseCount(),0);assert.equal(bars(),reference);
    }
    const missing=JSON.parse(JSON.stringify(fixture));missing.samples[0].observed_peaks=[];
    const absent=setup(t,program,{markers:true},missing);await flush();
    assert.match(absent.d.getElementById('phaseMarkerHint').textContent,/관측 피크표를 읽지 못했거나/);
    assert.equal(absent.d.getElementById('markers').disabled,false);
    const hidden=setup(t,program,{markers:true,hiddenPhases:['p1']});await flush();
    assert.match(hidden.d.getElementById('phaseMarkerHint').textContent,/모두 숨김/);
  });
  test(`${mode}: default reference tips visibly receive size changes even with phase markers off`,async t=>{
    const ui=setup(t,program);await flush();
    assert.equal(ui.d.getElementById('markers').checked,false);
    assert.equal(ui.d.getElementById('markerSize').disabled,false);
    for(const size of [3,20]){
      await ui.setSize(size);
      assert.equal(ui.renders.at(-1).data.find(t=>t.meta.role==='peakbar-tip').marker.size,size);
      assert.equal(ui.d.getElementById('markerSizeValue').textContent,String(size));
    }
    ui.d.getElementById('peakbar').click();await flush();
    assert.equal(ui.d.getElementById('markerSize').disabled,true);
    assert.match(ui.d.getElementById('markerSizeHint').textContent,/표시된 마커가 없습니다/);
    ui.d.getElementById('markers').click();await flush();
    assert.equal(ui.d.getElementById('markerSize').disabled,false);
    assert.equal(ui.renders.at(-1).data.find(t=>t.meta.role==='marker').marker.size,20);
  });
  test(`${mode}: live Origin toggle and both export buttons share state and persist it`,async t=>{
    const ui=setup(t,program,{phaseColors:{p1:'#12ab34'}});await flush();
    assert.ok(ui.d.querySelector('.graphActions #originStyle'));
    assert.equal(ui.d.querySelectorAll('#originStyle').length,1);
    await ui.setSize(17);
    for(const origin of [true,false]){
      ui.d.getElementById('originStyle').click();await flush();
      const figure=ui.renders.at(-1);
      assert.equal(figure.layout.xaxis2.ticks,origin?'inside':'outside');
      ui.d.getElementById(origin?'quickImageExport':'imageExport').click();await flush();
      const output=ui.exports.at(-1);
      assert.equal(output.layout.xaxis2.ticks,figure.layout.xaxis2.ticks);
      assert.equal(output.data.find(t=>t.meta.role==='peakbar-tip').marker.size,17);
      for(const rendered of [figure,output]){
        assert.equal(rendered.data.find(t=>t.meta.role==='peakbar').line.color,'#12ab34');
        assert.equal(rendered.data.find(t=>t.meta.role==='peakbar-tip').marker.color,'#12ab34');
        assert.equal(rendered.layout.annotations[0].font.color,'#12ab34');
      }
      assert.equal(ui.downloads.at(-1).includes('-Origin'),origin);
      const stored=JSON.parse(ui.w.localStorage.getItem(ui.w.localStorage.key(0)));
      assert.equal(stored.originStyle,origin);assert.equal(stored.markerSize,17);
    }
  });
  test(`${mode}: offline/named state restores Origin view and selected marker size`,async t=>{
    const ui=setup(t,program,{originStyle:true,markerSize:12,phaseColors:{p1:'#bd4c87'}});await flush();
    assert.equal(ui.d.getElementById('originStyle').checked,true);
    assert.equal(ui.d.getElementById('markerSize').value,'12');
    assert.equal(ui.renders.at(-1).layout.xaxis2.ticks,'inside');
    assert.equal(ui.renders.at(-1).data.find(t=>t.meta.role==='peakbar-tip').marker.color,'#bd4c87');
    assert.equal(ui.renders.at(-1).layout.annotations[0].font.color,'#bd4c87');
  });
  test(`${mode}: both Plotly range event formats survive style and size changes`,async t=>{
    const ui=setup(t,program);await flush();
    for(const event of [{'xaxis.range':[12,25],'yaxis.range':[-1,12]},
      {'xaxis2.range[0]':12,'xaxis2.range[1]':25,'yaxis.range[0]':-1,'yaxis.range[1]':12}]){
      ui.events.plotly_relayout(event);
      ui.d.getElementById('originStyle').click();await flush();await ui.setSize(15);
      assert.deepEqual(ui.renders.at(-1).layout.xaxis.range,[12,25]);
      assert.deepEqual(ui.renders.at(-1).layout.yaxis.range,[-1,12]);
    }
  });
}
