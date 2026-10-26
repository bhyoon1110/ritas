/* Bounded table editing for all four mail composers; no external dependencies. */
(function () {
  "use strict";
  window.RistMailTables = function (o) {
    var rich=o.rich, node=o.node, button=o.button, active=null, anchor=null, cells=[], multi=false, drag=null;
    var wrap=node("div");wrap.className="mail-rich-wrap";
    rich.parentNode.insertBefore(wrap,rich);wrap.appendChild(rich);
    var panel=node("div");panel.className="mail-toolbar mail-table-panel";panel.hidden=true;
    wrap.parentNode.insertBefore(panel,wrap);
    var insertPane=node("div",panel),editPane=node("div",panel);
    var status=node("p",editPane);status.className="mail-note";status.setAttribute("role","status");
    var style=document.getElementById("rist-mail-table-styles")||node("style",document.head);style.id="rist-mail-table-styles";
    style.textContent=".mail-rich-wrap{position:relative;min-width:0}.mail-table-panel{padding:8px;border:1px solid #a8bdcd;border-radius:6px;background:#f4f8fb}.mail-table-panel>div{width:100%;min-width:0}.mail-table-group{display:flex;flex-wrap:wrap;align-items:center;margin:4px 0}.mail-table-group label{display:inline-flex;align-items:center;margin:3px}.mail-table-group input{width:75px;margin:0 4px}.mail-table-group input[type=color]{width:36px;height:32px;padding:2px}.mail-table-panel .mail-note{margin:3px 0}.mail-table-grid{display:flex;flex-wrap:wrap;width:208px;max-width:100%;margin:4px 0}.mail-table-grid button{flex:0 0 24px;width:24px;min-width:24px;height:24px;min-height:24px;padding:0;margin:1px;border:1px solid #9baeba;border-radius:2px;background:white}.mail-table-grid button[data-on]{background:#b5d8ef;border-color:#276b95}.mail-rich [data-mail-selected]{outline:2px solid #2780b0;outline-offset:-2px}.mail-toolbar button[aria-pressed=true]{background:#d6eafa;color:#153e59;border-color:#2877a6}.mail-resize-handle{position:absolute;z-index:2;min-width:0!important;min-height:0!important;padding:0!important;margin:0!important;border:1px solid #fff!important;background:#23759f!important;border-radius:3px!important;touch-action:none}.mail-resize-col{width:10px!important;height:28px!important;cursor:col-resize}.mail-resize-row{width:28px!important;height:10px!important;cursor:row-resize}";

    function allowed(){return o.enabled()&&!drag;}
    function group(parent){var g=node("div",parent);g.className="mail-table-group";return g;}
    function field(parent,text,label,min,max,value){
      var l=node("label",parent,text),f=node("input",l);f.type="number";f.min=min;f.max=max;f.value=value;
      f.setAttribute("aria-label",label);return f;
    }
    function show(inserting){o.onShow();panel.hidden=false;insertPane.hidden=!inserting;editPane.hidden=inserting;}
    function table(){return active&&rich.contains(active)?active.closest("table"):null;}
    style.textContent+=".mail-resize-table{width:18px!important;height:18px!important;cursor:nwse-resize;color:white!important;font:14px/16px Arial!important}.mail-table-group strong{margin:3px 6px 3px 0;font-size:13px}";
    function clearSelection(){cells.forEach(function(c){c.removeAttribute("data-mail-selected");});cells=[];active=anchor=null;colHandle.hidden=rowHandle.hidden=tableHandle.hidden=true;window.removeEventListener("resize",positionHandles);}
    function reset(){if(drag)endDrag(false);clearSelection();panel.hidden=true;multi=false;multiButton.setAttribute("aria-pressed","false");}
    function coordinates(c){return [c.parentNode.rowIndex,c.cellIndex];}
    function select(c,extend){
      if(!c||!rich.contains(c)||!o.enabled())return;
      var t=c.closest("table"),first=extend&&anchor&&anchor.closest("table")===t?anchor:c;
      cells.forEach(function(x){x.removeAttribute("data-mail-selected");});cells=[];anchor=first;active=c;
      var a=coordinates(first),b=coordinates(c);
      for(var r=Math.min(a[0],b[0]);r<=Math.max(a[0],b[0]);r++)
        for(var col=Math.min(a[1],b[1]);col<=Math.max(a[1],b[1]);col++)if(t.rows[r].cells[col])cells.push(t.rows[r].cells[col]);
      cells.forEach(function(x){x.setAttribute("data-mail-selected","");});window.addEventListener("resize",positionHandles);show(false);refresh();
    }
    function refresh(){
      var t=table();if(!t){reset();return;}
      status.textContent=cells.length+"개 셀 선택 · Shift+클릭: 범위 선택 · 모바일: 겹친 셀 아이콘";
      rowHeight.value=parseInt(active.parentNode.style.height,10)||Math.round(active.parentNode.getBoundingClientRect().height)||36;
      colWidth.value=Math.round(widths(t)[active.cellIndex]*100)/100;
      tableWidth.value=parseFloat(t.style.width||t.getAttribute("width"))||100;
      tableHeight.min=t.rows.length*24;tableHeight.max=t.rows.length*400;
      tableHeight.value=Math.round(t.getBoundingClientRect().height)||rowHeights(t).reduce(function(a,b){return a+b;},0);
      hButtons.forEach(function(item){var same=cells.every(function(c){var align=c.style.textAlign||window.getComputedStyle(c).textAlign||"left";if(align==="start")align="left";if(align==="end")align="right";return align===item.value;});item.button.setAttribute("aria-pressed",String(same));});
      vButtons.forEach(function(item){var same=cells.every(function(c){return (c.style.verticalAlign||"top")===item.value;});item.button.setAttribute("aria-pressed",String(same));});
      positionHandles();
    }
    function change(){o.changed();refresh();}
    function setCellAlignment(value){
      cells.forEach(function(c){c.style.textAlign=value;c.setAttribute("align",value);
        Array.prototype.forEach.call(c.querySelectorAll("p,div,li"),function(p){p.style.textAlign=value;p.removeAttribute("align");});});
    }
    var hButtons=[],vButtons=[];
    [["왼쪽","left","justifyLeft"],["가운데","center","justifyCenter"],["오른쪽","right","justifyRight"],["양쪽","justify","justifyFull"]].forEach(function(info){
      var b=button(o.toolbar,info[0],"메일 글자 "+info[0]+" 정렬",function(){
        if(!allowed())return;
        if(table()&&cells.length){setCellAlignment(info[1]);change();}
        else{o.restore();document.execCommand(info[2],false,null);o.remember();o.changed();hButtons.forEach(function(item){item.button.setAttribute("aria-pressed",String(item.value===info[1]));});}
      },"align-"+info[1]);b.onmousedown=function(e){e.preventDefault();};hButtons.push({button:b,value:info[1]});
    });

    button(o.toolbar,"표","메일 표 삽입 설정",function(){if(!allowed())return;o.remember();clearSelection();show(true);},"table");
    var gridCaption=node("p",insertPane,"격자를 골라 클릭하거나 행·열 수를 입력하세요.");gridCaption.className="mail-note";
    var grid=node("div",insertPane);grid.className="mail-table-grid";grid.setAttribute("role","group");grid.setAttribute("aria-label","표 크기 격자 선택");
    var gridButtons=[];
    function highlight(r,c){
      gridCaption.textContent=r+"행 × "+c+"열";
      gridButtons.forEach(function(v){if(v.r<=r&&v.c<=c)v.b.setAttribute("data-on","");else v.b.removeAttribute("data-on");});
    }
    for(var r=1;r<=6;r++)for(var c=1;c<=8;c++)(function(r,c){
      var b=button(grid,"",r+"행 "+c+"열 표 삽입",function(){insertTable(r,c);});b.tabIndex=r===1&&c===1?0:-1;
      b.onmouseover=b.onfocus=function(){highlight(r,c);rows.value=r;cols.value=c;};
      b.onkeydown=function(e){var index=(r-1)*8+c-1,next=index;
        if(e.key==="ArrowRight")next=Math.min(47,index+1);else if(e.key==="ArrowLeft")next=Math.max(0,index-1);
        else if(e.key==="ArrowDown")next=Math.min(47,index+8);else if(e.key==="ArrowUp")next=Math.max(0,index-8);else return;
        e.preventDefault();b.tabIndex=-1;gridButtons[next].b.tabIndex=0;gridButtons[next].b.focus();};
      gridButtons.push({r:r,c:c,b:b});
    }(r,c));
    var sizes=group(insertPane),rows=field(sizes,"행","메일 표 행 수",1,20,3),cols=field(sizes,"열","메일 표 열 수",1,8,3);
    function insertTable(r,c){
      if(!allowed())return;
      if(!isFinite(r)||!isFinite(c)||r<1||r>20||c<1||c>8||r%1||c%1||rich.querySelectorAll("table").length>=3){o.note("표는 최대 3개, 각 1~20행·1~8열입니다.");return;}
      o.outsideTable();
      var html='<table><tbody>';for(var a=0;a<r;a++){html+='<tr>';for(var b=0;b<c;b++)html+='<td><br></td>';html+='</tr>';}
      o.insert(html+'</tbody></table><p><br></p>');panel.hidden=true;
    }
    button(sizes,"표 삽입","메일 표 삽입",function(){insertTable(Number(rows.value),Number(cols.value));},"insert-table");
    button(sizes,"닫기","표 삽입 닫기",reset,"close");

    var selection=group(editPane);
    var multiButton=button(selection,"여러 셀","여러 셀 범위 선택",function(){if(!allowed())return;multi=!multi;multiButton.setAttribute("aria-pressed",String(multi));},"select-range");multiButton.setAttribute("aria-pressed","false");
    button(selection,"표 전체","메일 표 전체 선택",function(){var t=table();if(!allowed()||!t)return;anchor=t.rows[0].cells[0];var last=t.rows[t.rows.length-1];select(last.cells[last.cells.length-1],true);},"select-all");
    button(selection,"선택 해제","메일 표 선택 해제",reset,"deselect");
    var vertical=group(editPane);node("span",vertical,"셀 세로 정렬 ");
    [["위","top"],["가운데","middle"],["아래","bottom"]].forEach(function(v){
      var b=button(vertical,v[0],"메일 셀 "+v[0]+" 정렬",function(){if(!allowed()||!table())return;cells.forEach(function(c){c.style.verticalAlign=v[1];c.setAttribute("valign",v[1]);});change();},"align-"+v[1]);
      vButtons.push({button:b,value:v[1]});b.setAttribute("aria-pressed",String(v[1]==="top"));
    });
    var colorLabel=node("label",vertical,"셀 배경 "),color=node("input",colorLabel);color.type="color";color.value="#e8f2fa";color.setAttribute("aria-label","메일 셀 배경색");
    color.oninput=function(){if(!allowed()||!table())return;cells.forEach(function(c){c.style.backgroundColor=color.value;});change();};
    button(vertical,"배경 지우기","메일 셀 배경색 지우기",function(){if(!allowed()||!table())return;cells.forEach(function(c){c.style.removeProperty("background-color");c.removeAttribute("bgcolor");});change();},"clear");

    function equalWidths(t){Array.prototype.forEach.call(t.rows,function(r){Array.prototype.forEach.call(r.cells,function(c){c.style.width=(100/r.cells.length)+"%";c.removeAttribute("width");});});}
    function edit(action){
      var t=table();if(!allowed()||!t)return;
      var row=active.parentNode,index=active.cellIndex,ri=row.rowIndex;
      if(action==="delete"){t.parentNode.removeChild(t);reset();o.changed();return;}
      if(action==="row+"||action==="rowBefore"){
        if(t.rows.length>=20){o.note("표는 최대 20행입니다.");return;}
        var added=t.insertRow(ri+(action==="row+"?1:0));for(var i=0;i<row.cells.length;i++){var cell=added.insertCell();cell.innerHTML="<br>";cell.style.width=row.cells[i].style.width;}
      }
      if(action==="col+"||action==="colBefore"){
        if(Array.prototype.some.call(t.rows,function(r){return r.cells.length>=8;})){o.note("표는 최대 8열입니다.");return;}
        Array.prototype.forEach.call(t.rows,function(r){r.insertCell(Math.min(index+(action==="col+"?1:0),r.cells.length)).innerHTML="<br>";});equalWidths(t);
      }
      if(action==="row-"||action==="col-"){
        var positions=[];cells.forEach(function(c){var n=action==="row-"?c.parentNode.rowIndex:c.cellIndex;if(positions.indexOf(n)<0)positions.push(n);});positions.sort(function(a,b){return b-a;});
        if((action==="row-"&&positions.length===t.rows.length)||(action==="col-"&&positions.length===row.cells.length))return edit("delete");
        positions.forEach(function(n){if(action==="row-")t.deleteRow(n);else Array.prototype.forEach.call(t.rows,function(r){if(r.cells[n])r.deleteCell(n);});});
        if(action==="col-")equalWidths(t);
        var next=t.rows[Math.min(ri,t.rows.length-1)];select(next.cells[Math.min(index,next.cells.length-1)],false);
      }else select(active,false);
      change();
    }
    var structure=group(editPane);
    [["↑ 행+","위 행 추가","rowBefore","row-before"],["↓ 행+","아래 행 추가","row+","row-after"],["← 열+","왼쪽 열 추가","colBefore","column-before"],["→ 열+","오른쪽 열 추가","col+","column-after"],["행−","선택 행 삭제","row-","delete-row"],["열−","선택 열 삭제","col-","delete-column"],["표 삭제","선택한 메일 표 삭제","delete","trash"]].forEach(function(v){button(structure,v[0],v[1],function(){edit(v[2]);},v[3]);});
    var geometry=group(editPane),colWidth=field(geometry,"열 폭(%)","메일 열 너비",5,100,50),rowHeight=field(geometry,"행 높이(px)","메일 행 높이",24,400,36);
    function widths(t){var values=Array.prototype.map.call(t.rows[0].cells,function(c){return c.getBoundingClientRect().width;}),sum=values.reduce(function(a,b){return a+b;},0);return values.map(function(w){return sum?w/sum*100:100/values.length;});}
    function applyWidths(t,values){Array.prototype.forEach.call(t.rows,function(r){Array.prototype.forEach.call(r.cells,function(c,i){c.style.width=values[i].toFixed(3)+"%";c.removeAttribute("width");});});}
    colWidth.onchange=function(){
      var t=table(),v=Number(colWidth.value);if(!allowed()||!t)return;
      var old=widths(t),i=active.cellIndex;
      if(!isFinite(v)||v<5||v>100-5*(old.length-1)||(old.length===1&&v!==100)){o.note("열 너비는 5% 이상이며 모든 열의 합은 100%입니다.");refresh();return;}
      var remainder=100-v,weight=100-old[i];
      var next=old.map(function(w,k){return k===i?v:5+(remainder-5*(old.length-1))*(w/Math.max(1,weight));});
      // Allocate the other columns proportionally after reserving their minimum width.
      applyWidths(t,next);change();
    };
    rowHeight.onchange=function(){var v=Number(rowHeight.value);if(!allowed()||!table())return;if(!isFinite(v)||v<24||v>400){o.note("행 높이는 24~400px입니다.");refresh();return;}cells.forEach(function(c){c.parentNode.style.height=Math.round(v)+"px";});change();};
    button(geometry,"열 균등","메일 열 너비 같게",function(){var t=table();if(!allowed()||!t)return;equalWidths(t);change();},"equal-columns");
    button(geometry,"높이 자동","메일 행 높이 자동",function(){if(!allowed()||!table())return;cells.forEach(function(c){c.parentNode.style.removeProperty("height");c.parentNode.removeAttribute("height");});change();},"auto-height");
    var whole=group(editPane);node("strong",whole,"표 전체 크기");
    var tableWidth=field(whole,"너비(%)","메일 표 전체 너비",20,100,100),tableHeight=field(whole,"높이(px)","메일 표 전체 높이",24,8000,72);
    function rowHeights(t){return Array.prototype.map.call(t.rows,function(r){return r.getBoundingClientRect().height||parseInt(r.style.height,10)||36;});}
    function bodyWidth(){var s=window.getComputedStyle(rich);return Math.max(1,(rich.clientWidth||rich.getBoundingClientRect().width)-(parseFloat(s.paddingLeft)||0)-(parseFloat(s.paddingRight)||0));}
    function resizeWidth(t,value){var width=(Math.round(Math.max(20,Math.min(100,value))*1000)/1000)+"%";t.style.width=width;t.setAttribute("width",width);}
    function resizeHeight(t,heights,value){
      // Scale rows together, redistributing at the per-row bounds. Content may grow a row further.
      var target=Math.round(Math.max(24*heights.length,Math.min(400*heights.length,value))),low=0,high=400/Math.min.apply(Math,heights);
      for(var iteration=0;iteration<40;iteration++){var scale=(low+high)/2,sum=heights.reduce(function(a,h){return a+Math.max(24,Math.min(400,h*scale));},0);if(sum<target)low=scale;else high=scale;}
      var result=heights.map(function(h){return Math.floor(Math.max(24,Math.min(400,h*low)));}),remaining=target-result.reduce(function(a,b){return a+b;},0);
      result.forEach(function(h,i){if(remaining>0&&h<400){result[i]++;remaining--;}});
      Array.prototype.forEach.call(t.rows,function(r,i){r.style.height=result[i]+"px";r.setAttribute("height",String(result[i]));});
    }
    tableWidth.onchange=function(){var t=table(),v=Number(tableWidth.value);if(!allowed()||!t)return;if(!isFinite(v)||v<20||v>100){o.note("표 전체 너비는 본문 폭의 20~100%입니다.");refresh();return;}resizeWidth(t,v);change();};
    tableHeight.onchange=function(){var t=table(),v=Number(tableHeight.value);if(!allowed()||!t)return;if(!isFinite(v)||v<t.rows.length*24||v>t.rows.length*400){o.note("표 전체 높이는 "+t.rows.length*24+"~"+t.rows.length*400+"px입니다. 내용에 필요한 최소 높이는 유지됩니다.");refresh();return;}resizeHeight(t,rowHeights(t),v);change();};
    button(whole,"본문 폭 맞춤","메일 표 전체 너비 초기화",function(){var t=table();if(!allowed()||!t)return;resizeWidth(t,100);change();},"fit-width");
    button(whole,"높이 자동","메일 표 전체 높이 자동",function(){var t=table();if(!allowed()||!t)return;Array.prototype.forEach.call(t.rows,function(r){r.style.removeProperty("height");r.removeAttribute("height");});change();},"auto-height");
    node("p",editPane,"표 우측 하단 ↘: 전체 크기 · 셀 경계: 열 폭·행 높이 · 글자 크기는 유지되며 내용보다 작게 줄어들지 않습니다. Tab: 셀 이동 · Esc: 선택 해제/드래그 취소").className="mail-note";

    var colHandle=button(wrap,"","선택 열 너비 드래그 조절",function(){}),rowHandle=button(wrap,"","선택 행 높이 드래그 조절",function(){}),tableHandle=button(wrap,"↘","표 전체 크기 드래그 조절",function(){});
    colHandle.className="mail-resize-handle mail-resize-col";rowHandle.className="mail-resize-handle mail-resize-row";
    tableHandle.className="mail-resize-handle mail-resize-table";
    colHandle.hidden=rowHandle.hidden=tableHandle.hidden=true;
    function positionHandles(){
      var t=table();if(!t||!o.enabled()){colHandle.hidden=rowHandle.hidden=tableHandle.hidden=true;return;}
      var c=active.getBoundingClientRect(),w=wrap.getBoundingClientRect(),v=rich.getBoundingClientRect(),b=t.getBoundingClientRect();
      var y=Math.min(c.bottom-14,Math.max(c.top+14,(v.top+v.bottom)/2));
      colHandle.hidden=active.cellIndex>=active.parentNode.cells.length-1||c.right<v.left+6||c.right>v.right-6||y<v.top+14||y>v.bottom-14;
      colHandle.style.left=(c.right-w.left-5)+"px";colHandle.style.top=(y-w.top-14)+"px";
      rowHandle.hidden=c.bottom<v.top+6||c.bottom>v.bottom-6;
      rowHandle.style.left=((c.left+c.right)/2-w.left-14)+"px";rowHandle.style.top=(c.bottom-w.top-5)+"px";
      tableHandle.hidden=b.right<v.left+18||b.right>v.right-2||b.bottom<v.top+18||b.bottom>v.bottom-2;
      tableHandle.style.left=(b.right-w.left-16)+"px";tableHandle.style.top=(b.bottom-w.top-16)+"px";
    }
    function endDrag(commit){
      if(!drag)return;var d=drag;drag=null;
      if(!commit){d.styles.forEach(function(item){["style","width","height"].forEach(function(attr,i){if(item[i+1]===null)item[0].removeAttribute(attr);else item[0].setAttribute(attr,item[i+1]);});});}
      document.removeEventListener(d.move,moveDrag);document.removeEventListener(d.up,finishDrag);document.removeEventListener("pointercancel",cancelDrag);document.removeEventListener("keydown",dragKey);window.removeEventListener("blur",cancelDrag);
      if(d.id!=null&&d.handle.releasePointerCapture){try{d.handle.releasePointerCapture(d.id);}catch(_){}}
      rich.style.userSelect="";if(commit)o.changed();refresh();
    }
    function finishDrag(e){if(drag&&drag.id!=null&&e.pointerId!==drag.id)return;endDrag(true);}
    function cancelDrag(e){if(drag&&drag.id!=null&&e&&e.pointerId!=null&&e.pointerId!==drag.id)return;endDrag(false);}
    function dragKey(e){if(e.key==="Escape"){e.preventDefault();e.stopPropagation();endDrag(false);}}
    function moveDrag(e){
      if(!drag||(drag.id!=null&&e.pointerId!==drag.id))return;if(!o.enabled())return endDrag(false);e.preventDefault();var d=drag;
      if(d.kind==="col"){var values=d.widths.slice(),delta=(e.clientX-d.x)/d.tableWidth*100,total=values[d.index]+values[d.index+1];values[d.index]=Math.max(5,Math.min(total-5,values[d.index]+delta));values[d.index+1]=total-values[d.index];applyWidths(d.table,values);}
      else if(d.kind==="table"){resizeWidth(d.table,(d.tableWidth+e.clientX-d.x)/d.bodyWidth*100);d.heightTouched=d.heightTouched||e.clientY!==d.y;if(d.heightTouched)resizeHeight(d.table,d.heights,d.tableHeight+e.clientY-d.y);}
      else d.row.style.height=Math.round(Math.max(24,Math.min(400,d.height+e.clientY-d.y)))+"px";
      positionHandles();
    }
    function beginDrag(e,kind,handle){
      var t=table();if(!allowed()||!t||(e.button!=null&&e.button!==0))return;e.preventDefault();
      var pointer=e.type==="pointerdown";
      drag={kind:kind,handle:handle,id:pointer?e.pointerId:null,table:t,row:active.parentNode,index:active.cellIndex,x:e.clientX,y:e.clientY,widths:widths(t),tableWidth:t.getBoundingClientRect().width,height:active.parentNode.getBoundingClientRect().height,bodyWidth:bodyWidth(),tableHeight:t.getBoundingClientRect().height,heights:rowHeights(t),
        styles:[t].concat(Array.prototype.slice.call(t.querySelectorAll("tr,td,th"))).map(function(c){return [c,c.getAttribute("style"),c.getAttribute("width"),c.getAttribute("height")];}),move:pointer?"pointermove":"mousemove",up:pointer?"pointerup":"mouseup"};
      rich.style.userSelect="none";if(pointer&&handle.setPointerCapture){try{handle.setPointerCapture(e.pointerId);}catch(_){}}
      document.addEventListener(drag.move,moveDrag);document.addEventListener(drag.up,finishDrag);document.addEventListener("pointercancel",cancelDrag);document.addEventListener("keydown",dragKey);window.addEventListener("blur",cancelDrag);
    }
    [colHandle,rowHandle,tableHandle].forEach(function(h,i){h.addEventListener(window.PointerEvent?"pointerdown":"mousedown",function(e){beginDrag(e,["col","row","table"][i],h);});
      h.onkeydown=function(e){var delta=(e.key==="ArrowRight"||e.key==="ArrowDown")?1:(e.key==="ArrowLeft"||e.key==="ArrowUp")?-1:0;if(!delta||!allowed())return;e.preventDefault();if(i===2){var f=e.key==="ArrowRight"||e.key==="ArrowLeft"?tableWidth:tableHeight;f.value=Number(f.value)+delta*(f===tableWidth?1:4);f.onchange();}else if(i===0){colWidth.value=Number(colWidth.value)+delta;colWidth.onchange();}else{rowHeight.value=Number(rowHeight.value)+delta*4;rowHeight.onchange();}};});
    rich.addEventListener("scroll",positionHandles);
    rich.addEventListener("input",positionHandles);
    // Range-select cells without leaving a native text range that could erase their contents on typing.
    rich.addEventListener("mousedown",function(e){if(o.enabled()&&(e.shiftKey||multi)&&e.target.closest("td,th"))e.preventDefault();});
    rich.addEventListener("click",function(e){var c=e.target.closest("td,th");if(c)select(c,e.shiftKey||multi);else reset();});
    rich.addEventListener("keydown",function(e){
      if(!o.enabled())return;var c=e.target.closest("td,th"),s=window.getSelection();
      if(!c&&s.anchorNode){var n=s.anchorNode.nodeType===1?s.anchorNode:s.anchorNode.parentNode;c=n.closest("td,th");}
      if(e.key==="Escape"&&table()){e.preventDefault();e.stopPropagation();reset();return;}
      if(e.key==="Tab"&&c&&rich.contains(c)){var list=Array.prototype.slice.call(c.closest("table").querySelectorAll("td,th")),next=list[list.indexOf(c)+(e.shiftKey?-1:1)];
        if(next){e.preventDefault();var range=document.createRange();range.selectNodeContents(next);range.collapse(true);s.removeAllRanges();s.addRange(range);select(next,false);o.remember();}}
    });
    return {reset:reset,lock:function(){if(drag)endDrag(false);positionHandles();},active:function(){return table();}};
  };
}());
