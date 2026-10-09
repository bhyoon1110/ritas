"""Responsive VOC board, kept separate from the experiment/Plotly workspaces."""
from __future__ import annotations

import json

from .auth import AuthContext
from .browser_support import with_browser_support_notice
from .voc import PROJECT_LABELS, STATUS_LABELS


def build_voc_page(context: AuthContext, project: str | None = None) -> str:
    config = {
        "userId": context.user_id, "admin": context.is_admin, "project": project,
        "projects": PROJECT_LABELS, "statuses": STATUS_LABELS,
        "allowedProjects": [code for code in PROJECT_LABELS if context.is_admin or code in context.projects],
    }
    encoded = json.dumps(config, ensure_ascii=True).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    return with_browser_support_notice(_PAGE.replace("__VOC_CONFIG__", encoded))


_PAGE = r'''<!doctype html>
<html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>RIST VOC · 불편사항 및 개선의견</title>
<style>
*{box-sizing:border-box}body{margin:0;background:#f5f7fb;color:#172b4d;font:15px/1.55 system-ui,-apple-system,sans-serif}
[hidden]{display:none!important}button,input,select,textarea{font:inherit}button,a,input,select,textarea{outline-offset:3px}
button,.button{display:inline-flex;align-items:center;justify-content:center;min-height:42px;border:1px solid #bdcadb;border-radius:7px;background:#fff;color:#263f61;padding:8px 14px;text-decoration:none;font-weight:650;cursor:pointer}
button:disabled{opacity:.5;cursor:not-allowed}.primary{background:#1769aa;color:#fff;border-color:#1769aa}
.top{display:flex;align-items:center;justify-content:space-between;gap:18px;flex-wrap:wrap;padding:20px 28px;background:#fff;border-bottom:1px solid #dbe3ed}
h1{font-size:24px;margin:0}h2{font-size:19px;margin:0 0 12px}p{margin:6px 0 12px}.muted{color:#63758b}.actions{display:flex;gap:8px;flex-wrap:wrap;align-items:center}
main{max-width:1220px;margin:auto;padding:24px}.panel{background:#fff;border:1px solid #dbe3ed;border-radius:10px;padding:20px;margin-bottom:18px;min-width:0}
.workflow{font-size:14px;color:#516982}.notice{padding:10px 14px;border-radius:7px;background:#eef7ff;color:#174c7b;white-space:pre-wrap;overflow-wrap:anywhere}.notice.error{background:#fff0ee;color:#a32b20}
.stats{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px;margin:18px 0}.stat{display:block;text-align:left;min-width:0;padding:14px;background:#fff}.stat strong{display:block;font-size:24px}.stat span{font-size:13px}.stat.active{border-color:#1769aa;background:#eef6ff}
.filters{display:grid;grid-template-columns:150px 220px minmax(160px,1fr) auto;gap:10px;align-items:end}.field{display:block;min-width:0}.field>span{display:block;font-size:13px;font-weight:650;margin-bottom:5px}
input,select,textarea{width:100%;min-width:0;border:1px solid #b9c7d8;border-radius:6px;padding:9px 11px;background:#fff;color:#172b4d}textarea{resize:vertical;min-height:140px}input[type=checkbox]{width:19px;height:19px;min-height:19px;flex:none;accent-color:#1769aa}
.form-grid{display:grid;grid-template-columns:180px minmax(0,1fr);gap:14px;margin:16px 0}.full{grid-column:1/-1}.form-footer{display:flex;justify-content:space-between;gap:12px;flex-wrap:wrap;align-items:center}.hint{font-size:13px;color:#63758b}
summary{cursor:pointer;font-weight:700;font-size:18px}.list-head{display:flex;justify-content:space-between;align-items:center;gap:12px;flex-wrap:wrap;margin:20px 0 10px}
#voc-list{display:grid;gap:10px}.voc-row{display:block;text-align:left;width:100%;min-width:0;padding:16px;border:1px solid #dbe3ed;border-radius:9px;background:#fff;line-height:1.55;font-weight:400}
.voc-row:hover{background:#f8fbff;border-color:#8daed2}.row-top{display:flex;gap:8px;align-items:center;flex-wrap:wrap}.row-title{display:block;font-size:16px;font-weight:700;margin:8px 0 4px;overflow-wrap:anywhere}.preview{display:block;color:#526780;overflow-wrap:anywhere}.row-meta{display:block;color:#63758b;font-size:12px;margin-top:8px}
.badge{display:inline-block;font-size:12px;font-weight:650;padding:3px 9px;border-radius:20px;background:#edf2f8;max-width:100%;overflow-wrap:anywhere}.status-IN_PROGRESS{background:#e7f1ff;color:#175b99}.status-RESOLVED{background:#fff0cc;color:#815700}.status-CONFIRMED{background:#e2f7e9;color:#16633b}
.empty{text-align:center;color:#63758b;padding:32px;border:1px dashed #c5d1df;border-radius:8px}.pagination{display:flex;justify-content:center;align-items:center;gap:14px;margin:20px 0}
dialog{width:min(780px,calc(100% - 24px));max-height:90vh;max-height:90dvh;border:1px solid #c9d5e4;border-radius:12px;padding:0;color:#172b4d;overflow:auto;box-shadow:0 12px 50px #172b4d33}dialog::backdrop{background:#10243b66}
.dialog-head{position:sticky;top:0;background:#fff;display:flex;justify-content:space-between;gap:12px;align-items:flex-start;padding:18px 22px;border-bottom:1px solid #dbe3ed;z-index:1}.dialog-head h2{margin:3px 0;overflow-wrap:anywhere;min-width:0}.close{flex:none;min-width:42px;font-size:20px;padding:3px}.dialog-body{padding:20px 22px}.metadata{display:flex;gap:8px;flex-wrap:wrap;color:#63758b;font-size:13px;margin:12px 0}
.content{white-space:pre-wrap;overflow-wrap:anywhere;background:#f6f8fb;border:1px solid #e1e8f0;border-radius:7px;padding:14px;margin:12px 0 22px}.resolution{background:#f0f8f3;border-color:#c8e3d1}
.section{border-top:1px solid #dbe3ed;padding-top:18px;margin-top:20px}.confirm-label{display:flex;align-items:center;gap:9px;margin-bottom:14px;font-weight:600}.events{padding-left:20px}.events li{padding:0 0 14px 5px}.event-note{white-space:pre-wrap;overflow-wrap:anywhere;margin-top:5px}.event-meta{font-size:12px;color:#63758b}.admin-form{display:grid;gap:12px}.admin-form .primary{justify-self:end}
@media(max-width:760px){.top{padding:16px}.top .actions{width:100%}main{padding:14px}.panel{padding:15px}.stats{grid-template-columns:repeat(2,minmax(0,1fr));gap:8px}.filters{grid-template-columns:1fr 1fr}.search-field{grid-column:1/-1}.filters button{grid-column:1/-1}.form-grid{grid-template-columns:1fr}.form-footer .primary{width:100%}.dialog-head{padding:14px}.dialog-body{padding:16px}.metadata{display:block}.metadata span{display:block}.voc-row{padding:13px}h1{font-size:21px}}
@media print{.top .actions,.filters,#create-panel,.pagination,.dialog-head .close,.admin-form,#confirm-form{display:none!important}}
</style></head><body>
<header class="top"><div><h1 id="page-title">VOC · 불편사항 및 개선의견</h1><p class="muted" id="scope-note"></p></div>
<nav class="actions" aria-label="VOC 메뉴"><a class="button" href="/">실험 선택</a><a class="button" href="/operations" id="admin-link" hidden>운영 관리</a><a class="button" href="/account">내 계정</a><button type="button" id="logout">로그아웃</button></nav></header>
<main>
<p class="workflow">접수 → 조치 중 → 조치 완료 → 작성자 확인 완료</p>
<div id="page-notice" class="notice" role="status" aria-live="polite" hidden></div>
<section class="stats" aria-label="VOC 상태별 건수" id="stats"></section>
<details class="panel" id="create-panel"><summary>새 VOC 등록</summary>
<p class="hint">등록한 내용과 조치 결과는 같은 실험 권한이 있는 회원과 관리자에게 공개됩니다. 비밀번호 등 인증 정보는 적지 마세요.</p>
<form id="create-form"><div class="form-grid">
<label class="field"><span>실험 페이지</span><select id="new-project" required></select></label>
<label class="field"><span>제목</span><input id="new-title" required maxlength="160" placeholder="어떤 불편이나 개선의견이 있나요?"></label>
<label class="field full"><span>내용</span><textarea id="new-content" required maxlength="10000" placeholder="발생 상황, 수행한 작업, 기대한 결과와 실제 결과 등을 남겨 주세요."></textarea></label>
</div><div class="form-footer"><span class="hint">최대 10,000자 · 조치 완료 후 확인 체크를 해 주세요.</span><button class="primary" type="submit" id="create-submit">VOC 등록</button></div></form></details>
<p id="no-project" class="notice" hidden>VOC를 조회하거나 등록하려면 관리자에게 실험 페이지 접근 권한을 요청해 주세요.</p>
<section aria-label="VOC 목록"><form class="panel filters" id="filter-form">
<label class="field"><span>실험</span><select id="project-filter"><option value="">모든 실험</option></select></label>
<label class="field"><span>상태</span><select id="status-filter"><option value="">모든 상태</option></select></label>
<label class="field search-field"><span>검색</span><input type="search" id="query" maxlength="200" placeholder="제목, 내용, 작성자 검색"></label><button type="submit">조회</button></form>
<div class="list-head"><h2 id="list-title">실험별 VOC</h2><span id="list-count" class="muted" role="status" aria-live="polite">조회 중…</span></div>
<div id="voc-list"></div><p id="empty" class="empty" hidden>조건에 맞는 VOC가 없습니다.</p>
<div class="pagination"><button type="button" id="prev" disabled>이전</button><span id="page-number">1</span><button type="button" id="next" disabled>다음</button></div></section>
</main>
<dialog id="detail" aria-labelledby="detail-title"><div class="dialog-head"><h2 id="detail-title">VOC 상세</h2><button class="close" type="button" id="close-detail" aria-label="VOC 상세 닫기">×</button></div>
<div class="dialog-body"><div class="notice" id="detail-notice" role="status" aria-live="polite" hidden></div>
<div id="detail-content" hidden><div class="row-top"><span class="badge" id="detail-project"></span><span class="badge" id="detail-status"></span><a class="button" id="experiment-link" target="_blank" rel="noopener">실험 페이지 열기</a></div>
<div class="metadata"><span id="detail-author"></span><span id="detail-created"></span></div>
<h3>등록 내용</h3><div class="content" id="detail-text"></div>
<section id="resolution-section" hidden><h3>관리자 조치 내용</h3><p class="muted" id="resolved-meta"></p><div class="content resolution" id="resolution-note"></div></section>
<p class="notice" id="confirmed-note" hidden></p>
<form class="section" id="confirm-form" hidden><label class="confirm-label"><input type="checkbox" id="confirm-check" required>조치 내용을 확인했습니다.</label><p class="hint">확인 완료 후에는 처리 기록이 확정됩니다.</p><button class="primary" type="submit" id="confirm-submit" disabled>확인 완료</button></form>
<section class="section" id="admin-section" hidden><h3>관리자 조치</h3><form class="admin-form" id="action-form">
<label class="field"><span>처리 상태</span><select id="action-status"><option value="OPEN">접수</option><option value="IN_PROGRESS">조치 중</option><option value="RESOLVED">조치 완료</option></select></label>
<label class="field"><span>조치 내용 · 같은 실험 권한 회원에게 공개</span><textarea id="action-note" maxlength="10000" placeholder="조치 내용이나 진행 상황을 남겨 주세요. 조치 완료 시 필수입니다."></textarea></label>
<button class="primary" type="submit" id="action-submit">조치 저장</button></form></section>
<section class="section"><h3>처리 이력</h3><ol class="events" id="events"></ol></section></div></div></dialog>
<script>
(function(){
'use strict';
const config=__VOC_CONFIG__, $=id=>document.getElementById(id);
let page=1, total=0, listVersion=0, detailVersion=0, current=null, currentId=null;
let createKey=null, createPayload=null;
const pageSize=20, dialog=$('detail');
function node(tag, text, cls){const el=document.createElement(tag);if(text!==undefined)el.textContent=text;if(cls)el.className=cls;return el;}
function option(select,value,label){const el=node('option',label);el.value=value;select.appendChild(el);}
function notice(id,message,error=false){const el=$(id);el.textContent=message||'';el.hidden=!message;el.classList.toggle('error',error);}
function date(value){return value?new Date(value).toLocaleString('ko-KR',{timeZone:'Asia/Seoul'}):'';}
function uuid(){const bytes=new Uint8Array(16);crypto.getRandomValues(bytes);bytes[6]=(bytes[6]&15)|64;bytes[8]=(bytes[8]&63)|128;const h=Array.from(bytes,b=>b.toString(16).padStart(2,'0')).join('');return h.slice(0,8)+'-'+h.slice(8,12)+'-'+h.slice(12,16)+'-'+h.slice(16,20)+'-'+h.slice(20);}
function busy(form,value){form.querySelectorAll('button,input,textarea,select').forEach(el=>{el.disabled=value;});}
async function api(url, options={}){
  const response=await fetch(url,{credentials:'same-origin',cache:'no-store',...options,headers:{'Content-Type':'application/json','X-Requested-With':'RIST-VOC',...(options.headers||{})}});
  let payload;try{payload=await response.json();}catch(_){throw new Error('서버 응답을 읽지 못했습니다. 잠시 후 다시 시도하세요.');}
  if(!response.ok){if(response.status===401)location.href='/login?returnTo='+encodeURIComponent(location.pathname+location.search);const error=new Error(payload.message||'요청을 처리하지 못했습니다.');error.status=response.status;throw error;}
  return payload;
}
Object.entries(config.projects).forEach(([code,label])=>{if(config.allowedProjects.includes(code)){option($('project-filter'),code,label);option($('new-project'),code,label);}});
Object.entries(config.statuses).forEach(([code,label])=>{
  option($('status-filter'),code,label);
  const card=node('button',undefined,'stat');card.type='button';card.dataset.state=code;card.append(node('strong','0'),node('span',label));
  card.addEventListener('click',()=>{$('status-filter').value=$('status-filter').value===code?'':code;page=1;loadList();});$('stats').appendChild(card);
});
if(config.project){$('project-filter').value=config.project;if(config.allowedProjects.includes(config.project))$('new-project').value=config.project;$('page-title').textContent=config.projects[config.project]+' VOC';$('create-panel').open=true;}
$('admin-link').hidden=!config.admin;$('list-title').textContent=config.admin?'전체 실험 VOC':'실험별 VOC';
$('scope-note').textContent=config.admin?'실험별 의견을 확인하고 조치 결과와 작성자 확인 상태를 관리합니다.':'승인된 실험의 의견과 조치 결과를 함께 확인합니다. 조치 완료 확인은 작성자 본인만 가능합니다.';
$('create-panel').hidden=config.allowedProjects.length===0;$('no-project').hidden=config.allowedProjects.length!==0;
async function loadList(){
  const token=++listVersion, params=new URLSearchParams({page:String(page),pageSize:String(pageSize)});
  for(const [key,id] of [['project','project-filter'],['status','status-filter'],['q','query']])if($(id).value.trim())params.set(key,$(id).value.trim());
  $('list-count').textContent='조회 중…';$('prev').disabled=true;$('next').disabled=true;
  try{
    const data=await api('/api/v1/voc?'+params);if(token!==listVersion)return;
    total=data.total;$('list-count').textContent=total+'건';$('voc-list').replaceChildren();$('empty').hidden=data.items.length>0;
    for(const item of data.items){
      const row=node('button',undefined,'voc-row');row.type='button';const badges=node('span',undefined,'row-top');
      badges.append(node('span',item.projectLabel,'badge'),node('span',item.statusLabel,'badge status-'+item.status));
      row.append(badges,node('span',item.title,'row-title'),node('span',item.content.slice(0,150)+(item.content.length>150?'…':''),'preview'),node('span',item.authorName+' · '+date(item.updatedAt),'row-meta'));
      row.addEventListener('click',()=>openDetail(item.vocId));$('voc-list').appendChild(row);
    }
    $('stats').querySelectorAll('.stat').forEach(card=>{card.querySelector('strong').textContent=data.counts[card.dataset.state]||0;card.classList.toggle('active',$('status-filter').value===card.dataset.state);});
    $('page-number').textContent=page+' / '+Math.max(1,Math.ceil(total/pageSize));$('prev').disabled=page<=1;$('next').disabled=page*pageSize>=total;
  }catch(error){if(token!==listVersion)return;$('list-count').textContent='조회 실패';notice('page-notice',error.message,true);}
}
function renderDetail(item){
  current=item;$('detail-title').textContent=item.title;$('detail-project').textContent=item.projectLabel;
  $('detail-status').textContent=item.statusLabel;$('detail-status').className='badge status-'+item.status;
  $('experiment-link').href=item.pageUrl;$('detail-author').textContent='작성자: '+item.authorName+' ('+item.authorLoginId+')';$('detail-created').textContent='등록: '+date(item.createdAt);
  $('detail-text').textContent=item.content;$('resolution-section').hidden=!item.resolutionNote;$('resolution-note').textContent=item.resolutionNote;
  $('resolved-meta').textContent=item.resolvedAt?(item.resolvedByName||'관리자')+' · '+date(item.resolvedAt):'진행 상황';
  $('confirmed-note').hidden=item.status!=='CONFIRMED';$('confirmed-note').textContent='작성자가 조치 내용을 확인했습니다. '+date(item.confirmedAt);
  $('confirm-form').hidden=item.status!=='RESOLVED'||item.authorUserId!==config.userId;$('confirm-check').checked=false;$('confirm-submit').disabled=true;
  $('admin-section').hidden=!config.admin||item.status==='CONFIRMED';$('action-status').value=item.status==='CONFIRMED'?'RESOLVED':item.status;$('action-note').value=item.resolutionNote||'';
  $('action-note').required=$('action-status').value==='RESOLVED';$('events').replaceChildren();
  for(const event of item.events||[]){const li=node('li');li.append(node('strong',event.statusLabel),node('div',event.actorName+' ('+event.actorLoginId+') · '+date(event.createdAt),'event-meta'));if(event.note)li.append(node('div',event.note,'event-note'));$('events').appendChild(li);}
  $('detail-content').hidden=false;
}
async function refreshDetail(id){
  // An older save may finish after the user opens another VOC. It must not
  // invalidate that newer detail request, even if the old record still exists.
  if(currentId!==id||!dialog.open)return false;
  const token=++detailVersion;
  const item=await api('/api/v1/voc/'+id);
  if(token!==detailVersion||currentId!==id||!dialog.open)return false;
  renderDetail(item);return true;
}
async function openDetail(id){
  currentId=id;current=null;$('detail-content').hidden=true;$('detail-title').textContent='VOC 상세';
  notice('detail-notice','내용을 불러오는 중…');if(!dialog.open)dialog.showModal();
  try{if(await refreshDetail(id))notice('detail-notice','');}
  catch(error){if(currentId===id&&dialog.open)notice('detail-notice',error.message,true);}
}
$('close-detail').addEventListener('click',()=>dialog.close());dialog.addEventListener('close',()=>{currentId=null;current=null;++detailVersion;});
$('filter-form').addEventListener('submit',event=>{event.preventDefault();page=1;notice('page-notice','');loadList();});
['project-filter','status-filter'].forEach(id=>$(id).addEventListener('change',()=>{page=1;loadList();}));
$('prev').addEventListener('click',()=>{if(page>1){page--;loadList();}});$('next').addEventListener('click',()=>{if(page*pageSize<total){page++;loadList();}});
$('create-form').addEventListener('submit',async event=>{
  event.preventDefault();const form=event.currentTarget;const body=JSON.stringify({project:$('new-project').value,title:$('new-title').value.trim(),content:$('new-content').value.trim()});
  if(body!==createPayload||!createKey){createKey=uuid();createPayload=body;}busy(form,true);notice('page-notice','');
  try{const item=await api('/api/v1/voc',{method:'POST',body,headers:{'Idempotency-Key':createKey}});form.reset();$('new-project').value=item.project;createKey=null;createPayload=null;$('project-filter').value=item.project;$('status-filter').value='';$('query').value='';page=1;notice('page-notice','VOC가 등록되었습니다. 관리자 조치 후 이 게시판에서 확인할 수 있습니다.');await loadList();await openDetail(item.vocId);}
  catch(error){notice('page-notice',error.message,true);}finally{busy(form,false);}
});
async function mutate(form,suffix,body){
  if(!current)return;const id=current.vocId;busy(form,true);notice('detail-notice','');
  try{await api('/api/v1/voc/'+id+'/'+suffix,{method:'POST',body:JSON.stringify(body)});await refreshDetail(id);await loadList();if(currentId===id)notice('detail-notice',suffix==='confirm'?'확인 완료되었습니다.':'조치 내용이 저장되었습니다.');}
  catch(error){if(error.status===409){try{await refreshDetail(id);await loadList();}catch(_){}}if(currentId===id)notice('detail-notice',error.message,true);}
  finally{busy(form,false);$('confirm-submit').disabled=!$('confirm-check').checked;}
}
$('action-status').addEventListener('change',()=>{$('action-note').required=$('action-status').value==='RESOLVED';});
$('action-form').addEventListener('submit',event=>{event.preventDefault();if(current)mutate(event.currentTarget,'status',{expectedVersion:current.version,status:$('action-status').value,note:$('action-note').value.trim()});});
$('confirm-check').addEventListener('change',()=>{$('confirm-submit').disabled=!$('confirm-check').checked;});
$('confirm-form').addEventListener('submit',event=>{event.preventDefault();if(current&&$('confirm-check').checked)mutate(event.currentTarget,'confirm',{expectedVersion:current.version});});
$('logout').addEventListener('click',async()=>{$('logout').disabled=true;try{await api('/api/v1/auth/logout',{method:'POST'});location.href='/login';}catch(error){$('logout').disabled=false;notice('page-notice',error.message,true);}});
loadList();
})();
</script></body></html>'''
