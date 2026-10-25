// Exercise shipped native and Chrome49 code, including real page/stack controllers.
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {execFileSync} = require('node:child_process');
const {createHash} = require('node:crypto');
const {JSDOM} = require('jsdom');
const clone = value => JSON.parse(JSON.stringify(value));
const tick = () => new Promise(resolve => setTimeout(resolve,100));
const pages = {};
for(const experiment of ['ftir','raman']) pages[experiment] = execFileSync(
  process.env.RIST_TEST_PYTHON || 'python3', ['-c',
    `from app.path_bootstrap import add_project_package_paths; add_project_package_paths(); from app.${experiment}_web import build_${experiment}_page; print(build_${experiment}_page())`],
  {encoding:'utf8',maxBuffer:8*1024*1024});
const root = path.resolve(__dirname,'../app/static/chrome49');
const manifest = JSON.parse(fs.readFileSync(path.join(root,'manifest.json'),'utf8'));

function figure(experiment, count=2) {
  const data = Array.from({length:count}, (_,i) => ({
    name:'NEW RAW '+i, x:[400,500,600], y:[-3+i,30+i,5+i], customdata:[-3+i,30+i,5+i],
    line:{color:'#123456',width:1,dash:'solid'}, legendgroup:'sample:'+i,
    meta:{rist_sample_parent:true,rist_sample_group:'sample:'+i,rist_raman_stack_offset:0,
      rist_ftir_stack_offset_absorbance:0,rist_ftir_stack_offset_transmittance:0,
      ...(experiment==='ftir'?{ftir_signal_toggle:{absorbance_y:[-3+i,30+i,5+i],transmittance_y:[40+i,50+i,60+i]}}:{})}
  }));
  return {data,layout:{title:{text:'NEW RAW'},xaxis:{range:[700,300]},yaxis:{range:[-5,40]},
    legend:{x:1.02,y:1,orientation:'v'},annotations:[],shapes:[],meta:{ristPeakLabels:[],
      [experiment==='ftir'?'ristFtirStack':'ristRamanStack']:{enabled:false,gap:1.2,unit:33,
        modeUnits:{absorbance:33,transmittance:100},sampleOrder:data.map(t=>t.legendgroup),
        sampleOffsets:Object.fromEntries(data.map(t=>[t.legendgroup,0])),
        samplePositions:Object.fromEntries(data.map(t=>[t.legendgroup,0]))}}}};
}
function sampleTemplate(experiment) {
  return {format:'rist-spectrum-work-template',version:1,experiment:experiment.toUpperCase(),name:'검증 템플릿',
    settings:{sensitivity:0,libraryIds:['custom'],originStyle:true,
      signalMode:experiment==='ftir'?'absorbance':'intensity',stack:{enabled:true,gap:1.8},
      plotSize:{width:1000,ratio:'4:3'},axes:{xaxis:{fixedrange:true},yaxis:{fixedrange:false}},
      legend:{font:{size:14},x:0.02,y:0.98,xanchor:'left',yanchor:'top'},
      peakLabelsVisible:false,peakLegendEntriesVisible:false,sampleStyles:[{color:'#e11d48',width:3,dash:'dash'}],
      conditions:experiment==='ftir'?{detector:'Template detector'}:{excitationWavelength:'532'}}};
}
function setup(t,experiment,mode) {
  const dom = new JSDOM(pages[experiment],{runScripts:'outside-only',url:'http://rist.test/'+experiment,pretendToBeVisual:true});
  const w=dom.window,d=w.document,gd=d.getElementById(experiment==='ftir'?'peak-plot':'raman-plot');
  t.after(()=>{w.dispatchEvent(new w.Event('pagehide'));w.close();});
  Object.defineProperty(gd.parentElement,'clientWidth',{value:1500});
  Object.defineProperty(gd,'clientWidth',{get:()=>parseFloat(gd.style.width)||1500});
  Object.defineProperty(gd,'clientHeight',{value:720});
  const initial=figure(experiment,0); gd.data=initial.data; gd.layout=initial.layout; gd._context={};
  gd.getClientRects=()=>[{width:1000,height:600}]; // JSDOM has no visual layout.
  const handlers={},downloads=[],requests=[]; let fail=false, sampleCount=2, controller, savedEnvironment, rawMetadata=[];
  const emit=(name,payload)=>{for(const fn of handlers[name]||[])fn(payload);};
  gd.on=(name,fn)=>{(handlers[name] ||= []).push(fn);return gd;};
  function set(object,key,value) { const keys=key.split('.');for(const part of keys.slice(0,-1)) object=object[part] ||= {};object[keys.at(-1)]=value; }
  function full() { gd._fullLayout={...clone(gd.layout),_size:{w:900,h:500},xaxis:{...gd.layout.xaxis,_length:900},yaxis:{...gd.layout.yaxis,_length:500}}; }
  full();
  w.Plotly={
    react:async(_,data,layout)=>{gd.data=data;gd.layout=layout;full();emit('plotly_afterplot');},
    relayout:async(_,patch)=>{for(const [key,value] of Object.entries(patch))set(gd.layout,key,clone(value));full();emit('plotly_relayout',patch);emit('plotly_afterplot');},
    restyle:async(_,patch,indexes)=>{for(const [pos,index] of indexes.entries())for(const [key,value] of Object.entries(patch))set(gd.data[index],key,clone(Array.isArray(value)?value[pos]:value));full();},
    update:async(_,data,layout,indexes)=>{await w.Plotly.restyle(gd,data,indexes);await w.Plotly.relayout(gd,layout);},
    Plots:{resize:async()=>{}}
  };
  w.URL.createObjectURL=blob=>{downloads.push(blob);return 'blob:test';}; w.URL.revokeObjectURL=()=>{};
  d.addEventListener('click',ev=>{if(ev.target.tagName==='A'&&ev.target.download)ev.preventDefault();});
  w.confirm=()=>true;
  if(mode==='chrome49') w.eval(fs.readFileSync(path.join(root,manifest.runtime),'utf8'));
  w.fetch=async(url,options={})=>{
    if(url==='/api/v1/auth/me')return {ok:true,json:async()=>({userId:'template-test',roles:[]})};
    if(url.endsWith('/assignment-libraries'))return {ok:true,json:async()=>({libraries:[
      {id:'default',name:'Default',valid:true,defaultSelected:true,assignmentCount:1},
      {id:'custom',name:'Custom',valid:true,assignmentCount:2}],deleteEnabled:false})};
    if(url.endsWith('/analyze')) {
      requests.push({sensitivity:options.body.get('sensitivity'),libraries:options.body.getAll('assignment_library_ids')});
      if(fail)return {ok:false,json:async()=>({message:'test failure'})};
      return {ok:true,json:async()=>({figure:figure(experiment,sampleCount),
        settings:{sensitivity:Number(options.body.get('sensitivity'))},
        samples:Array.from({length:sampleCount},(_,i)=>({label:'NEW RAW '+i,fileName:'new-'+i,peakCount:0,metadata:rawMetadata[i]||{}}))})};
    }
    return {ok:false,status:401,json:async()=>({})};
  };
  const sources=[...pages[experiment].matchAll(/<script\b[^>]*>([\s\S]*?)<\/script>/g)].map(m=>m[1]);
  function run(token) {
    let source=sources.find(s=>s.includes(token));assert(source,token);
    if(mode==='chrome49') {
      const name=manifest.scripts[createHash('sha256').update(source.trim()).digest('hex')];
      assert(name,'rebuild assets: '+token);source=fs.readFileSync(path.join(root,name),'utf8');
    }
    w.eval(source);
  }
  run('styleLayout: styleLayout');run('var axisLocksEnabled');run('var ratios =');
  if(experiment==='ftir')run('function tracesWithToggle');
  run(experiment==='ftir'?'gd._ristFtirStackInstalled':'gd._ristRamanStackInstalled');
  run('gd._ristPeakSensitivityInstalled');
  run('rist-spectrum-work-template');
  run('window.RistSpectrumWorkflow =');
  const install=w.RistWorkTemplate.install;
  w.RistWorkTemplate.install=options=>{savedEnvironment=options;controller=install(options);return controller;};
  run('function currentWorkspaceState');
  function upload(input,file) { Object.defineProperty(input,'files',{configurable:true,value:[file]});input.dispatchEvent(new w.Event('change',{bubbles:true})); }
  return {w,d,gd,run,requests,downloads,template:()=>controller,options:()=>savedEnvironment,
    fail:()=>{fail=true;},setCount:value=>{sampleCount=value;},
    setMetadata:value=>{rawMetadata=value;},
    addRaw:async()=>{upload(d.getElementById(experiment+'-file-input'),new w.File(['raw'],experiment==='ftir'?'new.dpt':'new.txt'));await tick();await tick();},
    read:blob=>new Promise((resolve,reject)=>{const reader=new w.FileReader();reader.onload=()=>resolve(reader.result);reader.onerror=reject;reader.readAsText(blob);}),
    uploadTemplate:async(value,name='template.json')=>{upload(d.querySelector('[data-template-file]'),new w.File([value],name));await tick();await tick();}
  };
}

for(const mode of ['native','chrome49']) for(const experiment of ['ftir','raman']) {
  const label=experiment+'/'+mode;
  test(label+': custom width/height and new portrait ratios round-trip through templates and fresh RAW',async t=>{
    const h=setup(t,experiment,mode);await tick();await h.addRaw();
    for(const size of [{width:1100,height:820,ratio:'custom'},{width:900,ratio:'3:4'},{width:1200,ratio:'21:9'}]) {
      const template=sampleTemplate(experiment);template.settings.plotSize=size;
      await h.template().load(template);await tick();
      assert.deepEqual(clone(h.gd.layout.meta.ristPlotSize),size);
      h.d.querySelector('[data-template-save]').click();
      const saved=JSON.parse(await h.read(h.downloads.at(-1)));
      assert.deepEqual(saved.settings.plotSize,size);
      await h.uploadTemplate(JSON.stringify(saved));await h.addRaw();
      assert.deepEqual(clone(h.gd.layout.meta.ristPlotSize),size);
    }
    for(const size of [{width:1100,ratio:'custom'}, {width:null,height:820,ratio:'custom'}, {width:1100,height:2401,ratio:'custom'}]) {
      const template=sampleTemplate(experiment);template.settings.plotSize=size;
      assert.throws(()=>h.template().load(template));
    }
  });
  test(label+': zero and expanded stack gaps survive template save/load and fresh RAW',async t=>{
    const h=setup(t,experiment,mode);await tick();await h.addRaw();
    const key=experiment==='ftir'?'ristFtirStack':'ristRamanStack';
    for(const gap of [0,0.05,2.2,6,0]) {
      const value=sampleTemplate(experiment);value.settings.stack.gap=gap;
      await h.template().load(value);await tick();
      assert.equal(h.gd.layout.meta[key].gap,gap);
      assert.equal(h.d.querySelector('.rist-'+experiment+'-stack-gap').value,String(Math.round(gap*100)));
      assert.deepEqual(clone(h.gd.layout.meta[key].legendOrder),['sample:0','sample:1']);
      assert.deepEqual(clone(h.gd.data[0].y),[-3,30,5].map(y=>y+33*gap));
      assert.deepEqual(clone(h.gd.data[1].y),[-2,31,6]);
      h.d.querySelector('[data-template-save]').click();
      const saved=JSON.parse(await h.read(h.downloads.at(-1)));
      assert.equal(saved.settings.stack.gap,gap);
      await h.uploadTemplate(JSON.stringify(saved));await h.addRaw();
      assert.equal(h.gd.layout.meta[key].gap,gap);
      assert.deepEqual(clone(h.gd.data[0].y),[-3,30,5].map(y=>y+33*gap));
      assert.deepEqual(clone(h.gd.data[1].y),[-2,31,6]);
      assert.deepEqual(clone(h.gd.data[1].customdata),[-2,31,6]);
    }
    const previous=clone(h.template().state());
    for(const gap of [-0.05,6.05,null,'0']) {
      const invalid=sampleTemplate(experiment);invalid.settings.stack.gap=gap;
      assert.throws(()=>h.template().load(invalid),/숫자 설정/);
      assert.deepEqual(clone(h.template().state()),previous);
    }
  });
  test(label+': template controls use the shared band styles without changing ready/busy states',async t=>{
    const h=setup(t,experiment,mode);await tick();
    const band=h.d.querySelector('.rist-work-template-band');
    assert(band.previousElementSibling.classList.contains(experiment+'-library-band'));
    assert.equal(band.getAttribute('aria-label'),'작업 템플릿');
    assert(!band.hasAttribute('style'));assert.equal(band.querySelectorAll('[style]').length,0);
    assert.equal(band.querySelectorAll('.rist-work-template-button').length,3);
    assert.equal(band.querySelectorAll('.rist-work-template-options input[type=checkbox]').length,2);
    assert(band.querySelector('[data-template-file]').hidden);
    assert.equal(band.querySelector('[data-template-status]').getAttribute('aria-live'),'polite');
    assert(!band.querySelector('[data-template-load]').disabled);
    assert(!band.querySelector('[data-template-save]').disabled);
    assert(band.querySelector('[data-template-clear]').disabled);
    h.template().setBusy(true);
    assert([...band.querySelectorAll('button,input')].every(n=>n.disabled));
    h.template().setBusy(false);
    assert(!band.querySelector('[data-template-load]').disabled);
    assert(band.querySelector('[data-template-clear]').disabled);
  });
  if(experiment==='raman')test(label+': common raw metadata is not repeated per sample in condition details',async t=>{
    const h=setup(t,experiment,mode);await tick();
    const metadata={'Excitation Wavelength':'532 nm','Grating':'1800 g/mm','Exposure Time':'3 s','constructor':'Raw export field'};
    h.setMetadata([metadata,metadata]);await h.addRaw();
    assert.equal(h.d.querySelector('[data-report-field="excitationWavelength"]').value,'532 nm');
    assert.equal(h.d.querySelector('[data-report-field="conditionDetail"]').value,'Exposure Time: 3 s\nconstructor: Raw export field');
    for(const input of h.d.querySelectorAll('[data-sample-condition-field="conditionDetail"]'))assert.equal(input.value,'Exposure Time: 3 s\nconstructor: Raw export field');
  });
  if(experiment==='raman')test(label+': different raw metadata values retain their sample attribution',async t=>{
    const h=setup(t,experiment,mode);await tick();
    h.setMetadata([{'Exposure Time':'3 s'},{'Exposure Time':'5 s'}]);await h.addRaw();
    assert.equal(h.d.querySelector('[data-report-field="conditionDetail"]').value,'NEW RAW 0 - Exposure Time: 3 s\nNEW RAW 1 - Exposure Time: 5 s');
  });
  test(label+': file round trip excludes RAW/evidence/identities; optional ranges and empty title survive',async t=>{
    const h=setup(t,experiment,mode);await tick();await h.addRaw();
    h.gd.layout.title.text='OLD SAMPLE SECRET';
    h.gd.layout.meta.ristPeaksVisible=false;
    h.gd.layout.meta.ristPeakMarkersVisible=false;h.gd.layout.meta.ristPeakMarkerSize=14;
    h.gd.layout.meta.ristLegendVisible=true;h.gd.layout.legend.font={size:22,weight:400};
    h.gd.layout.shapes=[{name:'old box',x0:10,x1:20}];
    if(experiment==='raman')h.d.querySelector('[data-report-field="conditionDetail"]').value='OLD SAMPLE HEADER '.repeat(500);
    h.d.querySelector('[data-transfer-field="requestNumber"]').value='OLD-REQUEST';
    h.d.querySelector('[data-template-save]').click();assert.equal(h.downloads.length,1);
    const text=await h.read(h.downloads[0]),value=h.w.RistWorkTemplate.parse(text,experiment.toUpperCase());
    for(const word of ['OLD SAMPLE','OLD-REQUEST','old box','NEW RAW','customdata','transmittance_y','conditionDetail'])assert(!text.includes(word),word);
    assert(!value.settings.axes.xaxis.range);
    assert.deepEqual(clone(value.settings.sampleStyles),[{color:'#123456',width:1,dash:'solid'},{color:'#123456',width:1,dash:'solid'}]);
    h.gd.data[0].line.width=3.5;h.gd.data[1].line.width=0.5;
    h.gd.layout.title.text='';h.d.querySelector('[data-template-title]').checked=true;h.d.querySelector('[data-template-ranges]').checked=true;
    h.d.querySelector('[data-template-save]').click();const next=JSON.parse(await h.read(h.downloads[1]));
    assert.equal(next.settings.title,'');assert.deepEqual(next.settings.axes.xaxis.range,[700,300]);
    assert.equal(next.settings.peaksVisible,false);
    assert.equal(next.settings.peakMarkersVisible,false);assert.equal(next.settings.peakMarkerSize,14);
    assert.equal(next.settings.legendVisible,true);assert.deepEqual(next.settings.legend.font,{size:22,weight:400});
    assert.deepEqual(next.settings.sampleStyles.map(s=>s.width),[3.5,0.5]);
  });
  test(label+': load before RAW, then recompute with chosen sensitivity/libraries and new-scale stack',async t=>{
    const h=setup(t,experiment,mode);await tick();
    await h.uploadTemplate(JSON.stringify(sampleTemplate(experiment)));
    assert.equal(h.requests.length,0);assert(h.template().state());
    await h.addRaw();assert.deepEqual(h.requests[0],{sensitivity:'0',libraries:['custom']});
    assert.equal(h.gd.data[0].name,'NEW RAW 0');assert.equal(h.gd.data[0].line.color,'#e11d48');
    assert.equal(h.gd.data[0].line.width,3);assert.equal(h.gd.data[0].line.dash,'dash');
    assert.equal(h.gd.data[1].line.color,'#123456','extra sample keeps original style');
    assert.deepEqual(clone(h.gd.data[0].customdata),[-3,30,5]);
    assert.deepEqual(clone(h.gd.data[0].y),[-3,30,5].map(y=>y+33*1.8));
    assert.deepEqual(clone(h.gd.data[1].y),[-2,31,6]);
    assert.equal(h.gd.layout.xaxis.fixedrange,true);
    assert.equal(h.gd.layout.meta.ristPlotSize.ratio,'4:3');assert.equal(h.gd.layout.width,1000);
    assert.equal(h.gd.layout.legend.x,0.02);
    assert.equal(h.gd.layout.meta.ristPeakLabelsVisible,false);
    assert.equal(h.gd.layout.meta.ristPeaksVisible,true,'old templates default to visible');
    assert.equal(h.gd.layout.meta.ristPeakMarkersVisible,true);assert.equal(h.gd.layout.meta.ristPeakMarkerSize,null);
    assert.equal(h.gd.layout.showlegend,false,'old templates default to hidden sample legend');
    assert.equal(h.d.querySelector('.rist-peak-sensitivity-number').value,'0');
    assert(h.gd.layout.yaxis.range[1]>90,'new RAW Y range, not previous dataset range');
    assert.equal(h.d.querySelector('[data-report-field="'+(experiment==='ftir'?'detector':'excitationWavelength')+'"]').value,
      experiment==='ftir'?'Template detector':'532');
    h.d.querySelector('[data-template-clear]').click();assert.equal(h.template().state(),null);
    assert.equal(h.gd.data[0].line.color,'#e11d48','detach keeps current plot');
  });
  test(label+': invalid file/version/project/values/missing library never changes current settings',async t=>{
    const h=setup(t,experiment,mode);await tick();
    const good=sampleTemplate(experiment),before=clone(h.options().environment());
    const invalid=['{',JSON.stringify({...good,version:2}),JSON.stringify({...good,experiment:'XRD'}),
      JSON.stringify({...good,settings:{...good.settings,sensitivity:101}}),
      JSON.stringify({...good,settings:{...good.settings,peakMarkerSize:25}}),
      JSON.stringify({...good,settings:{...good.settings,peakMarkerSize:4.5}}),
      JSON.stringify({...good,settings:{...good.settings,peakMarkersVisible:'false'}}),
      JSON.stringify({...good,settings:{...good.settings,libraryIds:['missing']}}),
      '{"__proto__":{"polluted":true}}',' '.repeat(256*1024+1)];
    for(const raw of invalid) {
      await h.uploadTemplate(raw);
      assert.equal(h.template().state(),null);assert.deepEqual(clone(h.options().environment()),before);
      assert(h.d.querySelector('[data-template-status]').textContent.length>0);
    }
    assert.equal({}.polluted,undefined);
  });
  test(label+': cancel and server failure preserve current edited graph and settings; controls recover',async t=>{
    const h=setup(t,experiment,mode);await tick();await h.addRaw();
    h.gd.data[0].name='CURRENT EDIT';h.gd.layout.annotations=[{text:'manual note',x:1,y:2}];
    const data=clone(h.gd.data),env=clone(h.options().environment());
    h.w.confirm=()=>false;assert.equal(await h.template().load(sampleTemplate(experiment)),false);
    assert.deepEqual(clone(h.gd.data),data);assert.equal(h.template().state(),null);
    h.w.confirm=()=>true;h.fail();
    await assert.rejects(h.template().load(sampleTemplate(experiment)),/분석에 실패/);
    assert.deepEqual(clone(h.options().environment()),env);assert.equal(h.template().state(),null);
    assert.equal(h.gd.data[0].name,'CURRENT EDIT');assert.equal(h.gd.layout.annotations[0].text,'manual note');
    assert.equal(h.d.querySelector('[data-template-load]').disabled,false);
  });
  test(label+': import onto existing RAW clears old evidence, respects saved ranges and keeps request identity',async t=>{
    const h=setup(t,experiment,mode);await tick();await h.addRaw();
    h.gd.layout.shapes=[{name:'OLD EVIDENCE'}];h.gd.data[0].name='OLD CUSTOM NAME';
    const request=h.d.querySelector('[data-transfer-field="requestNumber"]');request.value='KEEP-REQUEST';
    const value=sampleTemplate(experiment);value.settings.axes.xaxis.range=[650,450];value.settings.axes.yaxis.range=[-20,200];
    value.settings.peaksVisible=false;
    value.settings.peakMarkersVisible=false;value.settings.peakMarkerSize=18;
    value.settings.legendVisible=true;value.settings.legend.font={size:20,weight:400};
    h.gd.layout.meta.ristPlotSize={width:1200,ratio:'16:9'};
    value.settings.title='';h.setCount(3);
    assert.equal(await h.template().load(value),true);await tick();
    assert.equal(request.value,'KEEP-REQUEST');assert.equal(h.gd.data.length,3);
    assert.equal(h.gd.layout.meta.ristPeaksVisible,false);
    assert.equal(h.gd.layout.meta.ristPeakMarkersVisible,false);assert.equal(h.gd.layout.meta.ristPeakMarkerSize,18);
    assert.equal(h.gd.layout.showlegend,true);assert.deepEqual(clone(h.gd.layout.legend.font),{size:20,weight:400});
    assert.equal(h.gd.data[0].name,'NEW RAW 0');assert.equal(h.gd.layout.shapes.length,0);
    assert.equal(h.gd.layout.title.text,'');assert.deepEqual(clone(h.gd.layout.xaxis.range),[650,450]);
    assert.equal(h.gd.layout.meta.ristPlotSize.width,1000,'loaded template wins over previously selected plot size');
    assert.deepEqual(clone(h.gd.layout.yaxis.range),[-20,200]);
    assert.equal(h.d.getElementById('sw-save-graph').disabled,false,'template completion unlocks workflow');
    assert.equal(h.d.getElementById(experiment+'-clear').disabled,false);
    // JSDOM intentionally has no IndexedDB. Full reset must preserve the template
    // when persisted graph/current state cannot be cleared (real DB covered in browser QA).
    const beforeReset=clone(h.template().state());
    h.d.getElementById(experiment+'-clear').click();await tick();
    assert.deepEqual(clone(h.template().state()),beforeReset);
    assert.match(h.d.querySelector('.sw-status').textContent,/초기화하지 못했습니다/);
    h.d.querySelector('[data-template-clear]').click();assert.equal(h.template().state(),null);
  });
  test(label+': active selection restores, rejects missing libraries, and mismatched file cannot change analysis',async t=>{
    const h=setup(t,experiment,mode);await tick();
    const value=sampleTemplate(experiment);
    value.settings.peaksVisible=true;
    value.settings.legendVisible=false;
    value.settings.peakMarkersVisible=true;value.settings.peakMarkerSize=null;
    h.template().restore(value);assert.deepEqual(clone(h.template().state()),value);
    assert.equal(h.d.querySelector('[data-template-name]').value,value.name);
    h.template().restore({...value,settings:{...value.settings,libraryIds:['removed-library']}});
    assert.equal(h.template().state(),null);
    assert.match(h.d.querySelector('[data-template-status]').textContent,/자동 적용을 해제/);
    h.template().restore(value);
    await h.uploadTemplate(JSON.stringify({...value,experiment:'TEM'}));
    assert.deepEqual(clone(h.template().state()),value);
  });
  test(label+': a rendering failure rolls back the graph, manual evidence and report context',async t=>{
    const h=setup(t,experiment,mode);await tick();await h.addRaw();
    h.gd.data[0].name='BEFORE FAILURE';h.gd.layout.annotations=[{text:'KEEP MANUAL NOTE',x:500,y:12}];
    const savedEnvironment=clone(h.options().environment()),savedMeta=clone(h.options().snapshot().metadata);
    const relayout=h.w.Plotly.relayout;let injected=false;
    h.w.Plotly.relayout=async(gd,patch)=>{
      if(patch['xaxis.fixedrange']===true&&!injected){injected=true;throw Error('synthetic render failure');}
      return relayout(gd,patch);
    };
    await assert.rejects(h.template().load(sampleTemplate(experiment)),/분석에 실패/);
    assert(injected);assert.equal(h.gd.data[0].name,'BEFORE FAILURE');
    assert.equal(h.gd.layout.annotations[0].text,'KEEP MANUAL NOTE');
    assert.deepEqual(clone(h.options().environment()),savedEnvironment);
    assert.deepEqual(clone(h.options().snapshot().metadata),savedMeta);
    assert.equal(h.template().state(),null);
  });
  if(experiment==='ftir')test(label+': transmittance/absorbance and stack templates do not cumulatively alter RAW',async t=>{
    const h=setup(t,experiment,mode);await tick();await h.addRaw();
    const value=sampleTemplate(experiment);value.settings.signalMode='transmittance';
    await h.template().load(value);await tick();
    assert.equal(h.gd._ristFtirSignalMode,'transmittance');
    assert.deepEqual(clone(h.gd.data[0].y),[40,50,60].map(y=>y+180));
    assert.deepEqual(clone(h.gd.data[1].y),[41,51,61]);
    value.settings.stack.enabled=false;value.settings.signalMode='absorbance';
    await h.template().load(value);await tick();
    assert.deepEqual(clone(h.gd.data[1].y),[-2,31,6]);assert.equal(h.gd._ristFtirSignalMode,'absorbance');
  });
}
