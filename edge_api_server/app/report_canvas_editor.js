/* ES5, author-only object editing. Only outline repaint during dragging. */
(function () {
  "use strict";
  window.RistCanvasEditor = function (options) {
    var host=document.getElementById("canvas-tools"), layer=document.getElementById("canvas-layer"), stage=document.getElementById("slide-stage");
    var state={}, pages=[], page=null, selected=null, enabled=false, capable=false, locked=false, loading=false, drag=null;
    var undo=[], redo=[], snapshot=null, fields={}, palettes={}, controls=[], ratio=16/9, modeButton, list, props, undoButton, redoButton;
    var cellChoice, cellKey="", tableBox, legacyNote;
    var cellEditor=new window.RistCellEditor({editing:options.editing});
    var touchOptions=false;
    try { var probe=Object.defineProperty({},"passive",{get:function(){touchOptions={passive:false};}}),noop=function(){};
      window.addEventListener("rist-passive-probe",noop,probe);window.removeEventListener("rist-passive-probe",noop,probe);
    } catch (_) {}
    function copy(value) { return JSON.parse(JSON.stringify(value)); }
    function el(tag,parent,text) { var node=document.createElement(tag);if(text!=null)node.textContent=text;if(parent)parent.appendChild(node);return node; }
    function button(parent,text,fn,icon) { var b=el("button",parent);b.type="button";b.onclick=fn;controls.push(b);return window.RistReportIcons(b,icon||"edit",text); }
    function begin() { snapshot=copy(state); }
    function finish() {
      if (JSON.stringify(snapshot)===JSON.stringify(state)) return;
      undo.push(snapshot);if(undo.length>30)undo.shift();redo=[];snapshot=null;options.changed();availability();
    }
    function patches() { return page ? (state[page.id] || []) : []; }
    function merged() {
      if(!page)return [];
      var values=(page.objects||[]).map(copy), ids={};values.forEach(function(v){ids[v.id]=v;});
      patches().forEach(function(p){
        if(!ids[p.id]&&p.id.indexOf("object-")!==0)return;
        if(!ids[p.id]) { ids[p.id]={id:p.id,name:"추가 개체",kind:p.kind,x:.1,y:.2,w:.3,h:.15,text:"",fontSize:20,bold:false,
          color:"#172B4D",fill:"none",stroke:"#172B4D",lineWidth:1,dash:"solid",align:"left"};values.push(ids[p.id]); }
        Object.keys(p).forEach(function(k){ids[p.id][k]=p[k];});
      });
      return values.filter(function(v){return !v.hidden;});
    }
    function current() { return merged().filter(function(v){return v.id===selected;})[0]; }
    function tableCells(item) {
      var values=(item.cells||[]).map(copy);
      (item.tableCells||[]).forEach(function(p){values.forEach(function(c){
        if(c.row===p.row&&c.col===p.col)Object.keys(p).forEach(function(k){if(p[k]!=null)c[k]=p[k];});
      });});
      return values;
    }
    function chosenCell(item) { return tableCells(item).filter(function(c){return c.row+":"+c.col===cellKey;})[0]; }
    function editableCells(item) { return tableCells(item).filter(function(c){return isFinite(c.x)&&isFinite(c.y)&&c.w>0&&c.h>0;}); }
    function cellPatch(item,cell,key,value) {
      var cells=copy(item.tableCells||[]),found=null;
      cells.forEach(function(c){if(c.row===cell.row&&c.col===cell.col)found=c;});
      if(!found){found={row:cell.row,col:cell.col};cells.push(found);}
      found[key]=value;return {tableCells:cells};
    }
    function editCell(id,row,col) {
      if(locked||loading||!enabled)return;
      cellEditor.close(false);selected=id;cellKey=row+":"+col;draw();
      var item=current(),cell=chosenCell(item);if(!cell)return;
      begin();
      cellEditor.open({text:cell.text,label:"PPT 표 셀 수정 · "+(row+1)+"행 "+(col+1)+"열",
        rect:function(){var table=current(),c=table&&chosenCell(table);return c?{x:table.x+c.x*table.w,y:table.y+c.y*table.h,w:c.w*table.w,h:c.h*table.h}:null;},
        change:function(value){patch(cellPatch(current(),cell,"text",value));showProps();options.changed();},
        // Keep hit targets alive through an outside mousedown -> click so a
        // single click on the next cell both commits this one and opens it.
        end:function(cancel){if(cancel){state=snapshot;snapshot=null;options.changed();}else finish();showProps();},
        next:function(delta){var cells=editableCells(current()),index=-1;
          cells.forEach(function(c,i){if(c.row===row&&c.col===col)index=i;});
          var next=cells[index+delta];if(next)editCell(id,next.row,next.col);}
      });
    }
    function patch(values) {
      if(!page||!selected)return;
      var items=state[page.id]||(state[page.id]=[]),item=items.filter(function(v){return v.id===selected;})[0];
      if(!item){item={id:selected};items.push(item);}
      Object.keys(values).forEach(function(k){item[k]=values[k];});
    }
    function box(node,item) { node.style.left=item.x*100+"%";node.style.top=item.y*100+"%";node.style.width=item.w*100+"%";node.style.height=item.h*100+"%"; }
    function availability() {
      var blocked=locked||loading||!capable||!page, hasSelection=!blocked&&!!current();
      controls.forEach(function(b){b.disabled=blocked;});
      modeButton.disabled=!capable||!page||locked;
      Object.keys(fields).forEach(function(k){fields[k].disabled=!hasSelection;});
      Object.keys(palettes).forEach(function(k){palettes[k].disabled=!hasSelection;});
      undoButton.disabled=blocked||!undo.length;redoButton.disabled=blocked||!redo.length;
      document.getElementById("canvas-file").disabled=blocked;
      cellChoice.disabled=!hasSelection;
    }
    function showProps() {
      var item=current();props.hidden=!enabled||!item;
      tableBox.hidden=!item||item.kind!=="table";
      if(item&&item.kind==="table") {
        var cells=tableCells(item);cellChoice.textContent="";
        cells.forEach(function(c){el("option",cellChoice,(c.row===0?"머리글":c.row+"행")+" · "+(c.col+1)+"열 · "+(c.text||"빈 셀").slice(0,35)).value=c.row+":"+c.col;});
        if(!chosenCell(item))cellKey=cells.length?cells[0].row+":"+cells[0].col:"";
        cellChoice.value=cellKey;
      }
      Object.keys(fields).forEach(function(k){
        var input=fields[k];if(!item)return;
        var numeric=["x","y","w","h"].indexOf(k)>=0;
        var valueItem=item.kind==="table"&&!numeric?(chosenCell(item)||{}):item;
        if(document.activeElement!==input) {
          if(input.type==="checkbox")input.checked=!!valueItem[k];
          else input.value=numeric?Math.round(valueItem[k]*1000)/10:(valueItem[k]==null?"":valueItem[k]);
        }
        input.parentNode.hidden=(k==="text"||k==="fontSize"||k==="bold"||k==="color"||k==="align")&&item.kind!=="text"&&item.kind!=="table";
        if(palettes[k])palettes[k].value=/^#[0-9a-f]{6}$/i.test(valueItem[k])?valueItem[k]:"#172b4d";
      });
      availability();
    }
    function draw() {
      modeButton.setAttribute("aria-expanded",String(enabled&&capable));
      body.hidden=!enabled;layer.textContent="";layer.hidden=!enabled||!capable||!page||loading;
      legacyNote.hidden=!page||!page.legacyTableLayout;
      list.textContent="";el("option",list,"개체 선택 (겹친 개체는 목록 사용)").value="";
      merged().forEach(function(item,index){
        var kindName={text:"글자",image:"이미지",rect:"사각형",ellipse:"타원",line:"선",table:"표"}[item.kind]||"개체";
        var name=item.text||(item.id.indexOf("object-")===0?"추가 "+kindName:item.name)||"";
        var label=(index+1)+" · "+kindName+" · "+name.slice(0,45);
        el("option",list,label).value=item.id;
        var b=el(item.kind==="table"?"div":"button",layer);if(item.kind!=="table")b.type="button";else {b.tabIndex=0;b.setAttribute("role","group");}
        b.className="canvas-object"+(item.id===selected?" selected":"");
        b.setAttribute("aria-label",label);b.title=label;box(b,item);b.dataset.objectId=item.id;
        b.onclick=function(){select(item.id);};b.ondblclick=function(){select(item.id);if(item.kind==="text")fields.text.focus();else if(item.kind==="table")cellChoice.focus();};
        if(item.kind==="table") {
          editableCells(item).forEach(function(c){
            var hit=el("button",b);hit.type="button";hit.className="canvas-cell";box(hit,c);
            hit.dataset.row=c.row;hit.dataset.col=c.col;
            hit.setAttribute("aria-label",label+" · "+(c.row+1)+"행 "+(c.col+1)+"열 · "+(c.text||"빈 셀"));
            hit.title="클릭: 셀 내용 입력";
            hit.onclick=function(e){e.stopPropagation();editCell(item.id,c.row,c.col);};
            hit.ondblclick=function(e){e.stopPropagation();};
          });
          var mover=el("span",b,"↔");mover.className="canvas-move";mover.title="드래그: 표 전체 이동";
          mover.setAttribute("aria-label","표 전체 이동");
        }
        if(item.id===selected){var handle=el("span",b,"↘");handle.className="canvas-resize";handle.setAttribute("aria-label","크기 조절");handle.title="드래그: 자유 크기 조절 · Shift+드래그: 현재 가로세로 비율 유지";}
      });
      list.value=selected||"";showProps();
    }
    function select(id) { if(selected!==id)cellKey="";selected=id||null;draw(); }
    function change(k,input) {
      if(locked||loading||!current())return;
      var item=current(), value=input.type==="checkbox"?input.checked:input.value,update={};
      if(["x","y","w","h"].indexOf(k)>=0){
        value=Number(value)/100;if(!isFinite(value))return;
        if(k==="x")value=Math.max(0,Math.min(1-item.w,value));
        if(k==="y")value=Math.max(0,Math.min(1-item.h,value));
        if(k==="w")value=Math.max(.002,Math.min(1-item.x,value));
        if(k==="h")value=Math.max(.002,Math.min(1-item.y,value));
      }else if(k==="fontSize"||k==="lineWidth"){
        value=Number(value);if(!isFinite(value)||value<Number(input.min)||value>Number(input.max))return;
      }
      if((k==="fill"||k==="stroke")&&!/^(none|#[0-9a-f]{6})$/i.test(value)){options.message("색은 팔레트에서 선택하거나 #RRGGBB 또는 none으로 입력하세요.",true);return;}
      if(item.kind==="table"&&["x","y","w","h"].indexOf(k)<0) {
        var cell=chosenCell(item);if(!cell)return;
        update=cellPatch(item,cell,k,value);
      } else update[k]=value;
      begin();patch(update);finish();draw();
    }
    function field(label,key,type,choices) {
      var wrap=el("label",props,label),input=el(choices?"select":type==="textarea"?"textarea":"input",wrap);
      if(choices)choices.forEach(function(v){el("option",input,v[1]).value=v[0];});else if(type!=="textarea")input.type=type;
      input.setAttribute("aria-label","개체 "+label);fields[key]=input;
      if(type==="textarea"){input.maxLength=4000;input.rows=3;wrap.style.flexBasis="100%";}
      input.addEventListener(type==="textarea"?"input":"change",function(){change(key,input);});
      return input;
    }
    function add(kind) {
      if(locked||loading||!page||patches().length>=100){options.message("페이지당 편집 개체 한도를 확인하세요.",true);return;}
      begin();selected="object-"+Date.now().toString(36)+"-"+Math.random().toString(36).slice(2,9);
      patch({kind:kind,x:.15,y:.25,w:.35,h:kind==="line" ? .01 : .18,text:kind==="text"?"새 텍스트":"",fontSize:20,
        color:"#172B4D",fill:"none",stroke:kind==="text"?"none":"#172B4D",lineWidth:1.5,dash:"solid",bold:false,align:"left"});
      finish();draw();if(kind==="text")fields.text.focus();
    }
    function remove() {if(!current()||locked||loading)return;begin();patch({hidden:true});selected=null;finish();draw();}
    function history(from,to) {if(locked||loading||!from.length)return;to.push(copy(state));state=from.pop();selected=null;options.changed();draw();}
    function canInsertGraph() { return capable && !!page && !locked && !loading && patches().length < 100; }
    function insertGraph(file, position, done) {
      if (!canInsertGraph()) { done(new Error("페이지를 선택하고 저장·미리보기 갱신이 끝난 뒤 추가하세요. 페이지당 개체는 100개까지 가능합니다.")); return; }
      if (!file || file.type !== "image/png" || !file.size || file.size > 16*1024*1024) {
        done(new Error("저장 그래프는 16MB 이하 PNG여야 합니다.")); return;
      }
      var pageId=page.id;
      options.upload(file,function(error,result){
        if(error){done(error);return;}
        if(!page||page.id!==pageId||loading){done(new Error("페이지가 변경되었습니다. 원하는 페이지에서 다시 추가하세요."));return;}
        var w=.65,h=w*ratio*result.height/result.width;
        if(h>.7){h=.7;w=h/ratio*result.width/result.height;}
        var x=position ? position.x-w/2 : (1-w)/2, y=position ? position.y-h/2 : .18;
        begin();selected="object-"+Date.now().toString(36)+"-"+Math.random().toString(36).slice(2,9);
        // Always add, never replace the currently selected graph/image.
        patch({kind:"image",asset:result.asset,x:Math.max(0,Math.min(1-w,x)),y:Math.max(0,Math.min(1-h,y)),w:w,h:h,stroke:"none"});
        enabled=true;window.RistReportIcons(modeButton,"edit","자유 편집 끄기");modeButton.setAttribute("aria-pressed","true");options.mode(true);
        finish();draw();done(null);
      },true);
    }
    var bar=document.getElementById("preview-panel-toggles");
    modeButton=button(bar,"자유 편집 켜기",function(){
      cellEditor.close(false);
      enabled=!enabled;window.RistReportIcons(modeButton,"edit",enabled?"자유 편집 끄기":"자유 편집 켜기");
      modeButton.setAttribute("aria-pressed",String(enabled));options.mode(enabled);draw();
    });modeButton.id="canvas-toggle";modeButton.hidden=true;modeButton.setAttribute("aria-pressed","false");
    modeButton.setAttribute("aria-controls","canvas-controls");modeButton.setAttribute("aria-expanded","false");
    var body=el("div",host);body.id="canvas-controls";body.hidden=true;
    var actions=el("div",body);actions.className="actions";actions.setAttribute("role","group");actions.setAttribute("aria-label","PPT 자유편집 도구");
    function group(label){var g=el("div",actions);g.className="canvas-tool-group";g.setAttribute("role","group");g.setAttribute("aria-label",label);return g;}
    var insertTools=group("글자·도형·이미지 삽입"),historyTools=group("실행 취소·다시 실행"),objectTools=group("개체 관리");
    [["text","글자 삽입"],["rect","사각형 삽입"],["ellipse","타원 삽입"],["line","선 삽입"]].forEach(function(v){button(insertTools,v[1],function(){add(v[0]);},v[0]);});
    button(insertTools,"이미지 삽입 / 교체",function(){document.getElementById("canvas-file").click();},"image");
    var file=el("input",body);file.id="canvas-file";file.type="file";file.accept="image/png,image/jpeg,image/webp";file.hidden=true;
    file.onchange=function(){
      var chosen=file.files&&file.files[0], target=current(),pageId=page&&page.id;file.value="";
      if(!chosen||locked||loading)return;
      if((!target||target.kind!=="image")&&patches().length>=100){options.message("페이지당 편집 개체 한도(100개)에 도달했습니다.",true);return;}
      if(chosen.size>2*1024*1024){options.message("이미지는 2MB 이하 PNG/JPEG/WebP를 선택하세요.",true);return;}
      options.upload(chosen,function(error,result){
        if(error){options.message(error.message,true);return;}
        if(!page||page.id!==pageId){options.message("페이지가 변경되었습니다. 원래 페이지에서 다시 첨부하세요.",true);return;}
        begin();
        if(target&&target.kind==="image")selected=target.id;
        else {selected="object-"+Date.now().toString(36)+"-image";var w=.4,h=w*ratio*result.height/result.width;
          if(h>.65){h=.65;w=h/ratio*result.width/result.height;}
          patch({kind:"image",x:.1,y:.2,w:w,h:h,stroke:"none"});}
        patch({asset:result.asset});finish();draw();
      });
    };
    undoButton=button(historyTools,"개체 실행 취소",function(){history(undo,redo);},"undo");
    redoButton=button(historyTools,"개체 다시 실행",function(){history(redo,undo);},"redo");
    button(objectTools,"개체 삭제",remove,"trash");
    button(objectTools,"맨 앞으로",function(){if(current()){begin();patch({layer:"front"});finish();draw();}},"front");
    button(objectTools,"맨 뒤로",function(){if(current()){begin();patch({layer:"back"});finish();draw();}},"back");
    button(objectTools,"이 페이지 개체 편집 초기화",function(){if(page&&window.confirm("이 페이지의 개체 편집만 초기화할까요? 추가한 개체도 제거됩니다.")){begin();delete state[page.id];selected=null;finish();draw();}},"reset");
    el("p",body,"개체를 끌어 이동하고 오른쪽 아래 ↘로 크기를 조절하세요. 기본 드래그는 가로·세로 자유 조절, Shift+드래그는 현재 비율 유지입니다. 드래그 중 Shift를 누르거나 떼면 즉시 전환되며 Esc로 취소합니다. 모바일은 자유 조절입니다. 드래그 중에는 테두리로 표시하며 놓은 뒤 PPT를 갱신합니다. 자유 편집의 글자 수정은 이 페이지에만 적용되며 공통 정보·분석값은 바꾸지 않습니다. 배경·로고는 고정됩니다.").className="muted";
    legacyNote=el("p",body,"기존 셀별 글자·서식은 유지했습니다. 따로 이동·숨김 처리했던 셀은 실제 표의 행·열에 맞춰 정렬됩니다. 이전 보고서 파일은 변경되지 않습니다.");legacyNote.className="muted";legacyNote.hidden=true;
    list=el("select",body);list.setAttribute("aria-label","페이지 개체 선택");list.onchange=function(){select(list.value);};
    props=el("div",stage.parentNode);props.className="canvas-properties";props.hidden=true;
    tableBox=el("div",props);tableBox.style.flexBasis="100%";tableBox.style.minWidth="0";tableBox.hidden=true;
    var cellLabel=el("label",tableBox,"편집할 표 셀");cellChoice=el("select",cellLabel);cellChoice.setAttribute("aria-label","편집할 표 셀");
    cellChoice.onchange=function(){cellKey=cellChoice.value;showProps();};
    el("p",tableBox,"미리보기의 셀을 클릭해 바로 입력하세요. ↔는 표 전체 이동, ↘는 크기 조절입니다. 자유 편집은 이 페이지의 표만 바꿉니다. 공통 정보·분석값까지 바꾸려면 자유 편집을 끄고 셀을 클릭하세요.").className="muted";
    field("내용","text","textarea");
    [["왼쪽 (%)","x"],["위 (%)","y"],["너비 (%)","w"],["높이 (%)","h"]].forEach(function(v){var input=field(v[0],v[1],"number");input.min=0;input.max=100;input.step=.1;});
    var font=field("글자 크기 (pt)","fontSize","number");font.min=8;font.max=72;font.step=1;
    field("굵게","bold","checkbox");field("글자색","color","color");
    field("정렬","align","select",[["left","왼쪽"],["center","가운데"],["right","오른쪽"]]);
    field("채우기 색 (none: 없음)","fill","text");field("테두리 색 (none: 없음)","stroke","text");
    ["fill","stroke"].forEach(function(key){var pick=el("input",fields[key].parentNode);pick.type="color";pick.value="#172b4d";
      palettes[key]=pick;
      pick.setAttribute("aria-label",key==="fill"?"채우기 팔레트":"테두리 팔레트");pick.onchange=function(){fields[key].value=pick.value;change(key,fields[key]);};});
    var width=field("선 굵기 (pt)","lineWidth","number");width.min=.25;width.max=12;width.step=.25;
    field("선 종류","dash","select",[["solid","실선"],["dash","점선"],["dot","점선 (점)"],["dashDot","일점쇄선"]]);
    function point(event){var touch=event.touches&&event.touches[0];return touch||event;}
    function stop(event,cancel){
      if(!drag)return;var held=drag;drag=null;document.removeEventListener("mousemove",move);document.removeEventListener("mouseup",stop);
      document.removeEventListener("touchmove",move,touchOptions);document.removeEventListener("touchend",stop);document.removeEventListener("touchcancel",abort);
      document.removeEventListener("keydown",dragKey);document.removeEventListener("keyup",dragKey);
      if(cancel){snapshot=null;draw();return;}
      if(held.changed){patch(held.values);finish();}else snapshot=null;
      draw();
    }
    function abort(){stop(null,true);}
    function dragKey(event){
      if(!drag)return;
      if(event.key==="Escape"||event.keyCode===27){event.preventDefault();event.stopPropagation();abort();return;}
      if(drag.resize&&(event.key==="Shift"||event.keyCode===16))move({clientX:drag.lastX,clientY:drag.lastY,shiftKey:event.type==="keydown"});
    }
    function move(event){
      if(!drag)return;if(event.cancelable)event.preventDefault();var p=point(event),dx=(p.clientX-drag.x)/drag.rect.width,dy=(p.clientY-drag.y)/drag.rect.height;
      drag.lastX=p.clientX;drag.lastY=p.clientY;
      var v=copy(drag.item);
      if(drag.resize){
        if(event.shiftKey&&v.w>0&&v.h>0){
          // Use the larger relative gesture so vertical-only dragging scales too.
          var sx=dx/v.w,sy=dy/v.h,scale=1+(Math.abs(sx)>=Math.abs(sy)?sx:sy);
          scale=Math.min(Math.min((1-v.x)/v.w,(1-v.y)/v.h),Math.max(Math.max(.002/v.w,.002/v.h),scale));
          v.w*=scale;v.h*=scale;
        }else{v.w=Math.min(1-v.x,Math.max(.002,v.w+dx));v.h=Math.min(1-v.y,Math.max(.002,v.h+dy));}
      }
      else {v.x=Math.max(0,Math.min(1-v.w,v.x+dx));v.y=Math.max(0,Math.min(1-v.h,v.y+dy));}
      drag.changed=Math.abs(v.x-drag.item.x)+Math.abs(v.y-drag.item.y)+Math.abs(v.w-drag.item.w)+Math.abs(v.h-drag.item.h)>.000001;
      drag.values={x:v.x,y:v.y,w:v.w,h:v.h};box(drag.node,v);
    }
    function start(event){
      if(!enabled||locked||loading||drag||(event.type==="mousedown"&&event.button!==0))return;
      var target=event.target,resize=target.classList.contains("canvas-resize");if(resize)target=target.parentNode;
      if(target.classList.contains("canvas-move"))target=target.parentNode;
      if(!target.dataset.objectId)return;
      if(event.cancelable)event.preventDefault();var id=target.dataset.objectId;select(id);
      var p=point(event);begin();drag={x:p.clientX,y:p.clientY,rect:stage.getBoundingClientRect(),item:copy(current()),resize:resize,
        node:layer.querySelector('[data-object-id="'+id+'"]'),changed:false,lastX:p.clientX,lastY:p.clientY};
      document.addEventListener("mousemove",move);document.addEventListener("mouseup",stop);
      document.addEventListener("touchmove",move,touchOptions);document.addEventListener("touchend",stop);document.addEventListener("touchcancel",abort);
      document.addEventListener("keydown",dragKey);document.addEventListener("keyup",dragKey);
    }
    layer.addEventListener("mousedown",start);layer.addEventListener("touchstart",start,touchOptions);window.addEventListener("blur",abort);
    document.addEventListener("visibilitychange",function(){if(document.hidden)abort();});
    layer.addEventListener("keydown",function(e){
      if(e.target.classList.contains("canvas-cell"))return;
      if(!current()||locked||loading||drag)return;
      if(e.keyCode===46||e.keyCode===8){e.preventDefault();remove();return;}
      if(e.keyCode>=37&&e.keyCode<=40){e.preventDefault();var item=current(),step=e.shiftKey ? .01 : .002,values={};
        if(e.keyCode===37||e.keyCode===39)values.x=Math.max(0,Math.min(1-item.w,item.x+(e.keyCode===37?-step:step)));
        else values.y=Math.max(0,Math.min(1-item.h,item.y+(e.keyCode===38?-step:step)));
        begin();patch(values);finish();draw();var active=layer.querySelector('[data-object-id="'+selected+'"]');if(active)active.focus();}
    });
    return {
      load:function(value){cellEditor.close(false);state=copy(value||{});undo=[];redo=[];selected=null;},
      value:function(){return copy(state);},
      layout:function(value){capable=value.canvasEditing===true;pages=value.pages||[];ratio=value.ratio||16/9;host.hidden=!capable;modeButton.hidden=!capable;modeButton.setAttribute("aria-expanded",String(enabled&&capable));loading=false;},
      page:function(value){abort();if(!page||!value||page.id!==value.id){cellEditor.close(false);selected=null;}page=value;body.hidden=!enabled;draw();cellEditor.position();},
      lock:function(value){locked=value;if(value){abort();cellEditor.close(false);}availability();},
      loading:function(){loading=true;abort();layer.hidden=true;availability();},
      active:function(){return enabled;},
      canInsertGraph:canInsertGraph,insertGraph:insertGraph,pageId:function(){return page&&page.id;}
    };
  };
}());
