(function (root) {
  "use strict";
  // Shared by FTIR/Raman/TEM and XRD; ES5 for existing compatibility pages.
  root.RistPptxPassword = function (options) {
    var state = {}, locked = false, pending = false;
    function add(tag, parent, text) {
      var node = document.createElement(tag);
      if (text) node.textContent = text;
      parent.appendChild(node); return node;
    }
    var panel = add("details", options.host);
    panel.className = "pptx-password-panel";
    panel.style.cssText = "border:1px solid #ccd8e1;border-radius:6px;padding:10px;margin:12px 0;min-width:0";
    add("summary", panel, "PPT 수정 비밀번호");
    var status = add("p", panel); status.setAttribute("role", "status");
    var row = add("div", panel);
    row.style.cssText = "display:flex;flex-wrap:wrap;gap:8px;align-items:flex-end";
    function password(label) {
      var wrap = add("label", row, label);
      wrap.style.cssText = "flex:1 1 180px;min-width:0;margin:0";
      var input = add("input", wrap); input.type = "password";
      input.autocomplete = "new-password"; input.maxLength = 255;
      input.setAttribute("aria-label", label);
      input.style.cssText = "display:block;box-sizing:border-box;width:100%;min-width:0";
      return input;
    }
    var first = password("새 수정 비밀번호"), second = password("비밀번호 확인");
    var apply = add("button", row, "설정 / 변경"), remove = add("button", row, "해제");
    apply.type = remove.type = "button";
    apply.style.whiteSpace = remove.style.whiteSpace = "nowrap";
    add("p", panel, "파일은 비밀번호 없이 열람할 수 있습니다. 수정 비밀번호를 지원하는 PowerPoint에서 편집을 제한하며, 파일 자체를 암호화하지는 않습니다. 설정 후 저장된 새 PPT·전송 패키지에 적용됩니다. 서버 미리보기 편집은 계속 가능합니다.").className = "muted hint";
    function enabled() { return !!state.hashData; }
    function refresh() {
      first.disabled = second.disabled = apply.disabled = locked || pending;
      remove.disabled = locked || pending || !enabled();
    }
    function describe() { status.textContent = enabled() ? "수정 비밀번호 설정됨 · 원문은 저장하지 않습니다." : "수정 비밀번호 없음"; }
    this.value = function () { return JSON.parse(JSON.stringify(state)); };
    this.set = function (value) { state = value || {}; first.value = second.value = ""; describe(); refresh(); };
    this.lock = function (value) { locked = !!value; refresh(); };
    apply.onclick = function () {
      if (locked || pending) return;
      if (!first.value || first.value !== second.value) {
        status.textContent = "비밀번호와 확인 값을 동일하게 입력하세요."; second.focus(); return;
      }
      pending = true; refresh(); status.textContent = "수정 비밀번호를 확인하는 중입니다.";
      // Never copy the raw password into report state, drafts, localStorage or mail.
      var secret = first.value; first.value = second.value = "";
      options.request(secret, function (error, value) {
        pending = false; refresh();
        if (error) { status.textContent = error.message; return; }
        state = value.protection; describe(); refresh(); options.changed();
      });
      secret = "";
    };
    remove.onclick = function () {
      if (locked || pending) return;
      state = {}; first.value = second.value = ""; describe(); refresh(); options.changed();
    };
    first.onkeydown = second.onkeydown = function (event) {
      if (event.key === "Enter" || event.keyCode === 13) { event.preventDefault(); apply.click(); }
    };
    this.set(options.value);
  };
}(window));
