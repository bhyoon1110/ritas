(function () {
  "use strict";
  var modal = document.getElementById("tem-history");
  var opener = document.getElementById("ahn-history-open");
  if (!modal || !opener) return;
  var list = document.getElementById("th-list"), search = document.getElementById("th-search");
  var filter = document.getElementById("th-filter"), notice = document.getElementById("th-notice");
  var prev = document.getElementById("th-prev"), next = document.getElementById("th-next");
  var empty = document.getElementById("th-empty"), close = document.getElementById("th-close");
  var page = 1, serial = 0, pending = false, rows = [], debounce, oldOverflow;
  function bytes(n) { n = Number(n || 0); return n >= 1024 * 1024 * 1024 ? (n / 1024 / 1024 / 1024).toFixed(1) + " GiB" : (n / 1024 / 1024).toFixed(1) + " MiB"; }
  function date(n) { return n ? new Date(n * 1000).toLocaleString() : "-"; }
  function info(message, error) { notice.textContent = message; notice.className = error ? "th-error" : ""; }
  function api(url, options) {
    options = options || {}; options.credentials = "same-origin";
    return fetch(url, options).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (body) {
        if (!r.ok) throw new Error(body.message || "작업 이력을 처리하지 못했습니다. 다시 시도하세요.");
        return body;
      });
    });
  }
  function button(parent, text, action, row, title) {
    var b = document.createElement("button"); b.type = "button"; b.textContent = text;
    b.disabled = row.busy || pending;
    if (action === "purge" || action === "trash") b.className = "th-danger";
    b.onclick = function () { change([row], action, title ? title.value : ""); };
    parent.appendChild(b);
  }
  function render() {
    list.textContent = "";
    rows.forEach(function (row) {
      var item = document.createElement("div"); item.className = "th-row";
      var details = document.createElement("div"); details.className = "th-info";
      var name = document.createElement("input"); name.value = row.title; name.maxLength = 120;
      name.setAttribute("aria-label", "작업 이름: " + row.title); name.readOnly = !!row.deletedAt || row.busy || pending;
      var meta = document.createElement("p");
      var states = {queued:"대기 중", running:"처리 중", completed:"완료", failed:"실패"};
      meta.textContent = (row.pinned ? "[보존] " : "") + (states[row.status] || row.status) + " · " + date(row.createdAt) + " · " + bytes(row.bytes) + (row.requestNumber ? " · 의뢰 " + row.requestNumber : "") + (row.parentJobId ? " · 수정본" : "");
      var message = document.createElement("p");
      message.textContent = row.deletedAt ? "휴지통 이동: " + date(row.deletedAt) : row.message;
      details.appendChild(name); details.appendChild(meta); details.appendChild(message); item.appendChild(details);
      var actions = document.createElement("div"); actions.className = "th-actions";
      if (row.deletedAt) {
        button(actions, "복원", "restore", row);
        button(actions, "영구 삭제", "purge", row);
      } else {
        if (row.editUrl && /^\/api\/v1\/tem\/report\/jobs\/[0-9a-f]{32}\/edit$/.test(row.editUrl)) {
          var link = document.createElement("a"); link.textContent = "보고서 열기 ↗"; link.href = row.editUrl;
          link.target = "_blank"; link.rel = "noopener"; actions.appendChild(link);
        }
        button(actions, "이름 저장", "rename", row, name);
        button(actions, row.pinned ? "보존 해제" : "보존", row.pinned ? "unpin" : "pin", row);
        if (!row.pinned) button(actions, "휴지통", "trash", row);
      }
      item.appendChild(actions); list.appendChild(item);
    });
    if (!rows.length) list.textContent = "해당하는 작업이 없습니다.";
    empty.hidden = filter.value !== "trash" || !rows.length;
    empty.disabled = pending;
  }
  function load() {
    var current = ++serial;
    info("작업 이력을 불러오는 중입니다.");
    return api("/api/v1/tem/history?page=" + page + "&limit=20&trash=" + (filter.value === "trash") + "&q=" + encodeURIComponent(search.value)).then(function (data) {
      if (current !== serial || modal.hidden) return;
      var pages = Math.max(1, Math.ceil(data.total / 20));
      if (page > pages) { page = pages; return load(); }
      rows = data.items; render();
      document.getElementById("th-page").textContent = page + " / " + pages + " · " + data.total + "건";
      prev.disabled = page <= 1 || pending; next.disabled = page >= pages || pending;
      var s = data.storage;
      document.getElementById("th-policy").textContent = "완료·실패 후 " + s.retentionDays + "일 보관 → 휴지통 " + s.trashDays + "일 후 자동 삭제. 보존 지정은 자동 정리 제외. 전체 TEM 작업 공간 " + bytes(s.usedBytes) + " / " + bytes(s.limitBytes) + " (내 작업 " + bytes(s.myBytes) + ").";
      info(s.usedBytes >= s.limitBytes * 0.9 ? "저장 공간이 90% 이상 사용되었습니다. 불필요한 작업과 휴지통을 정리하세요." : "진행 중인 작업은 보호됩니다. 휴지통 이동만으로는 용량이 줄지 않습니다.", s.usedBytes >= s.limitBytes * 0.9);
    }).catch(function (error) { if (current === serial) info(error.message, true); });
  }
  function change(selected, action, title) {
    if (pending) return;
    if (action === "rename" && !title.trim()) { info("작업 이름을 입력하세요.", true); return; }
    if (action === "trash" && !window.confirm("작업을 휴지통으로 이동할까요? 보관 기간 내 복원할 수 있습니다. LIMS 전송 보고서는 유지됩니다.")) return;
    if (action === "purge" && !window.confirm("표시된 작업 중 " + selected.length + "건의 원본·미리보기·수정 이력을 영구 삭제할까요? 복구할 수 없습니다. LIMS 전송 보고서는 유지됩니다.")) return;
    pending = true; ++serial; render();
    prev.disabled = next.disabled = true; search.disabled = filter.disabled = close.disabled = true;
    var chain = Promise.resolve(), done = 0;
    selected.forEach(function (row) {
      chain = chain.then(function () {
        return api("/api/v1/tem/history/" + encodeURIComponent(row.jobId), {method:"POST",
          headers:{"Content-Type":"application/json", "X-Requested-With":"RIST-TEM-History"},
          body:JSON.stringify({action:action, title:title || ""})}).then(function () { done += 1; });
      });
    });
    var failure = "";
    chain.catch(function (error) { failure = error.message; }).then(function () {
      pending = false; search.disabled = filter.disabled = close.disabled = false;
      return load();
    }).then(function () { info(done + "건 처리했습니다." + (failure ? " " + failure : ""), !!failure); });
  }
  opener.onclick = function () {
    oldOverflow = document.body.style.overflow; document.body.style.overflow = "hidden";
    modal.hidden = false; page = 1; load(); search.focus();
  };
  close.onclick = function () {
    if (pending) return;
    modal.hidden = true; ++serial; clearTimeout(debounce); document.body.style.overflow = oldOverflow; opener.focus();
  };
  modal.addEventListener("keydown", function (event) {
    if (event.key === "Escape") { event.preventDefault(); close.click(); }
    if (event.key !== "Tab") return;
    var focus = Array.prototype.filter.call(modal.querySelectorAll("button,input,select,a"), function (el) { return !el.disabled && !el.hidden; });
    var first = focus[0], last = focus[focus.length - 1];
    if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
    if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
  });
  search.oninput = function () { clearTimeout(debounce); debounce = setTimeout(function () { page = 1; load(); }, 250); };
  filter.onchange = function () { page = 1; load(); };
  document.getElementById("th-refresh").onclick = function () { if (!pending) load(); };
  prev.onclick = function () { if (page > 1 && !pending) { page -= 1; load(); } };
  next.onclick = function () { if (!pending) { page += 1; load(); } };
  empty.onclick = function () { change(rows.filter(function (row) { return !!row.deletedAt && !row.busy; }), "purge"); };
})();
