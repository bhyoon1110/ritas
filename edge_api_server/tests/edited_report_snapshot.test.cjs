// Exercise the shipped upload functions, including edits made while PNG capture awaits.
const {test} = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const {execFileSync} = require('node:child_process');
const acorn = require('acorn');
const clone = value => JSON.parse(JSON.stringify(value));
const programs = JSON.parse(execFileSync(process.env.RIST_TEST_PYTHON || 'python3', ['-c',
  'import json; from app import ftir_web,raman_web; print(json.dumps([ftir_web._UPLOAD_SCRIPT,raman_web._UPLOAD_SCRIPT]))'], {encoding:'utf8'}));
function extract(source, name) {
  const start = source.search(new RegExp('  (?:async )?function '+name+'\\('));
  assert(start >= 0, name);
  return source.slice(start, source.indexOf('\n  }', start) + 4);
}
function fixture() {
  return {data:[{name:'Sample',x:[100,200],y:[1,3],meta:{rist_sample_parent:true}},
    {name:'Edited peak',x:[200],y:[13],meta:{rist_peak:{x:199,label:'old'},rist_raman_stack_offset:10}}],
    layout:{width:1000,height:600,annotations:[{text:'최종 메모'}],shapes:[],meta:{}}};
}
for (const [index,kind] of ['ftir','raman'].entries()) {
  const source = programs[index];
  const helpers = extract(source,'compactReportFigurePayload') + extract(source,'reportSnapshotSignature');
  test(kind+' snapshot detects analytical edits but omits raw arrays and responsive dimensions', () => {
    acorn.parse(helpers,{ecmaVersion:5});
    const ctx = vm.createContext({gd:{}}); vm.runInContext(helpers,ctx);
    const before = fixture(), signature = ctx.reportSnapshotSignature(before);
    const compact = JSON.parse(signature);
    assert.equal(compact.data[0].x,undefined);
    assert.deepEqual(compact.data[1].x,[200]);
    assert.equal(compact.data[1].meta.rist_view_offset,10);
    const resized = clone(before); resized.layout.width=400; resized.layout.height=500; resized.layout.autosize=true;
    assert.equal(ctx.reportSnapshotSignature(resized),signature);
    for (const edit of [f=>f.data[1].name='new', f=>f.data[1].x=[201], f=>f.data[1].y=[14],
      f=>f.data.pop(), f=>f.layout.annotations[0].text='새 메모',
      f=>f.layout.shapes.push({type:'rect',x0:190,x1:210,y0:2,y1:4}),
      f=>f.layout.meta.ristRamanRatios=[{id:1,title:'new ratio'}]]) {
      const after=clone(before); edit(after); assert.notEqual(ctx.reportSnapshotSignature(after),signature);
    }
    assert.deepEqual(before,fixture());
  });
  test(kind+' capture freezes graph and analysis at one instant and warns before stale preview', async () => {
    let captureFinish, stackFinish, handler, posted, imageFigure;
    let currentAnalysis={samples:[{label:'before'}]};
    const ctx = vm.createContext({gd:{_ristStackReady:new Promise(resolve=>stackFinish=resolve)},
      files:[{}],latestAnalysisPayload:{},lastReportJob:null,lastReportSnapshot:null,loading:{},status:{},
      reportTransferFormState:()=>({requestNumber:'REQ',equipmentCode:'LAB',operatorId:'OP'}),
      updatePersistentReportDownload:()=>{},updateReportSendAvailability:()=>{},setBusy:()=>{},setMessage:()=>{},
      setReportProgress:()=>{},setReportDownloadLink:()=>{},reportFilesForVisibleSamples:()=>[{}],
      reportAnalysisPayload:()=>clone(currentAnalysis), currentFigurePayload:()=>clone(ctx.figure),
      captureReportFigureImage:f=>{imageFigure=f;return new Promise(resolve=>captureFinish=resolve);},
      uploadReportFilesWithSession:async(files,body)=>{posted=body;return {jobId:'frozen'};},
      pollReportJob:async()=>({jobId:'frozen',status:'completed'}),Blob,
      reportPreviewLink:{getAttribute:()=> 'false',addEventListener:(_,fn)=>handler=fn},
      window:{confirm:message=>{ctx.warning=message;return false;}}});
    ctx.figure=fixture();
    vm.runInContext(helpers+extract(source,'createReport'),ctx);
    const request=ctx.createReport();
    assert.equal(imageFigure,undefined); // Wait for outstanding stack coordinates.
    stackFinish(); await new Promise(resolve=>setImmediate(resolve));
    assert(imageFigure);
    ctx.figure.data[1].name='later edit'; currentAnalysis.samples[0].label='later sample';
    captureFinish('png'); await request;
    assert.equal(JSON.parse(posted.analysis_json).samples[0].label,'before');
    assert.equal(JSON.parse(posted.figure_json).data[1].name,'Edited peak');
    assert.equal(JSON.parse(await posted.viewer_figure.text()).data[1].name,'Edited peak');
    assert.equal(imageFigure.data[1].name,'Edited peak');
    assert.equal(ctx.lastReportJob.jobId,'frozen');
    vm.runInContext(source.match(/if \(reportPreviewLink\) reportPreviewLink.addEventListener\("click", function\(event\) \{[\s\S]*?\n  \}\);/)[0],ctx);
    let prevented=0; handler({preventDefault:()=>prevented++});
    assert.equal(prevented,1);assert.match(ctx.warning,/보고서 생성.*다시/);
    // Opening an older manually revised report remains an explicit choice.
    ctx.window.confirm=()=>true; handler({preventDefault:()=>prevented++});assert.equal(prevented,1);
    ctx.figure=fixture();ctx.window.confirm=()=>{throw Error('unchanged report should open directly');};
    handler({preventDefault:()=>prevented++});assert.equal(prevented,1);
  });
}
