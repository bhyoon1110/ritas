/* ES5. Only explicit formatting patches cross report boundaries. */
(function () {
  "use strict";
  var styles=["x","y","w","h","fontSize","bold","color","fill","stroke","lineWidth","dash","align"], cellStyles=styles.slice(4);
  function copy(value){return JSON.parse(JSON.stringify(value));}
  function pick(value,keys){var out={};keys.forEach(function(k){if(value[k]!=null)out[k]=value[k];});return out;}
  function original(page){return (page.objects||[]).filter(function(o){return o.id.indexOf("shape-")===0;});}
  function capture(layout,state,experiment,name){
    var count=0,pages=[];
    (layout.pages||[]).forEach(function(page){
      if(!page.templateKey)return;
      var objects=[];
      original(page).forEach(function(o){
        var patch=(state[page.id]||[]).filter(function(p){return p.id===o.id;})[0];
        if(!patch||patch.hidden)return;
        var value=pick(patch,styles),cells=[];
        (patch.tableCells||[]).forEach(function(c){var v=pick(c,cellStyles);if(Object.keys(v).length){v.row=c.row;v.col=c.col;cells.push(v);}});
        if(cells.length)value.tableCells=cells;
        if(!Object.keys(value).length)return;
        if(["x","y","w","h"].some(function(k){return value[k]!=null;})){
          ["x","y","w","h"].forEach(function(k){value[k]=patch[k]!=null?patch[k]:o[k];});
        }
        value.id=o.id;value.kind=o.kind;objects.push(value);count++;
      });
      pages.push({key:page.templateKey,objects:objects});
    });
    if(!count)throw new Error("자유편집에서 배치·글자·표 서식을 변경한 후 저장하세요. 내용 변경과 추가 개체는 템플릿에 포함되지 않습니다.");
    return {version:1,name:name,experiment:experiment,ratio:layout.ratio,pages:pages};
  }
  function plan(template,layout,experiment){
    var result={edits:{},pages:0,objects:0,skipped:0,unused:0,cellsSkipped:0,error:""},groups={};
    if(template.experiment!==experiment)result.error="현재 실험과 다른 템플릿입니다.";
    else if(Math.abs(template.ratio-layout.ratio)>.001)result.error="페이지 가로세로 비율이 달라 적용할 수 없습니다.";
    if(result.error)return result;
    template.pages.forEach(function(p){(groups[p.key]||(groups[p.key]=[])).push(p);});
    (layout.pages||[]).forEach(function(page){
      var queue=groups[page.templateKey],source=queue&&queue.shift(),objects=[];
      if(!source){result.skipped++;return;}
      source.objects.forEach(function(p){
        var target=original(page).filter(function(o){return o.id===p.id&&o.kind===p.kind;})[0];
        if(!target)return;
        var value=pick(p,styles);value.id=p.id;value.kind=p.kind;
        if(p.tableCells){
          value.tableCells=p.tableCells.filter(function(c){
            var exists=(target.cells||[]).some(function(t){return t.row===c.row&&t.col===c.col;});
            if(!exists)result.cellsSkipped++;return exists;
          }).map(function(c){var v=pick(c,cellStyles);v.row=c.row;v.col=c.col;return v;});
          if(!value.tableCells.length)delete value.tableCells;
        }
        if(Object.keys(value).length>2)objects.push(value);
      });
      if(objects.length){result.edits[page.id]=objects;result.pages++;result.objects+=objects.length;}
    });
    Object.keys(groups).forEach(function(key){result.unused+=groups[key].length;});
    return result;
  }
  function merge(state,edits){
    var next=copy(state);
    Object.keys(edits).forEach(function(id){
      var list=next[id]||(next[id]=[]);
      edits[id].forEach(function(value){
        var item=list.filter(function(o){return o.id===value.id;})[0];
        if(!item){item={id:value.id};list.push(item);}
        Object.keys(value).forEach(function(k){if(k!=="tableCells")item[k]=value[k];});
        if(value.tableCells){
          var cells=item.tableCells||(item.tableCells=[]);
          value.tableCells.forEach(function(c){var found=cells.filter(function(v){return v.row===c.row&&v.col===c.col;})[0];
            if(!found){found={row:c.row,col:c.col};cells.push(found);}Object.keys(c).forEach(function(k){found[k]=c[k];});});
        }
      });
    });return next;
  }
  function Manager(options){
    var host=document.createElement("details");host.className="ppt-template-manager";
    options.host.appendChild(host);
    var busy=false,locked=true,revision=0,items=[],loaded=null,initialized=false;
    function el(tag,parent,text){var n=document.createElement(tag);if(text!=null)n.textContent=text;parent.appendChild(n);return n;}
    el("summary",host,"PPT 양식 템플릿");
    el("p",host,"내 계정의 실험별 양식입니다. 배치·글자·표 서식만 재사용합니다. 현재 분석값·그래프·배경은 유지되며, 페이지 추가·삭제·순서와 추가 개체는 복사하지 않습니다.");
    var fields=el("div",host);fields.className="actions";
    var select=el("select",fields);select.setAttribute("aria-label","PPT 템플릿 선택");
    var name=el("input",fields);name.type="text";name.maxLength=80;name.placeholder="템플릿 이름";name.setAttribute("aria-label","PPT 템플릿 이름");
    var row=el("div",host);row.className="actions";row.style.marginTop="8px";
    function button(label,icon,fn){var b=el("button",row);b.type="button";b.onclick=fn;window.RistReportIcons(b,icon,label);return b;}
    var refresh=button("PPT 템플릿 목록 새로고침","reset",function(){list(select.value);});
    var add=button("현재 PPT 서식을 새 템플릿으로 저장","add",function(){save(false);});
    var update=button("선택한 템플릿을 현재 PPT 서식으로 덮어쓰기","edit",function(){save(true);});
    var remove=button("선택한 PPT 템플릿 삭제","trash",function(){
      if(busy||!select.value)return;
      if(!window.confirm("선택한 템플릿을 삭제할까요? 작성 중인 보고서는 유지됩니다."))return;
      request("/"+select.value,"DELETE",{revision:revision},function(data){book(data);name.value="";message("템플릿을 삭제했습니다.");});
    });
    var apply=el("button",row,"양식 적용");apply.type="button";apply.onclick=function(){
      if(apply.disabled||!loaded)return;
      var result=plan(loaded,options.layout(),options.experiment);
      if(result.error||!result.objects){render();return;}
      if(options.apply(result.edits)!==false)message(result.pages+"개 페이지에 양식을 적용했습니다. "+(options.experiment==="XRD"?"최종 저장 전에는 ":"")+"자유편집의 실행취소로 되돌릴 수 있습니다.");
    };
    var summary=el("p",host),status=el("p",host);status.setAttribute("role","status");
    function message(text){status.textContent=text;}
    function render(){
      var layout=options.layout(),ready=!locked&&layout&&layout.canvasEditing===true;
      select.disabled=busy;name.disabled=busy;refresh.disabled=busy;
      add.disabled=busy||!ready||!initialized;update.disabled=busy||!ready||!loaded;remove.disabled=busy||!select.value;
      var result=loaded&&ready?plan(loaded,layout,options.experiment):null;
      apply.disabled=busy||!result||!!result.error||!result.objects;
      summary.textContent=!ready?"미리보기 반영이 완료되면 저장·적용할 수 있습니다.":!result?"템플릿을 선택하면 적용 범위를 확인할 수 있습니다.":
        result.error||(result.pages+"개 페이지 · "+result.objects+"개 개체에 적용 / 현재 보고서의 미대응 페이지 "+result.skipped+"개 / 템플릿의 미사용 페이지 "+result.unused+"개"+(result.cellsSkipped?" / 없는 표 셀 "+result.cellsSkipped+"개 제외":"")+". 같은 구성의 페이지는 순서대로 대응합니다.");
    }
    function request(tail,method,body,done){
      busy=true;render();message("템플릿 처리 중…");
      var xhr=new XMLHttpRequest();xhr.open(method,"/api/v1/report-ppt/templates"+tail,true);xhr.timeout=30000;
      xhr.setRequestHeader("X-Requested-With","RIST-Report-Editor");
      if(body)xhr.setRequestHeader("Content-Type","application/json");
      xhr.onload=function(){busy=false;var data;try{data=JSON.parse(xhr.responseText);}catch(_){message("템플릿 응답을 읽지 못했습니다.");render();return;}
        if(xhr.status<200||xhr.status>=300){message(data.message||"템플릿 처리에 실패했습니다.");render();return;}
        message("");done(data);render();};
      xhr.onerror=xhr.ontimeout=function(){busy=false;message("연결이 끊겼습니다. 목록을 새로고침해 저장 여부를 확인하세요.");render();};
      xhr.send(body?JSON.stringify(body):null);
    }
    function book(data,selected,preserveName){
      revision=data.revision;items=data.items;loaded=null;select.textContent="";el("option",select,"PPT 템플릿 선택").value="";
      items.filter(function(i){return i.experiment===options.experiment;}).forEach(function(i){el("option",select,i.name).value=i.id;});
      select.value=selected||"";if(select.value)read(preserveName);
    }
    function list(selected){var preserved=initialized?name.value:null;request("","GET",null,function(data){initialized=true;book(data,selected,preserved);});}
    function read(preserveName){loaded=null;if(!select.value){render();return;}
      request("/"+select.value,"GET",null,function(data){loaded=data.content;revision=data.revision;name.value=typeof preserveName==="string"?preserveName:loaded.name;});}
    function save(replace){
      if((replace?update:add).disabled)return;
      var value;try{value=capture(options.layout(),options.state(),options.experiment,name.value.trim());if(!value.name)throw new Error("템플릿 이름을 입력하세요.");}
      catch(error){message(error.message);return;}
      if(replace&&!window.confirm("선택한 템플릿의 이름·서식을 현재 내용으로 바꿀까요?"))return;
      request(replace?"/"+select.value:"",replace?"PUT":"POST",{revision:revision,content:value},function(data){book(data,data.selectedId);});
    }
    select.onchange=read;
    host.addEventListener("toggle",function(){if(host.open&&!initialized&&!busy)list();});
    // Old Chromium does not consistently emit toggle for <details>.
    host.querySelector("summary").onclick=function(){if(!initialized&&!busy)list();};
    render();return {lock:function(value){locked=value;render();},refresh:render};
  }
  window.RistPptTemplates={capture:capture,plan:plan,merge:merge,Manager:Manager};
}());
