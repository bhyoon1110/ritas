(function () {
  "use strict";
  // ES5/native controls: same behavior on Supermium 144 and the Chrome49 fallback.
  window.RistReportConditionOptions = function (config) {
    var fields = config.fields || [], key = config.storageKey, bindings = [], locked = false;
    var manager, toggle, group, list, note, draft = null, baseline = null;
    function element(tag, parent, text) {
      var node = document.createElement(tag);
      if (text != null) node.textContent = text;
      if (parent) parent.appendChild(node);
      return node;
    }
    function button(parent, text, action) {
      var node = element("button", parent, text); node.type = "button";
      node.onclick = function () { if (!locked) action(); }; return node;
    }
    function normalized(values) {
      var result = [], seen = [];
      (values || []).forEach(function (value) {
        var text = String(value == null ? "" : value).trim(), lower = text.toLowerCase();
        if (text && seen.indexOf(lower) < 0) { result.push(text); seen.push(lower); }
      });
      return result;
    }
    function stored() {
      var value = JSON.parse(window.localStorage.getItem(key) || "{}");
      return value && typeof value === "object" && !Array.isArray(value) ? value : {};
    }
    function values(data) {
      var result = {};
      fields.forEach(function (field) {
        result[field.field] = normalized(Array.isArray(data[field.field]) ? data[field.field] : field.defaults);
      }); return result;
    }
    function load() { try { return values(stored()); } catch (_) { return values({}); } }
    function match(label) {
      var text = String(label || "").trim().replace(/\s+/g, " ").toLowerCase();
      return fields.filter(function (field) { return field.label.toLowerCase() === text; })[0];
    }
    function refresh() {
      var current = load();
      bindings.forEach(function (binding) {
        var field = match(binding.label()), select = binding.select;
        select.hidden = !field; select.disabled = locked; select.textContent = "";
        if (!field) return;
        select.setAttribute("aria-label", binding.label() + " 선택지");
        var choices = current[field.field];
        element("option", select, choices.length ? "저장된 선택지에서 선택" : "등록된 선택지 없음 · 직접 입력 가능").value = "";
        choices.forEach(function (value, index) { element("option", select, value).value = String(index); });
        select.onchange = function () {
          if (locked || binding.input.disabled || select.value === "") return;
          binding.input.value = choices[Number(select.value)]; select.value = "";
          var event = document.createEvent("Event"); event.initEvent("input", true, false);
          binding.input.dispatchEvent(event); binding.input.focus();
        };
      });
    }
    function tell(text, error) { note.textContent = text; note.className = error ? "error" : "muted"; }
    function close() {
      draft = null; baseline = null; manager.hidden = true;
      toggle.setAttribute("aria-expanded", "false"); toggle.focus();
    }
    function render() {
      list.textContent = "";
      var field = fields.filter(function (item) { return item.field === group.value; })[0];
      draft[field.field].forEach(function (value, index) {
        var row = element("div", list); row.className = "report-condition-option-row";
        var input = element("input", row); input.type = "text"; input.value = value; input.maxLength = 4000;
        input.setAttribute("aria-label", field.label + " 선택지 " + (index + 1));
        input.oninput = function () { draft[field.field][index] = input.value; };
        var remove = button(row, "삭제", function () {
          draft[field.field].splice(index, 1); render();
          var next = list.querySelectorAll("input"); (next[Math.min(index, next.length - 1)] || group).focus();
        }); remove.setAttribute("aria-label", field.label + " 선택지 " + (index + 1) + " 삭제");
      });
      if (!draft[field.field].length) element("p", list, "등록된 선택지가 없습니다. 아래에서 추가할 수 있습니다.").className = "muted";
      lock(locked);
    }
    function open() {
      baseline = load(); draft = JSON.parse(JSON.stringify(baseline));
      manager.hidden = false; toggle.setAttribute("aria-expanded", "true");
      tell("선택지는 이 브라우저의 분석 화면과 공유됩니다. 입력한 보고서 값은 변경하지 않습니다.");
      render(); group.focus();
    }
    function save() {
      var latest, now, next = values(draft), changed = fields.filter(function (field) {
        return JSON.stringify(next[field.field]) !== JSON.stringify(baseline[field.field]);
      });
      if (!changed.length) { close(); return; }
      try {
        latest = stored(); now = values(latest);
        if (changed.some(function (field) { return JSON.stringify(now[field.field]) !== JSON.stringify(baseline[field.field]); })) {
          tell("다른 화면에서 같은 항목의 선택지를 수정했습니다. 취소 후 다시 열어 최신 목록을 확인하세요. 현재 수정 내용은 유지됩니다.", true); return;
        }
        // Merge only edited fields, preserving other tabs' and unknown preferences.
        changed.forEach(function (field) { latest[field.field] = next[field.field]; });
        window.localStorage.setItem(key, JSON.stringify(latest));
      } catch (_) {
        tell("선택지를 저장하지 못했습니다. 브라우저 저장 공간·설정을 확인하세요. 수정 내용은 유지됩니다.", true); return;
      }
      refresh(); close();
    }
    function mount(parent) {
      if (!fields.length || !key) return;
      var box = element("div", parent); box.className = "report-condition-options";
      toggle = button(box, "선택지 관리", function () { if (manager.hidden) open(); else close(); });
      toggle.setAttribute("aria-expanded", "false"); toggle.setAttribute("aria-controls", "report-condition-manager");
      manager = element("div", box); manager.id = "report-condition-manager"; manager.hidden = true;
      var label = element("label", manager, "관리할 실험조건·환경 항목");
      group = element("select", label); group.setAttribute("aria-label", "관리할 실험조건·환경 항목");
      fields.forEach(function (field) { element("option", group, field.label).value = field.field; });
      group.onchange = function () { if (draft) render(); };
      list = element("div", manager); list.className = "report-condition-option-list";
      button(manager, "선택지 추가", function () {
        draft[group.value].push(""); render(); var inputs = list.querySelectorAll("input"); inputs[inputs.length - 1].focus();
      });
      note = element("p", manager); note.setAttribute("role", "status");
      var actions = element("div", manager); actions.className = "report-condition-option-actions";
      button(actions, "전체 기본값 복원", function () { draft = values({}); render(); tell("선택지 저장을 눌러야 기본값이 반영됩니다."); });
      button(actions, "취소", close);
      button(actions, "선택지 저장", save);
      manager.addEventListener("keydown", function (event) {
        if (event.key === "Escape" && !locked) { event.preventDefault(); event.stopPropagation(); close(); }
      });
    }
    function lock(value) {
      locked = !!value;
      if (toggle) toggle.disabled = locked;
      if (manager) Array.prototype.forEach.call(manager.querySelectorAll("input,select,button"), function (node) { node.disabled = locked; });
      bindings.forEach(function (binding) { binding.select.disabled = locked; });
    }
    window.addEventListener("storage", function (event) {
      if (event.key !== key && event.key !== null) return;
      refresh();
      if (draft) tell("다른 화면에서 선택지가 변경되었습니다. 저장 시 충돌 여부를 확인합니다.");
    });
    return {
      mount:mount, lock:lock, refresh:refresh,
      reset:function () { bindings = []; draft = null; baseline = null; manager = toggle = null; },
      attach:function (parent, input, label) {
        if (!fields.length) return;
        var select = element("select", parent); select.className = "report-condition-picker";
        bindings.push({select:select,input:input,label:label});
      }
    };
  };
})();
