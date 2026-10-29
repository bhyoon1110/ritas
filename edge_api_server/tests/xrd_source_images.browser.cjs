// Actual Mac Chrome + shipped legacy bundle; not Supermium/XP hardware certification.
const assert=require('node:assert/strict'),fs=require('node:fs'),os=require('node:os'),path=require('node:path');
const {spawn}=require('node:child_process'),{chromium}=require('playwright');
const output=fs.mkdtempSync(path.join(os.tmpdir(),'rist-xrd-source-images-'));
const server=spawn(process.env.RIST_TEST_PYTHON||'python3',['-u',path.join(__dirname,'xrd_source_images_fixture.py'),output],{env:process.env});
let logs='';server.stderr.on('data',data=>logs+=data);
const startup=new Promise((resolve,reject)=>{let data='';server.stdout.on('data',chunk=>{data+=chunk;if(data.includes('\n'))resolve(JSON.parse(data.split('\n')[0]));});server.on('exit',code=>reject(Error('fixture exited '+code+' '+logs)));});
(async()=>{
  let browser;
  try{
    const start=await startup,origin='http://127.0.0.1:'+start.port,base='/api/v1/xrd/workbench';
    browser=await chromium.launch({headless:true,executablePath:process.env.CHROME_BIN||'/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'});
    for(const legacy of [false,true]){
      const page=await browser.newPage({viewport:{width:1440,height:1000},...(legacy?{userAgent:'Mozilla/5.0 Chrome/49.0.2623.75 Safari/537.36'}:{})});
      const errors=[];page.on('pageerror',e=>errors.push(e.message));page.setDefaultTimeout(30000);
      const ready=()=>page.waitForFunction(()=>!document.getElementById('save').disabled&&document.querySelector('#slide-image').naturalWidth>0);
      try{
        await page.goto(origin+base+'/report.html?project='+start.sid);await ready();
        if(legacy)assert.equal(await page.locator('script[src$="report.legacy.js"]').count(),1);
        await page.locator('[data-tab="'+start.sample_id+'"]').click();
        const selector=page.getByLabel('보고서에 사용할 상매칭 이미지',{exact:true});
        await selector.waitFor();
        // Exercise change-only legacy selects as well as native input+change.
        await selector.selectOption('');
        assert.match(await page.locator('#sampleForm').textContent(),/연결된 원본이 없어 뷰어/);
        const preview=page.waitForResponse(r=>r.url().endsWith('/draft-preview')&&r.ok());
        await selector.selectOption(start.image_id);await preview;await ready();
        const picture=page.locator('#sampleForm img[alt^="상매칭 원본"]');
        assert((await picture.getAttribute('src')).endsWith('/'+start.image_id));
        await picture.evaluate(e=>e.decode());
        assert.match(await page.locator('#sampleForm').textContent(),/사용할 원본: 고객 원본사진.png/);
        await page.locator('#sampleForm').screenshot({path:path.join(output,(legacy?'legacy':'native')+'-selection.png')});
        const saved=page.waitForResponse(r=>r.url().endsWith('/api/reports/'+start.sid)&&r.request().method()==='PUT');
        await page.locator('#save').click();assert.equal((await saved).status(),200);await ready();
        const report=await(await page.request.get(origin+base+'/api/reports/'+start.sid)).json();
        assert.equal(report.document.samples[0].phase_image_source_id,start.image_id);
        assert.equal(report.project.sources.find(r=>r.id===start.image_id).role,'ignore','original registration stays immutable');
        await page.reload();await ready();await page.locator('[data-tab="'+start.sample_id+'"]').click();
        assert.equal(await selector.inputValue(),start.image_id);
        await page.setViewportSize({width:390,height:844});await page.locator('[data-work-view="editor"]').click();
        assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+2));
        await page.screenshot({path:path.join(output,(legacy?'legacy':'native')+'-mobile.png'),fullPage:true});
        assert.deepEqual(errors,[]);
        console.log((legacy?'Legacy':'Native')+': source selection, live preview, original image, save/reload, mobile passed');
      }catch(e){await page.screenshot({path:path.join(output,'failure.png'),fullPage:true});throw e;}
      finally{await page.close();}
    }
  }finally{if(browser)await browser.close();server.kill();console.log('Artifacts: '+output);}
})().catch(e=>{console.error(e,logs.slice(-3000));process.exitCode=1;});
