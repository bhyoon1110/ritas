/* Staged FTIR/Raman authoring. ES5 syntax; no scientific edits in the recipient viewer. */
(function () {
  "use strict";
  function node(tag, parent, text) {
    var el = document.createElement(tag);
    if (text != null) el.textContent = text;
    if (parent) parent.appendChild(el);
    return el;
  }
  function button(parent, text, action, id) {
    var el = node("button", parent, text); el.type = "button";
    if (id) el.id = id;
    el.onclick = action; return el;
  }
  function install(api) {
    var kind = api.kind, gd = api.gd, graphs = [], active = null, step = 1, job = null;
    var busy = false, ready = false, stale = false, childBusy = false;
    var writing = Promise.resolve(), externalBusy = false, resetting = false, operation = "";
    var editorChecking = false, editorProblem = "", editorCheckSequence = 0;
    var thumbnails = [];
    var root = node("section"); root.className = "spectrum-workflow"; root.id = "spectrum-workflow";
    var header = document.querySelector("." + kind + "-app-bar");
    header.parentNode.insertBefore(root, header.nextSibling);
    var nav = node("nav", root); nav.className = "sw-steps"; nav.setAttribute("aria-label", "분석 작업 단계");
    var steps = ["① 데이터 업로드", "② 그래프 편집·저장", "③ 보고서 미리보기·편집", "④ 보고서 저장·전송"];
    var navButtons = steps.map(function (title, index) {
      return button(nav, title, function () { go(index + 1); }, "sw-step-" + (index + 1));
    });
    var help = node("p", root); help.className = "sw-help";
    var stateLine = node("p", root); stateLine.className = "sw-status"; stateLine.setAttribute("role", "status");
    var upload = node("section", root); upload.className = "sw-upload"; upload.id = "sw-upload";
    node("h3", upload, "RAW 데이터 업로드");
    node("p", upload, kind === "ftir" ? "DPT 원본 파일을 선택하거나 아래 영역에 끌어 놓으세요." : "TXT·CSV·TSV·Excel 원본 파일을 선택하거나 아래 영역에 끌어 놓으세요.").className = "sw-help";
    var input = document.getElementById(kind + "-file-input"), chooser = document.querySelector("." + kind + "-file-button") || input.closest("label");
    upload.appendChild(chooser);
    if (!chooser.contains(input)) upload.appendChild(input);
    upload.appendChild(document.getElementById(kind + "-drop-zone"));
    chooser.tabIndex = 0; chooser.setAttribute("role", "button");
    chooser.onkeydown = function (event) {
      if (event.keyCode === 13 || event.keyCode === 32) { event.preventDefault(); if (!unavailable()) input.click(); }
    };
    var importInput = node("input", root); importInput.type = "file"; importInput.accept = ".ristgraph";
    importInput.id = "sw-graph-file-input"; importInput.hidden = true;
    var importRow = node("div", upload); importRow.className = "sw-controls sw-import";
    var importUpload = button(importRow, "편집 그래프 불러오기 (RAW 포함)", chooseGraphFile, "sw-import-graph");
    node("p", upload, ".ristgraph 파일은 원본 RAW와 피크·라벨·축·스타일 편집을 함께 복원합니다. 표시 설정만 옮기는 작업 템플릿과는 다릅니다.").className = "sw-help";
    importInput.onchange = function () { var selected = this.files; importFiles(selected); this.value = ""; };
    var controls = node("div", root); controls.className = "sw-controls";
    var name = node("input", controls); name.type = "text"; name.maxLength = 100;
    name.id = "sw-graph-name"; name.placeholder = "저장할 그래프 이름"; name.setAttribute("aria-label", "그래프 이름");
    var save = button(controls, "그래프 저장", function () { saveGraph(false); }, "sw-save-graph");
    save.className = "sw-primary";
    var duplicate = button(controls, "새 그래프로 저장", function () { saveGraph(true); }, "sw-save-copy");
    var guide = node("section", root); guide.className = "sw-guide"; guide.id = "sw-guide";
    guide.setAttribute("aria-label", "다음 작업 안내");
    var guideHeading = node("div", guide); guideHeading.className = "sw-guide-heading";
    var guideTitle = node("strong", guideHeading); guideTitle.id = "sw-guide-title";
    var selectionCount = node("span", guideHeading); selectionCount.className = "sw-selection-count";
    var guideDetail = node("p", guide); guideDetail.id = "sw-guide-detail";
    guideDetail.setAttribute("role", "status");
    var actions = node("div", guide); actions.className = "sw-controls sw-actions";
    var previous = button(actions, "이전 단계", function () { go(step - 1); }, "sw-previous");
    var returnEdit = button(actions, "그래프 편집·저장으로 →", function () { go(2); }, "sw-return-edit");
    var build = button(actions, "선택한 그래프로 보고서 생성", generate, "sw-build-report");
    var next = button(actions, "다음 단계", function () { go(step + 1); }, "sw-next");
    var retryEditor = button(actions, "미리보기 다시 확인", function () { openEditor(); }, "sw-retry-editor");
    var library = node("section", root); library.id = "sw-library";
    var title = node("h3", library, "저장한 그래프 · 보고서 포함 순서");
    title.style.margin = "10px 0 0";
    var libraryControls = node("div", library); libraryControls.className = "sw-controls";
    var importLibrary = button(libraryControls, "그래프 파일 불러오기", chooseGraphFile, "sw-import-graph-library");
    node("span", libraryControls, "파일 내보내기: 저장 시점의 편집 상태 + 원본 RAW").className = "sw-help";
    var list = node("div", library); list.className = "sw-list"; list.id = "sw-graph-list";
    var frame = node("iframe", root); frame.className = "sw-report-frame"; frame.id = "sw-report-editor";
    frame.title = "보고서 미리보기·편집 및 저장·전송"; frame.hidden = true;
    var clear = document.getElementById(kind + "-clear");
    clear.textContent = "초기화";
    clear.title = "현재 그래프와 저장 그래프를 모두 비우고 데이터 업로드부터 시작";
    document.getElementById(kind + "-report").style.display = "none";
    function tell(text, error) { stateLine.textContent = text; stateLine.className = "sw-status" + (error ? " sw-error" : ""); }
    function unavailable() { return busy || externalBusy || editorChecking || !ready || api.busy(); }
    function snapshot() { return {version:1, graphs:graphs, active:active, step:step, job:job, stale:stale}; }
    function persist() {
      var state = snapshot();
      writing = writing.catch(function () {}).then(function () { return api.write(state); });
      return writing;
    }
    function persistSoon() {
      persist().catch(function () { tell("브라우저 저장 공간이 부족하거나 차단되었습니다. 현재 화면은 유지되지만 새로고침하면 최근 변경을 잃을 수 있습니다.", true); });
    }
    function changed() { stale = !!job; persistSoon(); render(); }
    function chosen() { return graphs.filter(function (g) { return g.included; }); }
    function toggle(el, visible) { if (el) el.classList.toggle("sw-stage-hidden", !visible); }
    function thumbnailFor(g) {
      return thumbnails.filter(function (item) { return item.id === g.id && item.source === g.report.image; })[0];
    }
    function pruneThumbnails() {
      thumbnails = thumbnails.filter(function (item) {
        var keep = graphs.some(function (g) { return g.id === item.id && g.report.image === item.source; });
        if (!keep && item.url) URL.revokeObjectURL(item.url);
        return keep;
      });
    }
    function makeThumbnail(g, image) {
      if (thumbnailFor(g) || !image.naturalWidth || !image.naturalHeight
          || !graphs.some(function (entry) { return entry === g; })) return;
      // Reuse the already decoded workspace image; keep only a small PNG per saved graph.
      var item = {id:g.id,source:g.report.image,url:""}; thumbnails.push(item);
      try {
        var scale = Math.min(1, 320 / image.naturalWidth, 180 / image.naturalHeight);
        var canvas = document.createElement("canvas");
        canvas.width = Math.max(1, Math.round(image.naturalWidth * scale));
        canvas.height = Math.max(1, Math.round(image.naturalHeight * scale));
        canvas.getContext("2d").drawImage(image, 0, 0, canvas.width, canvas.height);
        item.url = URL.createObjectURL(window.RistSpectrumWorkflow.imageFile(canvas.toDataURL("image/png")));
      } catch (_) {} // Image insertion/enlargement still works if thumbnail rendering is unavailable.
      if (step === 3) editorStage();
    }
    function editorStage() {
      if (frame.contentWindow && job) {
        frame.contentWindow.postMessage({type:"rist-workflow-stage", step:step}, window.location.origin);
        // Only IDs and small cached thumbnail URLs; RAW/full PNGs stay in the workspace.
        frame.contentWindow.postMessage({type:"rist-saved-graphs", jobId:job.jobId,
          graphs:graphs.map(function (g) {
            var thumb = thumbnailFor(g);
            return {id:g.id,title:g.title,thumbnail:thumb ? thumb.url : ""};
          })}, window.location.origin);
      }
    }
    function editorFailure(error, id) {
      if (!job || job.jobId !== id || resetting) return;
      var missing = (error.status === 404 && error.code === "REPORT_JOB_NOT_FOUND")
        || (error.status === 410 && error.code === "REPORT_EDIT_EXPIRED");
      editorProblem = missing
        ? "이전 보고서의 편집 작업이 서버에 없거나 만료되었습니다. RAW와 저장한 그래프는 유지됩니다. ‘선택한 그래프로 보고서 생성’을 눌러 미리보기를 다시 만드세요. 이전 보고서의 문구 수정은 자동 복구되지 않습니다."
        : (error.message || "미리보기를 확인하지 못했습니다.") + " 저장한 그래프와 보고서 연결은 유지됩니다. 로그인·연결 상태를 확인한 뒤 ‘미리보기 다시 확인’을 누르세요.";
      childBusy = false; frame.removeAttribute("src");
      if (missing) { job = null; stale = false; if (step === 4) step = 3; persistSoon(); }
      render(); tell(editorProblem, true);
    }
    function openEditor() {
      if (!job || resetting) return Promise.resolve();
      var id = job.jobId, sequence = ++editorCheckSequence;
      editorChecking = true; editorProblem = ""; render();
      return new Promise(function (resolve) {
        var xhr = new XMLHttpRequest();
        xhr.open("GET", "/api/v1/" + kind + "/report/jobs/" + encodeURIComponent(id) + "/editor-status", true);
        xhr.setRequestHeader("Cache-Control", "no-cache");
        xhr.timeout = 30000;
        function finish(error) {
          if (sequence !== editorCheckSequence || !job || job.jobId !== id || resetting) { resolve(); return; }
          editorChecking = false;
          if (error) editorFailure(error, id);
          else {
            var target = "/api/v1/" + kind + "/report/jobs/" + encodeURIComponent(id) + "/edit?workflow=1";
            if (frame.getAttribute("src") !== target) frame.src = target;
            render(); tell("보고서 미리보기를 확인하고 수정한 최종본을 저장·전송하세요.");
          }
          resolve();
        }
        xhr.onload = function () {
          var value;
          try { value = JSON.parse(xhr.responseText); } catch (_) { finish(new Error("서버 응답을 확인할 수 없습니다.")); return; }
          if (!value || typeof value !== "object") { finish(new Error("서버 응답을 확인할 수 없습니다.")); return; }
          if (xhr.status < 200 || xhr.status >= 300) {
            var error = new Error(value.message || "미리보기 확인에 실패했습니다.");
            error.status = xhr.status; error.code = value.code; finish(error); return;
          }
          if (value.jobId !== id || value.status !== "completed") { finish(new Error("보고서가 아직 준비되지 않았습니다.")); return; }
          finish();
        };
        xhr.onerror = xhr.ontimeout = function () { finish(new Error("서버 연결을 확인하지 못했습니다.")); };
        xhr.send();
      });
    }
    // Update action feedback without rebuilding graph cards or stealing checkbox focus.
    function refreshActions() {
      var locked = unavailable(), count = chosen().length, hasGraph = api.hasGraph();
      var waiting = editorChecking ? "보고서 미리보기 작업을 서버에서 확인하는 중입니다."
        : !ready ? "이전 작업을 불러오는 중입니다. 잠시 기다려 주세요."
        : locked ? (resetting ? "작업을 초기화하는 중입니다." : operation || "RAW 분석 또는 편집 설정을 적용하는 중입니다. 완료 후 버튼이 활성화됩니다.")
        : childBusy ? "보고서 변경 내용을 자동 저장 중입니다. 완료 후 생성·전송 버튼이 활성화됩니다. 계속 대기하면 아래 미리보기의 오류 안내를 확인하세요." : "";
      var buildReason = waiting || (!count ? (graphs.length ? "아래 저장 목록에서 보고서에 넣을 그래프를 체크하세요." : "현재 그래프를 먼저 저장하세요. 저장하면 보고서 포함 대상으로 자동 선택됩니다.") : "");
      var previewReason = waiting || ((!job || stale) && !count ? buildReason : "");
      var deliveryReason = waiting || editorProblem || (!job ? "먼저 선택한 그래프로 보고서를 생성하세요." : stale ? "그래프 선택·순서가 바뀌었습니다. 보고서를 다시 생성한 뒤 저장·전송하세요." : "");
      function availability(el, reason) {
        el.disabled = !!reason; el.title = reason;
        el.setAttribute("aria-describedby", "sw-guide-detail");
      }
      previous.hidden = step === 1; previous.disabled = locked;
      next.hidden = step === 4 || (step === 1 && !hasGraph) || (step === 2 && !graphs.length) || (step === 3 && !job);
      next.textContent = step === 1 ? "그래프 편집으로 →" : step === 2 ? (job && !stale ? "보고서 미리보기로 →" : "보고서 생성·미리보기로 →") : "최종 저장·전송으로 →";
      availability(next, step === 3 ? deliveryReason : step === 2 ? previewReason : locked ? waiting : "");
      availability(save, locked ? waiting : !hasGraph ? "RAW 파일을 업로드하고 그래프를 만든 후 저장하세요." : "");
      availability(duplicate, save.title); duplicate.hidden = !active;
      save.textContent = active ? "선택 그래프 덮어쓰기" : "그래프 저장";
      build.hidden = step !== 2 && step !== 3; availability(build, buildReason);
      build.textContent = job ? "선택한 그래프로 보고서 다시 생성" : "선택한 그래프로 보고서 생성 →";
      returnEdit.hidden = step !== 3 || graphs.length > 0; returnEdit.disabled = locked;
      retryEditor.hidden = step < 3 || !editorProblem || !job; retryEditor.disabled = locked;
      availability(navButtons[3], deliveryReason);
      clear.disabled = locked || childBusy;
      importUpload.disabled = importLibrary.disabled = importInput.disabled = locked || graphs.length >= 6;
      importUpload.title = importLibrary.title = graphs.length >= 6 ? "저장 그래프는 6개까지입니다. 불필요한 저장본을 먼저 정리하세요." : "원본 RAW가 포함된 .ristgraph 파일을 저장 목록에 추가합니다.";
      selectionCount.hidden = step === 1 || step === 4;
      selectionCount.textContent = "보고서 포함 " + count + "개 / 저장 " + graphs.length + "개";
      guide.setAttribute("data-state", waiting ? "waiting" : "ready");
      var title, detail, primary = null;
      if (waiting) { title = "처리 중 · 잠시 기다려 주세요"; detail = waiting; }
      else if (step >= 3 && editorProblem) { title = job ? "미리보기 연결 확인 필요" : "다음: 저장한 그래프로 보고서 다시 생성"; detail = editorProblem; primary = job ? retryEditor : count ? build : returnEdit; }
      else if (step === 1) {
        title = "다음: RAW 파일 업로드"; detail = "파일을 선택하거나 업로드 영역에 끌어 놓으세요. 분석이 끝나면 그래프 편집 단계로 이동합니다.";
        if (hasGraph) { title = "다음: 그래프 편집"; detail = "업로드한 그래프를 편집하거나 RAW 파일을 추가할 수 있습니다."; primary = next; }
      } else if (step === 2 && !hasGraph && !graphs.length) {
        title = "다음: RAW 파일 업로드"; detail = "‘이전 단계’를 눌러 RAW 파일을 먼저 업로드하세요."; primary = previous;
      } else if (step === 2 && (!graphs.length || (!active && hasGraph))) {
        title = "다음: 현재 그래프 저장"; detail = "편집을 마치면 ‘그래프 저장’을 누르세요. 저장된 그래프는 보고서에 자동 선택됩니다."; primary = save;
        if (count) detail += " 기존 저장본 " + count + "개로 바로 보고서를 생성할 수도 있습니다.";
      } else if ((step === 2 || step === 3) && !count && (!job || stale || step === 2)) {
        title = graphs.length ? "다음: 보고서에 넣을 그래프 선택" : "다음: 그래프 편집·저장";
        detail = buildReason; if (!graphs.length && step === 3) primary = returnEdit;
      } else if (step === 2 || (step === 3 && (!job || stale))) {
        title = step === 2 && job && !stale ? "다음: 보고서 미리보기" : stale ? "다음: 변경한 그래프로 보고서 다시 생성" : "다음: 선택한 그래프로 보고서 생성";
        detail = step === 2 && job && !stale
          ? "③ 단계로 이동하면 기존 보고서를 그대로 엽니다. 보고서에서 수정한 내용도 유지됩니다. 그래프를 추가 편집했다면 먼저 덮어쓰기 하세요."
          : "선택한 " + count + "개가 목록 순서대로 보고서에 들어갑니다. " + (step === 2 ? "③ 단계로 이동하면 자동 생성 후 미리보기가 열립니다. " : "생성이 끝나면 미리보기가 열립니다. ") + "추가 편집했다면 먼저 그래프를 덮어쓰기 하세요.";
        primary = step === 2 ? next : build;
      } else if (step === 3) {
        title = "다음: 미리보기 확인 후 최종 저장·전송";
        detail = "아래 PPT 미리보기에서 내용과 페이지를 확인·수정하세요. 자동 저장이 끝나면 ‘최종 저장·전송으로’를 누르세요."; primary = next;
      } else {
        title = "마지막: 최종 보고서 다운로드 또는 전송";
        detail = "아래 화면에서 최종본을 다운로드하거나 의뢰정보·이메일을 확인한 뒤 LIMS로 전송하세요. 전송에는 SSO 인증과 권한이 필요합니다.";
      }
      guideTitle.textContent = title; guideDetail.textContent = detail;
      [previous, save, build, next, returnEdit, retryEditor].forEach(function (el) { el.classList.toggle("sw-primary", el === primary); });
    }
    function render() {
      pruneThumbnails();
      navButtons.forEach(function (b, i) { b.setAttribute("aria-current", i + 1 === step ? "step" : "false"); b.disabled = unavailable(); });
      document.body.classList.toggle("sw-report-stage", step >= 3);
      root.setAttribute("data-step", String(step));
      // Body-level plot portals must not survive navigation into report editing.
      document.body.setAttribute("data-rist-spectrum-step", String(step));
      help.textContent = step === 1 ? "RAW 파일과 실험조건을 입력하세요. 이전 단계로 돌아가도 저장한 그래프와 보고서는 유지됩니다."
        : step === 2 ? "피크·주석·축을 편집한 뒤 그래프를 저장하세요. 보고서에 넣을 저장본을 체크하고 ③ 단계로 이동하면 보고서가 자동 생성됩니다. 변경이 없으면 기존 보고서를 엽니다."
        : step === 3 ? "저장한 그래프를 PPT 페이지로 끌어 놓거나 ‘현재 페이지에 추가’를 누르세요. 기존 문구를 유지하며 이미지로 추가됩니다. 체크·순서 변경은 보고서 다시 생성 시 적용됩니다."
        : "자동 저장이 끝난 최종본을 다운로드하거나 SSO 인증 후 LIMS로 전송하세요. 이전 단계에서 계속 수정할 수 있습니다.";
      toggle(gd, step === 2);
      toggle(document.querySelector("." + kind + "-library-band"), step === 2);
      toggle(document.querySelector(".rist-work-template-band"), step === 2);
      toggle(document.getElementById(kind + "-drop-zone"), step === 1);
      toggle(document.getElementById(kind + "-report-meta"), step === 1);
      toggle(document.querySelector("." + kind + "-file-button") || document.getElementById(kind + "-file-input").closest("label"), step === 1);
      toggle(document.getElementById(kind + "-origin").parentNode, step === 2);
      toggle(clear, true);
      upload.hidden = step !== 1;
      chooser.setAttribute("aria-disabled", String(unavailable()));
      controls.hidden = step !== 2; library.hidden = step === 1 || step === 4;
      refreshActions();
      frame.hidden = step < 3 || !job || editorChecking || !!editorProblem;
      if (step >= 3 && stale) tell("그래프 목록이 변경되었습니다. 현재 미리보기는 이전 보고서입니다. 반영하려면 보고서를 다시 생성하세요.");
      list.textContent = "";
      if (!graphs.length) node("p", list, "저장한 그래프가 없습니다. 그래프 편집 후 ‘그래프 저장’을 눌러 주세요.").className = "sw-empty";
      graphs.forEach(function (g, index) {
        var card = node("article", list); card.className = "sw-card" + (g.id === active ? " sw-active" : ""); card.dataset.graphId = g.id;
        var label = node("label", card), check = node("input", label); check.type = "checkbox"; check.checked = g.included;
        check.disabled = unavailable(); check.setAttribute("aria-label", g.title + " 보고서 포함");
        node("span", label, (index + 1) + ". " + g.title);
        check.onchange = function () { g.included = check.checked; changed(); };
        var image = node("img", card);
        image.onload = function () { makeThumbnail(g, image); };
        image.src = g.report.image; image.alt = g.title + " 저장본";
        image.draggable = step === 3 && !!job && !unavailable();
        image.title = step === 3 ? "PPT 미리보기 페이지로 끌어 놓아 추가" : g.title;
        image.ondragstart = function (event) {
          if (step !== 3 || !job || unavailable() || !event.dataTransfer) { event.preventDefault(); return; }
          event.dataTransfer.setData("application/x-rist-saved-graph", JSON.stringify({id:g.id,jobId:job.jobId}));
          event.dataTransfer.effectAllowed = "copy";
        };
        var row = node("div", card); row.className = "sw-controls";
        if (step === 3) button(row, "현재 페이지에 추가", function () {
          if (!job || unavailable()) return;
          frame.contentWindow.postMessage({type:"rist-insert-saved-graph",jobId:job.jobId,id:g.id},window.location.origin);
        });
        button(row, "미리보기", function () { preview(g); });
        button(row, "다시 편집", function () { edit(g); });
        button(row, "파일 내보내기", function () { exportGraph(g); }).title = "저장 시점의 편집 상태와 원본 RAW를 .ristgraph 파일로 저장";
        var management = node("div", row); management.className = "sw-card-management";
        button(management, "↑", function () { move(index, -1); }).setAttribute("aria-label", g.title + " 앞 순서로");
        button(management, "↓", function () { move(index, 1); }).setAttribute("aria-label", g.title + " 뒤 순서로");
        button(management, "삭제", function () {
          if (unavailable() || !window.confirm("‘" + g.title + "’ 저장본을 목록에서 삭제할까요? 원본 파일과 이미 생성한 보고서는 삭제하지 않습니다.")) return;
          graphs = graphs.filter(function (entry) { return entry !== g; }); if (active === g.id) active = null;
          changed();
        });
        Array.prototype.forEach.call(row.querySelectorAll("button"), function (b) { b.disabled = unavailable(); });
      });
      editorStage();
    }
    function go(value) {
      if (unavailable()) return;
      value = Math.max(1, Math.min(4, value));
      var autoPreview = step === 2 && value === 3;
      if (autoPreview && childBusy) {
        // Let users return to a dirty/failed-autosave editor to finish or retry;
        // never replace it with a new report while those edits are pending.
        if (job) { step = 3; render(); persistSoon(); }
        tell("기존 미리보기에서 보고서 자동 저장 상태를 확인해 주세요. 저장되지 않은 수정 내용이 있어 새 보고서는 생성하지 않습니다.", true); return;
      }
      if (autoPreview && (!job || stale)) {
        if (!chosen().length) { tell(graphs.length ? "보고서에 넣을 저장 그래프를 체크하세요." : "현재 그래프를 먼저 ‘그래프 저장’한 뒤 미리보기로 이동하세요.", true); return; }
        return generate();
      }
      if (value === 4 && !job) { tell("먼저 그래프를 저장·선택하고 보고서를 생성하세요.", true); return; }
      if (value === 4 && stale) { tell("변경한 그래프 목록으로 보고서를 다시 생성한 뒤 전송하세요. 이전 보고서는 별도로 보존됩니다.", true); return; }
      if (value === 4 && childBusy) { tell("보고서 자동 저장이 끝난 뒤 저장·전송 단계로 이동하세요.", true); return; }
      if (value === 4 && editorProblem) { tell(editorProblem, true); return; }
      var enteringEditor = value >= 3 && step < 3 && job && !childBusy;
      step = value; render(); persistSoon();
      if (enteringEditor) {
        return openEditor().then(function () {
          // Only an explicit 2→3 transition may regenerate an expired job.
          // Authentication/network errors retain job; a failed build never loops.
          if (autoPreview && step === 3 && !job && chosen().length) return generate();
        });
      }
      if (step === 1) chooser.focus();
      if (step === 2) window.requestAnimationFrame(function () { api.resize(); });
    }
    function move(index, delta) {
      if (unavailable() || index + delta < 0 || index + delta >= graphs.length) return;
      var entry = graphs.splice(index, 1)[0]; graphs.splice(index + delta, 0, entry); changed();
    }
    function preview(g, previewOnly) {
      if (document.querySelector(".sw-preview")) return;
      var focus = document.activeElement;
      if (focus === frame) { try { focus = frame.contentDocument.activeElement || frame; } catch (_) {} }
      var modal = node("div", document.body); modal.className = "sw-preview"; modal.setAttribute("role", "dialog"); modal.setAttribute("aria-label", "저장한 그래프 미리보기");
      modal.setAttribute("aria-modal", "true");
      var box = node("div", modal); node("h3", box, g.title);
      var image = node("img", box); image.src = g.report.image; image.alt = g.title;
      var actions = node("div", box); actions.className = "sw-controls sw-preview-actions";
      function dismiss() { modal.remove(); if (focus && focus.ownerDocument.documentElement.contains(focus)) focus.focus(); }
      var close = button(actions, "닫기", dismiss); close.focus();
      if (!previewOnly) {
        button(actions, "그래프 다시 편집", function () { dismiss(); edit(g); }).className = "sw-primary";
        button(actions, "파일 내보내기", function () { exportGraph(g); });
      }
      modal.onclick = function (event) { if (event.target === modal) dismiss(); };
      modal.onkeydown = function (event) {
        if (event.keyCode === 27) { event.preventDefault(); event.stopPropagation(); dismiss(); }
        else if (event.keyCode === 9) {
          // Shared floating-window controls may add opacity/reset inputs to the header.
          var buttons = modal.querySelectorAll("button:not(:disabled),input:not(:disabled),select:not(:disabled),textarea:not(:disabled),[href],[tabindex='0']");
          var first = buttons[0], last = buttons[buttons.length - 1];
          if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
          else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
        }
      };
    }
    function chooseGraphFile() { if (!unavailable() && graphs.length < 6) importInput.click(); }
    function exportGraph(g) {
      if (unavailable()) return Promise.resolve();
      busy = true; operation = "편집 그래프와 원본 RAW를 파일로 준비하는 중입니다."; render(); tell(operation);
      return window.RistSpectrumGraphFile.write(g,kind,function (message) { tell(message); }).then(function (blob) {
        var url = URL.createObjectURL(blob), link = node("a",document.body);
        link.href = url; link.download = kind + "-" + g.title.replace(/[\\/:*?"<>|\x00-\x1f]/g,"_").slice(0,80) + ".ristgraph";
        link.click(); link.remove(); window.setTimeout(function () { URL.revokeObjectURL(url); },30000);
        tell("그래프 파일 다운로드를 시작했습니다. 원본 RAW와 저장 시점의 편집 상태가 포함됩니다. 현재 화면의 미저장 수정은 먼저 그래프를 덮어쓴 뒤 내보내세요.");
      }).catch(function (error) { tell(error.message || "그래프 파일을 내보내지 못했습니다.",true); })
        .then(function () { busy = false; operation = ""; render(); });
    }
    function importFiles(incoming) {
      if (unavailable()) return Promise.resolve();
      if (!incoming || incoming.length !== 1 || !/\.ristgraph$/i.test(incoming[0].name || "")) {
        tell(".ristgraph 파일을 한 개씩 불러오세요. RAW 파일과 섞어서 업로드할 수 없습니다.",true); return Promise.resolve();
      }
      if (graphs.length >= 6) { tell("저장 그래프는 6개까지입니다. 불필요한 저장본을 먼저 정리하세요.",true); return Promise.resolve(); }
      var loaded = null;
      busy = true; operation = "그래프 파일과 원본 RAW를 검사하는 중입니다."; render(); tell(operation);
      return window.RistSpectrumGraphFile.load(incoming[0],kind,function (message) { tell(message); }).then(function (value) {
        var entry = {id:"g-"+Date.now()+"-"+Math.random().toString(36).slice(2,8),title:value.title,included:true,state:value.state,report:value.report};
        var previousGraphs = graphs, previousStale = stale;
        graphs = graphs.concat([entry]); stale = !!job;
        return persist().then(function () {
          loaded = entry; step = 2;
          tell("그래프 파일을 저장 목록에 추가했습니다. 원본 RAW도 복원되었습니다. 라이브러리 자동 적용·의뢰/전송 연결은 새로 확인하세요.");
        },function () {
          graphs = previousGraphs; stale = previousStale;
          throw new Error("브라우저 저장 공간 부족 또는 저장 차단으로 불러오지 못했습니다. 기존 작업은 유지됩니다.");
        });
      }).catch(function (error) { tell(error.message || "그래프 파일을 불러오지 못했습니다.",true); })
        .then(function () {
          busy = false; operation = ""; render();
          if (loaded) { persistSoon(); api.resize(); return edit(loaded); }
        });
    }
    function edit(g) {
      if (unavailable()) return;
      if (api.hasGraph() && !window.confirm("현재 편집 화면을 ‘" + g.title + "’ 저장본으로 바꿀까요? 저장하지 않은 현재 그래프 수정은 되돌릴 수 없습니다.")) return;
      var previousState = api.checkpoint ? api.checkpoint() : null;
      busy = true; operation = "저장한 그래프 편집 상태를 불러오는 중입니다."; render(); tell(operation);
      return api.restore(g.state).then(function () {
        active = g.id; name.value = g.title; step = 2; api.saveWorkspace(); persistSoon();
        tell("저장본을 불러왔습니다. 추가 편집 후 덮어쓰거나 새 그래프로 저장하세요." + (g.state.portableImport ? " 의뢰/전송 정보와 사용할 피크 라이브러리는 다시 선택하세요. 기존 피크 배정 이름은 유지됩니다." : ""));
      }).catch(function (error) {
        if (!previousState) { tell(error.message || "그래프를 복원하지 못했습니다.",true); return; }
        return api.restore(previousState).then(function () {
          api.saveWorkspace(); tell((error.message || "그래프를 복원하지 못했습니다.") + " 이전 편집 화면을 복구했습니다.",true);
        },function () { tell("그래프 복원에 실패했습니다. 저장 목록과 원본은 유지됩니다. 새로고침 후 다시 시도하세요.",true); });
      })
        .then(function () { busy = false; operation = ""; render(); api.resize(); });
    }
    function saveGraph(asCopy) {
      if (unavailable()) return Promise.resolve();
      if (!api.hasGraph()) { tell("RAW 파일을 업로드하고 그래프를 만든 후 저장하세요.", true); return Promise.resolve(); }
      if ((!active || asCopy) && graphs.length >= 6) { tell("한 작업에 그래프 6장까지 저장할 수 있습니다. 불필요한 저장본을 먼저 정리하세요.", true); return Promise.resolve(); }
      busy = true; operation = "그래프 이미지와 편집 상태를 저장하는 중입니다."; render(); tell(operation);
      return api.capture().then(function (captured) {
        var entry = {id:asCopy || !active ? "g-" + Date.now() + "-" + Math.random().toString(36).slice(2, 8) : active,
          title:name.value.trim() || "그래프 " + (graphs.length + 1), included:true, state:captured.state, report:captured.report};
        var old = graphs, next = graphs.slice(), index = next.findIndex(function (g) { return g.id === entry.id; });
        if (index >= 0) { entry.included = next[index].included; next[index] = entry; } else next.push(entry);
        graphs = next;
        var oldActive = active, oldStale = stale; active = entry.id; stale = !!job;
        return persist().then(function () {
          name.value = entry.title; tell("그래프를 저장했습니다. 이 브라우저의 로컬 저장 목록이며, 사이트 데이터를 지우면 삭제됩니다.");
        }, function () {
          graphs = old; active = oldActive; stale = oldStale;
          throw new Error("저장 공간 부족 또는 브라우저 저장 차단으로 저장하지 못했습니다. 기존 저장본은 유지됩니다.");
        });
      }).catch(function (error) { tell(error.message || "그래프 저장에 실패했습니다.", true); })
        .then(function () { busy = false; operation = ""; render(); });
    }
    function generate() {
      if (unavailable() || childBusy || !chosen().length) return;
      if (job && !window.confirm("선택한 그래프로 새 보고서를 생성할까요? 현재 보고서의 문구 수정은 새 보고서에 자동 복사되지 않습니다. 기존 수정본은 보존됩니다.")) return;
      busy = true; operation = "선택한 그래프 " + chosen().length + "개로 보고서를 생성하는 중입니다. 완료되면 미리보기가 열립니다."; render(); tell(operation);
      return api.build(chosen()).then(function (result) {
        if (!result || typeof result.jobId !== "string" || !/^[a-zA-Z0-9-]{1,100}$/.test(result.jobId)) throw new Error("보고서 작업 번호를 확인하지 못했습니다. 저장한 그래프를 유지한 채 다시 생성해 주세요.");
        job = {jobId:result.jobId}; stale = false; step = 3;
        editorProblem = "";
        persistSoon(); tell("보고서가 생성되었습니다. 미리보기에서 수정한 최종본을 다음 단계에서 저장·전송하세요.");
        return openEditor();
      }).catch(function (error) { tell(error.message, true); })
        .then(function () { busy = false; operation = ""; render(); });
    }
    frame.onload = function () {
      // The job can expire/restart between preflight and navigation. Never leave
      // a raw API error in the iframe; keep recovery in the parent workflow.
      try {
        var doc = frame.contentDocument;
        if (job && doc && doc.body && !doc.getElementById("editor-config")
            && doc.location.pathname === "/api/v1/" + kind + "/report/jobs/" + encodeURIComponent(job.jobId) + "/edit") {
          var value = JSON.parse((doc.querySelector("pre") || doc.body).textContent);
          if (value && value.status >= 400) { editorFailure(value, job.jobId); return; }
        }
      } catch (_) {} // Blank iframe, normal HTML or a cross-origin login redirect.
      editorStage();
    };
    window.addEventListener("message", function (event) {
      if (resetting || editorChecking || editorProblem || !job || event.origin !== window.location.origin || event.source !== frame.contentWindow) return;
      var data = event.data || {};
      if (data.type === "rist-saved-graph-request" && data.jobId === job.jobId && step === 3) {
        if (typeof data.requestId !== "string" || data.requestId.length > 100) return;
        var graph = graphs.filter(function (g) { return g.id === data.id; })[0];
        var reply = {type:"rist-saved-graph-data",jobId:job.jobId,requestId:data.requestId,id:data.id};
        try {
          if (unavailable() || !graph) throw new Error("저장 그래프를 사용할 수 없습니다. 목록과 작업 상태를 확인하세요.");
          reply.image = window.RistSpectrumWorkflow.imageFile(graph.report.image);
        } catch (error) { reply.error = error.message; }
        frame.contentWindow.postMessage(reply,window.location.origin);
      } else if (data.type === "rist-preview-saved-graph" && data.jobId === job.jobId && step === 3) {
        var selected = graphs.filter(function (g) { return g.id === data.id; })[0];
        if (selected) preview(selected, true); // Read-only: never upload, insert or alter report content.
      } else if (data.type === "rist-workflow-editor-state") {
        childBusy = !!data.busy;
        if (job && typeof data.jobId === "string" && /^[a-zA-Z0-9-]{1,100}$/.test(data.jobId) && job.jobId !== data.jobId) {
          job = {jobId:data.jobId}; persistSoon();
        }
        refreshActions();
        editorStage();
      } else if (data.type === "rist-workflow-editor-error" && data.jobId === job.jobId) editorFailure(data, job.jobId);
      else if (data.type === "rist-workflow-back") go(2);
    });
    function reset() {
      if (unavailable() || childBusy) return Promise.resolve();
      if (!window.confirm("현재 그래프와 저장된 그래프 " + graphs.length + "개를 모두 초기화하고 데이터 업로드부터 시작할까요? 저장하지 않은 편집 내용도 지워집니다. 원본 파일·피크 라이브러리·이미 생성하거나 전송한 서버 보고서는 보존됩니다.")) return Promise.resolve();
      busy = resetting = true; render(); tell("현재 작업과 저장 그래프를 초기화하는 중입니다.");
      var committed = false, errorMessage = "", empty = {version:1,graphs:[],active:null,step:1,job:null,stale:false};
      return api.reset(empty, function () {
        committed = true; graphs = []; active = job = null; stale = childBusy = false; step = 1; name.value = "";
        editorCheckSequence += 1; editorChecking = false; editorProblem = "";
        frame.removeAttribute("src");
        Array.prototype.forEach.call(document.querySelectorAll(".sw-preview"),function (modal) { modal.remove(); });
        render();
      }, writing).then(function () {
        tell("초기화했습니다. RAW 파일을 업로드하여 새 작업을 시작하세요. 서버에 생성·전송된 보고서는 보존됩니다.");
      }).catch(function () {
        errorMessage = committed ? "저장된 작업은 초기화했지만 그래프 화면을 비우지 못했습니다. 새로고침해 주세요."
          : "브라우저 저장 데이터를 초기화하지 못했습니다. 기존 작업은 유지됩니다. 저장 권한을 확인하고 다시 시도하세요.";
      }).then(function () { busy = resetting = false; render(); if (errorMessage) tell(errorMessage, true); if (committed) chooser.focus(); });
    }
    clear.addEventListener("click", reset);
    gd.addEventListener("rist-work-template-state-change", function () { if (ready) render(); });
    window.addEventListener("beforeunload", function (event) {
      if (busy || childBusy) { event.preventDefault(); event.returnValue = ""; }
    });
    return {
      start:function () {
        return api.read().then(function (state) {
          if (state && state.version === 1 && Array.isArray(state.graphs)) {
            graphs = state.graphs; active = state.active; step = Number(state.step) || 1; job = state.job; stale = !!state.stale;
            var selected = graphs.filter(function (g) { return g.id === active; })[0];
            if (selected) name.value = selected.title;
            if (step >= 3 && !job) step = 3;
            if (job && step >= 3) return openEditor();
          }
        }).catch(function () { tell("저장 목록을 읽지 못했습니다. 브라우저의 사이트 저장 권한을 확인하세요.", true); })
          .then(function () { ready = true; render(); if (step === 2) api.resize(); });
      },
      setBusy:function (value) { externalBusy = !!value; if (ready) render(); },
      dataLoaded:function () { active = null; name.value = ""; if (ready) { step = 2; render(); persistSoon(); api.resize(); } },
      go:go, save:saveGraph, reset:reset, importFiles:importFiles
    };
  }
  window.RistSpectrumWorkflow = {install:install, imageFile:function (url) {
    var prefix = "data:image/png;base64,", max = 16 * 1024 * 1024;
    if (typeof url !== "string" || url.indexOf(prefix) !== 0 || url.length === prefix.length) throw new Error("보고서 그래프 PNG를 만들지 못했습니다.");
    if (url.length - prefix.length > 4 * Math.ceil(max / 3)) throw new Error("보고서 그래프 이미지는 16MB 이하여야 합니다.");
    var parts = [];
    // Avoid a second full-size decoded string on 32-bit laboratory PCs.
    for (var offset = prefix.length; offset < url.length; offset += 32768) {
      var raw = window.atob(url.slice(offset, offset + 32768)), bytes = new Uint8Array(raw.length);
      for (var i = 0; i < raw.length; i += 1) bytes[i] = raw.charCodeAt(i);
      parts.push(bytes);
    }
    var image = new Blob(parts, {type:"image/png"});
    if (!image.size || image.size > max) throw new Error("보고서 그래프 이미지는 16MB 이하여야 합니다.");
    return image;
  }};
})();
