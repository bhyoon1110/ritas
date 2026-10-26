/* Author-only PPT regions. No code from this file is embedded in recipient HTML. */
(function () {
  "use strict";
  window.RistDirectEditor = function (options) {
    var stage = document.getElementById("slide-stage"), image = document.getElementById("slide-image");
    var layer = document.getElementById("edit-regions"), panel = document.getElementById("inline-editor");
    var fields = document.getElementById("inline-fields"), note = document.getElementById("inline-note");
    var select = document.getElementById("edit-page"), previous = document.getElementById("edit-previous"), next = document.getElementById("edit-next");
    var state = document.getElementById("page-status"), toggle = document.getElementById("edit-toggle");
    var pages = [], page = 0, imageBase = "", ready = false, shown = true, locked = false, copies = [], active = null, nextPageId = null, canvasMode = false;
    var nextSelection=null;
    var sidebar = new window.RistPageSidebar({select:function(index){show(index,false,true);},move:function(id,target,after){if(options.onReorder)options.onReorder(id,target,after);},
      changed:function(){if(options.onSelection)options.onSelection();},step:function(delta){if(options.onMoveSelection)options.onMoveSelection(delta);}});
    function node(tag, parent, text) { var e = document.createElement(tag); if (text != null) e.textContent = text; parent.appendChild(e); return e; }
    function close() { panel.hidden = true; copies = []; active = null; }
    document.getElementById("inline-close").onclick = close;
    function optionsList(control) {
      var selected = control.value;
      control.textContent = ""; var empty = node("option", control, "공통 정보 선택"); empty.value = "";
      options.variables().forEach(function (item) {
        var option = node("option", control, item.name + (item.value ? " = " + item.value.slice(0,60) : " (미입력)"));
        option.value = item.name;
      });
      control.value = selected;
    }
    function edit(region) {
      if (!ready || locked) return;
      fields.textContent = ""; copies = []; active = null;
      var shared = false;
      region.refs.forEach(function (ref) {
        var source = options.field(ref); if (!source) return;
        var label = node("label", fields, source.getAttribute("aria-label") || "보고서 내용");
        var condition = source.classList.contains("report-condition-input");
        var input = node(source.type === "date" || condition ? "input" : "textarea", label);
        if (source.type === "date") input.type = "date";
        if (condition) {
          input.type = "text"; input.placeholder = "선택 또는 직접 입력";
          if (source.hasAttribute("list")) input.setAttribute("list",source.getAttribute("list"));
        }
        input.setAttribute("aria-label", "PPT 수정 · " + (source.getAttribute("aria-label") || ref));
        input.value = source.value; input.maxLength = source.maxLength > 0 ? source.maxLength : 4000;
        input.readOnly = source.readOnly;
        shared = shared || ref.indexOf("transfer/") === 0 || ref === "completionDate" || options.shared(ref);
        copies.push({source:source,input:input,condition:condition});
        input.onfocus = function () { active = input; };
        ["input", "compositionstart", "compositionend"].forEach(function (name) {
          input.addEventListener(name, function () {
            source.value = input.value;
            var event = document.createEvent("Event"); event.initEvent(name, true, false); source.dispatchEvent(event);
          });
        });
      });
      if (!copies.length) return;
      note.textContent = shared ? "공통 정보입니다. 변경하면 이 값을 사용하는 다른 페이지와 연결된 문구에도 반영됩니다." : "이 영역의 원문을 수정합니다. 여러 페이지에 요약·분할 표시된 문구도 함께 갱신됩니다.";
      var choice = document.getElementById("insert-variable"); optionsList(choice);
      panel.hidden = false;
      // Keep the editor near the selected region, within the slide's visible width.
      panel.style.left = Math.max(0, Math.min(region.x * 100, 45)) + "%";
      panel.style.top = Math.min(60, Math.max(0, region.y * 100)) + "%";
      copies[0].input.focus();
    }
    document.getElementById("insert-variable-button").onclick = function () {
      var name = document.getElementById("insert-variable").value;
      if (!active || !name || active.readOnly || locked || active.type === "date") return;
      // Routing fields are literal identifiers, not templates.
      var item = copies.filter(function (copy) { return copy.input === active; })[0];
      if (!item || !options.canInsert(item.source)) { note.textContent = "전송 식별자와 완료일은 직접 입력하세요."; return; }
      var start = active.selectionStart, end = active.selectionEnd, token = "{{" + name + "}}";
      if (active.value.length - (end - start) + token.length > active.maxLength) return;
      active.value = active.value.slice(0,start) + token + active.value.slice(end);
      var event = document.createEvent("Event"); event.initEvent("input",true,false); active.dispatchEvent(event);
      active.focus(); active.setSelectionRange(start + token.length,start + token.length);
    };
    function regions() {
      layer.textContent = ""; layer.hidden = !ready || !shown || canvasMode;
      if (!pages[page]) return;
      pages[page].regions.forEach(function (region) {
        var valid = region.refs.filter(function (ref) { return options.field(ref); });
        if (!valid.length) return;
        var button = node("button", layer, region.empty ? "입력" : "");
        button.type = "button"; button.className = "edit-region";
        button.style.left = (region.x * 100) + "%"; button.style.top = (region.y * 100) + "%";
        button.style.width = (region.w * 100) + "%"; button.style.height = (region.h * 100) + "%";
        var label = valid.map(function (ref) { return options.field(ref).getAttribute("aria-label") || ref; }).join(" / ");
        button.setAttribute("aria-label", "PPT 편집 · " + label); button.title = "클릭 또는 더블클릭: " + label;
        button.disabled = locked; button.onclick = function () { edit(region); };
      });
    }
    function show(index, keepEditor, keepSelection) {
      if (!pages.length) return;
      page = Math.max(0, Math.min(pages.length - 1, index));
      select.value = String(page); previous.disabled = page === 0; next.disabled = page === pages.length - 1;
      state.textContent = (page + 1) + " / " + pages.length + " · " + pages[page].title;
      if (!keepEditor) close();
      ready = false; layer.hidden = true;
      image.alt = "PPT " + (page + 1) + "페이지";
      image.src = imageBase + "/" + page;
      sidebar.select(page,keepSelection);
      if (options.onPage) options.onPage();
    }
    image.onload = function () { ready = true; regions(); };
    image.onerror = function () { ready = false; layer.hidden = true; state.textContent = "PPT 페이지를 불러오지 못했습니다. ‘미리보기 다시 불러오기’를 눌러 주세요."; };
    previous.onclick = function () { show(page - 1); }; next.onclick = function () { show(page + 1); };
    select.onchange = function () { show(Number(select.value)); };
    toggle.onclick = function () { shown = !shown; toggle.setAttribute("aria-pressed", String(shown)); regions(); };
    panel.addEventListener("keydown",function (event) { if (event.keyCode === 27) { close(); event.stopPropagation(); } });
    return {
      setLayout:function (value, base, preservePending) {
        var previousId = (pages[page] || {}).id;
        var previousSelection=sidebar.selectedIds();
        var pendingSelection=!preservePending&&nextPageId;
        var selectedId = (!preservePending && nextPageId) || previousId;
        pages = value.pages || []; imageBase = base; select.textContent = "";
        pages.forEach(function (item,index) { var option = node("option",select,(index + 1) + ". " + item.title); option.value = String(index); });
        stage.style.paddingTop = (100 / (value.ratio || 16/9)) + "%";
        var found = -1;
        pages.forEach(function (item,index) { if (item.id && item.id === selectedId) found = index; });
        if (found >= 0) page = found;
        if ((!preservePending && nextPageId) || (selectedId && found < 0)) close();
        sidebar.layout(value,base);
        if(pendingSelection&&nextSelection)sidebar.setSelected(nextSelection);
        var keepSelection=(!pendingSelection&&!!previousId&&(!previousSelection.length||sidebar.selectedIds().length>0))||!!(pendingSelection&&nextSelection);
        if (!preservePending) {nextPageId = null;nextSelection=null;}
        show(page, true,keepSelection);
        if (options.onLayout) options.onLayout(value);
      },
      current:function () { return pages[page] || null; },
      canvasMode:function (value) { canvasMode=value;if(value)close();regions(); },
      count:function () { return pages.length; },
      pageIds:function () { return pages.map(function(item){return item.id;}); },
      selectedPageIds:function () { return sidebar.selectedIds(); },
      sidebarLock:function (blocked,sending) { sidebar.lock(blocked,sending); },
      selectOnNext:function (id,ids) { nextPageId = id;nextSelection=ids?ids.slice():null;close(); },
      loading:function () { sidebar.loading();layer.hidden = true; state.textContent = "PPT 편집 영역을 불러오는 중입니다."; },
      error:function (message) { layer.hidden = true; state.textContent = message; },
      sync:function () {
        copies.forEach(function (copy) {
          if (document.activeElement !== copy.input) copy.input.value = copy.source.value;
          if (copy.condition) {
            if (copy.source.hasAttribute("list")) copy.input.setAttribute("list",copy.source.getAttribute("list"));
            else copy.input.removeAttribute("list");
          }
        });
        if (!panel.hidden) optionsList(document.getElementById("insert-variable"));
      },
      lock:function (value) {
        locked = value; copies.forEach(function (copy) { copy.input.disabled = value; });
        Array.prototype.forEach.call(layer.querySelectorAll("button"), function (button) { button.disabled = value; });
      }
    };
  };
}());
