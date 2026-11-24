const {test}=require('node:test');
const assert=require('node:assert/strict');
const {execFileSync}=require('node:child_process');
const {createHash}=require('node:crypto');
const fs=require('node:fs'),path=require('node:path');
const {JSDOM,VirtualConsole}=require('jsdom');
const acorn=require('acorn');
const pages=JSON.parse(execFileSync(process.env.RIST_TEST_PYTHON||'python3',['-c',`
import json
from datetime import datetime, timedelta
from pathlib import Path
from app.auth import AuthContext, _account_page, _admin_page
from app.config import Settings
c=AuthContext(user_id='alice',login_id='alice',email=None,display_name='시험 사용자',status='ACTIVE',session_id='s',session_expires_at=datetime.utcnow()+timedelta(hours=8),sso_authenticated_at=datetime.utcnow(),projects=frozenset({'XRD'}),roles=frozenset({'REPORT_SENDER'}),sso_identity={'employee_id':'0a0516'})
print(json.dumps({'account':_account_page(c,Settings(storage_root=Path('/tmp/sso-ui-fixture'),sso_mode='lims',edge_public_base_url='https://edge.test')),'admin':_admin_page()}))
`],{encoding:'utf8'}));
const assetRoot=path.resolve(__dirname,'../app/static/chrome49');
const manifest=JSON.parse(fs.readFileSync(path.join(assetRoot,'manifest.json'),'utf8'));
const flush=async()=>{await new Promise(setImmediate);await new Promise(setImmediate);};
function fixture(t,name,mode){
 const errors=[],calls=[];let confirmed=true,policy=30;
 let me={ssoLinked:true,ssoRecentlyAuthenticated:true,ssoValidMinutes:30,ssoExpiresAt:'2026-11-25T09:00:00Z'};
 const members=['alice','bob'].map(userId=>({userId,loginId:userId,displayName:userId,status:'ACTIVE',projects:['XRD'],roles:['REPORT_SENDER'],sso:{active:true,employee_id:userId,last_authenticated_at:'2026-11-24T09:00:00Z'}}));
 const vc=new VirtualConsole();vc.on('jsdomError',e=>errors.push(e.message));
 const dom=new JSDOM(pages[name],{url:'https://edge.test/'+(name==='account'?'account':'admin/users'),runScripts:'outside-only',virtualConsole:vc});
 t.after(()=>dom.window.close());const w=dom.window,d=w.document;
 w.confirm=()=>confirmed;w.HTMLElement.prototype.scrollIntoView=function(){};
 w.fetch=async(url,options={})=>{
  calls.push({url,options});const method=options.method||'GET';let body={};
  if(url==='/api/v1/auth/me')body=me;
  else if(url==='/api/v1/auth/sso/reset'){assert.equal(options.headers['X-Requested-With'],'RIST-Account');me={...me,ssoLinked:false,ssoRecentlyAuthenticated:false,ssoExpiresAt:null};body={reset:true};}
  else if(url==='/api/v1/auth/sso/verify'){me={...me,ssoLinked:true,ssoRecentlyAuthenticated:true};body={authenticated:true,returnTo:'/account'};}
  else if(url==='/api/v1/auth/admin/sso-policy'){
   if(method==='PATCH'){assert.equal(options.headers['X-Requested-With'],'RIST-Admin');policy=JSON.parse(options.body).validMinutes;}
   body={validMinutes:policy};
  }else if(url==='/api/v1/auth/admin/users')body={items:members};
  else if(url.endsWith('/sso/reset')){assert.equal(options.headers['X-Requested-With'],'RIST-Admin');const user=members.find(x=>url.includes('/'+x.userId+'/'));assert(user);user.sso={...user.sso,active:false,last_authenticated_at:null};body={reset:true};}
  else throw Error('Unexpected URL '+url);
  return {ok:true,json:async()=>body};
 };
 const source=Array.from(d.scripts).find(s=>s.textContent.includes(name==='account'?'const profileForm=':"const projects=['FTIR'")).textContent.trim();
 let program=source;
 if(mode==='chrome49'){const key=createHash('sha256').update(source).digest('hex');assert(manifest.scripts[key],name+': rebuild compatibility assets');program=fs.readFileSync(path.join(assetRoot,manifest.scripts[key]),'utf8');}
 acorn.parse(program,{ecmaVersion:mode==='chrome49'?5:2022});w.eval(program);
 return {w,d,calls,errors,members,setConfirm:v=>confirmed=v};
}
for(const mode of ['native','chrome49']){
 test(mode+' account: self reset keeps local page and immediately updates persistent status',async t=>{
  const ui=fixture(t,'account',mode);await flush();
  ui.setConfirm(false);ui.d.getElementById('sso-reset').click();await flush();assert(!ui.calls.some(x=>x.url.endsWith('/reset')));
  ui.setConfirm(true);ui.d.getElementById('sso-reset').click();await flush();
  assert.equal(ui.d.getElementById('sso-link-state').textContent,'연결 안 됨');
  assert.equal(ui.d.getElementById('sso-valid-state').textContent,'재인증 필요');
  assert.match(ui.d.getElementById('sso-reset-message').textContent,/초기화했습니다/);
  assert.equal(ui.w.location.pathname,'/account');assert.deepEqual(ui.errors,[]);
 });
 test(mode+' account: successful verification keeps its success message and clears password',async t=>{
  const ui=fixture(t,'account',mode);await flush();const form=ui.d.getElementById('sso-form');form.elements.password.value='test-only';
  form.dispatchEvent(new ui.w.Event('submit',{bubbles:true,cancelable:true}));await flush();
  assert.equal(form.elements.password.value,'');assert.equal(ui.d.getElementById('sso-message').textContent,'SSO 인증을 완료했습니다.');
  assert.equal(ui.d.getElementById('sso-valid-state').textContent,'유효');assert.deepEqual(ui.errors,[]);
 });
 test(mode+' admin: save policy, reset individual and selected members',async t=>{
  const ui=fixture(t,'admin',mode);await flush();
  assert.equal(ui.d.getElementById('sso-policy-minutes').value,'30');ui.d.getElementById('sso-policy-minutes').value='480';
  ui.d.getElementById('sso-policy-form').dispatchEvent(new ui.w.Event('submit',{bubbles:true,cancelable:true}));await flush();
  assert.match(ui.d.getElementById('sso-policy-message').textContent,/480분/);
  ui.d.querySelector('[data-member-row] .row-edit').click();ui.d.getElementById('drawer-sso-reset').click();await flush();
  assert.equal(ui.members[0].sso.active,false);assert.match(ui.d.getElementById('drawer-sso').textContent,/연결 중지/);
  ui.d.getElementById('drawer-close').click();const all=ui.d.getElementById('select-all-members');all.checked=true;all.dispatchEvent(new ui.w.Event('change'));
  assert.equal(ui.d.getElementById('bulk-sso-reset').disabled,false);ui.d.getElementById('bulk-sso-reset').click();await flush();await flush();
  assert(ui.members.every(x=>!x.sso.active));assert.match(ui.d.getElementById('message').textContent,/2명/);assert.deepEqual(ui.errors,[]);
 });
}
