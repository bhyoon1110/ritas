(function () {
  "use strict";
  var config = JSON.parse(document.getElementById("editor-config").textContent);
  var currentId = config.jobId, model = null, dirty = false, busy = false;
  var editor = document.getElementById("editor"), status = document.getElementById("status");
  var save = document.getElementById("save"), send = document.getElementById("send"), download = document.getElementById("download");
  var inputs = {}, sections = [], auxiliary = {};
  var saved = "", timer = null, composing = false, sending = false, activeRevision = null, retries = 0;
  var preview = document.getElementById("preview"), previewState = {view:"slides",page:0};
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
    Array.prototype.forEach.call(editor.querySelectorAll("input,textarea"),function (input) { input.disabled = sending; });
    if (lookup) lookup.availability();
  }
  function schedule(delay) {
    window.clearTimeout(timer);
    if (dirty && !composing && !busy) timer = window.setTimeout(saveChanges, delay || 1200);
  }
  function changed() {
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
      var project = config.prefix.indexOf("/raman/") >= 0 ? "RAMAN" : "FT-IR";
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
    model = data.document; editor.textContent = ""; sections = []; auxiliary = {}; inputs = {};
    var top = element("section", editor); element("h2", top, "보고서 전송 정보");
    inputs.title = field(top, "보고서 제목", model.title, false, 200);
    var grid = element("div", top); grid.className = "grid";
    var labels = {requestNumber:"의뢰번호",limsExperimentCode:"실험코드",equipmentCode:"실험장비",operatorId:"실험자"};
    Object.keys(labels).forEach(function (key) { inputs[key] = field(grid, labels[key], model.transfer[key], false, key === "limsExperimentCode" ? 50 : 100); });
    lookup = requestLookup(top);
    (model.sections || []).forEach(function (section) {
      var block = element("details", editor);
      block.open = ["experiment_conditions","summary","key_findings","interpretation","qc_notes"].indexOf(section.sectionId) >= 0;
      var summary = element("summary", block, section.heading);
      var source = element("span", summary, " · " + (section.source === "user" ? "사용자 수정" : section.source === "llm" ? "로컬 AI 초안" : "보고서 내용")); source.className = "source";
      var entry = {sectionId:section.sectionId, heading:field(block,"항목 제목 · " + section.heading,section.heading,false,200), paragraphs:[], bullets:[], rows:null};
      (section.paragraphs.length ? section.paragraphs : (section.columns || section.bullets.length ? [] : [""])).forEach(function (text, i) {
        entry.paragraphs.push(field(block,section.heading + " · 문단 " + (i+1),text,true));
      });
      section.bullets.forEach(function (text, i) { entry.bullets.push(field(block,section.heading + " · 항목 " + (i+1),text,true)); });
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
            cell.placeholder = "미입력"; watch(cell); cells.push(cell);
          }); entry.rows.push(cells);
        });
      } sections.push(entry);
    });
    var mail = element("details", editor); element("summary", mail, "보고서 안내 메일 문안");
    ["email_subject","email_body"].forEach(function (key) { auxiliary[key] = field(mail,key === "email_subject" ? "메일 제목" : "메일 본문",model.auxiliary[key] || "",key === "email_body"); });
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
    var result = {title:inputs.title.value,transfer:{},sections:[],auxiliary:{}};
    ["requestNumber","limsExperimentCode","equipmentCode","operatorId"].forEach(function (key) { result.transfer[key] = inputs[key].value.trim(); });
    sections.forEach(function (section) {
      result.sections.push({sectionId:section.sectionId,heading:section.heading.value,
        paragraphs:section.paragraphs.map(function (e) { return e.value; }),bullets:section.bullets.map(function (e) { return e.value; }),
        rows:section.rows && section.rows.map(function (row) { return row.map(function (e) { return e.value; }); })});
    });
    Object.keys(auxiliary).forEach(function (key) { result.auxiliary[key] = auxiliary[key].value; }); return result;
  }
  function refreshPreview() {
    preview.src = url(currentId, "/preview") + "#view=" + previewState.view + "&page=" + previewState.page;
  }
  window.addEventListener("message", function (event) {
    var data = event.data;
    // The read-only iframe has an opaque origin. Trust only its window and a bounded display-state schema.
    if (event.source !== preview.contentWindow || !data || data.type !== "rist-report-view-state") return;
    if (["slides","analysis","graph"].indexOf(data.view) < 0 || typeof data.page !== "number" || data.page % 1 !== 0 || data.page < 0 || data.page >= 200) return;
    previewState = {view:data.view,page:data.page};
  });
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
        message((error ? error.message : job.error) + " 입력 내용은 유지됩니다. ‘지금 반영 / 재시도’를 눌러 주세요.",true); availability(); return;
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
  save.onclick = function () { retries = 0; saveChanges(); };
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
