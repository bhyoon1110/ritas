// Raw-range measurements are separate from detected/manual peaks. No smoothing,
// baseline correction, resampling or stack offsets enter ID/IG calculations.
(function() {
  function number(v) { return v !== null && v !== undefined && v !== "" && Number.isFinite(Number(v)); }
  function intensity(trace, index) {
    if (trace.customdata && trace.customdata.length === trace.x.length) {
      return number(trace.customdata[index]) ? Number(trace.customdata[index]) : NaN;
    }
    var y = trace.y && trace.y[index];
    return number(y) ? Number(y) - Number((trace.meta || {}).rist_raman_stack_offset || 0) : NaN;
  }
  function maximum(trace, range) {
    if (!range || !range.every(number) || range[0] >= range[1]) return null;
    var best = null, first = null, last = null, count = 0, tied = false;
    for (var i = 0; i < (trace.x || []).length; i++) {
      var x = trace.x[i], y = intensity(trace, i);
      if (!number(x) || !Number.isFinite(y) || x < range[0] || x > range[1]) continue;
      x = Number(x); count++;
      first = first === null ? x : Math.min(first, x);
      last = last === null ? x : Math.max(last, x);
      if (!best || y > best.intensity) { best = {x:x, intensity:y, index:i}; tied = false; }
      else if (y === best.intensity) {
        tied = true;
        // Deterministic even when exported raw data runs in descending order.
        if (x < best.x) best = {x:x, intensity:y, index:i};
      }
    }
    if (best) { best.edge = best.x === first || best.x === last; best.tied = tied; best.count = count; }
    return best;
  }
  function parent(data, group) {
    return data.filter(function(t) { return t.meta && t.meta.rist_sample_parent && t.meta.rist_sample_group === group; })[0];
  }
  function point(data, item, role) {
    var tr = parent(data, item.sampleGroup), p = item[role + "Point"];
    if (!tr || !p || !number(p.x) || !number(p.intensity) || !Number.isInteger(p.index)
        || !tr.x || Number(tr.x[p.index]) !== p.x || intensity(tr, p.index) !== p.intensity) return null;
    return {x:p.x, intensity:p.intensity, y:p.intensity + Number((tr.meta || {}).rist_raman_stack_offset || 0)};
  }
  function rows(data, ranges) {
    return data.filter(function(t) {
      return t.meta && t.meta.rist_sample_parent && t.visible !== false && t.visible !== "legendonly";
    }).map(function(tr) {
      var n = maximum(tr, ranges.numerator), d = maximum(tr, ranges.denominator), warnings = [], error = "";
      if (!n || !d) error = "구간에 유효한 Raw 데이터 없음";
      else if (n.count < 2 || d.count < 2) error = "구간의 데이터가 2점 미만";
      else if (Math.abs(d.intensity) < 1e-12) error = "분모 강도가 0 또는 매우 작음";
      else if (!Number.isFinite(n.intensity / d.intensity)) error = "비율 계산 범위 초과";
      if (n && n.edge) warnings.push("D 최고점이 구간 경계 · 범위 확인");
      if (d && d.edge) warnings.push("G 최고점이 구간 경계 · 범위 확인");
      if ((n && n.tied) || (d && d.tied)) warnings.push("동일 최고값 여러 개 · 가장 낮은 X 선택");
      if ((n && n.intensity < 0) || (d && d.intensity < 0)) warnings.push("음의 강도 · 베이스라인 확인");
      return {sampleGroup:tr.meta.rist_sample_group, sample:String(tr.name || tr.meta.rist_sample_group).replace(/<[^>]*>/g, ""),
        numeratorPoint:n, denominatorPoint:d, rawRanges:ranges, method:"raw-range-max",
        warnings:warnings, error:error, value:error ? null : n.intensity / d.intensity};
    });
  }
  function install(gd, bridge) {
    var active = false, role = "numerator", ranges = {numerator:null, denominator:null}, review = [], busy = false;
    var down = null, pointerId = null, frame = null;
    var control = gd.querySelector(".rist-raman-ratio-control"), button = document.createElement("button");
    button.type = "button"; button.className = "rist-raman-ratio-button"; button.textContent = "ID/IG 일괄 계산";
    button.setAttribute("data-ratio-batch", ""); control.appendChild(button);
    var panel = document.createElement("section"); panel.className = "rist-ratio-batch"; panel.hidden = true;
    panel.setAttribute("role", "dialog"); panel.setAttribute("aria-label", "ID/IG 구간 일괄 계산");
    panel.innerHTML = "<header><strong>ID/IG 일괄 계산</strong><span><button type='button' data-batch-collapse aria-expanded='true'>접기</button> <button type='button' data-batch-close>닫기</button></span></header>"
      + "<p>화면에 표시된 Raw 시료별 원본 강도 최고값으로 계산합니다. 피크 민감도·스택 이동과 무관하며 보정·면적비가 아닙니다.</p>"
      + "<div data-batch-ranges></div><p data-batch-status role='status' aria-live='polite'></p>"
      + "<div class='rist-ratio-batch-table' data-batch-review hidden><table><thead><tr><th>적용</th><th>시료</th>"
      + "<th>① D · 위치 / 강도</th><th>② G · 위치 / 강도</th><th>ID/IG</th><th>확인 사항</th></tr></thead><tbody></tbody></table></div>"
      + "<button type='button' data-batch-apply disabled>선택 결과 적용</button>";
    gd.appendChild(panel);
    var surface = document.createElement("div"); surface.className = "rist-ratio-range-surface"; surface.hidden = true;
    surface.setAttribute("aria-label", "X축 구간 드래그"); surface.setAttribute("touch-action", "none");
    gd.appendChild(surface);
    var bands = {};
    ["numerator", "denominator"].forEach(function(key) {
      var band = document.createElement("div"); band.className = "rist-ratio-range-band " + key;
      band.textContent = key === "numerator" ? "A · D (분자)" : "B · G (분모)";
      surface.appendChild(band); bands[key] = band;
      var row = document.createElement("div"); row.className = "rist-ratio-range-input";
      row.innerHTML = "<button type='button' data-batch-pick='" + key + "'>" + band.textContent + " 선택</button>"
        + "<input type='number' step='any' data-bound='0' aria-label='" + band.textContent + " 시작 cm⁻¹'>"
        + "<span>~</span><input type='number' step='any' data-bound='1' aria-label='" + band.textContent + " 끝 cm⁻¹'><span>cm⁻¹</span>";
      panel.querySelector("[data-batch-ranges]").appendChild(row);
      row.querySelector("button").onclick = function() { if (!busy) { role = key; down = null; update(); } };
      row.querySelectorAll("input").forEach(function(input) {
        input.addEventListener("change", function() {
          var inputs = row.querySelectorAll("input"), a = inputs[0].value, b = inputs[1].value;
          ranges[key] = number(a) && number(b) && Number(a) !== Number(b) ? [Number(a), Number(b)].sort(function(x,y) { return x-y; }) : null;
          recompute();
        });
      });
    });
    function message(text) { panel.querySelector("[data-batch-status]").textContent = text; }
    function stop(ev) { if (ev.preventDefault) ev.preventDefault(); if (ev.stopPropagation) ev.stopPropagation(); }
    function release() {
      var id = pointerId; pointerId = null; down = null;
      if (id !== null && surface.releasePointerCapture) {
        try { surface.releasePointerCapture(id); } catch (_) { /* already released */ }
      }
    }
    function close() {
      release(); active = false; panel.hidden = true; surface.hidden = true; button.classList.remove("is-active");
      bridge.preview(null); bridge.mode(false);
    }
    function updateGeometry() {
      if (!active) return;
      var drag = gd.querySelector(".nsewdrag"), axis = gd._fullLayout && gd._fullLayout.xaxis;
      if (!drag || !axis) return;
      var r = drag.getBoundingClientRect(), g = gd.getBoundingClientRect();
      surface.style.left = r.left-g.left + "px"; surface.style.top = r.top-g.top + "px";
      surface.style.width = r.width + "px"; surface.style.height = r.height + "px";
      ["numerator", "denominator"].forEach(function(key) {
        var range = down && role === key ? [down.x, down.last] : ranges[key], band = bands[key];
        band.hidden = !range;
        if (!range) return;
        var a = axis.d2p(range[0]) * r.width / axis._length, b = axis.d2p(range[1]) * r.width / axis._length;
        var left = Math.max(0, Math.min(a,b)), right = Math.min(r.width, Math.max(a,b));
        band.hidden = right < left; band.style.left = left + "px"; band.style.width = Math.max(0,right-left) + "px";
      });
    }
    function update() {
      surface.hidden = !active; surface.classList.toggle("picking", !!role);
      panel.classList.toggle("reviewing", !role && review.length > 0);
      panel.querySelector("[data-batch-review]").hidden = !!role || !review.length;
      panel.querySelectorAll("[data-batch-pick]").forEach(function(b) {
        var key = b.getAttribute("data-batch-pick"), row = b.parentNode;
        b.setAttribute("aria-pressed", String(key === role));
        row.querySelectorAll("input").forEach(function(input, i) {
          // Keep a partially typed range when focus moves to its second bound.
          if (ranges[key]) input.value = ranges[key][i];
          input.disabled = busy;
        });
        b.disabled = busy;
      });
      panel.querySelector("[data-batch-apply]").disabled = busy || !!role || !review.some(function(r) { return !r.error; });
      if (role) message((role === "numerator" ? "① A(D/분자)" : "② B(G/분모)") + " 구간을 그래프에서 가로로 드래그하세요. 숫자로도 입력할 수 있습니다.");
      updateGeometry();
    }
    function fmt(v) { return Number(v.toPrecision(7)).toString(); }
    function fillReview() {
      var body = panel.querySelector("tbody"); body.textContent = "";
      review.forEach(function(item, index) {
        var tr = document.createElement("tr"), td = document.createElement("td"), check = document.createElement("input");
        td.setAttribute("data-label", "적용");
        check.type = "checkbox"; check.checked = !item.error; check.disabled = !!item.error;
        check.setAttribute("data-batch-row", String(index)); check.setAttribute("aria-label", item.sample + " 적용"); td.appendChild(check); tr.appendChild(td);
        var cells = [item.sample];
        [item.numeratorPoint, item.denominatorPoint].forEach(function(p) { cells.push(p ? fmt(p.x) + " cm⁻¹ / " + fmt(p.intensity) : "—"); });
        cells.push(item.error ? "계산 불가" : fmt(item.value)); cells.push(item.error || item.warnings.join(" · ") || "정상");
        cells.forEach(function(text, i) {
          var cell = document.createElement("td"); cell.textContent = text;
          cell.setAttribute("data-label", ["시료", "① D · 위치/강도", "② G · 위치/강도", "ID/IG", "확인 사항"][i]);
          tr.appendChild(cell);
        });
        tr.tabIndex = 0;
        tr.addEventListener("mouseenter", function() { if (!item.error) bridge.preview(item); });
        tr.addEventListener("focus", function() { if (!item.error) bridge.preview(item); });
        tr.addEventListener("click", function() { if (!item.error) bridge.preview(item); });
        body.appendChild(tr);
      });
    }
    function recompute() {
      review = []; bridge.preview(null);
      if (!ranges.numerator || !ranges.denominator) { role = ranges.numerator ? "denominator" : "numerator"; update(); return; }
      if (Math.max(ranges.numerator[0], ranges.denominator[0]) <= Math.min(ranges.numerator[1], ranges.denominator[1])) {
        role = "denominator"; update(); message("A와 B 구간이 겹칩니다. 서로 다른 구간을 선택하세요."); return;
      }
      role = null; review = rows(gd.data || [], ranges); fillReview(); update();
      message(review.length ? "시료별 결과를 확인하세요. 경고가 있는 범위는 조정하고 제외할 시료는 체크를 해제하세요. 행을 가리키거나 누르면 최고점을 강조합니다."
        : "계산할 Raw 시료가 없습니다. 표시할 시료를 선택하세요.");
    }
    function xAt(ev) {
      var r = surface.getBoundingClientRect(), axis = gd._fullLayout.xaxis;
      // Avoid binary pixel conversion turning an intended boundary 200 into
      // 199.99999999997 and excluding that exact Raw point.
      return Number(axis.p2d(Math.max(0, Math.min(r.width, ev.clientX-r.left)) * axis._length / r.width).toFixed(6));
    }
    function begin(ev) {
      if (!active || !role || busy || (ev.button != null && ev.button !== 0) || down) return;
      stop(ev); var x = xAt(ev); down = {x:x, last:x, clientX:ev.clientX};
      if (ev.pointerId != null && surface.setPointerCapture) { pointerId = ev.pointerId; surface.setPointerCapture(pointerId); }
      updateGeometry();
    }
    function move(ev) {
      if (!down || (pointerId !== null && ev.pointerId !== pointerId)) return;
      stop(ev); down.last = xAt(ev);
      if (frame === null) frame = window.requestAnimationFrame(function() { frame = null; updateGeometry(); });
    }
    function end(ev) {
      if (!down || (pointerId !== null && ev.pointerId !== pointerId)) return;
      stop(ev); var a = down.x, b = xAt(ev), moved = Math.abs(ev.clientX-down.clientX) >= 4;
      release();
      if (!moved || !number(a) || !number(b) || a === b) { updateGeometry(); return; }
      ranges[role] = [a,b].sort(function(x,y) { return x-y; }); recompute();
    }
    if (window.PointerEvent) {
      surface.addEventListener("pointerdown", begin); surface.addEventListener("pointermove", move); surface.addEventListener("pointerup", end);
      surface.addEventListener("pointercancel", function() { release(); updateGeometry(); });
      surface.addEventListener("lostpointercapture", function() { release(); updateGeometry(); });
    } else {
      surface.addEventListener("mousedown", begin); window.addEventListener("mousemove", move); window.addEventListener("mouseup", end);
      surface.addEventListener("touchstart", function(ev) { if (ev.touches.length === 1) { ev.preventDefault(); begin(ev.touches[0]); } }, {passive:false});
      surface.addEventListener("touchmove", function(ev) { if (down) { ev.preventDefault(); move(ev.touches[0]); } }, {passive:false});
      surface.addEventListener("touchend", function(ev) { if (down) { ev.preventDefault(); end(ev.changedTouches[0]); } }, {passive:false});
      surface.addEventListener("touchcancel", function() { release(); updateGeometry(); });
    }
    // Suppress Plotly double-click/zoom and compatibility mouse events only on the picker surface.
    ["mousedown", "click", "dblclick", "wheel"].forEach(function(name) { surface.addEventListener(name, stop, {passive:false}); });
    button.onclick = function() {
      if (active) { close(); return; }
      gd.dispatchEvent(new CustomEvent("rist-exclusive-interaction-start", {detail:{mode:"raman-ratio-range"}}));
      active = true; ranges = {numerator:null, denominator:null}; review = []; role = "numerator";
      panel.querySelectorAll("[data-bound]").forEach(function(input) { input.value = ""; });
      panel.classList.remove("collapsed");
      panel.querySelector("[data-batch-collapse]").textContent = "접기";
      panel.querySelector("[data-batch-collapse]").setAttribute("aria-expanded", "true");
      bridge.mode(true); panel.hidden = false; button.classList.add("is-active"); update();
      var toolsClose = gd.classList.contains("rist-raman-tools-open") && gd.querySelector(".rist-raman-tools-close");
      if (toolsClose) toolsClose.click();
    };
    panel.querySelector("[data-batch-close]").onclick = close;
    panel.querySelector("[data-batch-collapse]").onclick = function() {
      var collapsed = panel.classList.toggle("collapsed");
      this.textContent = collapsed ? "펼치기" : "접기"; this.setAttribute("aria-expanded", String(!collapsed));
    };
    panel.querySelector("[data-batch-apply]").onclick = function() {
      if (busy || role) return;
      if (JSON.stringify(rows(gd.data || [], ranges)) !== JSON.stringify(review)) {
        recompute(); message("Raw 또는 표시 시료가 변경되었습니다. 갱신된 결과를 다시 확인하세요."); return;
      }
      var selected = [];
      panel.querySelectorAll("[data-batch-row]").forEach(function(c) { if (c.checked && !c.disabled) selected.push(review[Number(c.getAttribute("data-batch-row"))]); });
      if (!selected.length) { message("적용할 시료를 선택하세요."); return; }
      busy = true; update(); message("선택한 결과를 반영하고 있습니다.");
      bridge.apply(selected).then(function() { busy = false; close(); }, function() { busy = false; update(); message("반영하지 못했습니다. 기존 결과를 유지했습니다. 다시 시도하세요."); });
    };
    gd.on("plotly_afterplot", updateGeometry);
    window.addEventListener("resize", updateGeometry);
    window.addEventListener("blur", function() { release(); updateGeometry(); });
    document.addEventListener("visibilitychange", function() { if (document.hidden) { release(); updateGeometry(); } });
    document.addEventListener("keydown", function(ev) { if (!gd._ristInteractionModes && active && ev.key === "Escape") { stop(ev); close(); } });
    gd.addEventListener("rist-exclusive-interaction-start", function(ev) { if (active && ev.detail.mode !== "raman-ratio-range") close(); });
    ["rist-plot-data-replaced", "rist-history-restored", "rist-plot-structure-changed"].forEach(function(name) { gd.addEventListener(name, function() { if (active) close(); }); });
  }
  window.RistRamanRanges = {maximum:maximum, rows:rows, point:point, install:install};
})();
