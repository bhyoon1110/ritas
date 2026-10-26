// Actual four editor UIs, isolated mocked storage/queue; never sends email.
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),os=require('node:os');
const {spawn,execFileSync}=require('node:child_process'),{chromium}=require('playwright');
const output=fs.mkdtempSync(path.join(os.tmpdir(),'rist-mail-browser-')),web=path.resolve(__dirname,'../app/xrd_workbench/web');
const python=process.env.RIST_TEST_PYTHON||'python3';
const fixture=JSON.parse(execFileSync(python,['-c','import json; from tests.xrd_workbench.test_posco_pptx import fixture; p,d=fixture(); print(json.dumps(dict(project=p,document=d)))'],{env:process.env,encoding:'utf8'}));
const imageBytes=Buffer.from(execFileSync(python,['-c','from tests.test_report_mail import embedded_image; print(embedded_image((2400,1800)).split(",")[1])'],{env:process.env,encoding:'utf8'}).trim(),'base64');
const server=spawn(python,['-u',path.join(__dirname,'spectrum_workflow_fixture.py'),output],{env:process.env});
let logs='';server.stderr.on('data',s=>logs+=s);
const port=new Promise((resolve,reject)=>{let s='';server.stdout.on('data',chunk=>{s+=chunk;if(s.includes('\n'))resolve(JSON.parse(s.split('\n')[0]).port);});server.on('exit',code=>reject(Error('fixture exit '+code)));});
(async()=>{
  let browser;
  try{
    const origin='http://127.0.0.1:'+await port;
    browser=await chromium.launch({headless:true,executablePath:'/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'});
    for(const kind of (process.env.RIST_MAIL_TEST_KINDS||'ftir,raman,tem,xrd').split(','))for(const legacy of [false,true]){
      const name=kind+(legacy?'-fallback':'-native'),context=await browser.newContext({viewport:{width:1440,height:1000},...(legacy?{userAgent:'Mozilla/5.0 (Windows NT 5.1) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/49.0.2623.75 Safari/537.36'}:{})});
      const page=await context.newPage(),errors=[];page.setDefaultTimeout(15000);page.on('pageerror',e=>errors.push(e.message));
      let signature='',revision=0,doc=kind==='xrd'?structuredClone(fixture.document):{title:'메일 편집 테스트',completionDate:'2026-10-26',transfer:{},sections:[],auxiliary:{email_subject:'분석 결과',email_body:'분석 결과를 안내합니다.'},variables:[]};
      if(kind==='xrd')doc.email={subject:'분석 결과',body:'분석 결과를 안내합니다.',status:'manual',source_fingerprint:'',model_used:'',message:''};
      await page.route('**/*',async route=>{
        const r=route.request(),url=new URL(r.url()),p=url.pathname;
        const json=(data,status=200)=>route.fulfill({status,contentType:'application/json',body:JSON.stringify(data)});
        if(p==='/api/v1/report-mail/signature'){
          if(r.method()==='PUT')signature=r.postDataJSON().signature;
          return json({signature});
        }
        if(p.endsWith('/send'))throw Error('No real sends allowed');
        if(kind==='xrd'){
          if(p==='/__test__/xrd-editor'){
            let html=fs.readFileSync(path.join(web,'report.html'),'utf8').replace('<script type="module" src="/shared/shell.mjs"></script>','');
            if(legacy)html=html.replace('<script type="module" src="/report/report.mjs"></script>','<script src="/report/report.legacy.js"></script>').replace(/\.css"/g,'.legacy.css"');
            return route.fulfill({contentType:'text/html',body:html});
          }
          if(/^\/(report|shared|viewer)\/[a-z_]+\.(mjs|js|css|legacy\.js|legacy\.css)$/.test(p))return route.fulfill({contentType:p.endsWith('.css')?'text/css':'application/javascript',body:fs.readFileSync(path.join(web,p))});
          if(p==='/api/platform')return json({operatorId:doc.metadata.operator_id});
          if(p==='/api/presets')return json([]);
          if(p.endsWith('/draft'))return json({revision:1,document:null});
          if(p.endsWith('/draft/clear'))return json({revision:2,document:null});
          if(p.endsWith('/draft-preview'))return json({});
          if(p.endsWith('/checklist'))return json({samples:[],summary:{blockers:0,warnings:0}});
          if(p==='/api/reports/sample'){
            if(r.method()==='PUT'){doc=r.postDataJSON().document;doc.revision++;return json(doc);}
            return json({...fixture,document:doc});
          }
        }else if(p.startsWith('/api/v1/')){
          if(p.endsWith('/editor-data'))return json({document:doc});
          if(p.endsWith('/edit-layout'))return json({ratio:16/9,pages:[{id:'cover',title:'표지',regions:[]}],pageEditing:true,canvasEditing:true});
          if(p.includes('/edit-slides/'))return route.fulfill({contentType:'image/svg+xml',body:'<svg xmlns="http://www.w3.org/2000/svg" width="800" height="450"><rect width="800" height="450" fill="white"/></svg>'});
          if(p.endsWith('/revisions')){doc=r.postDataJSON();return json({jobId:'revision'+(++revision),status:'completed'});}
          return json({jobId:'revision'+revision,status:'completed'});
        }
        return route.continue();
      });
      const url=origin+(kind==='xrd'?'/__test__/xrd-editor?project=sample':'/__test__/request-editor/'+kind);
      async function open(){await page.goto(url);if(kind==='xrd')await page.locator('[data-tab="email"]').click();await page.getByLabel('수신자 이름',{exact:true}).waitFor();}
      async function save(){if(kind==='xrd'){await page.locator('#save').click();await page.waitForFunction(()=>/저장했습니다/.test(document.getElementById('status').textContent));}else await page.waitForFunction(()=>/자동 저장 완료/.test(document.getElementById('status').textContent));}
      try{
        await open();
        await page.getByLabel('수신자 이름',{exact:true}).fill('의뢰 담당자');await page.getByLabel('수신자 이메일 주소',{exact:true}).fill('to@example.com');await page.getByLabel('참조 (CC)',{exact:true}).fill('cc@example.com');await page.getByLabel('비밀참조 (BCC)',{exact:true}).fill('secret@example.com');
        await page.getByLabel('메일 서명',{exact:true}).fill('윤병훈\nRIST');await page.getByRole('button',{name:'내 기본 서명 저장',exact:true}).click();await page.getByText('내 기본 서명을 저장했습니다.',{exact:false}).waitFor();assert.equal(signature,'윤병훈\nRIST');
        const rich=page.getByRole('textbox',{name:'메일 본문 서식 편집',exact:true});await rich.fill('수정한 분석 결과입니다.');await rich.press('Meta+a');await page.getByRole('button',{name:'굵게',exact:true}).click();assert(await rich.locator('b,strong').count()>0);
        await page.getByRole('button',{name:'메일 글자 가운데 정렬',exact:true}).click();await save();
        const delivery=kind==='xrd'?doc.email.delivery:doc.deliveryEmail;assert.equal(delivery.bcc,'secret@example.com');assert.match(delivery.bodyHtml,/<(b|strong)>/);assert.equal(delivery.signature,'윤병훈\nRIST');
        await open();assert.equal(await page.getByLabel('비밀참조 (BCC)',{exact:true}).inputValue(),'secret@example.com');assert(await rich.locator('b,strong').count()>0);assert(await rich.locator('[align="center"]').count()>0);
        await rich.click();await rich.press('ControlOrMeta+End');
        await page.getByRole('button',{name:'메일 표 삽입 설정',exact:true}).click();
        await page.getByRole('button',{name:'2행 2열 표 삽입',exact:true}).click();
        assert.equal(await rich.locator('table tr').count(),2);
        await rich.locator('td').first().click();
        const align=await rich.locator('td').first().evaluate(c=>getComputedStyle(c).textAlign);
        if(align==='center')assert.equal(await page.getByRole('button',{name:'메일 글자 가운데 정렬',exact:true}).getAttribute('aria-pressed'),'true');
        await page.keyboard.type('Sample A');
        await page.getByRole('button',{name:'아래 행 추가',exact:true}).click();assert.equal(await rich.locator('tr').count(),3);
        await rich.locator('td').first().click();await page.getByRole('button',{name:'오른쪽 열 추가',exact:true}).click();assert.equal(await rich.locator('tr').first().locator('td').count(),3);
        await rich.locator('td').first().click();await page.getByRole('button',{name:'선택 열 삭제',exact:true}).click();assert.equal(await rich.locator('tr').first().locator('td').count(),2);
        await rich.locator('td').first().click();await page.getByRole('button',{name:'선택 행 삭제',exact:true}).click();assert.equal(await rich.locator('tr').count(),2);
        for(const [i,text] of ['시료','분석 결과','Sample A','3.2'].entries()){await rich.locator('td').nth(i).click();await page.keyboard.type(text);}
        await rich.locator('td').first().click();await rich.locator('td').last().click({modifiers:['Shift']});assert.equal(await rich.locator('[data-mail-selected]').count(),4);
        await page.getByRole('button',{name:'메일 글자 가운데 정렬',exact:true}).click();await page.getByRole('button',{name:'메일 셀 가운데 정렬',exact:true}).click();
        await page.getByLabel('메일 행 높이',{exact:true}).fill('72');await page.getByLabel('메일 행 높이',{exact:true}).press('Tab');
        await page.getByLabel('메일 셀 배경색',{exact:true}).fill('#e8f2fa');
        assert(await rich.locator('td').evaluateAll(list=>list.every(c=>c.style.textAlign==='center'&&c.style.verticalAlign==='middle')));
        await rich.locator('td').first().click();await page.getByLabel('메일 열 너비',{exact:true}).fill('60');await page.getByLabel('메일 열 너비',{exact:true}).press('Tab');
        const colHandle=page.getByRole('button',{name:'선택 열 너비 드래그 조절',exact:true}),rowHandle=page.getByRole('button',{name:'선택 행 높이 드래그 조절',exact:true});
        async function drag(handle,dx,dy,cancel=false){await handle.scrollIntoViewIfNeeded();const box=await handle.boundingBox();assert(box);await page.mouse.move(box.x+box.width/2,box.y+box.height/2);await page.mouse.down();await page.mouse.move(box.x+box.width/2+dx,box.y+box.height/2+dy,{steps:4});if(cancel)await page.keyboard.press('Escape');await page.mouse.up();}
        await drag(colHandle,40,0);const savedWidth=await rich.locator('td').first().evaluate(c=>parseFloat(c.style.width));assert(savedWidth>60);
        await drag(colHandle,-30,0,true);assert.equal(await rich.locator('td').first().evaluate(c=>parseFloat(c.style.width)),savedWidth);
        await drag(rowHandle,0,30);assert(await rich.locator('tr').first().evaluate(r=>parseFloat(r.style.height)>72));
        await rich.locator('td').first().click();await page.keyboard.press('Tab');assert.equal(await rich.locator('td').nth(1).getAttribute('data-mail-selected'),'');
        await page.keyboard.press('Escape');assert.equal(await rich.locator('[data-mail-selected]').count(),0);
        await rich.click();await rich.press('ControlOrMeta+End');
        await page.getByLabel('메일 이미지 파일',{exact:true}).setInputFiles({name:'graph.png',mimeType:'image/png',buffer:imageBytes});
        await rich.locator('img').waitFor();assert(await rich.locator('img').evaluate(e=>e.complete&&e.naturalWidth<=1280));
        assert(await rich.locator('img').evaluate(e=>e.src.length<350000));
        await rich.locator('img').click();await page.getByLabel('메일 이미지 표시 폭',{exact:true}).fill('320');await page.getByLabel('메일 이미지 표시 폭',{exact:true}).press('Tab');
        assert.equal(await rich.locator('img').getAttribute('width'),'320');await save();
        if(kind==='xrd')assert.equal(await page.getByLabel('메일 이미지 표시 폭',{exact:true}).isVisible(),false,'Reload must clear selection of replaced image nodes');
        await open();assert.equal(await rich.locator('table').count(),1);assert.equal(await rich.locator('img').count(),1);assert.equal(await rich.locator('img').getAttribute('width'),'320');
        assert(await rich.locator('td').evaluateAll(list=>list.every(c=>c.style.textAlign==='center'&&c.style.verticalAlign==='middle')));assert.equal(await rich.locator('td').first().evaluate(c=>parseFloat(c.style.width)),savedWidth);assert.match(await rich.locator('table').textContent(),/Sample A/);
        await page.getByLabel('메일 이미지 파일',{exact:true}).setInputFiles({name:'large.png',mimeType:'image/png',buffer:Buffer.alloc(5*1024*1024+1)});
        await page.getByText('원본 이미지는 5MB 이하로 선택하세요.',{exact:true}).waitFor();assert.equal(await rich.locator('img').count(),1);
        await page.getByLabel('메일 서명',{exact:true}).fill('');await page.getByRole('button',{name:'내 기본 서명 불러오기',exact:true}).click();await page.getByText('기본 서명을 현재 보고서에 적용했습니다.',{exact:true}).waitFor();assert.equal(await page.getByLabel('메일 서명',{exact:true}).inputValue(),'윤병훈\nRIST');
        for(const width of [1440,390]){
          await page.setViewportSize({width,height:1000});await rich.scrollIntoViewIfNeeded();
          if(width===390&&kind!=='xrd'&&await page.locator('#page-sidebar-close').isVisible())await page.locator('#page-sidebar-close').click();
          await rich.locator('td').first().click();
          assert(await page.locator('.mail-compose').evaluate(e=>e.scrollWidth<=e.clientWidth+2));
          assert(await page.locator('.mail-toolbar').first().evaluate(e=>e.scrollWidth<=e.clientWidth+2));
          assert(await page.locator('.mail-table-panel').evaluate(e=>e.scrollWidth<=e.clientWidth+2));
          await page.locator('.mail-compose').screenshot({path:path.join(output,name+'-'+width+'.png')});
        }
        await rich.locator('img').click();await page.getByRole('button',{name:'선택한 메일 이미지 삭제',exact:true}).click();assert.equal(await rich.locator('img').count(),0);
        await rich.locator('td').first().click();await page.getByRole('button',{name:'선택한 메일 표 삭제',exact:true}).click();assert.equal(await rich.locator('table').count(),0);
        assert.deepEqual(errors,[]);console.log(name+': mail fields, image/table insert/edit/delete, compression/limits, save/reopen and responsive layout passed');
      }catch(error){await page.screenshot({path:path.join(output,name+'-failure.png'),fullPage:true});throw error;}
      finally{await context.close();}
    }
  }finally{if(browser)await browser.close();server.kill();console.log('Artifacts: '+output);}
})().catch(error=>{console.error(error,logs.slice(-2000));process.exitCode=1;});
