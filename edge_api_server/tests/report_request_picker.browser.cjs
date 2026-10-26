// Real report-editor UI with isolated mocked LIMS/job responses; no real delivery.
// Mac Chrome + Chrome49-UA bundle, not Windows/Supermium hardware validation.
const assert=require('node:assert/strict'),fs=require('node:fs'),os=require('node:os'),path=require('node:path');
const {spawn}=require('node:child_process'),{chromium}=require('playwright');
const output=fs.mkdtempSync(path.join(os.tmpdir(),'rist-request-picker-'));
const server=spawn(process.env.RIST_TEST_PYTHON||'python3',['-u',path.join(__dirname,'spectrum_workflow_fixture.py'),output],{env:process.env});
let logs='';server.stderr.on('data',data=>logs+=data);
const port=new Promise((resolve,reject)=>{let data='';server.stdout.on('data',chunk=>{data+=chunk;if(data.includes('\n'))resolve(JSON.parse(data.split('\n')[0]).port);});server.on('exit',code=>reject(Error('fixture exit '+code+' '+logs)));});
(async()=>{
  const origin='http://127.0.0.1:'+await port,browser=await chromium.launch({headless:true,executablePath:'/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'});
  try{
    for(const kind of ['ftir','raman','tem'])for(const legacy of [false,true]){
      const name=kind+(legacy?'-fallback-mobile':'-desktop'),errors=[],queries=[],posts=[];
      const context=await browser.newContext({viewport:{width:legacy?390:1440,height:1000},...(legacy?{userAgent:'Mozilla/5.0 (Windows NT 5.1) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/49.0.2623.75 Safari/537.36'}:{})});
      const page=await context.newPage();page.setDefaultTimeout(15000);page.on('pageerror',e=>errors.push(e.message));
      let doc={title:'Lookup test',completionDate:'2026-10-26',transfer:{requestNumber:'',limsExperimentCode:'',equipmentCode:'CURRENT-LAB',operatorId:'SSO-001'},sections:[],auxiliary:{},variables:[]},revision=0,failLookup=false;
      const items=Array.from({length:200},(_,i)=>({requestNumber:i<2?'REQ-SAME':'REQ-'+i,experimentCode:'CODE-'+i,
        sampleName:i<2?'같은 시료 <b>검증</b> '+('긴 이름 '.repeat(i?15:0)):'Other '+i,...(i?{equipmentCode:'LAB-'+i}:{})}));
      await page.route('**/api/v1/**',async route=>{
        const request=route.request(),url=new URL(request.url()),p=url.pathname;
        const json=(value,status=200)=>route.fulfill({status,contentType:'application/json',body:JSON.stringify(value)});
        if(p==='/api/v1/requests'){
          queries.push(url.searchParams);assert.equal(url.searchParams.get('experimentType'),kind==='ftir'?'FT-IR':kind.toUpperCase());
          return json(failLookup?{message:'테스트 조회 오류'}:{items:url.searchParams.get('page')==='1'?items:[]},failLookup?503:200);
        }
        if(p.endsWith('/editor-data'))return json({document:doc,authenticatedOperatorId:'SSO-001'});
        if(p.endsWith('/edit-layout'))return json({ratio:16/9,pages:[{id:'cover',title:'표지',regions:[]}],pageEditing:false,canvasEditing:false});
        if(p.includes('/edit-slides/'))return route.fulfill({contentType:'image/png',body:Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aGncAAAAASUVORK5CYII=','base64')});
        if(p.endsWith('/revisions')){doc=request.postDataJSON();posts.push(doc);return json({jobId:'revision'+(++revision),status:'completed'});}
        if(p.endsWith('/send'))throw Error('Test must not send a report');
        return json({jobId:'revision'+revision,status:'completed',transfer:doc.transfer});
      });
      const search=page.getByRole('combobox',{name:'의뢰 검색·선택',exact:true}),list=page.locator('#request-results');
      async function saved(){await page.waitForFunction(()=>document.getElementById('status').textContent.includes('자동 저장 완료'));}
      async function bounds(){
        assert(await list.isVisible(),'result menu must be visible for geometry checks');
        const r=await list.evaluate(e=>{const r=e.getBoundingClientRect();return {left:r.left,top:r.top,right:r.right,bottom:r.bottom,w:innerWidth,h:innerHeight};});
        assert(r.left>=0&&r.top>=0&&r.right<=r.w+1&&r.bottom<=r.h+1,JSON.stringify(r));
        assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));
      }
      try{
        await page.goto(origin+'/__test__/request-editor/'+kind);await search.waitFor();
        if(legacy){assert(await page.locator('script[src*="/assets/browser-compat/"]').count()>0);}
        await page.locator('#request-load').click();await list.getByRole('option').first().waitFor();
        assert.equal(await page.locator('#request-select').count(),0);assert.equal(await page.locator('.request-combo input').count(),1);
        await search.fill('같은 시료');assert.equal(await list.getByRole('option').count(),2);assert.equal(queries.length,1);assert.equal(posts.length,0);
        assert.equal(await list.locator('b').count(),0);await search.press('Enter');assert.equal(await page.getByLabel('의뢰번호',{exact:true}).inputValue(),'');
        await bounds();await page.screenshot({path:path.join(output,name+'-open.png')});
        await search.press('ArrowDown');await search.press('ArrowDown');await search.press('Enter');await saved();
        assert.equal(doc.transfer.requestNumber,'REQ-SAME');assert.equal(doc.transfer.limsExperimentCode,'CODE-1');assert.equal(doc.transfer.equipmentCode,'LAB-1');assert.equal(doc.transfer.operatorId,'SSO-001');
        assert(await list.isHidden());const count=posts.length;
        await search.fill('does not match');assert.match(await list.textContent(),/일치하는 의뢰가 없습니다/);await search.press('Escape');assert(await list.isHidden());assert.equal(posts.length,count);
        await search.fill('같은 시료');await page.locator('#request-result-0').click();await saved();
        assert.equal(doc.transfer.limsExperimentCode,'CODE-0');assert.equal(doc.transfer.equipmentCode,'LAB-1');
        // Same number, different experiment: click uses the exact result, never a text lookup.
        await page.locator('#request-toggle').click();await list.getByRole('option').first().waitFor();await bounds();
        await search.press('Tab');assert(await list.isHidden());
        await page.locator('#request-next').click();await page.waitForFunction(()=>document.getElementById('request-info').textContent.includes('2페이지'));
        assert.equal(queries[queries.length-1].get('page'),'2');assert.equal(await list.getByRole('option').count(),0);
        assert.equal(doc.transfer.requestNumber,'REQ-SAME');failLookup=true;await page.locator('#request-load').click();
        await page.waitForFunction(()=>document.getElementById('request-info').textContent.includes('조회 실패'));
        await page.getByLabel('의뢰번호',{exact:true}).fill('MANUAL-REQ');await saved();
        assert.equal(doc.transfer.requestNumber,'MANUAL-REQ');assert.equal(doc.transfer.limsExperimentCode,'');assert.equal(await search.inputValue(),'');
        assert.deepEqual(errors,[]);console.log(name+': one field, local filter, exact selection, keyboard/click, bounds, autosave, pagination and failure passed');
      }catch(e){await page.screenshot({path:path.join(output,name+'-failure.png'),fullPage:true});throw e;}
      finally{await context.close();}
    }
  }finally{await browser.close();server.kill();console.log('Artifacts: '+output);}
})().catch(e=>{server.kill();console.error(e);console.error(logs.slice(-3000));process.exitCode=1;});
