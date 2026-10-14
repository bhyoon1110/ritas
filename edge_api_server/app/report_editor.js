(function () {
  "use strict";
  var config = JSON.parse(document.getElementById("editor-config").textContent);
  var currentId = config.jobId, model = null, dirty = false, busy = false;
  var editor = document.getElementById("editor"), status = document.getElementById("status");
  var save = document.getElementById("save"), send = document.getElementById("send"), download = document.getElementById("download");
  var inputs = {}, sections = [], auxiliary = {};
  function message(text, error) { status.textContent = text; status.className = error ? "error" : ""; }
  function url(id, tail) { return config.prefix + "/" + encodeURIComponent(id) + (tail || ""); }
  function request(path, method, body, done) {
    var xhr = new XMLHttpRequest(); xhr.open(method, path, true); xhr.timeout = 60000;
    xhr.setRequestHeader("X-Requested-With", "RIST-Report-Editor");
    if (body) xhr.setRequestHeader("Content-Type", "application/json");
    xhr.onload = function () {
      var result; try { result = JSON.parse(xhr.responseText); } catch (_) { done(new Error("서버 응답을 확인할 수 없습니다. 로그인 상태를 확인하세요.")); return; }
      if (xhr.status < 200 || xhr.status >= 300) {
        done(new Error((result.error && result.error.message) || result.message || "요청에 실패했습니다.")); return;
      } done(null, result);
    };
    xhr.onerror = xhr.ontimeout = function () { done(new Error("연결이 끊겼습니다. 원본은 유지됩니다. 다시 시도하세요.")); };
    xhr.send(body ? JSON.stringify(body) : null);
  }
  function availability() {
    save.disabled = busy || !model;
    send.disabled = busy || dirty || !model;
    download.setAttribute("aria-disabled", String(busy || dirty || !model));
    Array.prototype.forEach.call(editor.querySelectorAll("input,textarea"),function (input) { input.disabled = busy; });
  }
  function changed() { dirty = true; message("저장하지 않은 변경이 있습니다. 저장 후 미리보기·다운로드·전송에 반영됩니다."); availability(); }
  function element(tag, parent, text) { var e = document.createElement(tag); if (text != null) e.textContent = text; if (parent) parent.appendChild(e); return e; }
  function field(parent, label, value, multiline, limit) {
    var wrap = element("label", parent, label), input = element(multiline ? "textarea" : "input", wrap);
    if (!multiline) input.type = "text";
    input.value = value || ""; input.maxLength = limit || 4000; input.setAttribute("aria-label", label);
    input.placeholder = "미입력 · 빈칸으로 유지하거나 내용을 입력하세요";
    input.addEventListener("input", changed); return input;
  }
  function show(data) {
    model = data.document; editor.textContent = ""; sections = []; auxiliary = {}; inputs = {};
    var top = element("section", editor); element("h2", top, "보고서 전송 정보");
    inputs.title = field(top, "보고서 제목", model.title, false, 200);
    var grid = element("div", top); grid.className = "grid";
    var labels = {requestNumber:"의뢰번호",limsExperimentCode:"실험코드",equipmentCode:"실험장비",operatorId:"실험자"};
    Object.keys(labels).forEach(function (key) { inputs[key] = field(grid, labels[key], model.transfer[key], false, key === "limsExperimentCode" ? 50 : 100); });
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
            cell.placeholder = "미입력"; cell.addEventListener("input",changed); cells.push(cell);
          }); entry.rows.push(cells);
        });
      } sections.push(entry);
    });
    var mail = element("details", editor); element("summary", mail, "보고서 안내 메일 문안");
    ["email_subject","email_body"].forEach(function (key) { auxiliary[key] = field(mail,key === "email_subject" ? "메일 제목" : "메일 본문",model.auxiliary[key] || "",key === "email_body"); });
    dirty = false;
    if (data.authenticatedOperatorId) {
      inputs.operatorId.readOnly = true;
      if (inputs.operatorId.value !== data.authenticatedOperatorId) { inputs.operatorId.value = data.authenticatedOperatorId; dirty = true; }
      element("p", top, "실험자는 연결된 SSO 계정으로 전송됩니다. 변경된 경우 저장해 보고서에도 반영하세요.").className = "muted";
    }
    document.getElementById("preview").src = url(currentId, "/preview");
    download.href = url(currentId,"/download");
    message(dirty ? "인증된 SSO 실험자 정보가 변경되었습니다. 저장해 반영하세요." : "저장된 보고서를 표시합니다. 빈칸과 분석 문안을 수정할 수 있습니다.");
    availability();
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
  function load(id, notifyPrevious) {
    request(url(id,"/editor-data"),"GET",null,function (error,data) {
      busy = false;
      if (error) { message(error.message,true); availability(); return; }
      currentId = id; show(data);
      if (notifyPrevious) {
        window.history.replaceState(null,"",url(id,"/edit"));
        try { window.localStorage.setItem("rist-report-revision",JSON.stringify({previousJobId:notifyPrevious,job:data.job,time:Date.now()})); } catch (_) {}
        message("수정본을 저장했습니다. 오른쪽 미리보기와 다운로드·전송에 수정 내용이 반영되었습니다.");
      }
    });
  }
  function poll(id, previous, attempts) {
    request(url(id),"GET",null,function (error,job) {
      if (error || job.status === "failed") { busy = false; message(error ? error.message : job.error,true); availability(); return; }
      if (job.status === "completed") { load(id,previous); return; }
      if (attempts >= 300) { busy = false; message("생성이 계속 진행 중입니다. 잠시 후 이 주소에서 확인하세요: " + url(id,"/edit"),true); availability(); return; }
      message(job.message || "수정본 생성 중입니다."); window.setTimeout(function () { poll(id,previous,attempts+1); },1500);
    });
  }
  save.onclick = function () {
    if (busy || !model) return;
    busy = true; availability(); message("수정 내용을 저장하는 중입니다.");
    var previous = currentId;
    request(url(currentId,"/revisions"),"POST",collect(),function (error,job) {
      if (error) { busy = false; message(error.message,true); availability(); return; } poll(job.jobId,previous,0);
    });
  };
  send.onclick = function () {
    if (busy || dirty || !model) return;
    var transfer = collect().transfer;
    if (!transfer.requestNumber || !transfer.limsExperimentCode || !transfer.equipmentCode || !transfer.operatorId) {
      message("전송하려면 의뢰번호·실험코드·실험장비·실험자를 입력하고 저장하세요.",true); return;
    }
    busy = true; availability(); message("SSO 및 전송 권한을 확인하는 중입니다.");
    request(url(currentId,"/send"),"POST",{requestNumber:transfer.requestNumber,experimentCode:transfer.limsExperimentCode,equipmentCode:transfer.equipmentCode,operatorId:transfer.operatorId},function (error) {
      busy = false; availability(); message(error ? error.message : "현재 수정본을 LIMS 전송 대기열에 등록했습니다.",!!error);
    });
  };
  download.onclick = function (event) { if (busy || dirty || !model) event.preventDefault(); };
  window.addEventListener("beforeunload",function (event) { if (dirty || busy) { event.preventDefault(); event.returnValue = "저장하지 않은 변경 또는 생성 중인 수정본이 있습니다."; } });
  load(currentId);
}());
