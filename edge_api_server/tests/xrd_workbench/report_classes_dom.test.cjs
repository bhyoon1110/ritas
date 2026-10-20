// Exercise the actual editor's select/apply/save flow in both shipped code paths.
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const {execFileSync}=require('node:child_process');
const {JSDOM,VirtualConsole}=require('jsdom');
const esbuild=require('esbuild');
const root=path.resolve(__dirname,'../../app/xrd_workbench/web');
const repo=path.resolve(__dirname,'../../..');
const fixture=JSON.parse(execFileSync(process.env.RIST_TEST_PYTHON||'python3',['-c',
  'import json; from tests.xrd_workbench.test_posco_pptx import fixture; p,d=fixture(); d["revision"]=1; print(json.dumps(dict(project=p,document=d)))'],
  {cwd:path.join(repo,'edge_api_server'),env:{...process.env,PYTHONDONTWRITEBYTECODE:'1',
    PYTHONPATH:[repo,path.join(repo,'common'),path.join(repo,'edge_api_server')].join(path.delimiter)},encoding:'utf8'}));
const programs={
  native:esbuild.buildSync({entryPoints:[path.join(root,'report/report.mjs')],bundle:true,write:false,format:'iife',logLevel:'silent'}).outputFiles[0].text,
  chrome49:fs.readFileSync(path.join(root,'report/report.legacy.js'),'utf8'),
};
const clone=v=>JSON.parse(JSON.stringify(v));
const flush=async()=>{for(let i=0;i<12;i++)await new Promise(setImmediate);};
function setup(t,program){
  const errors=[],saves=[];
  const console=new VirtualConsole();console.on('jsdomError',e=>errors.push(e.message));
  const dom=new JSDOM(fs.readFileSync(path.join(root,'report.html'),'utf8'),{
    url:'http://rist.test/report.html?project=sample',runScripts:'outside-only',pretendToBeVisual:true,virtualConsole:console});
  t.after(()=>dom.window.close());
  const w=dom.window,d=w.document;
  let saved=clone(fixture.document);
  const response=value=>({ok:true,status:200,headers:{get:()=>null},json:async()=>clone(value),text:async()=>'<html>preview</html>'});
  w.structuredClone=clone;w.confirm=()=>true;
  w.fetch=async(url,options={})=>{
    if(url==='/api/platform')return response({});
    if(url==='/api/presets')return response([]);
    if(url.endsWith('/draft-preview'))return response({});
    if(url.endsWith('/draft'))return response({revision:0,document:null});
    if(url.endsWith('/checklist'))return response({samples:[],summary:{blockers:0,warnings:0}});
    if(url.includes('/phase-classes?'))return response({samples:fixture.project.samples.map(s=>({
      sample_id:s.id,phases:s.phases.map(p=>({phase:{name:p.name,card:p.card,db:p.db},quantity:null,trace:null}))}))});
    if(url==='/api/reports/sample'){
      if(options.method==='PUT'){
        const body=JSON.parse(options.body);
        saves.push(clone(body.document));
        saved={...body.document,revision:body.expected_revision+1};
        return response(saved);
      }
      return response({...fixture,document:saved});
    }
    throw Error('Unexpected request: '+url);
  };
  w.eval(program);
  return {w,d,errors,saves};
}
for(const [mode,program] of Object.entries(programs)){
  test(`${mode}: major is the bulk default; saved row classes survive until explicit apply, then save`,async t=>{
    const ui=setup(t,program);await flush();
    ui.d.querySelector('[data-tab="classes"]').click();await flush();
    const bulk=()=>ui.d.getElementById('pcRowClass');
    const row=(id,i)=>ui.d.querySelector(`[data-pc-sample-id="${id}"][data-pc-phase-index="${i}"]`);
    assert.equal(bulk().value,'major');
    assert.equal(bulk().selectedOptions[0].textContent,'주요상');
    assert.equal(ui.d.getElementById('classForm').textContent.includes('(고정)'),false);
    assert.equal(row('A',0).value,'major');
    assert.equal(row('A',1).value,'trace');
    assert.equal(row('A',2).value,'presumed');
    assert.equal(row('B',0).value,''); // no implicit scientific classification
    ui.d.getElementById('save').click();await flush();
    assert.deepEqual(ui.saves.at(-1).phase_classes,fixture.document.phase_classes);
    ui.d.querySelector('[data-tab="classes"]').click();await flush();
    ui.d.getElementById('pcRowApply').click();await flush();
    assert.equal(row('B',0).value,'major');
    assert.equal(row('A',1).value,'trace');
    assert.equal(row('A',2).value,'presumed');
    ui.d.getElementById('save').click();await flush();
    const labels=ui.saves.at(-1).phase_classes.labels;
    assert.equal(labels.find(l=>l.sample_id==='B' && l.phase.name==='Phase0').class,'major');
    assert.equal(labels.find(l=>l.sample_id==='A' && l.phase.name==='Phase1').class,'trace');
    assert.equal(labels.find(l=>l.sample_id==='A' && l.phase.name==='Phase2').class,'presumed');
    assert.deepEqual(ui.errors,[]);
  });
}
