(function () {
  "use strict";
  var config = JSON.parse(document.getElementById("editor-config").textContent);
  var project = config.project || (config.prefix.indexOf("/tem/") >= 0 ? "TEM" : config.prefix.indexOf("/raman/") >= 0 ? "RAMAN" : "FT-IR");
  var workflow = window.parent !== window && project !== "TEM" && /(?:^|[?&])workflow=1(?:&|$)/.test(window.location.search);
  if (workflow) document.body.classList.add("workflow-embedded", "workflow-edit");
  var back = document.getElementById("analysis-back");
  back.href = project === "TEM" ? "/tem" : project === "RAMAN" ? "/raman" : "/ftir";
  if (workflow) back.onclick = function (event) {
    event.preventDefault(); window.parent.postMessage({type:"rist-workflow-back"}, window.location.origin);
  };
  window.addEventListener("message", function (event) {
    if (!workflow || event.source !== window.parent || event.origin !== window.location.origin || !event.data || event.data.type !== "rist-workflow-stage") return;
    document.body.classList.toggle("workflow-edit", event.data.step !== 4);
    document.querySelector("h1").textContent = event.data.step === 4 ? "최종 보고서 저장·전송" : "보고서 미리보기·편집";
  });
  if (project === "TEM") {
    document.getElementById("recipient-preview").textContent = "읽기 전용 보고서";
    document.getElementById("analysis-help").textContent = "페이지 관리와 자유편집 도구로 글자·이미지·표·도형을 수정할 수 있습니다. 수정본 PPT·PDF가 최종 ZIP에 포함되며 원본 분석 데이터는 유지됩니다. 재분석은 TEM 화면에서 진행하세요. 코팅층 두께를 수정하면 전체 평균도 함께 확인·수정하세요. 전송에는 기존 SSO 인증과 전송 권한이 필요합니다.";
  }
  var currentId = config.jobId, model = null, dirty = false, busy = false;
  var editor = document.getElementById("editor"), status = document.getElementById("status");
  var taskProgress = window.RistFileProgress.monitor(status);
  var save = document.getElementById("save"), send = document.getElementById("send"), download = document.getElementById("download");
  var inputs = {}, sections = [], auxiliary = {};
  var saved = "", timer = null, composing = false, cellEditing = false, sending = false, activeRevision = null, retries = 0;
  var refs = {}, variables = [], layoutSequence = 0, canvas = null;
  var mailEditor = null;
  var pptxPassword = null;
  var pptTemplates = null, pptLayout = null;
  var pageEdits = {hidden:[],added:[]}, pageFields = [], pageBox = null, pageEditing = false, structureDirty = false, layoutReady = false, layoutStructure = "";
  var deletedPageGroups=[];
  var labels = {requestNumber:"의뢰번호",limsExperimentCode:"실험코드",equipmentCode:"실험장비",operatorId:"실험자"};
  var direct = new window.RistDirectEditor({
    editing:cellActivity,
    field:function (ref) { return refs[ref]; }, variables:variableOptions,
    shared:function (ref) { return !!(refs[ref] && refs[ref].getAttribute("data-common")); },
    canInsert:function (input) { return input !== inputs.completionDate && !Object.keys(labels).some(function (key) { return input === inputs[key]; }); },
    onPage:function () { pageAvailability();if(canvas)canvas.page(direct.current()); },
    onReorder:reorderPages,
    onSelection:pageAvailability,onMoveSelection:moveSelectedPages,
    onLayout:function (value) {
      pptLayout = value;
      pageEditing = value.pageEditing === true;
      document.getElementById("page-actions").hidden = !pageEditing;
      document.getElementById("page-selection-help").hidden = !pageEditing;
      if(canvas){canvas.layout(value);canvas.page(direct.current());}
      availability();
    }
  });
  if(window.RistCanvasEditor) canvas=new window.RistCanvasEditor({
    editing:cellActivity,
    changed:changed,message:message,mode:function(value){direct.canvasMode(value);},
    upload:function(file,done,savedGraph){
      if(busy||activeRevision){done(new Error("보고서 저장이 끝난 뒤 이미지를 첨부하세요."));return;}
      busy=true;window.clearTimeout(timer);availability();message("이미지를 확인하고 업로드하는 중입니다.");
      var xhr=new XMLHttpRequest();xhr.open("POST",url(currentId,savedGraph?"/editor-graphs":"/editor-images"),true);xhr.timeout=60000;
      xhr.setRequestHeader("X-Requested-With","RIST-Report-Editor");xhr.setRequestHeader("Content-Type",file.type||"application/octet-stream");
      function complete(error,value){busy=false;availability();done(error,value);schedule();}
      xhr.onload=function(){var value;try{value=JSON.parse(xhr.responseText);}catch(_){complete(new Error("이미지 업로드 응답을 확인하세요."));return;}
        complete(xhr.status>=200&&xhr.status<300?null:new Error((value.error&&value.error.message)||value.message||"이미지 업로드 실패"),value);};
      xhr.onerror=xhr.ontimeout=function(){complete(new Error("이미지 업로드 연결이 끊겼습니다. 다시 첨부하세요."));};xhr.send(file);
    }
  });
  if(canvas && window.RistPptTemplates){
    var templateHost=document.createElement("div"),canvasHost=document.getElementById("canvas-tools");
    canvasHost.parentNode.insertBefore(templateHost,canvasHost);
    pptTemplates=new window.RistPptTemplates.Manager({host:templateHost,experiment:project.replace("-",""),
      layout:function(){return pptLayout;},state:function(){return canvas.value();},apply:function(edits){return canvas.applyStyles(edits);}});
  }
  if(workflow && canvas && window.RistSavedGraphs) new window.RistSavedGraphs({
    canvas:canvas,job:function(){return currentId;},message:message
  });
  var lookup = null;
  var conditions = window.RistReportConditionOptions ? new window.RistReportConditionOptions(config.conditionOptions || {}) : null;
  function message(text, error) { status.textContent = text; status.className = error ? "error" : ""; }
  function url(id, tail) { return config.prefix + "/" + encodeURIComponent(id) + (tail || ""); }
  function request(path, method, body, done) {
    var xhr = new XMLHttpRequest(); xhr.open(method, path, true); xhr.timeout = 60000;
    xhr.setRequestHeader("X-Requested-With", "RIST-Report-Editor");
    xhr.setRequestHeader("X-Request-Id", "report-editor-" + Date.now());
    if (body) xhr.setRequestHeader("Content-Type", "application/json");
    xhr.onload = function () {
      var result; try { result = JSON.parse(xhr.responseText); } catch (_) { done(new Error("서버 응답을 확인할 수 없습니다. 로그인 상태를 확인하세요.")); return; }
      if (xhr.status < 200 || xhr.status >= 300) {
        var error = new Error((result.error && result.error.message) || result.message || (typeof result.detail === "string" && result.detail) || "요청에 실패했습니다.");
        error.status = xhr.status; error.code = result.code; done(error); return;
      } done(null, result);
    };
    xhr.onerror = xhr.ontimeout = function () { done(new Error("연결이 끊겼습니다. 원본은 유지됩니다. 다시 시도하세요.")); };
    xhr.send(body ? JSON.stringify(body) : null);
  }
  function availability() {
    if (busy || sending) taskProgress.start(); else taskProgress.stop();
    save.disabled = busy || !model;
    send.disabled = busy || dirty || !!activeRevision || !model;
    download.setAttribute("aria-disabled", String(busy || dirty || !!activeRevision || !model));
    Array.prototype.forEach.call(document.querySelectorAll("#editor input,#editor textarea,#mail-editor textarea"),function (input) { input.disabled = sending; });
    if (lookup) lookup.availability();
    if (conditions) conditions.lock(sending);
    if (mailEditor) mailEditor.lock(sending);
    if (pptxPassword) pptxPassword.lock(busy || sending || !!activeRevision);
    direct.lock(sending);
    if(canvas)canvas.lock(busy||sending||!!activeRevision||structureDirty);
    if(pptTemplates)pptTemplates.lock(busy||sending||dirty||!!activeRevision||structureDirty||!layoutReady);
    pageAvailability();
    if (workflow) window.parent.postMessage({type:"rist-workflow-editor-state", jobId:currentId,
      ready:!!model, busy:busy || dirty || !!activeRevision}, window.location.origin);
  }
  function schedule(delay) {
    window.clearTimeout(timer);
    if (dirty && !composing && !cellEditing && !busy) timer = window.setTimeout(saveChanges, delay || 650);
  }
  function cellActivity(value) {cellEditing=value;window.clearTimeout(timer);if(!value)schedule();}
  function changed() {
    if (mailEditor) mailEditor.refreshRecipient();
    // Collect tables and canvas patches only once per keystroke on low-end PCs.
    var value = collect();
    structureDirty = structure(value.pageEdits) !== layoutStructure;
    var conditionLabelsChanged = false;
    sections.forEach(function (section) {
      (section.conditionLabels || []).forEach(function (item) {
        var label = (item.row[0].value === "공통" ? "" : item.row[0].value + " · ") + item.row[1].value;
        if (item.label.firstChild.nodeValue !== label) conditionLabelsChanged = true;
        item.label.firstChild.nodeValue = label;
      });
    });
    if (conditions && conditionLabelsChanged) conditions.refresh();
    direct.sync();
    dirty = JSON.stringify(value) !== saved; retries = 0;
    message(busy ? "PPT 생성 중입니다. 추가 입력도 이어서 자동 반영합니다." : dirty ? (cellEditing ? "셀 입력 중 · Enter 또는 다른 곳을 클릭하면 자동 저장합니다. Esc는 이번 입력 취소입니다." : "입력이 멈추면 자동 저장하고 PPT 미리보기에 반영합니다.") : "모든 변경 내용이 반영되었습니다.");
    availability(); schedule();
  }
  function watch(input) {
    input.addEventListener("input", changed);
    input.addEventListener("compositionstart", function () { composing = true; window.clearTimeout(timer); });
    input.addEventListener("compositionend", function () { composing = false; changed(); });
  }
  function element(tag, parent, text) { var e = document.createElement(tag); if (text != null) e.textContent = text; if (parent) parent.appendChild(e); return e; }
  function field(parent, label, value, multiline, limit) {
    var wrap = element("label", parent, label), input = element(multiline ? "textarea" : "input", wrap);
    if (!multiline) input.type = "text";
    input.value = value || ""; input.maxLength = limit || 4000; input.setAttribute("aria-label", label);
    input.placeholder = "미입력 · 빈칸으로 유지하거나 내용을 입력하세요";
    watch(input); return input;
  }
  function bind(ref, input) { refs[ref] = input; return input; }
  function structure(value) {
    value = value || {hidden:[],added:[]};
    return JSON.stringify({hidden:value.hidden,order:value.order||[],added:value.added.map(function (item) { return {id:item.id,after:item.after}; })});
  }
  function pageAvailability() {
    var blocked = !pageEditing || !layoutReady || busy || sending || !!activeRevision || structureDirty || !direct || !direct.current();
    document.getElementById("page-add").disabled = blocked || pageFields.length >= 50 || direct.count() >= 200;
    var ids=direct?direct.pageIds():[],selected=direct?direct.selectedPageIds():[];
    var remove=document.getElementById("page-delete");
    remove.disabled = blocked || !selected.length || selected.length >= direct.count();
    var removeLabel=selected.length>1?"선택한 "+selected.length+"개 페이지 삭제":"선택한 페이지 삭제";
    remove.setAttribute("aria-label",removeLabel);remove.title=selected.length>=direct.count()?"최소 1개 페이지는 남겨야 합니다.":removeLabel;
    document.getElementById("page-restore").disabled = !pageEditing || busy || sending || !!activeRevision || !pageEdits.hidden.length;
    document.getElementById("page-up").disabled=blocked||!selected.length||ids.indexOf(selected[0])<=0;
    document.getElementById("page-down").disabled=blocked||!selected.length||ids.indexOf(selected[selected.length-1])>=ids.length-1;
    if(direct)direct.sidebarLock(blocked,sending);
  }
  [["page-add","add","페이지 추가"],["page-delete","trash","선택한 페이지 삭제"],["page-restore","undo","최근 삭제 취소"],["page-up","up","선택한 페이지 위로"],["page-down","down","선택한 페이지 아래로"]].forEach(function(item){window.RistReportIcons(document.getElementById(item[0]),item[1],item[2]);});
  function ensurePageOrder() {
    var order=(pageEdits.order||[]).slice(),previous=null;
    direct.pageIds().forEach(function(id){
      if(order.indexOf(id)<0)order.splice(previous?order.indexOf(previous)+1:0,0,id);
      previous=id;
    });
    pageEdits.order=order;return order;
  }
  function reorderPages(id,target,after) {
    if(!pageEditing||!layoutReady||busy||sending||activeRevision||structureDirty||id===target)return;
    var visible=direct.pageIds();if(visible.indexOf(id)<0||visible.indexOf(target)<0)return;
    var selected=direct.selectedPageIds();if(selected.indexOf(id)<0)selected=[id];
    if(selected.indexOf(target)>=0)return;
    var prior=pageEdits.order,order=ensurePageOrder(),before=order.join("|");
    order=order.filter(function(value){return selected.indexOf(value)<0;});
    var at=order.indexOf(target)+(after?1:0);
    order=order.slice(0,at).concat(selected,order.slice(at));pageEdits.order=order;
    if(before===order.join("|")){pageEdits.order=prior;return;}
    direct.selectOnNext(id,selected);pageChanged();
  }
  function moveSelectedPages(delta) {
    var control=document.getElementById(delta<0?"page-up":"page-down");if(control.disabled)return;
    var ids=direct.pageIds(),selected=direct.selectedPageIds();if(!selected.length)return;
    var at=ids.indexOf(delta<0?selected[0]:selected[selected.length-1]);
    var active=direct.current().id;
    reorderPages(selected.indexOf(active)>=0?active:selected[0],ids[at+delta],delta>0);
  }
  ["page-up","page-down"].forEach(function(id,index){document.getElementById(id).onclick=function(){
    moveSelectedPages(index?1:-1);
  };});
  function pageRow(item, open) {
    var block = element("details",pageBox); block.open = !!open;
    var summary = element("summary",block,item.title || "추가 페이지");
    var title = bind("page/" + item.id + "/title",field(block,"추가 페이지 제목",item.title,false,100));
    var body = bind("page/" + item.id + "/body",field(block,"추가 페이지 본문",item.body,true,2000));
    element("p",block,"내용이 많으면 페이지를 나누어 추가하세요. 페이지 안의 글자 크기는 내용 길이에 맞춰 조절합니다.").className = "muted";
    title.addEventListener("input",function () { summary.textContent = title.value || "추가 페이지"; });
    pageFields.push({id:item.id,after:item.after,title:title,body:body,block:block});
    block.hidden = pageEdits.hidden.indexOf(item.id) >= 0;
    return title;
  }
  function pageChanged() {
    structureDirty = true;
    pageFields.forEach(function (item) { item.block.hidden = pageEdits.hidden.indexOf(item.id) >= 0; });
    changed();
  }
  document.getElementById("page-add").onclick = function () {
    if (this.disabled) return;
    var id = "added-" + Date.now().toString(36) + "-" + Math.random().toString(36).slice(2,10);
    var order=ensurePageOrder();order.splice(order.indexOf(direct.current().id)+1,0,id);
    var title = pageRow({id:id,after:direct.current().id,title:"추가 페이지",body:""},true);
    direct.selectOnNext(id); pageChanged(); title.focus();
  };
  document.getElementById("page-delete").onclick = function () {
    if (this.disabled) return;
    var page = direct.current();
    var selected=direct.selectedPageIds();if(!selected.length||selected.length>=direct.count())return;
    var label="선택한 "+selected.length+"개 페이지";
    if (!window.confirm(label+"를 수정본에서 삭제할까요? 원본과 분석 데이터는 보존됩니다.")) return;
    if(pageEdits.order&&pageEdits.order.length)ensurePageOrder();
    pageEdits.hidden=pageEdits.hidden.concat(selected);deletedPageGroups.push(selected.slice());
    direct.selectOnNext(page.id);pageChanged();
  };
  document.getElementById("page-restore").onclick = function () {
    if (this.disabled) return;
    var group=deletedPageGroups.pop()||[pageEdits.hidden[pageEdits.hidden.length-1]];
    group=group.filter(function(id){return pageEdits.hidden.indexOf(id)>=0;});if(!group.length)return;
    pageEdits.hidden=pageEdits.hidden.filter(function(id){return group.indexOf(id)<0;});
    direct.selectOnNext(group[0],group);pageChanged();
  };
  function variableOptions() {
    var result = Object.keys(labels).map(function (key) { return {name:labels[key],value:inputs[key] ? inputs[key].value : ""}; });
    result.push({name:"완료일",value:inputs.completionDate ? inputs.completionDate.value : ""});
    sections.forEach(function (section) {
      if (section.sectionId !== "experiment_conditions") return;
      (section.rows || []).forEach(function (row) {
        if (row[0].value === "공통" && row[1].value.trim()) result.push({name:"공통." + row[1].value.trim(),value:row[2].value});
      });
    });
    variables.forEach(function (item) { result.push({name:item.name,value:item.value.value + (item.value.value && item.unit.value ? " " + item.unit.value : "")}); });
    return result;
  }
  function commonVariables(parent) {
    var box = element("section", parent); element("h2", box, "추가 공통 항목");
    element("p",box,"이름·값·단위를 등록한 뒤 PPT 문구에서 ‘공통 정보 삽입’을 사용하세요. 연결한 곳만 함께 바뀝니다.").className = "muted";
    var list = element("div",box), addRow = element("div",box); addRow.className = "variable-add-row";
    var name = element("input",addRow); name.placeholder = "새 항목 이름 (예: 레이저 파장)";
    name.maxLength = 60; name.setAttribute("aria-label","새 공통 항목 이름");
    var add = element("button",addRow); add.id = "variable-add"; add.type = "button";
    window.RistReportIcons(add,"plus","공통 항목 추가");
    function row(item) {
      var block = element("div",list); block.className = "variable-row";
      element("strong",block,item.name);
      var entry = {name:item.name,value:field(block,item.name + " · 값",item.value,false,1000),unit:field(block,item.name + " · 단위",item.unit,false,40)};
      variables.push(entry);
      var remove = element("button",block,"항목 삭제");
      remove.onclick = function () {
        if (sending) return;
        function references(text) {
          var found = false; text.replace(/\{\{\s*([^{}\n]+?)\s*\}\}/g,function (_,token) { if (token.trim() === entry.name) found = true; }); return found;
        }
        var used = Object.keys(refs).some(function (key) { return references(refs[key].value); }) ||
          variables.some(function (v) { return v !== entry && (references(v.value.value) || references(v.unit.value)); });
        if (used) { message("사용 중인 공통 항목입니다. 문구의 연결을 먼저 제거하세요.",true); return; }
        variables.splice(variables.indexOf(entry),1); block.parentNode.removeChild(block); changed();
      };
    }
    (model.variables || []).forEach(row);
    add.onclick = function () {
      if (sending) return;
      var value = name.value.trim();
      if (!value || /[{}\r\n]/.test(value) || value.indexOf("공통.") === 0 || variables.length >= 32 || variableOptions().some(function (v) { return v.name === value; })) {
        message("중복되지 않는 항목 이름을 입력하세요. 최대 32개까지 등록할 수 있습니다.",true); return;
      }
      row({name:value,value:"",unit:""}); name.value = ""; changed();
    };
  }
  function requestLookup(parent) {
    var box = element("div", parent), actions = element("div", box); actions.className = "actions";
    var loadButton = element("button", actions, "의뢰 조회"), previous = element("button", actions, "이전 목록"), next = element("button", actions, "다음 목록");
    loadButton.id = "request-load"; previous.id = "request-previous"; next.id = "request-next";
    var label = element("label", box, "의뢰 검색·선택"), combo = element("div", box); combo.className = "request-combo";
    var filter = element("input", combo), toggle = element("button", combo, "▾");
    filter.id = "request-search"; label.htmlFor = filter.id;
    filter.type = "text"; filter.maxLength = 1000; filter.autocomplete = "off";
    filter.placeholder = "의뢰번호·시료·시험명으로 검색 후 선택";
    filter.setAttribute("aria-label", "의뢰 검색·선택"); filter.setAttribute("role", "combobox");
    filter.setAttribute("aria-autocomplete", "list"); filter.setAttribute("aria-expanded", "false");
    filter.setAttribute("aria-controls", "request-results"); filter.setAttribute("aria-describedby", "request-info");
    toggle.id = "request-toggle"; toggle.type = "button"; toggle.className = "request-toggle";
    toggle.setAttribute("aria-label", "의뢰 목록 열기"); toggle.setAttribute("aria-controls", "request-results"); toggle.setAttribute("aria-expanded", "false");
    var results = element("div", box); results.id = "request-results"; results.className = "request-results";
    results.setAttribute("role", "listbox"); results.setAttribute("aria-label", "조회된 의뢰 목록"); results.hidden = true;
    var info = element("p", box, "의뢰 조회 후 한 칸에서 검색·선택하세요. 목록은 200개씩 조회하며, 의뢰번호 직접 입력도 가능합니다.");
    info.id = "request-info"; info.className = "muted"; info.setAttribute("role", "status");
    var items = [], matches = [], page = 0, loading = false, selected = null, selectedText = "", active = -1, typing = false, lookupError = "";
    function close() {
      results.hidden = true; active = -1; filter.removeAttribute("aria-activedescendant");
      filter.setAttribute("aria-expanded", "false"); toggle.setAttribute("aria-expanded", "false");
    }
    function position() {
      if (results.hidden) return;
      var rect = combo.getBoundingClientRect(), width = Math.min(rect.width, window.innerWidth - 16);
      var below = window.innerHeight - rect.bottom - 12, above = rect.top - 12, up = below < 180 && above > below;
      var height = Math.max(40,Math.min(260,up ? above : below));
      results.style.width = Math.max(0,width) + "px"; results.style.maxHeight = height + "px";
      results.style.left = Math.max(8,Math.min(rect.left,window.innerWidth - width - 8)) + "px";
      results.style.top = (up ? Math.max(8,rect.top - Math.min(height,results.scrollHeight) - 4) : rect.bottom + 4) + "px";
    }
    function open() {
      if (sending || loading) return;
      results.hidden = false; filter.setAttribute("aria-expanded", "true"); toggle.setAttribute("aria-expanded", "true"); position();
    }
    function controls() {
      loadButton.disabled = sending || loading;
      previous.disabled = sending || loading || page <= 1;
      next.disabled = sending || loading || items.length < 200;
      filter.disabled = toggle.disabled = sending || loading;
      if (sending || loading) close();
    }
    function text(item) {
      return [item.requestNumber, item.experimentCode || item.testMethodCode, item.experimentName || item.testMethodName,
        item.sampleName, item.requestDate, item.customerRequestName, item.requestStateName].filter(Boolean).join(" · ");
    }
    function choose(index) {
      if (sending || loading || !items[index]) return;
      selected = items[index]; selectedText = text(selected); filter.value = selectedText; close();
      inputs.requestNumber.value = selected.requestNumber || "";
      inputs.limsExperimentCode.value = selected.experimentCode || selected.testMethodCode || "";
      var equipment = selected.equipmentCode || selected.deviceCode || selected.instrumentCode;
      if (equipment) inputs.equipmentCode.value = equipment;
      changed(); filter.focus(); close();
      info.textContent = "선택한 의뢰를 자동 반영합니다. " + (equipment ? "" : "조회 결과에 장비 코드가 없어 기존 값을 유지합니다. 장비를 확인하세요. ") + "실험자는 SSO 계정을 유지합니다.";
    }
    function render() {
      results.textContent = ""; matches = []; active = -1; filter.removeAttribute("aria-activedescendant");
      var query = selected && filter.value === selectedText ? "" : filter.value.trim().toLowerCase();
      items.forEach(function (item, index) {
        var description = text(item); if (query && description.toLowerCase().indexOf(query) < 0) return;
        var option = element("button", results, description); option.type = "button"; option.tabIndex = -1;
        option.id = "request-result-" + index; option.setAttribute("role", "option");
        option.setAttribute("aria-selected", String(item === selected)); option.title = description;
        option.onmousedown = function (event) { event.preventDefault(); };
        option.onclick = function () { choose(index); }; matches.push(option);
      });
      if (!matches.length) element("p",results,page ? "일치하는 의뢰가 없습니다. 검색어 또는 이전·다음 목록을 확인하세요." : "먼저 ‘의뢰 조회’를 눌러 목록을 불러오세요.").className = "muted";
      if (page) info.textContent = lookupError + page + "페이지 · " + items.length + "건 중 " + matches.length + "건 표시. 검색만으로 전송 정보가 바뀌지 않습니다.";
      position();
    }
    function highlight(index) {
      active = index; matches.forEach(function (item, i) { item.classList.toggle("is-active",i === index); });
      var option = matches[index]; if (!option) return;
      filter.setAttribute("aria-activedescendant",option.id);
      if (option.offsetTop < results.scrollTop) results.scrollTop = option.offsetTop;
      else if (option.offsetTop + option.offsetHeight > results.scrollTop + results.clientHeight) results.scrollTop = option.offsetTop + option.offsetHeight - results.clientHeight;
    }
    function loadPage(number) {
      if (loading || sending) return;
      var progress=window.RistFileProgress.monitor(info);progress.start();
      loading = true; lookupError = ""; controls(); info.textContent = "의뢰 목록 조회 중입니다.";
      request("/api/v1/requests?page=" + number + "&pageSize=200&experimentType=" + encodeURIComponent(project), "GET", null, function (error, result) {
        progress.stop();
        loading = false;
        if (error) { lookupError = "조회 실패: " + error.message + " 직접 입력은 계속 사용할 수 있습니다. "; info.textContent = lookupError; }
        else { page = number; items = Array.isArray(result.items) ? result.items : []; render(); }
        controls();
        if (!error && !sending) { filter.focus(); open(); }
      });
    }
    loadButton.onclick = function () { loadPage(1); };
    previous.onclick = function () { loadPage(page - 1); };
    next.onclick = function () { loadPage(page + 1); };
    filter.oninput = function () { if (!sending && !loading) { render(); open(); } };
    filter.onfocus = function () { render(); open(); };
    filter.oncompositionstart = function () { typing = true; };
    filter.oncompositionend = function () { typing = false; render(); open(); };
    filter.onkeydown = function (event) {
      if (sending || loading || typing || event.isComposing || event.keyCode === 229) return;
      if (event.key === "ArrowDown" || event.key === "ArrowUp") {
        event.preventDefault(); if (results.hidden) { render(); open(); }
        if (matches.length) highlight(active < 0 ? (event.key === "ArrowDown" ? 0 : matches.length - 1) : Math.max(0,Math.min(matches.length - 1,active + (event.key === "ArrowDown" ? 1 : -1))));
      } else if (event.key === "Enter" && !results.hidden) {
        event.preventDefault(); if (active >= 0) matches[active].click();
      } else if (event.key === "Escape") { if (!results.hidden) { event.preventDefault(); event.stopPropagation(); } close(); }
      else if (event.key === "Tab") close();
    };
    toggle.onmousedown = function (event) { event.preventDefault(); };
    toggle.onclick = function () { if (results.hidden) { filter.focus(); render(); open(); } else close(); };
    function outside(event) { if (!combo.contains(event.target) && !results.contains(event.target)) close(); }
    function scrolled(event) { if (!event.target.nodeType || !results.contains(event.target)) close(); }
    document.addEventListener("mousedown",outside); document.addEventListener("touchstart",outside);
    document.addEventListener("focusin",outside); window.addEventListener("scroll",scrolled,true); window.addEventListener("resize",close);
    inputs.requestNumber.addEventListener("input", function () {
      if (selected && inputs.requestNumber.value.trim() !== String(selected.requestNumber || "").trim()) {
        if (inputs.limsExperimentCode.value === String(selected.experimentCode || selected.testMethodCode || "")) inputs.limsExperimentCode.value = "";
        filter.value = ""; selected = null; selectedText = ""; close(); render();
        info.textContent = "의뢰번호를 직접 변경했습니다. 해당 LIMS 실험코드도 확인해 입력하세요."; changed();
      }
    });
    render(); controls(); return {availability:controls,destroy:function () {
      document.removeEventListener("mousedown",outside); document.removeEventListener("touchstart",outside);
      document.removeEventListener("focusin",outside); window.removeEventListener("scroll",scrolled,true); window.removeEventListener("resize",close);
    }};
  }
  function show(data) {
    if (lookup) lookup.destroy();
    if (conditions) conditions.reset();
    model = data.document; editor.textContent = ""; sections = []; auxiliary = {}; inputs = {}; refs = {}; variables = [];
    pageEdits = model.pageEdits || {hidden:[],added:[]}; pageFields = [];deletedPageGroups=[];structureDirty = false;
    if(canvas)canvas.load(model.canvasEdits||{});
    var top = element("section", editor); element("h2", top, "공통 정보 / 의뢰 조회");
    inputs.title = bind("title",field(top, "보고서 제목", model.title, false, 200));
    inputs.completionDate = bind("completionDate",field(top,"완료일",model.completionDate,false,10)); inputs.completionDate.type = "date";
    if (project === "TEM") element("p",top,"공통 정보는 표지·분석 이력과 최종 전송 정보에 함께 반영됩니다. 오른쪽에서 페이지를 관리하고 자유편집 도구로 본문을 편집하세요. 원본은 별도로 보존됩니다.").className = "muted";
    var grid = element("div", top); grid.className = "grid";
    Object.keys(labels).forEach(function (key) { inputs[key] = bind("transfer/" + key,field(grid, labels[key], model.transfer[key], false, key === "limsExperimentCode" ? 50 : 100)); });
    lookup = requestLookup(top);
    (model.sections || []).forEach(function (section) {
      var block = element("details", editor);
      block.hidden = section.sectionId !== "experiment_conditions";
      block.open = section.sectionId === "experiment_conditions";
      var summary = element("summary", block, section.heading);
      var source = element("span", summary, " · " + (section.source === "user" ? "사용자 수정" : section.source === "llm" ? "로컬 AI 초안" : "보고서 내용")); source.className = "source";
      var entry = {sectionId:section.sectionId, heading:field(block,"항목 제목 · " + section.heading,section.heading,false,200), paragraphs:[], bullets:[], rows:null};
      var base = "section/" + section.sectionId + "/";
      bind(base + "heading",entry.heading); entry.heading.parentNode.hidden = true;
      (section.paragraphs.length ? section.paragraphs : (section.columns || section.bullets.length ? [] : [""])).forEach(function (text, i) {
        entry.paragraphs.push(bind(base + "paragraphs/" + i,field(block,section.heading + " · 문단 " + (i+1),text,true)));
      });
      section.bullets.forEach(function (text, i) { entry.bullets.push(bind(base + "bullets/" + i,field(block,section.heading + " · 항목 " + (i+1),text,true))); });
      if (section.columns) {
        var wrapper = element("div", block); wrapper.className = "table-wrap";
        var table = element("table", wrapper), head = element("tr", element("thead", table));
        section.columns.forEach(function (column) { element("th", head, column); });
        var body = element("tbody", table); entry.rows = [];
        section.rows.forEach(function (row, r) {
          var tr = element("tr", body), cells = [];
          row.forEach(function (value, c) {
            var choice = section.sectionId === "experiment_conditions" && c === 2 && conditions && conditions.supports(row[1],value);
            var cell = element(choice ? "input" : "textarea", element("td", tr));
            if (choice) cell.type = "text";
            cell.value = value; cell.maxLength = 4000;
            cell.setAttribute("aria-label",section.heading + " " + (r+1) + "행 " + section.columns[c]);
            cell.placeholder = "미입력"; watch(cell); cells.push(bind(base + "rows/" + r + "/" + c,cell));
          }); entry.rows.push(cells);
        });
        if (section.sectionId === "experiment_conditions") {
          entry.conditionLabels = [];
          wrapper.hidden = true;
          if (conditions) conditions.mount(block);
          element("p",block,"공통 조건은 ‘공통.항목명’으로 문구에 연결할 수 있습니다. 샘플별 조건은 해당 샘플에만 적용됩니다.").className = "muted";
          if (config.conditionOptions && config.conditionOptions.fields.length) element("p",block,"선택지에서 고르거나 직접 입력하세요. 선택한 값은 PPT에 자동 반영됩니다.").className = "muted";
          var sample = element("details",block); element("summary",sample,"샘플별 조건");
          entry.rows.forEach(function (row) {
            if (row.length !== 3) return;
            var common = row[0].value === "공통";
            var label = element("label",common ? block : sample,(common ? "" : row[0].value + " · ") + row[1].value);
            // Keep even a blank label's text node so later direct edits update it.
            if (!label.firstChild) label.appendChild(document.createTextNode(""));
            entry.conditionLabels.push({label:label,row:row});
            label.appendChild(row[2]); if (common) row[2].setAttribute("data-common","true");
            if (conditions) conditions.attach(label,row[2],function () { return row[1].value; });
          });
          sample.hidden = !sample.querySelector("input,textarea");
        }
      } sections.push(entry);
    });
    commonVariables(editor);
    if (conditions) conditions.refresh();
    pageBox = element("div",editor);
    (pageEdits.added || []).forEach(function (item) { pageRow(item,false); });
    var mailPane = document.getElementById("mail-editor"); mailPane.textContent = "";
    pptxPassword = new window.RistPptxPassword({host:mailPane,value:model.pptxProtection,
      changed:changed,request:function(secret,done){
        busy=true;window.clearTimeout(timer);availability();
        request(url(currentId,"/pptx-password"),"POST",{password:secret},function(error,value){
          busy=false;done(error,value);availability();schedule();
        });
      }});
    var mail = element("details", mailPane); mail.open = true; element("summary", mail, "보고서 안내 메일 문안");
    element("p",mail,"입력 내용은 자동 저장됩니다. 실제 메일 발송은 보고서 전송 후 Spring/LIMS 연계에서 처리합니다.").className = "muted";
    ["email_subject","email_body"].forEach(function (key) {
      var isBody = key === "email_body";
      var input = bind("auxiliary/" + key,field(mail,isBody ? "메일 본문" : "메일 제목",model.auxiliary[key] || "",true));
      input.className = isBody ? "mail-body" : "mail-subject"; input.rows = isBody ? 12 : 2;
      auxiliary[key] = input;
    });
    mailEditor = new window.RistMailEditor({host:mail,body:auxiliary.email_body,before:auxiliary.email_subject.parentNode,
      value:model.deliveryEmail,changed:changed,requestNumber:function(){return inputs.requestNumber.value;},
      subject:auxiliary.email_subject,experiment:project.replace("-",""),
      context:function(){return window.RistMailTemplateContext.report(collect(),project,variableOptions());}});
    mail.addEventListener("compositionstart", function () { composing = true; window.clearTimeout(timer); });
    mail.addEventListener("compositionend", function () { composing = false; changed(); });
    saved = JSON.stringify(collect()); dirty = false;
    if (data.authenticatedOperatorId) {
      inputs.operatorId.readOnly = true;
      if (inputs.operatorId.value !== data.authenticatedOperatorId) { inputs.operatorId.value = data.authenticatedOperatorId; dirty = true; }
      element("p", top, "실험자는 연결된 SSO 계정으로 전송되며 보고서에도 자동 반영됩니다.").className = "muted";
    }
    refreshPreview();
    download.href = url(currentId,"/download");
    message(dirty ? "인증된 SSO 실험자 정보를 자동 반영합니다." : "포스코 양식의 PPT 페이지를 표시합니다. 수정 내용은 자동 저장·반영됩니다.");
    availability(); schedule();
  }
  function collect() {
    var result = {title:inputs.title.value,transfer:{},sections:[],auxiliary:{},completionDate:inputs.completionDate.value,
      variables:variables.map(function (v) { return {name:v.name,value:v.value.value,unit:v.unit.value}; })};
    ["requestNumber","limsExperimentCode","equipmentCode","operatorId"].forEach(function (key) { result.transfer[key] = inputs[key].value.trim(); });
    sections.forEach(function (section) {
      result.sections.push({sectionId:section.sectionId,heading:section.heading.value,
        paragraphs:section.paragraphs.map(function (e) { return e.value; }),bullets:section.bullets.map(function (e) { return e.value; }),
        rows:section.rows && section.rows.map(function (row) { return row.map(function (e) { return e.value; }); })});
    });
    Object.keys(auxiliary).forEach(function (key) { result.auxiliary[key] = auxiliary[key].value; });
    if(mailEditor) result.deliveryEmail = mailEditor.value();
    if(pptxPassword) result.pptxProtection = pptxPassword.value();
    result.pageEdits = {hidden:pageEdits.hidden.slice(),order:(pageEdits.order||[]).slice(),added:pageFields.map(function (item) {
      return {id:item.id,after:item.after,title:item.title.value,body:item.body.value};
    })};
    if(canvas)result.canvasEdits=canvas.value();
    return result;
  }
  function refreshPreview() {
    var seq = ++layoutSequence, id = currentId, savedStructure = structure(JSON.parse(saved).pageEdits);
    layoutReady = false; pageAvailability(); direct.loading();
    if(pptTemplates)pptTemplates.lock(true);
    if(canvas)canvas.loading();
    document.getElementById("recipient-preview").href = url(id,"/preview");
    request(url(id,"/edit-layout"),"GET",null,function (error,value) {
      if (seq !== layoutSequence || id !== currentId) return;
      if (error) { direct.error(error.message); return; }
      layoutReady = true; layoutStructure = savedStructure;
      structureDirty = structure(collect().pageEdits) !== layoutStructure;
      direct.setLayout(value,url(id,"/edit-slides"),structureDirty);
    });
  }
  document.getElementById("reload-preview").onclick = refreshPreview;
  function load(id) {
    busy = true; availability();
    request(url(id,"/editor-data"),"GET",null,function (error,data) {
      busy = false;
      if (error) {
        direct.error(error.message);
        message(error.message,true); availability();
        if (workflow) window.parent.postMessage({type:"rist-workflow-editor-error", jobId:id,
          status:error.status, code:error.code, message:error.message},window.location.origin);
        return;
      }
      currentId = id; show(data);
    });
  }
  function poll(id, previous, snapshot, attempts) {
    request(url(id),"GET",null,function (error,job) {
      if (error || job.status === "failed") {
        direct.stopProgress();
        busy = false; if (!error) activeRevision = null;
        message((error ? error.message : (job.error && job.error.message) || job.error || job.message) + " 입력 내용은 유지됩니다. ‘지금 반영 / 재시도’를 눌러 주세요.",true); availability(); return;
      }
      if (job.status === "completed") {
        direct.stopProgress();
        currentId = id; saved = snapshot; activeRevision = null; busy = false; retries = 0;
        dirty = JSON.stringify(collect()) !== saved;
        refreshPreview(); download.href = url(id,"/download");
        window.history.replaceState(null,"",url(id,"/edit"));
        try { window.localStorage.setItem("rist-report-revision",JSON.stringify({previousJobId:previous,job:job,time:Date.now()})); } catch (_) {}
        message(dirty ? "PPT가 갱신되었습니다. 그동안 추가한 수정 내용도 이어서 반영합니다." : "자동 저장 완료 · PPT 미리보기와 다운로드·전송에 수정 내용이 반영되었습니다.");
        availability(); schedule(); return;
      }
      if (attempts >= 300) { busy = false;direct.stopProgress(); message("생성이 계속 진행 중입니다. ‘지금 반영 / 재시도’로 진행 상태를 다시 확인하세요. 입력 내용은 유지됩니다.",true); availability(); return; }
      message((job.message || "수정 내용을 PPT에 반영 중입니다.") + " 추가 입력도 계속할 수 있습니다.");
      direct.progress(job.message || "PPT 수정본을 생성하는 중입니다.");
      // Small reports often finish in a few seconds. Detect that promptly,
      // but retain the low-frequency polling for long jobs or background tabs.
      window.setTimeout(function () { poll(id,previous,snapshot,attempts+1); },attempts < 6 && !document.hidden ? 500 : 1500);
    });
  }
  function saveChanges() {
    window.clearTimeout(timer);
    if (busy || !model || composing || cellEditing) return;
    if (activeRevision) {
      direct.progress("생성 중인 PPT 수정본을 확인하는 중입니다.");
      busy = true; availability(); poll(activeRevision.id,activeRevision.previous,activeRevision.snapshot,0); return;
    }
    if (!dirty) return;
    if(mailEditor && mailEditor.validate()){message(mailEditor.validate(),true);return;}
    busy = true; availability(); message("수정 내용을 저장하는 중입니다.");
    direct.progress("수정 내용을 저장하고 PPT 미리보기를 생성하는 중입니다.");
    var previous = currentId, value = collect(), snapshot = JSON.stringify(value);
    request(url(currentId,"/revisions"),"POST",value,function (error,job) {
      if (error) {
        direct.stopProgress();
        busy = false; availability();
        if (error.status === 429 && retries < 5) {
          retries += 1; message("다른 보고서를 생성 중입니다. 잠시 후 자동 반영을 다시 시도합니다."); schedule(Math.min(30000,3000 * retries));
        } else message(error.message + " 입력 내용은 유지됩니다. ‘지금 반영 / 재시도’를 눌러 주세요.",true);
        return;
      }
      activeRevision = {id:job.jobId,previous:previous,snapshot:snapshot}; poll(job.jobId,previous,snapshot,0);
    });
  }
  save.onclick = function () { retries = 0; if (!busy && model && !activeRevision) dirty = true; saveChanges(); };
  send.onclick = function () {
    if (busy || dirty || activeRevision || !model) return;
    var transfer = collect().transfer;
    if (!transfer.requestNumber || !transfer.limsExperimentCode || !transfer.equipmentCode || !transfer.operatorId) {
      message("전송하려면 의뢰번호·실험코드·실험장비·실험자를 입력하고 저장하세요.",true); return;
    }
    sending = true; busy = true; availability(); message("SSO 및 전송 권한을 확인하는 중입니다.");
    request(url(currentId,"/send"),"POST",{requestNumber:transfer.requestNumber,experimentCode:transfer.limsExperimentCode,equipmentCode:transfer.equipmentCode,operatorId:transfer.operatorId},function (error) {
      sending = false; busy = false; availability(); message(error ? error.message : "현재 수정본을 LIMS 전송 대기열에 등록했습니다.",!!error);
    });
  };
  download.onclick = function (event) {
    event.preventDefault();
    if (busy || dirty || activeRevision || !model) return;
    var id=currentId;
    window.RistFileSave.open({name:(inputs.requestNumber.value||project)+"-"+inputs.title.value,
      extension:".zip",mime:"application/zip",url:url(id,"/download"),
      ready:function(){return currentId===id&&!busy&&!dirty&&!activeRevision;}
    }).then(function(result){
      if(result.status!=="cancelled")message(result.status==="saved"?"선택한 위치에 보고서 패키지를 저장했습니다.":"보고서 패키지 다운로드를 시작했습니다. 저장 위치는 브라우저 설정을 따릅니다.");
    });
  };
  window.addEventListener("beforeunload",function (event) { if (dirty || busy || activeRevision) { event.preventDefault(); event.returnValue = "저장하지 않은 변경 또는 생성 중인 수정본이 있습니다."; } });
  load(currentId);
}());
