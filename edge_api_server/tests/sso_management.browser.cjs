// Actual account/admin screens against isolated mocks. No external authentication or mail.
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),os=require('node:os');
const {execFileSync}=require('node:child_process'),{chromium}=require('playwright');
const repo=path.resolve(__dirname,'../..'),assetRoot=path.resolve(__dirname,'../app/static/chrome49');
const pages=JSON.parse(execFileSync(process.env.RIST_TEST_PYTHON||'python3',['-c',`
import json, os
from datetime import datetime, timedelta
from pathlib import Path
from app.auth import AuthContext, _account_page, _admin_page
from app.config import Settings
from app.browser_compat import compatible_html
settings=Settings(storage_root=Path('/tmp/sso-ui-fixture'),sso_mode='lims',edge_public_base_url='https://edge.test')
def account(admin=False):
 c=AuthContext(user_id='alice',login_id='alice',email=None,display_name='시험 사용자',status='ACTIVE',session_id='s',session_expires_at=datetime.utcnow()+timedelta(hours=8),sso_authenticated_at=datetime.utcnow(),projects=frozenset({'XRD'}),roles=frozenset({'ADMIN'} if admin else {'REPORT_SENDER'}),sso_identity={'employee_id':'0a0516'})
 return _account_page(c,settings)
pages={'account':account(),'adminAccount':account(True),'admin':_admin_page()}
print(json.dumps({'native':pages,**({} if os.environ.get('RIST_SSO_NATIVE_ONLY') else {'chrome49':{key:compatible_html(html) for key,html in pages.items()}})}))
`],{cwd:path.join(repo,'edge_api_server'),env:{...process.env,PYTHONDONTWRITEBYTECODE:'1',PYTHONPATH:[repo,path.join(repo,'common'),path.join(repo,'edge_api_server'),process.env.PYTHONPATH||''].join(path.delimiter)},encoding:'utf8'}));
const output=fs.mkdtempSync(path.join(os.tmpdir(),'rist-sso-browser-'));
(async()=>{
 let browser;
 try{
  browser=await chromium.launch({headless:true,executablePath:process.env.RIST_CHROME_EXECUTABLE||'/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'});
  for(const mode of Object.keys(pages))for(const width of [1440,390]){
   const context=await browser.newContext({viewport:{width,height:1000},locale:'ko-KR',...(mode==='chrome49'?{userAgent:'Mozilla/5.0 (Windows NT 5.1) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/49.0.2623.75 Safari/537.36'}:{})});
   const page=await context.newPage(),errors=[],calls=[];page.setDefaultTimeout(10000);page.on('pageerror',e=>errors.push(e.message));
   let confirm=true,policy=30,verifyCount=0;
   let me={ssoLinked:true,ssoRecentlyAuthenticated:true,ssoValidMinutes:30,ssoExpiresAt:'2026-11-25T09:00:00Z'};
   const members=['alice','bob'].map(userId=>({userId,loginId:userId,displayName:userId==='alice'?'시험 사용자':'시험 관리자',email:null,status:'ACTIVE',projects:['XRD'],roles:userId==='alice'?['REPORT_SENDER']:['ADMIN'],sso:{provider:'lims',active:true,employee_id:userId,last_authenticated_at:'2026-11-24T09:00:00Z'}}));
   page.on('dialog',dialog=>confirm?dialog.accept():dialog.dismiss());
   await page.route('**/*',async route=>{
    const request=route.request(),url=new URL(request.url()),p=url.pathname,method=request.method();
    const json=(value,status=200)=>route.fulfill({status,contentType:'application/json',body:JSON.stringify(value)});
    if(p==='/account'||p==='/admin-account'||p==='/admin/users')return route.fulfill({contentType:'text/html',body:pages[mode][p==='/account'?'account':p==='/admin-account'?'adminAccount':'admin']});
    if(p.startsWith('/assets/browser-compat/')){const file=path.join(assetRoot,path.basename(p));return route.fulfill({contentType:p.endsWith('.css')?'text/css':'application/javascript',body:fs.readFileSync(file)});}
    if(p==='/favicon.ico')return route.fulfill({status:204,body:''});
    calls.push({url:p,method,body:request.postData()});
    if(p==='/api/v1/auth/me')return json(me);
    if(p==='/api/v1/auth/sso/reset'){assert.equal(request.headers()['x-requested-with'],'RIST-Account');me={...me,ssoLinked:false,ssoRecentlyAuthenticated:false,ssoExpiresAt:null};return json({reset:true});}
    if(p==='/api/v1/auth/sso/verify'){assert.equal(request.headers()['x-requested-with'],'RIST-Account');verifyCount++;me={...me,ssoLinked:true,ssoRecentlyAuthenticated:true,ssoExpiresAt:'2026-11-25T09:00:00Z'};return json({authenticated:true,returnTo:'/account'});}
    if(p==='/api/v1/auth/admin/sso-policy'){
     if(method==='PATCH'){assert.equal(request.headers()['x-requested-with'],'RIST-Admin');policy=request.postDataJSON().validMinutes;}
     return json({validMinutes:policy});
    }
    if(p==='/api/v1/auth/admin/users')return json({items:members});
    if(p.endsWith('/sso/reset')){assert.equal(request.headers()['x-requested-with'],'RIST-Admin');const member=members.find(x=>p.includes('/'+x.userId+'/'));assert(member);member.sso={...member.sso,active:false,last_authenticated_at:null};return json({reset:true});}
    throw Error('Unexpected browser request '+request.url());
   });
   async function noOverflow(selector='html'){
    const metrics=await page.locator(selector).evaluate(e=>({client:e.clientWidth,scroll:e.scrollWidth}));
    if(metrics.scroll>metrics.client+2)console.error('Overflow elements:',await page.evaluate(()=>Array.from(document.querySelectorAll('body *')).map(e=>({tag:e.tagName,id:e.id,cls:e.className,right:Math.round(e.getBoundingClientRect().right),position:getComputedStyle(e).position,overflow:getComputedStyle(e).overflow})).filter(x=>x.right>innerWidth+2&&x.position!=='static').slice(0,10)));
    assert(metrics.scroll<=metrics.client+2,mode+' '+width+' '+selector+' horizontal overflow: '+JSON.stringify(metrics));
   }
   const name=mode+'-'+width;
   if(!process.env.RIST_SSO_ADMIN_ONLY){
   await page.goto('https://edge.test/account');
   await page.waitForFunction(()=>document.getElementById('sso-expires-at').textContent.includes('2026'));
   assert.equal(await page.locator('#sso-valid-minutes').textContent(),'30');
   assert.equal(await page.getByRole('link',{name:'회원·권한 관리',exact:true}).count(),0);
   assert.equal(await page.locator('#sso-policy-form').count(),0);
   await page.locator('#sso-auth').scrollIntoViewIfNeeded();await noOverflow();await noOverflow('#sso-auth');
   await page.screenshot({path:path.join(output,name+'-account.png'),fullPage:true});
   const authButton=page.getByRole('button',{name:'SSO 인증',exact:true});
   await page.getByLabel('SSO 비밀번호',{exact:true}).fill('isolated-test-only');await authButton.click();
   await page.waitForFunction(()=>document.getElementById('sso-message').textContent==='SSO 인증을 완료했습니다.');
   assert.equal(await page.getByLabel('SSO 비밀번호',{exact:true}).inputValue(),'');
   assert.equal(await page.locator('#sso-valid-state').textContent(),'유효');
   await page.waitForTimeout(1200);assert.equal(await page.locator('#sso-message').textContent(),'SSO 인증을 완료했습니다.');
   confirm=false;await page.locator('#sso-reset').click();assert(!calls.some(c=>c.url==='/api/v1/auth/sso/reset'));
   confirm=true;await page.locator('#sso-reset').click();await page.waitForFunction(()=>document.getElementById('sso-valid-state').textContent==='재인증 필요');
   assert.equal(await page.locator('#sso-expires-at').textContent(),'인증 필요');assert.equal(new URL(page.url()).pathname,'/account');
   assert.equal(await authButton.isEnabled(),true,'Self reset must permit immediate authentication on the same page');
   await page.getByLabel('SSO 비밀번호',{exact:true}).fill('isolated-test-again');await authButton.click();
   await page.waitForFunction(()=>document.getElementById('sso-valid-state').textContent==='유효');assert.equal(verifyCount,2);
   await page.goto('https://edge.test/admin-account');assert.equal(await page.getByRole('link',{name:'회원·권한 관리',exact:true}).count(),1);
   }
   await page.goto('https://edge.test/admin/users');await page.waitForFunction(()=>document.getElementById('sso-policy-duration').value==='30');
   assert.equal(await page.locator('#bulk-sso-reset').isEnabled(),false);
   await noOverflow();await noOverflow('#sso-policy-form');await noOverflow('#bulk-toolbar');
   await page.screenshot({path:path.join(output,name+'-admin.png'),fullPage:true});
   const policyInput=page.getByLabel('SSO 인증 유효기간',{exact:true});
   await policyInput.fill('0');await page.locator('#sso-policy-save').click();assert.equal(calls.filter(c=>c.url.endsWith('/sso-policy')&&c.method==='PATCH').length,0);
   await policyInput.fill('480');await page.locator('#sso-policy-save').click();await page.waitForFunction(()=>document.getElementById('sso-policy-message').textContent.includes('8시간'));
   assert.equal(policy,480);
   const policyUnit=page.getByLabel('SSO 인증 유효기간 단위',{exact:true});
   assert.equal(await policyInput.inputValue(),'8');assert.equal(await policyUnit.inputValue(),'hours');
   await policyUnit.selectOption('days');await policyInput.fill('31');await page.locator('#sso-policy-save').click();assert.equal(policy,480);
   await policyInput.fill('7');await page.locator('#sso-policy-save').click();await page.waitForFunction(()=>document.getElementById('sso-policy-message').textContent.includes('7일'));assert.equal(policy,10080);
   await page.reload();await page.waitForFunction(()=>document.getElementById('sso-policy-duration').value==='7');assert.equal(await policyUnit.inputValue(),'days');
   await noOverflow('#sso-policy-form');await page.screenshot({path:path.join(output,name+'-weekly-policy.png')});
   await policyUnit.selectOption('hours');await policyInput.fill('2');await page.locator('#sso-policy-save').click();await page.waitForFunction(()=>document.getElementById('sso-policy-message').textContent.includes('2시간'));assert.equal(policy,120);

   await page.locator('[data-member-row]').first().click();await page.locator('#member-drawer').waitFor();
   await noOverflow('#member-drawer');assert.equal(await page.getByLabel('보고서 전송 권한',{exact:true}).isChecked(),true);
   await page.locator('#drawer-sso-reset').scrollIntoViewIfNeeded();await page.screenshot({path:path.join(output,name+'-drawer.png')});
   confirm=false;await page.locator('#drawer-sso-reset').click();assert(members[0].sso.active);
   confirm=true;await page.locator('#drawer-sso-reset').click();await page.waitForFunction(()=>document.getElementById('drawer-sso').textContent.includes('연결 중지'));assert(!members[0].sso.active);
   await page.locator('#drawer-close').click();await page.getByRole('checkbox',{name:'검색 결과 전체 선택',exact:true}).check();
   assert.equal(await page.locator('#bulk-sso-reset').isEnabled(),true);await page.locator('#bulk-sso-reset').click();
   await page.waitForFunction(()=>document.getElementById('message').textContent.includes('2명의 SSO 인증을 초기화'));
   assert(members.every(x=>!x.sso.active));assert.deepEqual(errors,[]);
   console.log(name+': '+(process.env.RIST_SSO_ADMIN_ONLY?'admin checks':'account expiry/self reset/re-auth and permission controls')+', policy bounds, individual/bulk reset and layout passed');
   await context.close();
  }
 }finally{if(browser)await browser.close();console.log('Artifacts: '+output);}
})().catch(error=>{console.error(error);process.exitCode=1;});
