// Real RAW chooser, rendered geometry, removal and step navigation.
// Mac Chrome and Chrome49-UA fallback only; not Windows/Supermium hardware QA.
const assert=require('node:assert/strict'),fs=require('node:fs'),os=require('node:os'),path=require('node:path');
const {spawn}=require('node:child_process'),{chromium}=require('playwright');
const output=fs.mkdtempSync(path.join(os.tmpdir(),'rist-upload-files-'));
const server=spawn(process.env.RIST_TEST_PYTHON||'python3',['-u',path.join(__dirname,'spectrum_workflow_fixture.py'),output],{env:process.env});
let logs='';server.stderr.on('data',data=>logs+=data);
const port=new Promise((resolve,reject)=>{let data='';server.stdout.on('data',chunk=>{data+=chunk;if(data.includes('\n'))resolve(JSON.parse(data.split('\n')[0]).port);});server.on('exit',code=>reject(Error('fixture exit '+code+' '+logs)));});
const raw=Buffer.from(Array.from({length:241},(_,i)=>{const x=400+i*15;return x+','+(.05+Math.exp(-Math.pow(x-1700,2)/(2*55**2)));}).join('\n'));
(async()=>{
  const origin='http://127.0.0.1:'+await port;
  const browser=await chromium.launch({headless:true,executablePath:'/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'});
  try{
    for(const kind of ['ftir','raman'])for(const legacy of [false,true]){
      const name=kind+(legacy?'-fallback':'-native'),errors=[];
      const context=await browser.newContext({viewport:{width:1280,height:800},...(legacy?{userAgent:'Mozilla/5.0 (Windows NT 5.1) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/49.0.2623.75 Safari/537.36'}:{})});
      const page=await context.newPage();page.on('pageerror',e=>errors.push(e.message));page.on('dialog',d=>d.accept());page.setDefaultTimeout(30000);
      const extension=kind==='ftir'?'dpt':'txt';
      const filenames=['260819_BHD_Anode.'+extension,...Array.from({length:9},(_,i)=>'시료_'+(i+1)+'_장비조건_검토완료_'+('long-unbroken-name_'.repeat(i===0?7:1))+'.'+extension)];
      async function ready(){await page.waitForFunction(()=>!document.getElementById('sw-step-1').disabled&&!document.getElementById('sw-save-graph').disabled);await page.locator('#sw-step-1').click();}
      async function geometry(count,label){
        const result=await page.locator('#'+kind+'-file-list').evaluate((list,kind)=>{
          function rect(e){const r=e.getBoundingClientRect();return {left:r.left,top:r.top,right:r.right,bottom:r.bottom,width:r.width,height:r.height};}
          return {list:rect(list),sw:list.scrollWidth,cw:list.clientWidth,sh:list.scrollHeight,ch:list.clientHeight,
            pageWidth:document.documentElement.scrollWidth,viewport:window.innerWidth,
            items:Array.from(list.children).map(item=>{const text=item.querySelector('.'+kind+'-file-name'),button=item.querySelector('button');
              const range=document.createRange();range.selectNodeContents(text||item);
              return {item:rect(item),button:rect(button),text:text&&rect(text),name:text&&text.textContent,title:text&&text.title,
                lines:Array.from(range.getClientRects()).map(r=>({left:r.left,right:r.right,top:r.top,bottom:r.bottom})),buttonStyle:{minHeight:getComputedStyle(button).minHeight,padding:getComputedStyle(button).padding}};
            })};
        },kind);
        fs.writeFileSync(path.join(output,name+'-'+label+'.json'),JSON.stringify(result,null,2));
        assert.equal(result.items.length,count);assert(result.sw<=result.cw+1,'file list has horizontal clipping');
        assert(result.sh<=result.ch+1,'file list has vertical clipping');
        for(const item of result.items){
          assert(item.button.top>=item.item.top&&item.button.bottom<=item.item.bottom,'delete button escapes chip: '+JSON.stringify(item));
          assert(item.button.right<=item.item.right+1&&item.button.width>=30,'delete button is clipped or shrunk');
          assert(item.item.left>=result.list.left-1&&item.item.right<=result.list.right+1,'chip leaves file list');
          assert(item.name&&item.title===item.name,'complete filename and hover title must be available');
          assert(item.lines.every(r=>r.left>=item.text.left-1&&r.right<=item.text.right+1&&r.top>=item.text.top-1&&r.bottom<=item.text.bottom+1),'filename is visually clipped');
        }
        assert(result.pageWidth<=result.viewport+2,'page overflows horizontally');
      }
      try{
        await page.goto(origin+'/'+kind);await page.waitForFunction(()=>document.getElementById('sw-step-2')&&!document.getElementById('sw-step-2').disabled);
        await page.locator('#'+kind+'-file-input').setInputFiles({name:filenames[0],mimeType:'text/plain',buffer:raw});await ready();
        await page.locator('#sw-upload').screenshot({path:path.join(output,name+'-single.png')});await geometry(1,'single');
        await page.locator('#'+kind+'-file-input').setInputFiles(filenames.slice(1).map(name=>({name,mimeType:'text/plain',buffer:raw})));await ready();
        for(const width of [1440,1280,768,390,320]){
          await page.setViewportSize({width,height:900});await page.locator('#sw-upload').scrollIntoViewIfNeeded();
          await geometry(10,String(width));
          if(width===1280||width===390)await page.locator('#sw-upload').screenshot({path:path.join(output,name+'-'+width+'.png')});
        }
        await page.locator('#'+kind+'-file-list button').nth(1).click();await ready();await geometry(9,'removed');
        assert(!await page.getByRole('button',{name:filenames[1]+' 제거',exact:true}).count());
        assert.deepEqual(await page.locator('.'+kind+'-file-name').allTextContents(),filenames.filter((_,i)=>i!==1));
        await page.locator('#sw-step-2').click();await page.locator('#sw-step-1').click();await geometry(9,'returned');
        if(legacy)assert(await page.locator('script[src*="/assets/browser-compat/"]').count()>0);
        assert.deepEqual(errors,[]);console.log(name+': one/ten files, long names, five widths, delete and step return passed');
      }catch(e){await page.screenshot({path:path.join(output,name+'-failure.png'),fullPage:true});throw e;}
      finally{await context.close();}
    }
  }finally{await browser.close();server.kill();console.log('Artifacts: '+output);}
})().catch(e=>{server.kill();console.error(e);console.error(logs.slice(-3000));process.exitCode=1;});
