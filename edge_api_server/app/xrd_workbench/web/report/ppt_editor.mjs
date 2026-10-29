// XRD adapter; the page sidebar, selection gestures, icons, cell editor and
// object tools are the same ES5 components used by FTIR/Raman/TEM.
export function mountPptEditor({host,document:documentValue,changed,linked,message,upload,editing}) {
  host.innerHTML=`<div class="actions"><span id="preview-panel-toggles" class="preview-panel-toggles"><button id="page-sidebar-toggle" hidden>페이지 목록</button></span><button id="ppt-prev">이전</button><select id="ppt-page" aria-label="PPT 페이지 선택"></select><button id="ppt-next">다음</button></div>
    <p id="page-status" role="status">PPT 미리보기 준비 중…</p><div id="canvas-tools" hidden></div>
    <div class="preview-workspace"><aside id="page-sidebar" class="page-sidebar" aria-label="보고서 페이지 목록" hidden><div class="page-sidebar-head"><strong>페이지</strong><button id="page-sidebar-close">닫기</button></div><p id="page-count" class="muted"></p>
    <div class="actions"><button id="page-add">추가</button><button id="page-delete">삭제</button><button id="page-restore">삭제 취소</button><button id="page-up">위로</button><button id="page-down">아래로</button></div><ol id="page-thumbnails" class="page-thumbnails"></ol></aside>
    <div class="slide-workspace"><div id="slide-stage"><img id="slide-image" alt="PPT 미리보기"><div id="edit-regions"></div><div id="canvas-layer" hidden></div></div></div></div>
    <details class="ppt-help"><summary>페이지 선택·편집 도움말</summary>Shift+클릭: 범위 선택 · Ctrl/Cmd+클릭: 개별 선택 · 목록에서 Ctrl/Cmd+A: 전체 선택 · 끌어서 순서 변경 · Alt+↑/↓. 자유 편집에서는 글자·표·이미지·도형을 수정합니다. 기본 화면의 테두리를 클릭하면 연결된 분석 입력 항목으로 이동합니다. 변경은 초안에 반영되며 최종 저장 후 다운로드·전송하세요.</details>`;
  const el=id=>window.document.getElementById(id);
  const progress=window.RistFileProgress.monitor(el('page-status'));
  let rendering=true,imagePending=false,imageSequence=0;
  progress.start();
  el('slide-image').hidden=true;
  let pages=[],index=0,locked=false,pending=false,nextId=null,nextSelection=null,canvasMode=false,deleted=[];
  const edits=()=>{const doc=documentValue();return doc.pageEdits||(doc.pageEdits={hidden:[],added:[],order:[]});};
  const sidebar=new window.RistPageSidebar({select:i=>show(i,true),move:reorder,changed:availability,step:move});
  const canvas=new window.RistCanvasEditor({changed:()=>{documentValue().canvasEdits=canvas.value();changed();},
    mode:value=>{canvasMode=value;el('edit-regions').hidden=value;},message,upload,editing});
  let templateLayout=null,templatePending=true;
  const templateHost=window.document.createElement('div');
  el('canvas-tools').parentNode.insertBefore(templateHost,el('canvas-tools'));
  const templates=new window.RistPptTemplates.Manager({host:templateHost,experiment:'XRD',
    layout:()=>templateLayout,state:()=>canvas.value(),apply:edits=>canvas.applyStyles(edits),
    upload:(file,done)=>canvas.uploadTemplateBackground(file,done)});
  function availability(){
    const blocked=locked||pending||!pages.length, selected=sidebar.selectedIds();
    canvas.lock(blocked);sidebar.lock(blocked,locked);
    templates.lock(blocked||rendering||templatePending);
    el('page-add').disabled=blocked||(edits().added||[]).length>=50||pages.length>=200;
    el('page-delete').disabled=blocked||!selected.length||selected.length>=pages.length;
    el('page-restore').disabled=locked||pending||!(edits().hidden||[]).length;
    el('page-up').disabled=blocked||!selected.length||pages.findIndex(p=>p.id===selected[0])<=0;
    el('page-down').disabled=blocked||!selected.length||pages.findIndex(p=>p.id===selected[selected.length-1])>=pages.length-1;
    el('ppt-prev').disabled=locked||index===0;el('ppt-next').disabled=locked||index===pages.length-1;
    el('ppt-page').disabled=locked||!pages.length;
  }
  function show(i,keepSelection=false){
    if(!pages.length)return;
    index=Math.max(0,Math.min(pages.length-1,i));const page=pages[index];
    el('ppt-page').value=String(index);
    el('page-status').textContent=`${index+1} / ${pages.length} · ${page.title}`;
    const image=el('slide-image'),sequence=++imageSequence;
    imagePending=true;progress.start();
    image.onload=()=>{if(sequence!==imageSequence)return;image.hidden=false;imagePending=false;if(!rendering)progress.stop();};
    image.onerror=()=>{if(sequence!==imageSequence)return;image.hidden=true;imagePending=false;progress.stop();el('page-status').textContent='PPT 페이지 이미지를 불러오지 못했습니다. 미리보기 다시 생성을 눌러 주세요.';};
    image.src=page.image;
    if(image.complete&&image.naturalWidth)image.onload();
    const layer=el('edit-regions');layer.textContent='';layer.hidden=canvasMode;
    for(const region of page.targets||[]) {
      const button=window.document.createElement('button');button.className='edit-region';button.type='button';
      button.style.cssText=`left:${region.x*100}%;top:${region.y*100}%;width:${region.w*100}%;height:${region.h*100}%`;
      button.setAttribute('aria-label','연결된 분석 입력 항목 수정');button.title='클릭: 연결된 분석 입력 항목';
      button.onclick=()=>{if(!locked)linked(region.target);};layer.appendChild(button);
    }
    sidebar.select(index,keepSelection);canvas.page(page);availability();
  }
  function order(){
    const list=(edits().order||[]).slice();let previous=null;
    for(const page of pages){if(!list.includes(page.id))list.splice(previous?list.indexOf(previous)+1:0,0,page.id);previous=page.id;}
    edits().order=list;return list;
  }
  function structureChanged(){pending=true;availability();changed();}
  function reorder(id,target,after){
    if(locked||pending||id===target)return;
    let selection=sidebar.selectedIds();if(!selection.includes(id))selection=[id];
    if(selection.includes(target))return;
    const list=order().filter(v=>!selection.includes(v)),at=list.indexOf(target)+(after?1:0);
    edits().order=list.slice(0,at).concat(selection,list.slice(at));nextId=id;structureChanged();
  }
  function move(delta){
    if(el(delta<0?'page-up':'page-down').disabled)return;
    const selected=sidebar.selectedIds(),at=pages.findIndex(p=>p.id===(delta<0?selected[0]:selected[selected.length-1]));
    reorder(selected.includes(pages[index].id)?pages[index].id:selected[0],pages[at+delta].id,delta>0);
  }
  for(const [id,icon,label] of [['page-add','add','페이지 추가'],['page-delete','trash','선택한 페이지 삭제'],['page-restore','undo','최근 삭제 취소'],['page-up','up','선택한 페이지 위로'],['page-down','down','선택한 페이지 아래로']])
    window.RistReportIcons(el(id),icon,label);
  el('page-add').onclick=()=>{
    if(el('page-add').disabled)return;
    const id='added-'+Date.now().toString(36)+'-'+Math.random().toString(36).slice(2,10),after=pages[index].id;
    const list=order();list.splice(list.indexOf(after)+1,0,id);
    edits().added.push({id,after,title:'추가 페이지',body:''});nextId=id;nextSelection=[id];structureChanged();
  };
  el('page-delete').onclick=()=>{
    if(el('page-delete').disabled)return;
    const selection=sidebar.selectedIds();
    if(!confirm(`선택한 ${selection.length}개 페이지를 보고서에서 삭제할까요? 원자료는 보존됩니다.`))return;
    edits().hidden=(edits().hidden||[]).concat(selection);deleted.push(selection);structureChanged();
  };
  el('page-restore').onclick=()=>{
    if(el('page-restore').disabled)return;
    const selection=deleted.pop()||[edits().hidden[edits().hidden.length-1]];
    edits().hidden=edits().hidden.filter(id=>!selection.includes(id));nextId=selection[0];nextSelection=selection;structureChanged();
  };
  el('page-up').onclick=()=>move(-1);el('page-down').onclick=()=>move(1);
  el('ppt-page').onchange=()=>show(Number(el('ppt-page').value));
  el('ppt-prev').onclick=()=>show(index-1);el('ppt-next').onclick=()=>show(index+1);
  host.addEventListener('compositionstart',()=>editing(true));host.addEventListener('compositionend',()=>editing(false));
  return {
    load(){canvas.load(documentValue().canvasEdits||{});},
    accept(value){
      templateLayout=value;templatePending=false;
      const selected=sidebar.selectedIds(),current=nextId||(pages[index]||{}).id;
      pages=value.pages||[];pending=false;el('ppt-page').textContent='';
      if(!pages.length){imagePending=false;imageSequence++;progress.stop();el('slide-image').removeAttribute('src');el('page-status').textContent='표시할 PPT 페이지가 없습니다.';}
      pages.forEach((p,i)=>{const option=window.document.createElement('option');option.value=String(i);option.textContent=`${i+1}. ${p.title}`;el('ppt-page').appendChild(option);});
      el('slide-stage').style.paddingTop=(100/value.ratio)+'%';
      sidebar.layout(value,'');canvas.layout(value);
      sidebar.setSelected(nextSelection||selected);
      const at=pages.findIndex(p=>p.id===current);show(at<0?index:at,sidebar.selectedIds().length>0);nextId=null;nextSelection=null;
    },
    lock(value){locked=value;if(documentValue())availability();},
    locate(target){const at=pages.findIndex(p=>(p.targets||[]).some(r=>r.target.tab===target.tab&&(!target.field||r.target.field===target.field)));if(at>=0)show(at);},
    invalidateTemplates(){templatePending=true;templates.lock(true);},
    rendering(value){rendering=value;if(value)progress.start('PPT 미리보기 생성 중…');else if(!imagePending)progress.stop();templates.lock(locked||pending||value||templatePending||!pages.length);},
    failed(message){rendering=false;pending=false;progress.stop();if(message)el('page-status').textContent=message;if(documentValue())availability();}
  };
}
