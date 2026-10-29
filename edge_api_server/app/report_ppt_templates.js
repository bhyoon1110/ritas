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
  function externalPlan(template,source,layout,experiment){
    var result={edits:{},backgrounds:[],pages:0,objects:0,skipped:0,unused:0,cellsSkipped:0,error:""},used={};
    if(template.experiment!==experiment)result.error="현재 실험과 다른 템플릿입니다.";
    else if(Math.abs(template.ratio-layout.ratio)>.001)result.error="페이지 가로세로 비율이 달라 적용할 수 없습니다. 현재 보고서와 같은 비율의 PPTX를 사용하세요.";
    if(result.error)return result;
    (layout.pages||[]).forEach(function(page){
      var binding=template.bindings.filter(function(b){return b.key===page.templateKey;})[0],patches=[];
      if(!binding||!source.pages[binding.slide]){result.skipped++;return;}used[binding.key]=true;
      var slide=source.pages[binding.slide];
      binding.objects.forEach(function(link){
        var slot=slide.objects.filter(function(o){return o.id===link.source;})[0];
        var target=original(page).filter(function(o){return o.id===link.target;})[0];
        if(!slot||!target||slot.kind!==target.kind)return;
        var patch=pick(slot.style,styles);patch.id=target.id;patch.kind=target.kind;
        if(target.kind==="table"){
          patch.tableCells=(slot.cells||[]).filter(function(c){
            var found=(target.cells||[]).some(function(t){return t.row===c.row&&t.col===c.col;});
            if(!found)result.cellsSkipped++;return found;
          }).map(function(c){var v=pick(c,cellStyles);v.row=c.row;v.col=c.col;return v;});
        }
        patches.push(patch);
      });
      if(binding.background)result.backgrounds.push({page:page.id,slide:binding.slide,digest:slide.backgroundDigest});
      if(patches.length||binding.background){result.edits[page.id]=patches;result.pages++;result.objects+=patches.length+(binding.background?1:0);}
    });
    result.unused=template.bindings.filter(function(b){return !used[b.key];}).length;
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
    var busy=false,locked=true,revision=0,items=[],loaded=null,loadedId="",initialized=false,sourceDocument=null,bindingDirty=false,mappingLayout="";
    function el(tag,parent,text){var n=document.createElement(tag);if(text!=null)n.textContent=text;parent.appendChild(n);return n;}
    el("summary",host,"PPT 양식 템플릿");
    el("p",host,"내 계정의 실험별 양식입니다. 현재 서식을 저장하거나 외부 PPTX의 자리를 연결해 재사용합니다. 현재 분석값·그래프와 페이지 구성은 유지합니다.");
    var fields=el("div",host);fields.className="actions";
    var select=el("select",fields);select.setAttribute("aria-label","PPT 템플릿 선택");
    var name=el("input",fields);name.type="text";name.maxLength=80;name.placeholder="템플릿 이름";name.setAttribute("aria-label","PPT 템플릿 이름");
    var row=el("div",host);row.className="actions";row.style.marginTop="8px";
    function button(label,icon,fn){var b=el("button",row);b.type="button";b.onclick=fn;window.RistReportIcons(b,icon,label);return b;}
    var refresh=button("PPT 템플릿 목록 새로고침","reset",function(){if(bindingDirty&&!window.confirm("저장하지 않은 연결 변경을 버리고 목록을 새로고침할까요?"))return;list(select.value);});
    var add=button("현재 PPT 서식을 새 템플릿으로 저장","add",function(){save(false);});
    var update=button("선택한 템플릿을 현재 PPT 서식으로 덮어쓰기","edit",function(){save(true);});
    var remove=button("선택한 PPT 템플릿 삭제","trash",function(){
      if(busy||!select.value)return;
      if(!window.confirm("선택한 템플릿을 삭제할까요? 작성 중인 보고서는 유지됩니다."))return;
      request("/"+select.value,"DELETE",{revision:revision},function(data){book(data);name.value="";message("템플릿을 삭제했습니다.");});
    });
    var apply=el("button",row,"양식 적용");apply.type="button";apply.onclick=function(){
      if(apply.disabled||!loaded)return;
      var result=currentPlan();
      if(result.error||!result.objects){render();return;}
      if(loaded.version===2){applyExternal(result);return;}
      if(options.apply(result.edits)!==false)message(result.pages+"개 페이지에 양식을 적용했습니다. "+(options.experiment==="XRD"?"최종 저장 전에는 ":"")+"자유편집의 실행취소로 되돌릴 수 있습니다.");
    };
    var summary=el("p",host),status=el("p",host);status.setAttribute("role","status");
    var importRow=el("div",host);importRow.className="actions";
    var file=el("input",importRow);file.type="file";file.accept=".pptx";file.setAttribute("aria-label","외부 PPTX 양식 파일");
    var importButton=el("button",importRow,"PPTX 업로드");importButton.type="button";importButton.onclick=importPptx;
    var progress=el("progress",host);progress.hidden=true;progress.setAttribute("aria-label","PPTX 양식 처리 진행률");
    el("p",host,"PPTX 10MB·12페이지 이하. 서버에서 미리보기를 만듭니다. 회전·그룹·차트 개체는 직접 연결하지 않으며, 예제 문장·그림은 새 보고서에 복사하지 않습니다.");
    var mapping=el("div",host);mapping.className="ppt-template-mapping";mapping.hidden=true;
    var selectors=el("div",mapping);selectors.className="actions";
    var targetPage=el("select",selectors);targetPage.setAttribute("aria-label","연결할 현재 보고서 페이지");
    var sourcePage=el("select",selectors);sourcePage.setAttribute("aria-label","사용할 PPTX 양식 페이지");
    var image=el("img",mapping);image.alt="업로드한 PPTX 원본 양식 미리보기";image.className="ppt-template-preview";
    var backgroundLabel=el("label",mapping),background=el("input",backgroundLabel);background.type="checkbox";
    backgroundLabel.appendChild(document.createTextNode("마스터·레이아웃의 배경 그림도 적용"));
    el("p",mapping,"배경은 마스터·레이아웃의 그림과 장식을 사용합니다. 슬라이드 본문의 그림·문장과 마스터의 문자는 제외되며, 현재 보고서의 로고·고정 요소는 유지됩니다. 같은 구성의 보고서 페이지에는 동일하게 적용됩니다.");
    var mappingStatus=el("p",mapping),links=el("div",mapping);links.className="ppt-template-links";
    var clear=el("button",mapping,"이 페이지 연결 해제");clear.type="button";
    var download=el("a",mapping,"원본 PPTX 다운로드");download.style.marginLeft="12px";
    function isExternal(){return !!(loaded&&loaded.version===2&&sourceDocument);}
    function currentPlan(){return isExternal()?externalPlan(loaded,sourceDocument,options.layout(),options.experiment):plan(loaded,options.layout(),options.experiment);}
    function reportPage(){return ((options.layout()||{}).pages||[]).filter(function(p){return p.id===targetPage.value;})[0];}
    function showMapping(){
      var page=reportPage();links.textContent="";if(!page||!isExternal())return;
      var binding=loaded.bindings.filter(function(b){return b.key===page.templateKey;})[0];
      sourcePage.value=String(binding?binding.slide:0);background.checked=!!(binding&&binding.background);
      var slide=sourceDocument.pages[Number(sourcePage.value)];
      image.src="/api/v1/report-ppt/templates/"+select.value+"/slides/"+sourcePage.value+"/preview";
      mappingStatus.textContent="양식 자리 → 현재 보고서 개체. 명시된 서식을 적용하며, 테마 상속 서식과 연결하지 않은 개체는 현재 상태를 유지합니다."+(slide.omitted?" 직접 연결을 지원하지 않는 개체 "+slide.omitted+"개 제외.":"");
      slide.objects.forEach(function(slot,index){
        var label=el("label",links,(index+1)+". "+slot.label+" → "),choose=el("select",label);
        choose.setAttribute("data-slot",slot.id);choose.setAttribute("aria-label","양식 자리 "+(index+1)+" 연결");
        el("option",choose,"연결 안 함").value="";
        original(page).filter(function(o){return o.kind===slot.kind;}).forEach(function(o){el("option",choose,(o.text||o.name||o.kind).slice(0,90)).value=o.id;});
        var match=binding&&binding.objects.filter(function(o){return o.source===slot.id;})[0];choose.value=match?match.target:"";
        choose.onchange=storeMapping;
      });
      download.href="/api/v1/report-ppt/templates/"+select.value+"/source";
    }
    function storeMapping(){
      var page=reportPage();if(!page||!isExternal()||busy||locked)return;
      var objects=[],used={},duplicate=false;
      Array.prototype.forEach.call(links.querySelectorAll("select"),function(s){if(s.value){if(used[s.value])duplicate=true;used[s.value]=true;objects.push({source:s.getAttribute("data-slot"),target:s.value});}});
      if(duplicate){message("같은 보고서 개체를 두 자리에 연결할 수 없습니다.");showMapping();return;}
      loaded.bindings=loaded.bindings.filter(function(b){return b.key!==page.templateKey;});
      loaded.bindings.push({key:page.templateKey,slide:Number(sourcePage.value),background:background.checked,objects:objects});
      bindingDirty=true;render();
    }
    targetPage.onchange=showMapping;
    sourcePage.onchange=function(){
      var page=reportPage();if(!page)return;
      loaded.bindings=loaded.bindings.filter(function(b){return b.key!==page.templateKey;});
      loaded.bindings.push({key:page.templateKey,slide:Number(sourcePage.value),background:false,objects:[]});
      bindingDirty=true;showMapping();render();
    };
    background.onchange=storeMapping;
    clear.onclick=function(){var page=reportPage();if(!page||clear.disabled)return;loaded.bindings=loaded.bindings.filter(function(b){return b.key!==page.templateKey;});bindingDirty=true;showMapping();render();};
    function importPptx(){
      if(importButton.disabled)return;
      var chosen=file.files&&file.files[0];
      if(!chosen||!/\.pptx$/i.test(chosen.name)||chosen.size>10*1024*1024){message("10MB 이하의 PPTX를 선택하세요.");return;}
      if(!name.value.trim()){message("템플릿 이름을 입력하세요.");return;}
      busy=true;render();progress.hidden=false;progress.max=100;progress.value=0;message("PPTX 업로드 중…");
      var xhr=new XMLHttpRequest();xhr.open("POST","/api/v1/report-ppt/templates/import?experiment="+options.experiment+"&revision="+revision+"&name="+encodeURIComponent(name.value.trim()),true);xhr.timeout=180000;
      xhr.setRequestHeader("X-Requested-With","RIST-Report-Editor");xhr.setRequestHeader("Content-Type","application/vnd.openxmlformats-officedocument.presentationml.presentation");
      if(xhr.upload){xhr.upload.onprogress=function(e){if(e.lengthComputable)progress.value=Math.round(100*e.loaded/e.total);};xhr.upload.onload=function(){progress.removeAttribute("value");message("서버에서 양식 미리보기를 만드는 중…");};}
      xhr.onload=function(){busy=false;progress.hidden=true;var data;try{data=JSON.parse(xhr.responseText);}catch(_){message("양식 응답을 확인하지 못했습니다. 목록을 새로고침하세요.");render();return;}
        if(xhr.status>=200&&xhr.status<300){file.value="";book(data,data.selectedId);message("PPTX를 등록했습니다. 페이지와 개체를 연결한 뒤 연결 저장 아이콘을 누르세요.");}
        else message(data.message||"PPTX 업로드에 실패했습니다.");render();};
      xhr.onerror=xhr.ontimeout=function(){busy=false;progress.hidden=true;message("연결이 끊겼습니다. 재업로드 전 목록을 새로고침해 등록 여부를 확인하세요.");render();};xhr.send(chosen);
    }
    function applyExternal(result){
      var before=JSON.stringify([options.state(),options.layout()]),id=select.value,cache={},queue=result.backgrounds.slice();
      busy=true;progress.hidden=false;progress.removeAttribute("value");render();message("양식 배경을 보고서에 준비하는 중…");
      function finish(error){busy=false;progress.hidden=true;render();if(error){message(error.message);return;}
        if(locked||before!==JSON.stringify([options.state(),options.layout()])){message("보고서가 변경되어 적용을 중단했습니다. 미리보기 반영 후 다시 적용하세요.");return;}
        if(options.apply(result.edits)!==false)message(result.pages+"개 페이지에 외부 양식을 적용했습니다. "+(options.experiment==="XRD"?"최종 저장 전 ":"")+"자유편집의 실행취소로 되돌릴 수 있습니다.");
      }
      function next(){
        if(!queue.length){finish();return;}
        var entry=queue.shift();
        function attached(error,value){
          if(error){finish(error);return;}cache[entry.digest]=value;
          result.edits[entry.page].push({id:"object-template-background",kind:"image",asset:value.asset,x:0,y:0,w:1,h:1,layer:"back"});next();
        }
        if(cache[entry.digest]){attached(null,cache[entry.digest]);return;}
        var xhr=new XMLHttpRequest();xhr.open("GET","/api/v1/report-ppt/templates/"+id+"/slides/"+entry.slide+"/background",true);xhr.responseType="blob";xhr.timeout=30000;
        xhr.onload=function(){if(xhr.status!==200){finish(new Error("양식 배경을 가져오지 못했습니다. 목록을 새로고침하세요."));return;}
          if(!options.upload){finish(new Error("배경 업로드를 지원하지 않는 화면입니다."));return;}
          options.upload(xhr.response,attached);};
        xhr.onerror=xhr.ontimeout=function(){finish(new Error("배경 다운로드가 중단됐습니다. 다시 적용하세요."));};xhr.send();
      }
      next();
    }
    function message(text){status.textContent=text;}
    function render(){
      var layout=options.layout(),ready=!locked&&layout&&layout.canvasEditing===true;
      select.disabled=busy;name.disabled=busy;refresh.disabled=busy;
      add.disabled=busy||!ready||!initialized;update.disabled=busy||!ready||!loaded;remove.disabled=busy||!select.value;
      file.disabled=busy;importButton.disabled=busy||!initialized;
      window.RistReportIcons(update,"edit",isExternal()?"PPTX 양식 이름·연결 저장":"선택한 템플릿을 현재 PPT 서식으로 덮어쓰기");
      mapping.hidden=!isExternal();
      if(isExternal()){
        var signature=JSON.stringify((layout&&layout.pages||[]).map(function(p){return [p.id,p.templateKey,p.title];}));
        if(mappingLayout!==signature){mappingLayout=signature;var selected=targetPage.value;targetPage.textContent="";sourcePage.textContent="";
          (layout&&layout.pages||[]).filter(function(p){return !!p.templateKey;}).forEach(function(p,i){el("option",targetPage,(i+1)+". "+p.title).value=p.id;});
          sourceDocument.pages.forEach(function(p,i){el("option",sourcePage,"양식 "+(i+1)+"페이지").value=String(i);});
          if(selected&&Array.prototype.some.call(targetPage.options,function(o){return o.value===selected;}))targetPage.value=selected;
          showMapping();
        }
        Array.prototype.forEach.call(mapping.querySelectorAll("select,input,button"),function(n){n.disabled=busy||!ready;});
      }
      var result=loaded&&ready?currentPlan():null;
      apply.disabled=busy||bindingDirty||!result||!!result.error||!result.objects;
      summary.textContent=!ready?"미리보기 반영이 완료되면 저장·적용할 수 있습니다.":!result?"템플릿을 선택하면 적용 범위를 확인할 수 있습니다.":
        result.error||(result.pages+"개 페이지 · "+result.objects+"개 개체에 적용 / 현재 보고서의 미대응 페이지 "+result.skipped+"개 / 템플릿의 미사용 페이지 "+result.unused+"개"+(result.cellsSkipped?" / 없는 표 셀 "+result.cellsSkipped+"개 제외":"")+(isExternal()?". 같은 구성의 페이지에 연결을 재사용합니다.":". 같은 구성의 페이지는 순서대로 대응합니다.")+(bindingDirty?" 변경한 연결을 저장한 뒤 적용하세요.":""));
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
      revision=data.revision;items=data.items;loaded=null;loadedId="";sourceDocument=null;bindingDirty=false;mappingLayout="";select.textContent="";el("option",select,"PPT 템플릿 선택").value="";
      items.filter(function(i){return i.experiment===options.experiment;}).forEach(function(i){el("option",select,i.name).value=i.id;});
      select.value=selected||"";if(select.value)read(preserveName);
    }
    function list(selected){var preserved=initialized?name.value:null;request("","GET",null,function(data){initialized=true;book(data,selected,preserved);});}
    function read(preserveName){loaded=null;if(!select.value){loadedId="";bindingDirty=false;sourceDocument=null;mappingLayout="";render();return;}
      request("/"+select.value,"GET",null,function(data){loaded=data.content;loadedId=select.value;sourceDocument=data.sourceDocument||null;bindingDirty=false;mappingLayout="";revision=data.revision;name.value=typeof preserveName==="string"?preserveName:loaded.name;});}
    function save(replace){
      if((replace?update:add).disabled)return;
      var value;try{value=replace&&isExternal()?copy(loaded):capture(options.layout(),options.state(),options.experiment,name.value.trim());value.name=name.value.trim();if(!value.name)throw new Error("템플릿 이름을 입력하세요.");}
      catch(error){message(error.message);return;}
      if(replace&&!isExternal()&&!window.confirm("선택한 템플릿의 이름·서식을 현재 내용으로 바꿀까요?"))return;
      request(replace?"/"+select.value:"",replace?"PUT":"POST",{revision:revision,content:value},function(data){book(data,data.selectedId);});
    }
    select.onchange=function(){if(bindingDirty&&!window.confirm("저장하지 않은 연결 변경을 버리고 다른 템플릿을 열까요?")){select.value=loadedId;return;}read();};
    host.addEventListener("toggle",function(){if(host.open&&!initialized&&!busy)list();});
    // Old Chromium does not consistently emit toggle for <details>.
    host.querySelector("summary").onclick=function(){if(!initialized&&!busy)list();};
    render();return {lock:function(value){locked=value;render();},refresh:render};
  }
  window.RistPptTemplates={capture:capture,plan:plan,externalPlan:externalPlan,merge:merge,Manager:Manager};
}());
