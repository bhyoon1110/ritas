(function () {
  "use strict";
  var config = JSON.parse(document.getElementById("editor-config").textContent);
  var project = config.project || (config.prefix.indexOf("/tem/") >= 0 ? "TEM" : config.prefix.indexOf("/raman/") >= 0 ? "RAMAN" : "FT-IR");
  var back = document.getElementById("analysis-back");
  back.href = project === "TEM" ? "/tem" : project === "RAMAN" ? "/raman" : "/ftir";
  if (project === "TEM") {
    document.getElementById("recipient-preview").textContent = "읽기 전용 보고서";
    document.getElementById("analysis-help").textContent = "전송에는 기존 SSO 인증과 전송 권한이 필요합니다. 이미지 교체·재분석은 TEM 화면에서 진행하세요. 문구·표 수정은 원본 분석 데이터와 별도로 최종 PPT에 반영됩니다. 코팅층 두께를 수정하면 전체 평균도 함께 확인·수정하세요.";
  }
  var currentId = config.jobId, model = null, dirty = false, busy = false;
  var editor = document.getElementById("editor"), status = document.getElementById("status");
  var save = document.getElementById("save"), send = document.getElementById("send"), download = document.getElementById("download");
  var inputs = {}, sections = [], auxiliary = {};
  var saved = "", timer = null, composing = false, sending = false, activeRevision = null, retries = 0;
  var refs = {}, variables = [], layoutSequence = 0;
  var labels = {requestNumber:"의뢰번호",limsExperimentCode:"실험코드",equipmentCode:"실험장비",operatorId:"실험자"};
  var direct = new window.RistDirectEditor({
    field:function (ref) { return refs[ref]; }, variables:variableOptions,
    shared:function (ref) { return !!(refs[ref] && refs[ref].getAttribute("data-common")); },
    canInsert:function (input) { return input !== inputs.completionDate && !Object.keys(labels).some(function (key) { return input === inputs[key]; }); }
  });
  var lookup = null;
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
        error.status = xhr.status; done(error); return;
      } done(null, result);
    };
    xhr.onerror = xhr.ontimeout = function () { done(new Error("연결이 끊겼습니다. 원본은 유지됩니다. 다시 시도하세요.")); };
    xhr.send(body ? JSON.stringify(body) : null);
  }
  function availability() {
    save.disabled = busy || !model;
    send.disabled = busy || dirty || !!activeRevision || !model;
    download.setAttribute("aria-disabled", String(busy || dirty || !!activeRevision || !model));
    Array.prototype.forEach.call(document.querySelectorAll("#editor input,#editor textarea,#mail-editor textarea"),function (input) { input.disabled = sending; });
    if (lookup) lookup.availability();
    direct.lock(sending);
  }
  function schedule(delay) {
    window.clearTimeout(timer);
    if (dirty && !composing && !busy) timer = window.setTimeout(saveChanges, delay || 1200);
  }
  function changed() {
    sections.forEach(function (section) {
      (section.conditionLabels || []).forEach(function (item) {
        item.label.firstChild.nodeValue = (item.row[0].value === "공통" ? "" : item.row[0].value + " · ") + item.row[1].value;
      });
    });
    direct.sync();
    dirty = JSON.stringify(collect()) !== saved; retries = 0;
    message(busy ? "PPT 생성 중입니다. 추가 입력도 이어서 자동 반영합니다." : dirty ? "입력이 멈추면 자동 저장하고 PPT 미리보기에 반영합니다." : "모든 변경 내용이 반영되었습니다.");
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
    var list = element("div",box), name = element("input",box); name.placeholder = "새 항목 이름 (예: 레이저 파장)";
    name.maxLength = 60; name.setAttribute("aria-label","새 공통 항목 이름");
    var add = element("button",box,"항목 추가"); add.id = "variable-add";
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
    var label = element("label", box, "현재 조회 목록 검색 (의뢰번호·시료·시험명)"), filter = element("input", label);
    filter.type = "search"; filter.maxLength = 100; filter.setAttribute("aria-label", "현재 의뢰 목록 검색");
    label = element("label", box, "조회된 의뢰 선택"); var select = element("select", label); select.id = "request-select";
    select.setAttribute("aria-label", "조회된 의뢰 선택");
    var info = element("p", box, "의뢰를 조회하여 선택하거나 아래에 직접 입력하세요. 목록은 200개씩 조회합니다.");
    info.className = "muted"; info.setAttribute("role", "status");
    var items = [], page = 0, loading = false, selected = null;
    function controls() {
      loadButton.disabled = sending || loading;
      previous.disabled = sending || loading || page <= 1;
      next.disabled = sending || loading || items.length < 200;
      select.disabled = sending || loading || !items.length;
    }
    function render() {
      select.textContent = ""; var option = element("option", select, "의뢰를 선택하세요"); option.value = "";
      var query = filter.value.trim().toLowerCase(), count = 0;
      items.forEach(function (item, index) {
        var text = [item.requestNumber, item.experimentCode || item.testMethodCode, item.experimentName || item.testMethodName,
          item.sampleName, item.requestDate, item.customerRequestName, item.requestStateName].filter(Boolean).join(" · ");
        if (query && text.toLowerCase().indexOf(query) < 0) return;
        option = element("option", select, text); option.value = String(index); count += 1;
      });
      if (page) info.textContent = page + "페이지 · " + items.length + "건 중 " + count + "건 표시. 다른 의뢰는 다음 목록에서 확인하세요.";
    }
    function loadPage(number) {
      if (loading || sending) return;
      loading = true; controls(); info.textContent = "의뢰 목록 조회 중입니다.";
      request("/api/v1/requests?page=" + number + "&pageSize=200&experimentType=" + encodeURIComponent(project), "GET", null, function (error, result) {
        loading = false;
        if (error) info.textContent = "조회 실패: " + error.message + " 직접 입력은 계속 사용할 수 있습니다.";
        else { page = number; items = Array.isArray(result.items) ? result.items : []; render(); }
        controls();
      });
    }
    loadButton.onclick = function () { loadPage(1); };
    previous.onclick = function () { loadPage(page - 1); };
    next.onclick = function () { loadPage(page + 1); };
    filter.oninput = render;
    select.onchange = function () {
      if (select.value === "" || sending || loading) return;
      selected = items[Number(select.value)]; if (!selected) return;
      inputs.requestNumber.value = selected.requestNumber || "";
      inputs.limsExperimentCode.value = selected.experimentCode || selected.testMethodCode || "";
      var equipment = selected.equipmentCode || selected.deviceCode || selected.instrumentCode;
      if (equipment) inputs.equipmentCode.value = equipment;
      info.textContent = "선택한 의뢰를 자동 반영합니다. " + (equipment ? "" : "조회 결과에 장비 코드가 없어 기존 값을 유지합니다. 장비를 확인하세요. ") + "실험자는 SSO 계정을 유지합니다.";
      changed();
    };
    inputs.requestNumber.addEventListener("input", function () {
      if (selected && inputs.requestNumber.value.trim() !== String(selected.requestNumber || "").trim()) {
        if (inputs.limsExperimentCode.value === String(selected.experimentCode || selected.testMethodCode || "")) inputs.limsExperimentCode.value = "";
        select.value = ""; selected = null;
        info.textContent = "의뢰번호를 직접 변경했습니다. 해당 LIMS 실험코드도 확인해 입력하세요."; changed();
      }
    });
    render(); controls(); return {availability:controls};
  }
  function show(data) {
    model = data.document; editor.textContent = ""; sections = []; auxiliary = {}; inputs = {}; refs = {}; variables = [];
    var top = element("section", editor); element("h2", top, "공통 정보 / 의뢰 조회");
    inputs.title = bind("title",field(top, "보고서 제목", model.title, false, 200));
    inputs.completionDate = bind("completionDate",field(top,"완료일",model.completionDate,false,10)); inputs.completionDate.type = "date";
    if (project === "TEM") element("p",top,"제목은 PPT 문서 속성에, 완료일·전송 정보는 최종 ZIP의 report-review.json에 저장됩니다. 페이지에 표시된 문구는 오른쪽에서 직접 수정하세요.").className = "muted";
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
            var cell = element("textarea", element("td", tr)); cell.value = value; cell.maxLength = 4000;
            cell.setAttribute("aria-label",section.heading + " " + (r+1) + "행 " + section.columns[c]);
            cell.placeholder = "미입력"; watch(cell); cells.push(bind(base + "rows/" + r + "/" + c,cell));
          }); entry.rows.push(cells);
        });
        if (section.sectionId === "experiment_conditions") {
          entry.conditionLabels = [];
          wrapper.hidden = true;
          element("p",block,"공통 조건은 ‘공통.항목명’으로 문구에 연결할 수 있습니다. 샘플별 조건은 해당 샘플에만 적용됩니다.").className = "muted";
          var sample = element("details",block); element("summary",sample,"샘플별 조건");
          entry.rows.forEach(function (row) {
            if (row.length !== 3) return;
            var common = row[0].value === "공통";
            var label = element("label",common ? block : sample,(common ? "" : row[0].value + " · ") + row[1].value);
            // Keep even a blank label's text node so later direct edits update it.
            if (!label.firstChild) label.appendChild(document.createTextNode(""));
            entry.conditionLabels.push({label:label,row:row});
            label.appendChild(row[2]); if (common) row[2].setAttribute("data-common","true");
          });
          sample.hidden = !sample.querySelector("textarea");
        }
      } sections.push(entry);
    });
    commonVariables(editor);
    var mailPane = document.getElementById("mail-editor"); mailPane.textContent = "";
    var mail = element("details", mailPane); mail.open = true; element("summary", mail, "보고서 안내 메일 문안");
    element("p",mail,"보고서와 함께 제공되는 안내 문안입니다. 입력 내용은 자동 저장되며, 입력칸의 아래쪽 모서리를 드래그해 높이를 더 늘릴 수 있습니다.").className = "muted";
    ["email_subject","email_body"].forEach(function (key) {
      var isBody = key === "email_body";
      var input = bind("auxiliary/" + key,field(mail,isBody ? "메일 본문" : "메일 제목",model.auxiliary[key] || "",true));
      input.className = isBody ? "mail-body" : "mail-subject"; input.rows = isBody ? 12 : 2;
      auxiliary[key] = input;
    });
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
    Object.keys(auxiliary).forEach(function (key) { result.auxiliary[key] = auxiliary[key].value; }); return result;
  }
  function refreshPreview() {
    var seq = ++layoutSequence, id = currentId; direct.loading();
    document.getElementById("recipient-preview").href = url(id,"/preview");
    request(url(id,"/edit-layout"),"GET",null,function (error,value) {
      if (seq !== layoutSequence || id !== currentId) return;
      if (error) { direct.error(error.message); return; }
      direct.setLayout(value,url(id,"/edit-slides"));
    });
  }
  document.getElementById("reload-preview").onclick = refreshPreview;
  function load(id) {
    request(url(id,"/editor-data"),"GET",null,function (error,data) {
      busy = false;
      if (error) { message(error.message,true); availability(); return; }
      currentId = id; show(data);
    });
  }
  function poll(id, previous, snapshot, attempts) {
    request(url(id),"GET",null,function (error,job) {
      if (error || job.status === "failed") {
        busy = false; if (!error) activeRevision = null;
        message((error ? error.message : (job.error && job.error.message) || job.error || job.message) + " 입력 내용은 유지됩니다. ‘지금 반영 / 재시도’를 눌러 주세요.",true); availability(); return;
      }
      if (job.status === "completed") {
        currentId = id; saved = snapshot; activeRevision = null; busy = false; retries = 0;
        dirty = JSON.stringify(collect()) !== saved;
        refreshPreview(); download.href = url(id,"/download");
        window.history.replaceState(null,"",url(id,"/edit"));
        try { window.localStorage.setItem("rist-report-revision",JSON.stringify({previousJobId:previous,job:job,time:Date.now()})); } catch (_) {}
        message(dirty ? "PPT가 갱신되었습니다. 그동안 추가한 수정 내용도 이어서 반영합니다." : "자동 저장 완료 · PPT 미리보기와 다운로드·전송에 수정 내용이 반영되었습니다.");
        availability(); schedule(); return;
      }
      if (attempts >= 300) { busy = false; message("생성이 계속 진행 중입니다. ‘지금 반영 / 재시도’로 진행 상태를 다시 확인하세요. 입력 내용은 유지됩니다.",true); availability(); return; }
      message((job.message || "수정 내용을 PPT에 반영 중입니다.") + " 추가 입력도 계속할 수 있습니다.");
      window.setTimeout(function () { poll(id,previous,snapshot,attempts+1); },1500);
    });
  }
  function saveChanges() {
    window.clearTimeout(timer);
    if (busy || !model || composing) return;
    if (activeRevision) {
      busy = true; availability(); poll(activeRevision.id,activeRevision.previous,activeRevision.snapshot,0); return;
    }
    if (!dirty) return;
    busy = true; availability(); message("수정 내용을 저장하는 중입니다.");
    var previous = currentId, value = collect(), snapshot = JSON.stringify(value);
    request(url(currentId,"/revisions"),"POST",value,function (error,job) {
      if (error) {
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
  download.onclick = function (event) { if (busy || dirty || activeRevision || !model) event.preventDefault(); };
  window.addEventListener("beforeunload",function (event) { if (dirty || busy || activeRevision) { event.preventDefault(); event.returnValue = "저장하지 않은 변경 또는 생성 중인 수정본이 있습니다."; } });
  load(currentId);
}());
