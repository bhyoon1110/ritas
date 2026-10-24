/* ES5, author-only object editing. Only outline repaint during dragging. */
(function () {
  "use strict";
  window.RistCanvasEditor = function (options) {
    var host=document.getElementById("canvas-tools"), layer=document.getElementById("canvas-layer"), stage=document.getElementById("slide-stage");
    var state={}, pages=[], page=null, selected=null, enabled=false, capable=false, locked=false, loading=false, drag=null;
    var undo=[], redo=[], snapshot=null, fields={}, palettes={}, controls=[], ratio=16/9, modeButton, list, props, undoButton, redoButton;
    var touchOptions=false;
    try { var probe=Object.defineProperty({},"passive",{get:function(){touchOptions={passive:false};}}),noop=function(){};
      window.addEventListener("rist-passive-probe",noop,probe);window.removeEventListener("rist-passive-probe",noop,probe);
    } catch (_) {}
    function copy(value) { return JSON.parse(JSON.stringify(value)); }
    function el(tag,parent,text) { var node=document.createElement(tag);if(text!=null)node.textContent=text;if(parent)parent.appendChild(node);return node; }
    function button(parent,text,fn) { var b=el("button",parent,text);b.type="button";b.onclick=fn;controls.push(b);return b; }
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
    function patch(values) {
      if(!page||!selected)return;
      var items=state[page.id]||(state[page.id]=[]),item=items.filter(function(v){return v.id===selected;})[0];
      if(!item){item={id:selected};items.push(item);}
      Object.keys(values).forEach(function(k){item[k]=values[k];});
    }
    function box(node,item) { node.style.left=item.x*100+"%";node.style.top=item.y*100+"%";node.style.width=item.w*100+"%";node.style.height=item.h*100+"%"; }
    function availability() {
      var blocked=locked||loading||!capable||!page;
      controls.forEach(function(b){b.disabled=blocked;});
      modeButton.disabled=!capable||!page||locked;
      Object.keys(fields).forEach(function(k){fields[k].disabled=blocked||!current();});
      Object.keys(palettes).forEach(function(k){palettes[k].disabled=blocked||!current();});
      undoButton.disabled=blocked||!undo.length;redoButton.disabled=blocked||!redo.length;
      document.getElementById("canvas-file").disabled=blocked;
    }
    function showProps() {
      var item=current();props.hidden=!enabled||!item;
      Object.keys(fields).forEach(function(k){
        var input=fields[k];if(!item)return;
        var numeric=["x","y","w","h"].indexOf(k)>=0;
        if(document.activeElement!==input) {
          if(input.type==="checkbox")input.checked=!!item[k];
          else input.value=numeric?Math.round(item[k]*1000)/10:(item[k]==null?"":item[k]);
        }
        input.parentNode.hidden=(k==="text"||k==="fontSize"||k==="bold"||k==="color"||k==="align")&&item.kind!=="text";
        if(palettes[k])palettes[k].value=/^#[0-9a-f]{6}$/i.test(item[k])?item[k]:"#172b4d";
      });
      availability();
    }
    function draw() {
      body.hidden=!enabled;layer.textContent="";layer.hidden=!enabled||!capable||!page||loading;
      list.textContent="";el("option",list,"개체 선택 (겹친 개체는 목록 사용)").value="";
      merged().forEach(function(item,index){
        var kindName={text:"글자",image:"이미지",rect:"사각형",ellipse:"타원",line:"선"}[item.kind]||"개체";
        var name=item.text||(item.id.indexOf("object-")===0?"추가 "+kindName:item.name)||"";
        var label=(index+1)+" · "+kindName+" · "+name.slice(0,45);
        el("option",list,label).value=item.id;
        var b=el("button",layer);b.type="button";b.className="canvas-object"+(item.id===selected?" selected":"");
        b.setAttribute("aria-label",label);b.title=label;box(b,item);b.dataset.objectId=item.id;
        b.onclick=function(){select(item.id);};b.ondblclick=function(){select(item.id);if(item.kind==="text")fields.text.focus();};
        if(item.id===selected){var handle=el("span",b,"↘");handle.className="canvas-resize";handle.setAttribute("aria-label","크기 조절");}
      });
      list.value=selected||"";showProps();
    }
    function select(id) { selected=id||null;draw(); }
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
      update[k]=value;begin();patch(update);finish();draw();
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
    var bar=el("div",host);bar.className="actions";
    modeButton=button(bar,"자유 편집 켜기",function(){
      enabled=!enabled;modeButton.textContent=enabled?"자유 편집 끄기":"자유 편집 켜기";
      modeButton.setAttribute("aria-pressed",String(enabled));options.mode(enabled);draw();
    });modeButton.id="canvas-toggle";modeButton.setAttribute("aria-pressed","false");
    var body=el("div",host);body.id="canvas-controls";body.hidden=true;
    var actions=el("div",body);actions.className="actions";
    [["text","글자 삽입"],["rect","사각형 삽입"],["ellipse","타원 삽입"],["line","선 삽입"]].forEach(function(v){button(actions,v[1],function(){add(v[0]);});});
    button(actions,"이미지 삽입 / 교체",function(){document.getElementById("canvas-file").click();});
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
    undoButton=button(actions,"개체 실행 취소",function(){history(undo,redo);});
    redoButton=button(actions,"개체 다시 실행",function(){history(redo,undo);});
    button(actions,"개체 삭제",remove);
    button(actions,"맨 앞으로",function(){if(current()){begin();patch({layer:"front"});finish();draw();}});
    button(actions,"맨 뒤로",function(){if(current()){begin();patch({layer:"back"});finish();draw();}});
    button(actions,"이 페이지 개체 편집 초기화",function(){if(page&&window.confirm("이 페이지의 개체 편집만 초기화할까요? 추가한 개체도 제거됩니다.")){begin();delete state[page.id];selected=null;finish();draw();}});
    el("p",body,"개체를 끌어 이동하고 오른쪽 아래 ↘로 크기를 조절하세요. 드래그 중에는 테두리로 표시하며 놓은 뒤 PPT를 갱신합니다. 자유 편집의 글자 수정은 이 페이지에만 적용되며 공통 정보·분석값은 바꾸지 않습니다. 배경·로고는 고정됩니다.").className="muted";
    list=el("select",body);list.setAttribute("aria-label","페이지 개체 선택");list.onchange=function(){select(list.value);};
    props=el("div",stage.parentNode);props.className="canvas-properties";props.hidden=true;
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
      if(cancel){snapshot=null;draw();return;}
      if(held.changed){patch(held.values);finish();}else snapshot=null;
      draw();
    }
    function abort(){stop(null,true);}
    function move(event){
      if(!drag)return;if(event.cancelable)event.preventDefault();var p=point(event),dx=(p.clientX-drag.x)/drag.rect.width,dy=(p.clientY-drag.y)/drag.rect.height;
      var v=copy(drag.item);
      if(drag.resize){v.w=Math.max(.01,Math.min(1-v.x,v.w+dx));v.h=Math.max(.01,Math.min(1-v.y,v.h+dy));
        if(v.kind==="image"||event.shiftKey){var aspect=drag.item.w/drag.item.h;v.h=v.w/aspect;if(v.y+v.h>1){v.h=1-v.y;v.w=v.h*aspect;}}}
      else {v.x=Math.max(0,Math.min(1-v.w,v.x+dx));v.y=Math.max(0,Math.min(1-v.h,v.y+dy));}
      drag.changed=Math.abs(dx)+Math.abs(dy)>.001;drag.values={x:v.x,y:v.y,w:v.w,h:v.h};box(drag.node,v);
    }
    function start(event){
      if(!enabled||locked||loading||drag||(event.type==="mousedown"&&event.button!==0))return;
      var target=event.target,resize=target.classList.contains("canvas-resize");if(resize)target=target.parentNode;
      if(!target.dataset.objectId)return;
      if(event.cancelable)event.preventDefault();var id=target.dataset.objectId;select(id);
      var p=point(event);begin();drag={x:p.clientX,y:p.clientY,rect:stage.getBoundingClientRect(),item:copy(current()),resize:resize,
        node:layer.querySelector('[data-object-id="'+id+'"]'),changed:false};
      document.addEventListener("mousemove",move);document.addEventListener("mouseup",stop);
      document.addEventListener("touchmove",move,touchOptions);document.addEventListener("touchend",stop);document.addEventListener("touchcancel",abort);
    }
    layer.addEventListener("mousedown",start);layer.addEventListener("touchstart",start,touchOptions);window.addEventListener("blur",abort);
    document.addEventListener("visibilitychange",function(){if(document.hidden)abort();});
    layer.addEventListener("keydown",function(e){
      if(!current()||locked||loading)return;
      if(e.keyCode===46||e.keyCode===8){e.preventDefault();remove();return;}
      if(e.keyCode>=37&&e.keyCode<=40){e.preventDefault();var item=current(),step=e.shiftKey ? .01 : .002,values={};
        if(e.keyCode===37||e.keyCode===39)values.x=Math.max(0,Math.min(1-item.w,item.x+(e.keyCode===37?-step:step)));
        else values.y=Math.max(0,Math.min(1-item.h,item.y+(e.keyCode===38?-step:step)));
        begin();patch(values);finish();draw();var active=layer.querySelector('[data-object-id="'+selected+'"]');if(active)active.focus();}
    });
    return {
      load:function(value){state=copy(value||{});undo=[];redo=[];selected=null;},
      value:function(){return copy(state);},
      layout:function(value){capable=value.canvasEditing===true;pages=value.pages||[];ratio=value.ratio||16/9;host.hidden=!capable;loading=false;},
      page:function(value){abort();if(!page||!value||page.id!==value.id)selected=null;page=value;body.hidden=!enabled;draw();},
      lock:function(value){locked=value;if(value)abort();availability();},
      loading:function(){loading=true;abort();layer.hidden=true;availability();},
      active:function(){return enabled;}
    };
  };
}());
