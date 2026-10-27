// Real APIs, PPT revisions and browser controls on Mac Chrome; not a Windows Office test.
const assert=require('node:assert/strict'),fs=require('node:fs'),os=require('node:os'),path=require('node:path');
const {spawn}=require('node:child_process'),{chromium}=require('playwright');
const output=fs.mkdtempSync(path.join(os.tmpdir(),'rist-ppt-password-'));
const server=spawn(process.env.RIST_TEST_PYTHON||'python3',['-u',path.join(__dirname,'pptx_password_fixture.py'),output],{env:process.env});
let logs='';server.stderr.on('data',b=>logs+=b);
const startup=new Promise((resolve,reject)=>{let data='';server.stdout.on('data',b=>{data+=b;if(data.includes('\n'))resolve(JSON.parse(data.split('\n')[0]));});server.on('exit',code=>reject(Error('Fixture exit '+code+' '+logs)));});
(async()=>{
  let browser;
  try{
    const start=await startup,origin='http://127.0.0.1:'+start.port;
    browser=await chromium.launch({headless:true,executablePath:'/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'});
    for(const legacy of [false,true])for(const kind of ['ftir','raman','tem','xrd']){
      const name=kind+(legacy?'-chrome49':'-native'),context=await browser.newContext({viewport:{width:legacy?800:1440,height:1000},
        ...(legacy?{userAgent:'Mozilla/5.0 (Windows NT 5.1) AppleWebKit/537.36 Chrome/49.0.2623.75 Safari/537.36'}:{})});
      const page=await context.newPage(),errors=[];page.on('pageerror',e=>errors.push(e.message));page.setDefaultTimeout(180000);
      await page.goto(origin+start.urls[kind]);
      if(kind==='xrd')await page.locator('#nextStep').click();
      const panel=page.locator('.pptx-password-panel');await panel.locator('summary').click();
      const button=panel.getByRole('button',{name:'설정 / 변경',exact:true});
      await panel.getByLabel('새 수정 비밀번호',{exact:true}).fill('Browser-검증42!');
      await panel.getByLabel('비밀번호 확인',{exact:true}).fill('wrong');
      await button.click();await page.waitForFunction(()=>document.querySelector('.pptx-password-panel [role=status]').textContent.includes('동일하게'));
      await panel.getByLabel('비밀번호 확인',{exact:true}).fill('Browser-검증42!');
      const old=kind==='xrd'?'':await page.locator('#download').getAttribute('href');
      await button.click();await page.waitForFunction(()=>document.querySelector('.pptx-password-panel [role=status]').textContent.includes('설정됨'));
      assert.equal(await panel.getByLabel('새 수정 비밀번호',{exact:true}).inputValue(),'');
      assert.equal(await panel.getByLabel('비밀번호 확인',{exact:true}).inputValue(),'');
      if(kind==='xrd'){
        // Return to the editor to exercise its normal final-save button.
        await page.locator('a[data-step="report"]').click();
        await page.locator('#save').click();
        await page.waitForFunction(()=>/버전 \d+을 저장/.test(document.getElementById('status').textContent));
        await page.locator('#nextStep').click();
      }else{
        await page.waitForFunction(old=>{const d=document.getElementById('download');return d.getAttribute('href')!==old&&d.getAttribute('aria-disabled')==='false';},old);
      }
      await panel.screenshot({path:path.join(output,name+'.png')});
      assert(await panel.evaluate(e=>e.scrollWidth<=e.clientWidth+1));
      const enabled=await panel.getByRole('button',{name:'해제',exact:true}).isEnabled();assert(enabled);
      assert.equal(await page.evaluate(()=>JSON.stringify({...localStorage}).includes('Browser-검증42!')),false);
      await page.reload();
      if(kind==='xrd')await page.locator('#savePane').waitFor({state:'visible'});
      await panel.locator('summary').click();
      await page.waitForFunction(()=>document.querySelector('.pptx-password-panel [role=status]').textContent.includes('설정됨'));
      assert.deepEqual(errors,[]);console.log(name+' saved and reopened');await context.close();
    }
    console.log('Browser QA: '+output);
  }finally{if(browser)await browser.close();server.kill();fs.writeFileSync(path.join(output,'server.log'),logs);}
})().catch(e=>{console.error(e);process.exitCode=1;});
