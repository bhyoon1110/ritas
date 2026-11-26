const {test}=require('node:test');
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');

for(const file of ['ftir_web.py','raman_web.py','ahn_web.py','xrd_web.py']){
  const source=fs.readFileSync(path.join(__dirname,'../app',file),'utf8');
  const start=source.indexOf('async function loadRequestItems()');
  const end=source.indexOf('function applySelectedRequest()',start);
  const program=source.slice(start,end);
  for(const mode of ['success','network-error','invalid-filter']){
    test(file+' releases filter controls after '+mode,async()=>{
      const busy=[],urls=[],results=[],messages=[];
      const context={requestLoad:{},requestItems:[],REQUEST_EXPERIMENT_TYPE:'TEM',Date,Array,encodeURIComponent,
        requestFilters:{setBusy:value=>busy.push(value),url(){if(mode==='invalid-filter')throw new Error('filter');return '/api/v1/requests?source=lims&testMtdCode=A01';},showResult:value=>results.push(value)},
        renderRequestOptions(){},setSuccessMessage:m=>messages.push(m),setMessage:m=>messages.push(m),setStatus:m=>messages.push(m),syncActionState(){}};
      context.fetchJson=context.requestJson=async url=>{urls.push(url);if(mode==='network-error')throw new Error('network');return {items:[{requestNumber:'REQ'}],totalCount:1,page:1,pageSize:200};};
      vm.createContext(context);vm.runInContext(program,context);await context.loadRequestItems();
      assert.deepEqual(busy,[true,false]);
      assert.equal(context.requestLoad.textContent,'의뢰 조회');
      if(mode==='success'){assert.equal(results.length,1);assert.equal(urls[0],'/api/v1/requests?source=lims&testMtdCode=A01');}
      else {assert.equal(results.length,0);assert.equal(messages.length,1);}
    });
  }
}

for(const file of ['ftir_web.py','raman_web.py','ahn_web.py','xrd_web.py']){
  test(file+' applies AX registration code and clears a changed search without guessing from display code',()=>{
    const source=fs.readFileSync(path.join(__dirname,'../app',file),'utf8');
    const start=source.indexOf('function applySelectedRequest()');
    // Balanced braces select exactly this function across the four page templates.
    let at=source.indexOf('{',start),depth=1;for(at++;depth;at++){if(source[at]==='{')depth++;if(source[at]==='}')depth--;}
    const fields={},row={requestNumber:'2022T00793',testMethodCode:'A11111',experimentCode:'A11111',axTestMtdCode:'2022A11111'};
    const context={window:{RistRequestFilters:{transferMethodCode:item=>item.axTestMtdCode||''}},selectedRequestItem:()=>row,
      setReportTransferValue:(key,value)=>fields[key]=value,applyRequestEquipmentCode(){},renderRequestDetail(){},updateReportSendAvailability(){},scheduleWorkspaceSave(){}};
    vm.createContext(context);vm.runInContext(source.slice(start,at),context);context.applySelectedRequest();
    assert.equal(fields.limsExperimentCode,'2022A11111');delete row.axTestMtdCode;context.applySelectedRequest();assert.equal(fields.limsExperimentCode,'');
    const clearStart=source.indexOf('function clearRequestSearchSelection()'),clearEnd=source.indexOf('function selectedRequestItem()',clearStart);
    context.requestSelect={value:'0'};context.requestItems=[row];context.renderRequestOptions=()=>{};context.reportTransferValue=key=>fields[key];
    vm.runInContext(source.slice(clearStart,clearEnd),context);row.axTestMtdCode='AX';fields.limsExperimentCode='AX';context.clearRequestSearchSelection();
    assert.equal(fields.limsExperimentCode,'');assert.equal(context.requestSelect.value,'');assert.equal(context.requestItems.length,0);
  });
}
