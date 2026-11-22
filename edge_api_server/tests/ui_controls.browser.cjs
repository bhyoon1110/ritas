// CSS matrix in Mac Chrome; native/legacy CSS, not Windows certification.
const assert=require('node:assert/strict'),fs=require('node:fs'),os=require('node:os'),path=require('node:path');
const {spawn}=require('node:child_process'),{chromium}=require('playwright');
const output=fs.mkdtempSync(path.join(os.tmpdir(),'rist-ui-controls-'));
const server=spawn(process.env.RIST_TEST_PYTHON||'python3',['-u',path.join(__dirname,'ui_controls_fixture.py')],{env:process.env});
let logs='';server.stderr.on('data',data=>logs+=data);
const ready=new Promise((resolve,reject)=>{let data='';server.stdout.on('data',chunk=>{data+=chunk;if(data.includes('\n'))resolve(JSON.parse(data.split('\n')[0]));});server.on('exit',code=>reject(Error('fixture '+code+' '+logs)));});
(async()=>{
 let browser;
 try{
  const start=await ready,origin='http://127.0.0.1:'+start.port;
  browser=await chromium.launch({headless:true,executablePath:'/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'});
  const page=await browser.newPage(),css=fs.readFileSync(path.join(__dirname,'../app/ui_controls.css'),'utf8');
  const selectors=css.slice(css.indexOf('body[data-rist-ui]'),css.indexOf(' {')).trim();let cases=0;
  for(const legacy of [false,true])for(const width of [1440,390,320]){
   await page.setViewportSize({width,height:1000});
   for(const name of start.pages){
    await page.goto(origin+'/'+name+(legacy?'?legacy=1':''));
    assert.equal(await page.locator('#rist-ui-controls').count(),1,name+' stylesheet');
    assert.equal(await page.locator('body[data-rist-ui]').count(),1,name+' scope');
    const result=await page.locator(selectors).evaluateAll((nodes,min)=>nodes.filter(n=>n.getBoundingClientRect().height&&getComputedStyle(n).visibility!=='hidden').map(n=>{
     const r=n.getBoundingClientRect(),c=getComputedStyle(n);return {label:(n.textContent||n.title).trim().slice(0,60),height:r.height,radius:c.borderRadius,font:c.fontSize,clipped:n.scrollWidth>n.clientWidth+2,tooSmall:r.height<min-1};
    }).filter(n=>n.tooSmall||n.clipped||n.radius!=='6px'||parseFloat(n.font)<12),width<=640?44:36);
    if(result.length)await page.screenshot({path:path.join(output,name+'-'+width+'-'+legacy+'-failure.png'),fullPage:true});
    assert.deepEqual(result,[],name+' '+width+' '+legacy+' controls');
    const overflow=await page.evaluate(()=>{const styled=document.documentElement.scrollWidth,s=document.getElementById('rist-ui-controls');s.disabled=true;const original=document.documentElement.scrollWidth;s.disabled=false;return {styled,original};});
    assert(overflow.styled<=Math.max(overflow.original,width)+2,name+' added overflow '+JSON.stringify(overflow));
    if(name==='account'){
     const link=page.getByRole('link',{name:'대시보드',exact:true});assert.equal(await link.getAttribute('href'),'/');
     await link.focus();assert.equal(await link.evaluate(e=>getComputedStyle(e).outlineStyle),'solid');await link.click();assert.equal(new URL(page.url()).pathname,'/');await page.goto(origin+'/account'+(legacy?'?legacy=1':''));
    }
    if(!legacy&&width!==320&&['account','ftir','raman','tem','xrd-report','report-editor','index','voc','admin'].includes(name))await page.screenshot({path:path.join(output,name+'-'+width+'.png'),fullPage:true});
    cases++;
   }
  }
  await page.goto(origin+'/report-editor');
  const dims=await page.evaluate(()=>{
   const area=document.createElement('div');area.innerHTML='<button class="canvas-object" style="width:250px;height:100px">Graph</button><button class="page-thumb" style="width:170px;height:140px">Slide</button><button class="primary" hidden>Hidden</button>';document.body.appendChild(area);
   const measure=()=>Array.from(area.children).map(e=>{const r=e.getBoundingClientRect();return [r.width,r.height];});const after=measure();document.getElementById('rist-ui-controls').disabled=true;return {after,before:measure()};
  });assert.deepEqual(dims.after,dims.before);
  console.log(cases+' native/legacy desktop/mobile layouts passed; dashboard navigation, focus, hidden controls and canvas dimensions verified.');
 }finally{if(browser)await browser.close();server.kill();console.log('Artifacts: '+output);}
})().catch(error=>{console.error(error);console.error(logs.slice(-3000));server.kill();process.exitCode=1;});
