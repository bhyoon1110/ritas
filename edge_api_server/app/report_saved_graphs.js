/* Same-origin workflow bridge. Only one bounded PNG is requested per insertion. ES5. */
(function () {
  "use strict";
  window.RistSavedGraphs = function (options) {
    var mime="application/x-rist-saved-graph", graphs=[], pending=null, sequence=0, stage=document.getElementById("slide-stage");
    var host=document.createElement("section"), list=document.createElement("div"), hint=document.createElement("p");
    host.id="report-saved-graphs";host.hidden=true;host.setAttribute("aria-label","저장 그래프 삽입");
    hint.textContent="저장 그래프를 아래 PPT 페이지에 끌어 놓으세요. 클릭하면 현재 페이지에 추가합니다. 추가 후 이동·크기 조절·삭제할 수 있습니다.";
    list.className="report-graph-list";host.appendChild(hint);host.appendChild(list);
    var tools=document.getElementById("canvas-tools");tools.parentNode.insertBefore(host,tools);
    function known(id){return graphs.some(function(g){return g.id===id;});}
    function clear(){if(pending)window.clearTimeout(pending.timer);pending=null;stage.classList.remove("graph-drop-target");}
    function insert(id,position){
      if(!known(id))return;
      if(pending||!options.canvas.canInsertGraph()){options.message("저장·미리보기 갱신이 끝난 뒤 원하는 페이지에 그래프를 추가하세요.",true);return;}
      pending={id:id,jobId:options.job(),pageId:options.canvas.pageId(),position:position,requestId:"graph-"+Date.now()+"-"+(++sequence)};
      pending.timer=window.setTimeout(function(){clear();options.message("저장 그래프 응답이 없습니다. 다시 추가하세요.",true);},15000);
      options.message("저장 그래프를 불러오는 중입니다.");
      window.parent.postMessage({type:"rist-saved-graph-request",id:id,jobId:pending.jobId,requestId:pending.requestId},window.location.origin);
    }
    function render(){
      list.textContent="";host.hidden=!graphs.length;
      graphs.forEach(function(g){
        var b=document.createElement("button");b.type="button";b.textContent=g.title;b.draggable=true;b.dataset.graphId=g.id;
        b.title=g.title+" · 끌어서 배치 / 클릭하여 현재 페이지에 추가";b.setAttribute("aria-label",g.title+" 현재 페이지에 추가");list.appendChild(b);
        b.onclick=function(){insert(g.id);};
        b.ondragstart=function(event){
          if(pending||!options.canvas.canInsertGraph()||!event.dataTransfer){event.preventDefault();return;}
          event.dataTransfer.setData(mime,JSON.stringify({id:g.id,jobId:options.job()}));event.dataTransfer.effectAllowed="copy";
        };
      });
    }
    function hasGraph(event){return event.dataTransfer&&Array.prototype.indexOf.call(event.dataTransfer.types||[],mime)>=0;}
    stage.addEventListener("dragover",function(event){
      if(!hasGraph(event))return;event.preventDefault();
      var allowed=!pending&&options.canvas.canInsertGraph();event.dataTransfer.dropEffect=allowed?"copy":"none";
      stage.classList.toggle("graph-drop-target",allowed);
    });
    stage.addEventListener("dragleave",function(event){if(!stage.contains(event.relatedTarget))stage.classList.remove("graph-drop-target");});
    stage.addEventListener("drop",function(event){
      stage.classList.remove("graph-drop-target");if(!hasGraph(event))return;event.preventDefault();event.stopPropagation();
      var data;try{data=JSON.parse(event.dataTransfer.getData(mime));}catch(_){return;}
      if(!data||data.jobId!==options.job()||!known(data.id))return;
      var rect=stage.getBoundingClientRect();if(!rect.width||!rect.height)return;
      insert(data.id,{x:Math.max(0,Math.min(1,(event.clientX-rect.left)/rect.width)),y:Math.max(0,Math.min(1,(event.clientY-rect.top)/rect.height))});
    });
    window.addEventListener("dragend",function(){stage.classList.remove("graph-drop-target");});
    window.addEventListener("message",function(event){
      if(event.source!==window.parent||event.origin!==window.location.origin||!event.data)return;
      var data=event.data;
      if(data.jobId!==options.job())return;
      if(data.type==="rist-saved-graphs"){
        if(!Array.isArray(data.graphs))return;
        var next=data.graphs.slice(0,6).filter(function(g){return g&&typeof g.id==="string"&&g.id.length<=100&&typeof g.title==="string";})
          .map(function(g){return {id:g.id,title:g.title.slice(0,100)};});
        if(JSON.stringify(next)!==JSON.stringify(graphs)){graphs=next;render();}
      }else if(data.type==="rist-insert-saved-graph")insert(data.id);
      else if(data.type==="rist-saved-graph-data"&&pending&&data.requestId===pending.requestId&&data.id===pending.id){
        var target=pending;clear();
        if(data.error){options.message(data.error,true);return;}
        if(target.pageId!==options.canvas.pageId()||target.jobId!==options.job()) {options.message("페이지가 변경되었습니다. 원하는 페이지에서 다시 추가하세요.",true);return;}
        options.canvas.insertGraph(data.image,target.position,function(error){
          if(error)options.message(error.message,true);
          else options.message("그래프를 추가했습니다. 위치·크기를 조절할 수 있으며 자동 저장 후 최종 PPT에 반영됩니다.");
        });
      }
    });
  };
}());
