// Actual RAW uploads, IndexedDB, Plotly, multipart jobs, LibreOffice/PPT, and revisions.
// Chromium engine QA is not a claim of Windows XP / Supermium hardware testing.
const assert = require('node:assert/strict');
const fs = require('node:fs'), os = require('node:os'), path = require('node:path');
const {spawn} = require('node:child_process');
const {chromium} = require('playwright');
const output = fs.mkdtempSync(path.join(os.tmpdir(), 'rist-spectrum-workflow-'));
const server = spawn(process.env.RIST_TEST_PYTHON || 'python3', ['-u', path.join(__dirname, 'spectrum_workflow_fixture.py'), output], {env:process.env});
let logs = ''; server.stderr.on('data', chunk => { logs += chunk; });
const port = new Promise((resolve, reject) => {
  let data = '';
  server.stdout.on('data', chunk => { data += chunk; if (data.includes('\n')) { try { resolve(JSON.parse(data.split('\n')[0]).port); } catch (e) { reject(e); } } });
  server.on('exit', code => { if (code) reject(new Error(logs)); });
});
const raw = Buffer.from(Array.from({length:241}, (_, i) => {
  const x = 400 + i * 15;
  return x + ',' + (.05 + Math.exp(-Math.pow(x - 1700, 2) / (2 * 55 ** 2)) + .55 * Math.exp(-Math.pow(x - 1250, 2) / (2 * 80 ** 2)));
}).join('\n'));
(async () => {
  const origin = 'http://127.0.0.1:' + await port;
  for (let i = 0; i < 80; i++) { try { if ((await fetch(origin + '/ftir')).ok) break; } catch (_) {} await new Promise(r => setTimeout(r, 100)); }
  const browser = await chromium.launch({headless:true, executablePath:'/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'});
  try {
    for (const kind of ['ftir','raman']) {
      const page = await browser.newPage({viewport:{width:1440,height:1050}}), errors = [];
      page.on('pageerror', error => errors.push(error.message)); page.on('dialog', dialog => dialog.accept());
      page.setDefaultTimeout(20000);
      const gd = kind === 'ftir' ? 'peak-plot' : 'raman-plot';
      try {
        await page.goto(origin + '/' + kind);
        await page.waitForFunction(() => { const e=document.getElementById('sw-step-2'); return e && !e.disabled; });
        assert.equal(await page.locator('#spectrum-workflow').getAttribute('data-step'),'1');
        await page.locator('#'+kind+'-file-input').setInputFiles({name:'workflow.'+(kind==='ftir'?'dpt':'txt'),mimeType:'text/plain',buffer:raw});
        await page.waitForFunction(() => document.getElementById('spectrum-workflow').dataset.step==='2' && !document.getElementById('sw-save-graph').disabled);
        await page.evaluate(async id => {
          const p=document.getElementById(id);
          await Plotly.relayout(p, {'title.text':'첫 번째 그래프', annotations:[{text:'첫 번째 메모',x:1700,y:1.2,showarrow:false}]});
        },gd);
        await page.locator('#sw-graph-name').fill('비교 그래프 1'); await page.locator('#sw-save-graph').click();
        await page.waitForFunction(() => document.querySelectorAll('.sw-card').length===1 && !document.getElementById('sw-save-graph').disabled);
        await page.locator('#sw-step-1').click(); await page.locator('#'+kind+'-clear').click();
        await page.locator('#'+kind+'-file-input').setInputFiles({name:'second.'+(kind==='ftir'?'dpt':'txt'),mimeType:'text/plain',buffer:raw});
        await page.waitForFunction(() => document.getElementById('spectrum-workflow').dataset.step==='2' && !document.getElementById('sw-save-graph').disabled);
        await page.evaluate(async id => { const p=document.getElementById(id); await Plotly.relayout(p, {'title.text':'두 번째 그래프',annotations:[{text:'두 번째 메모',x:1700,y:1.2,showarrow:false}]}); await Plotly.restyle(p, {'line.color':'#e74c3c'}, [0]); },gd);
        await page.locator('#sw-graph-name').fill('비교 그래프 2'); await page.locator('#sw-save-graph').click();
        await page.waitForFunction(() => document.querySelectorAll('.sw-card').length===2 && !document.getElementById('sw-save-graph').disabled);
        await page.locator('.sw-card').first().getByRole('button',{name:'미리보기',exact:true}).click();
        assert.equal(await page.locator('.sw-preview img').count(),1); await page.locator('.sw-preview').getByRole('button',{name:'닫기',exact:true}).click();
        await page.locator('#sw-step-1').click(); await page.locator('#'+kind+'-clear').click();
        await page.locator('#sw-step-2').click();
        await page.locator('.sw-card').first().getByRole('button',{name:'다시 편집',exact:true}).click();
        await page.waitForFunction(id => document.getElementById(id).layout.title.text==='첫 번째 그래프' && !document.getElementById('sw-save-graph').disabled,gd);
        assert.equal(await page.evaluate(id=>document.getElementById(id).layout.annotations[0].text,gd),'첫 번째 메모');
        await page.evaluate(async id => { await Plotly.relayout(document.getElementById(id),{'annotations[0].text':'다시 편집한 메모'}); },gd);
        await page.locator('#sw-save-graph').click();
        await page.waitForFunction(()=>!document.getElementById('sw-save-graph').disabled);
        assert.equal(await page.locator('.sw-card').count(),2);
        await page.locator('#'+gd).screenshot({path:path.join(output,kind+'-edit.png')});
        await page.locator('#sw-step-3').click();
        assert.equal(await page.locator('#'+kind+'-home').isVisible(),true);
        assert.equal(await page.locator('#'+kind+'-account-link').isVisible(),true);
        const second=page.locator('.sw-card').nth(1);
        await second.getByRole('button',{name:'비교 그래프 2 앞 순서로',exact:true}).click();
        await page.locator('.sw-card').first().getByRole('checkbox').uncheck();
        await page.locator('.sw-card').first().getByRole('checkbox').check();
        await page.locator('#sw-build-report').click();
        await page.waitForFunction(()=>{const f=document.getElementById('sw-report-editor');return f.src.includes('/edit?workflow=1')&&!f.hidden;},null,{timeout:180000});
        let editor=page.frameLocator('#sw-report-editor');
        await editor.locator('#edit-page option').first().waitFor({state:'attached',timeout:120000});
        await editor.locator('#slide-image').evaluate(img => new Promise((resolve,reject)=>{if(img.complete&&img.naturalWidth)resolve();else{img.onload=resolve;img.onerror=reject;}}));
        const originalUrl=await page.locator('#sw-report-editor').getAttribute('src');
        assert.equal(await editor.locator('#send').isVisible(),false);
        await page.screenshot({path:path.join(output,kind+'-report-edit.png'),fullPage:true});
        await editor.getByLabel('보고서 제목',{exact:true}).fill('검토 완료 '+kind.toUpperCase());
        await page.waitForFunction(()=>{const f=document.getElementById('sw-report-editor');return f.contentDocument.getElementById('status').textContent.includes('자동 저장 완료');},null,{timeout:180000});
        const revisedUrl=await page.locator('#sw-report-editor').evaluate(f=>f.contentWindow.location.pathname);
        assert.notEqual(revisedUrl,originalUrl.split('?')[0]);
        await page.locator('#sw-step-2').click();
        assert.equal(await page.locator('#sw-report-editor').evaluate(f=>f.contentWindow.location.pathname),revisedUrl);
        await page.locator('#sw-step-4').click();
        await editor.locator('#send').waitFor({state:'visible'});
        assert.equal(await editor.locator('#send').isVisible(),true);
        const download=await editor.locator('#download').getAttribute('href');
        const response=await page.request.get(origin+download); assert.equal(response.status(),200);
        fs.writeFileSync(path.join(output,kind+'-reviewed.zip'),await response.body());
        const viewer=await browser.newPage(); await viewer.goto(origin+download.replace('/download','/preview'));
        await viewer.locator('#graph-tab').click();
        assert.equal(await viewer.locator('#graph-choice option').count(),2);
        await viewer.locator('#graph-choice').selectOption('1');
        await viewer.waitForFunction(()=>document.getElementById('report-graph').data&&document.getElementById('report-graph').layout.title.text==='첫 번째 그래프');
        await viewer.screenshot({path:path.join(output,kind+'-recipient.png'),fullPage:true}); await viewer.close();
        await page.reload();
        await page.waitForFunction(()=>document.getElementById('spectrum-workflow')?.dataset.step==='4'&&!document.getElementById('sw-step-2').disabled);
        assert.equal(await page.locator('#sw-report-editor').getAttribute('src'),revisedUrl+'?workflow=1');
        await page.locator('#sw-step-2').click(); assert.equal(await page.locator('.sw-card').count(),2);
        await page.setViewportSize({width:390,height:844});
        await page.waitForTimeout(500);
        await page.evaluate(()=>window.scrollTo(0,0));
        await page.screenshot({path:path.join(output,kind+'-mobile.png')});
        assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth <= window.innerWidth+1),true);
        assert.deepEqual(errors,[]);
        console.log(kind+': upload, two graphs, preview, restore/re-edit, selection/order, real PPT/PDF, revision, back navigation, recipient graph switching, reload, mobile passed');
      } catch(error) { await page.screenshot({path:path.join(output,kind+'-failure.png'),fullPage:true}); console.error(logs.slice(-8000)); throw error; }
      finally { await page.close(); }
    }
    console.log(JSON.stringify({browser:await browser.version(),output}));
  } finally { await browser.close(); server.kill('SIGTERM'); }
})().catch(error=>{console.error(error);console.error('Artifacts:',output);server.kill('SIGTERM');process.exitCode=1;});
