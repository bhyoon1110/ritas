// Actual page programs with mocked HTTP/Plotly: not Windows/Supermium binary QA.
const {test} = require('node:test');
const assert = require('node:assert/strict');
const {execFileSync} = require('node:child_process');
const {createHash} = require('node:crypto');
const fs = require('node:fs');
const path = require('node:path');
const {JSDOM} = require('jsdom');
const pages = JSON.parse(execFileSync(process.env.RIST_TEST_PYTHON || 'python3', ['-c', [
  'import json',
  'from app.ftir_web import build_ftir_page',
  'from app.raman_web import build_raman_page',
  'from app.xrd_web import build_xrd_page',
  'from app.ahn_web import build_ahn_page',
  'print(json.dumps(dict(ftir=build_ftir_page(), raman=build_raman_page(), xrd=build_xrd_page(), ahn=build_ahn_page())))'
].join(';')], {encoding:'utf8', maxBuffer:32*1024*1024}));
const assetRoot = path.resolve(__dirname, '../app/static/chrome49');
const manifest = JSON.parse(fs.readFileSync(path.join(assetRoot,'manifest.json'),'utf8'));
const tick = () => new Promise(resolve=>setTimeout(resolve,15));
const response = (body, status=200) => ({ok:status<400, status, json:async()=>body, text:async()=>JSON.stringify(body)});

async function setup(t, prefix, mode) {
  const dom = new JSDOM(pages[prefix], {runScripts:'outside-only',pretendToBeVisual:true,url:'http://localhost/'+(prefix==='ahn'?'tem':prefix)});
  t.after(()=>dom.window.close());
  const w=dom.window, d=w.document;
  if(mode==='chrome49') w.eval(fs.readFileSync(path.join(assetRoot,manifest.runtime),'utf8'));
  const gd=d.getElementById('peak-plot') || d.getElementById('raman-plot');
  if(gd) {
    gd.data=[];gd.layout={xaxis:{},yaxis:{},meta:{},annotations:[]};gd._context={};gd.on=()=>gd;
    w.Plotly={react:async(_,data,layout)=>{gd.data=data;gd.layout=layout;},relayout:async()=>{},Plots:{resize(){}}};
  }
  const items=[
    {requestNumber:'REQ-A',experimentCode:'LIMS-A',sampleName:'SAMPLE-A',requestResultNo:'RESULT-A',equipmentCode:'DEVICE-A'},
    {requestNumber:'REQ-B',testMethodCode:'LIMS-B',sampleName:'SAMPLE-B',requestResultNo:'RESULT-B',equipmentCode:'DEVICE-B'}
  ];
  const sent=[];let lookupStatus=200;
  w.fetch=async(url,options={})=>{
    if(url.startsWith('/api/v1/requests?')) return response({items,message:'lookup unavailable'},lookupStatus);
    if(url.endsWith('/send')){const body=JSON.parse(options.body);sent.push(body);return response(body);}
    return response(url.includes('assignment-libraries')?{libraries:[]}:{roles:[]});
  };
  let script=[...pages[prefix].matchAll(/<script\b[^>]*>([\s\S]*?)<\/script>/g)]
    .map(m=>m[1]).find(s=>s.includes('function selectedRequestItem()'));
  assert(script);
  if (mode==='chrome49') {
    const key=createHash('sha256').update(script.trim()).digest('hex');
    assert(manifest.scripts[key],`${prefix}: rebuild compatibility assets`);
    script=fs.readFileSync(path.join(assetRoot,manifest.scripts[key]),'utf8');
  }
  const hook=/function reportTransferValue\(field\)\s*\{/;
  assert(hook.test(script));
  w.eval(script.replace(hook, `
    window.__requestEntry = {
      state:reportTransferFormState, selected:selectedRequestItem,
      setReport:function(){lastReportJob={jobId:'test-report',status:'completed'};updateReportSendAvailability();},
      workspace:typeof currentWorkspaceState === 'function' ? currentWorkspaceState : null
    };
    function reportTransferValue(field) {`));
  await tick();
  const attr=['ftir','raman'].includes(prefix)?'data-transfer-field':`data-${prefix}-transfer-field`;
  const input=name=>d.querySelector(`[${attr}="${name}"]`);
  const select=d.getElementById(`${prefix}-request-select`);
  const detail=d.getElementById(`${prefix}-request-detail`);
  function type(name,value,event='input'){input(name).value=value;input(name).dispatchEvent(new w.Event(event,{bubbles:true}));}
  async function lookup(){d.getElementById(`${prefix}-request-load`).click();await tick();}
  function choose(value){select.value=value;select.dispatchEvent(new w.Event('change'));}
  function fill(){type('requestNumber','  MANUAL-001  ');type('limsExperimentCode',' MANUAL-CODE ');type('equipmentCode','DEVICE');type('operatorId','operator');}
  async function send(){d.getElementById(`${prefix}-report-send`).click();await tick();}
  return {w,d,api:w.__requestEntry,input,type,select,detail,items,sent,lookup,choose,fill,send,failLookup:()=>{lookupStatus=503;}};
}

for(const mode of ['native','chrome49']) for(const prefix of Object.keys(pages)) {
  test(`${mode} ${prefix}: manual request and LIMS code are editable and sent without lookup`,async t=>{
    const h=await setup(t,prefix,mode);
    assert.equal(h.input('requestNumber').readOnly,false);
    assert.equal(h.input('limsExperimentCode').readOnly,false);
    assert.match(h.input('requestNumber').placeholder,/직접 입력/);
    h.fill();h.api.setReport();await h.send();
    assert.deepEqual(h.sent,[{requestNumber:'MANUAL-001',experimentCode:'MANUAL-CODE',equipmentCode:'DEVICE',operatorId:'operator'}]);
    if(h.api.workspace) assert.equal(h.api.workspace().reportTransfer.requestNumber,'MANUAL-001');
  });
  test(`${mode} ${prefix}: lookup and empty selection do not overwrite manual input or select item zero`,async t=>{
    const h=await setup(t,prefix,mode);h.fill();await h.lookup();
    assert.equal(h.select.value,'');assert.equal(h.api.selected(),null);
    h.choose('');assert.equal(h.api.state().requestNumber,'MANUAL-001');
    assert.equal(h.api.state().limsExperimentCode,'MANUAL-CODE');
    assert.equal(h.api.state().requestResultNo,undefined);
    assert.match(h.detail.textContent,/직접 입력/);
    assert(!h.detail.textContent.includes('SAMPLE-A'));
  });
  test(`${mode} ${prefix}: selected request can be changed to manual without leaking prior request metadata`,async t=>{
    const h=await setup(t,prefix,mode);await h.lookup();h.choose('0');
    assert.equal(h.api.state().requestNumber,'REQ-A');
    assert.equal(h.api.state().limsExperimentCode,'LIMS-A');
    assert.match(h.detail.textContent,/SAMPLE-A/);
    h.type('requestNumber','MANUAL-002','change');
    assert.equal(h.select.value,'');assert.equal(h.api.selected(),null);
    assert.equal(h.api.state().limsExperimentCode,'');
    assert.equal(h.api.state().sampleName,undefined);
    assert.equal(h.api.state().requestResultNo,undefined);
    assert(!h.detail.textContent.includes('SAMPLE-A'));
    h.type('limsExperimentCode','CUSTOM-CODE');h.type('operatorId','operator');h.api.setReport();await h.send();
    assert.equal(h.sent[0].requestNumber,'MANUAL-002');assert.equal(h.sent[0].experimentCode,'CUSTOM-CODE');
  });
  test(`${mode} ${prefix}: editing LIMS code detaches selection, preserves manual values, and allows reselect`,async t=>{
    const h=await setup(t,prefix,mode);await h.lookup();h.choose('0');
    h.type('limsExperimentCode','CUSTOM-CODE');h.type('requestNumber','CUSTOM-REQ');
    assert.equal(h.select.value,'');assert.equal(h.api.state().limsExperimentCode,'CUSTOM-CODE');
    h.choose('1');assert.equal(h.api.state().requestNumber,'REQ-B');
    assert.equal(h.api.state().limsExperimentCode,'LIMS-B');assert.match(h.detail.textContent,/SAMPLE-B/);
    h.type('operatorId','operator');h.api.setReport();await h.send();
    assert.equal(h.sent[0].requestNumber,'REQ-B');assert.equal(h.sent[0].experimentCode,'LIMS-B');
  });
  test(`${mode} ${prefix}: failed lookup still permits manual entry; blank values cannot be sent`,async t=>{
    const h=await setup(t,prefix,mode);h.failLookup();await h.lookup();h.fill();h.api.setReport();
    h.type('requestNumber','  ');await h.send();assert.equal(h.sent.length,0);
    h.type('requestNumber','REQ');h.type('limsExperimentCode','  ');await h.send();assert.equal(h.sent.length,0);
    h.type('limsExperimentCode','CODE');await h.send();assert.equal(h.sent.length,1);
  });
}
