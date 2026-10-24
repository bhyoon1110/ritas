/* Author-only page navigator. Load visible thumbnails one at a time on low-end PCs. */
(function () {
  "use strict";
  window.RistPageSidebar = function (options) {
    var host=document.getElementById("page-sidebar"),list=document.getElementById("page-thumbnails"),toggle=document.getElementById("page-sidebar-toggle");
    var pages=[],buttons=[],current=0,base="",blocked=true,locked=false,capable=false,drag=null,version=0,pending=null;
    var collapsed=window.innerWidth<=700;
    function visible() {
      host.hidden=!capable||collapsed;toggle.hidden=!capable;
      toggle.setAttribute("aria-expanded",String(!host.hidden));
      window.RistReportIcons(toggle,"pages",host.hidden?"페이지 목록 열기":"페이지 목록 닫기");
    }
    toggle.onclick=function(){collapsed=!collapsed;visible();loadVisible();};
    var close=window.RistReportIcons(document.getElementById("page-sidebar-close"),"pages","페이지 목록 닫기");
    close.onclick=function(){collapsed=true;visible();toggle.focus();};
    function loadVisible() {
      if(host.hidden||pending||!list.clientHeight)return;
      var bounds=list.getBoundingClientRect();
      for(var i=0;i<buttons.length;i++) {
        var img=buttons[i].querySelector("img"),rect=buttons[i].getBoundingClientRect();
        if(img.getAttribute("src")||rect.bottom<bounds.top-100||rect.top>bounds.bottom+100)continue;
        var token=version;pending=img;
        img.onload=img.onerror=function(){if(token!==version)return;pending=null;loadVisible();};
        img.src=base+"/"+i+"?thumbnail=true";return;
      }
    }
    function selection(reveal) {
      buttons.forEach(function(b,i){b.setAttribute("aria-current",i===current?"page":"false");b.disabled=locked;});
      document.getElementById("page-count").textContent=pages.length+"페이지";
      var b=buttons[current];
      if(reveal&&b&&list.clientHeight){
        var br=b.getBoundingClientRect(),lr=list.getBoundingClientRect();
        if(br.top<lr.top)list.scrollTop-=lr.top-br.top;
        else if(br.bottom>lr.bottom)list.scrollTop+=br.bottom-lr.bottom;
      }
      loadVisible();
    }
    function cancel() {
      drag=null;
      buttons.forEach(function(b){b.classList.remove("is-dragging","drop-before","drop-after");});
    }
    function move(id,target,after) {
      if(blocked||locked||id===target)return;
      cancel();options.move(id,target,after);
    }
    function render(ratio) {
      var focused=list.contains(document.activeElement)?document.activeElement.getAttribute("data-page-id"):null;
      cancel();version++;pending=null;buttons=[];list.textContent="";
      pages.forEach(function(p,index){
        var li=document.createElement("li"),b=document.createElement("button");list.appendChild(li);li.appendChild(b);
        b.type="button";b.className="page-thumb";b.setAttribute("data-page-id",p.id);b.draggable=!blocked;
        b.setAttribute("aria-label",(index+1)+"페이지 · "+p.title);b.title=(index+1)+". "+p.title;
        var frame=document.createElement("span");frame.className="page-thumb-image";frame.style.paddingTop=(100/(ratio||16/9))+"%";b.appendChild(frame);
        var img=document.createElement("img");img.alt="";frame.appendChild(img);
        var caption=document.createElement("span");caption.className="page-thumb-title";caption.textContent=(index+1)+". "+p.title;b.appendChild(caption);
        b.onclick=function(){options.select(index);};
        b.onkeydown=function(e){
          if(locked)return;
          var delta=e.keyCode===38?-1:e.keyCode===40?1:0;if(!delta)return;
          e.preventDefault();var target=index+delta;if(target<0||target>=pages.length)return;
          if(e.altKey)move(p.id,pages[target].id,delta>0);
          else {options.select(target);buttons[target].focus();}
        };
        b.ondragstart=function(e){
          if(blocked||locked){e.preventDefault();return;}
          cancel();drag=p.id;b.classList.add("is-dragging");
          if(e.dataTransfer){e.dataTransfer.effectAllowed="move";e.dataTransfer.setData("text/plain",p.id);}
        };
        b.ondragover=function(e){
          if(!drag||blocked||drag===p.id)return;e.preventDefault();
          buttons.forEach(function(item){item.classList.remove("drop-before","drop-after");});
          var r=b.getBoundingClientRect();b.classList.add(e.clientY>(r.top+r.bottom)/2?"drop-after":"drop-before");
          var lr=list.getBoundingClientRect();if(e.clientY<lr.top+35)list.scrollTop-=18;else if(e.clientY>lr.bottom-35)list.scrollTop+=18;
        };
        b.ondrop=function(e){
          if(!drag||blocked)return;e.preventDefault();var r=b.getBoundingClientRect();move(drag,p.id,e.clientY>(r.top+r.bottom)/2);cancel();
        };
        b.ondragend=cancel;buttons.push(b);
      });
      selection(true);
      buttons.forEach(function(b){if(focused&&b.getAttribute("data-page-id")===focused)b.focus();});
    }
    list.addEventListener("scroll",loadVisible);window.addEventListener("resize",loadVisible);
    window.addEventListener("blur",cancel);document.addEventListener("keydown",function(e){if(e.keyCode===27)cancel();});
    document.addEventListener("visibilitychange",function(){if(document.hidden)cancel();});
    visible();
    return {
      layout:function(value,imageBase){pages=value.pages||[];base=imageBase;capable=value.pageEditing===true;visible();render(value.ratio);},
      select:function(index){current=index;selection(true);},
      lock:function(reorderDisabled,selectionDisabled){blocked=reorderDisabled;locked=selectionDisabled;if(blocked)cancel();buttons.forEach(function(b){b.draggable=!blocked;});selection(false);},
      loading:function(){blocked=true;cancel();buttons.forEach(function(b){b.draggable=false;});}
    };
  };
}());
