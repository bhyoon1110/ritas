/* Portable spectrum snapshot: bounded JSON + original RAW bytes, no ZIP/base64 RAW copies.
 * ES5 / FileReader / Blob; works over the laboratory HTTP endpoint without Web Crypto.
 * CRC32 detects accidental damage, not authenticity. Treat every imported field as untrusted.
 */
(function () {
  "use strict";
  var MAGIC = "RISTGRF1", HEADER = 16, META_MAX = 64 * 1024 * 1024, RAW_MAX = 50 * 1024 * 1024;
  var BLOCK = 256 * 1024, table = null;
  function fail(message) { throw new Error(message); }
  function object(value) { return value && typeof value === "object" && !Array.isArray(value); }
  function integer(value) { return typeof value === "number" && isFinite(value) && Math.floor(value) === value; }
  function read(blob, text) {
    return new Promise(function (resolve, reject) {
      var reader = new FileReader();
      reader.onload = function () { resolve(reader.result); };
      reader.onerror = reader.onabort = function () { reject(new Error("그래프 파일을 읽지 못했습니다. 파일을 다시 선택하세요.")); };
      if (text) reader.readAsText(blob, "UTF-8"); else reader.readAsArrayBuffer(blob);
    });
  }
  function checksum(blob) {
    if (!table) {
      table = new Uint32Array(256);
      for (var i = 0; i < 256; i++) {
        var c = i; for (var k = 0; k < 8; k++) c = (c & 1) ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
        table[i] = c >>> 0;
      }
    }
    var offset = 0, crc = 0xffffffff;
    function next() {
      if (offset >= blob.size) return Promise.resolve((crc ^ 0xffffffff) >>> 0);
      return read(blob.slice(offset, offset + BLOCK)).then(function (buffer) {
        var bytes = new Uint8Array(buffer);
        for (var n = 0; n < bytes.length; n++) crc = table[(crc ^ bytes[n]) & 255] ^ (crc >>> 8);
        offset += bytes.length; return next();
      });
    }
    return next();
  }
  function hex(value) { return ("00000000" + value.toString(16)).slice(-8); }
  function inspectTree(value) {
    var nodes = 0;
    function visit(item, depth) {
      if (++nodes > 4000000 || depth > 64) fail("편집 데이터가 너무 복잡합니다. 그래프를 나누어 저장하세요.");
      if (typeof item === "number" && !isFinite(item)) fail("유효하지 않은 그래프 숫자입니다.");
      if (typeof item === "string" && /(?:javascript|vbscript):|<\s*(?:script|iframe|object|embed)\b/i.test(item)) fail("허용되지 않은 실행 내용이 포함된 그래프 파일입니다.");
      if (Array.isArray(item)) { for (var i = 0; i < item.length; i++) visit(item[i], depth + 1); }
      else if (object(item)) Object.keys(item).forEach(function (key) {
        if (key === "__proto__" || key === "constructor" || key === "prototype") fail("허용되지 않은 그래프 속성입니다.");
        visit(item[key], depth + 1);
      });
    }
    visit(value, 0);
  }
  function png(url) {
    if (typeof url !== "string" || !/^data:image\/png;base64,[A-Za-z0-9+/]+={0,2}$/.test(url)
        || url.length > 22 + 4 * Math.ceil(16 * 1024 * 1024 / 3)) fail("그래프 미리보기 PNG가 없거나 16MB를 초과합니다.");
    var encoded = url.slice(22), padding = /==$/.test(encoded) ? 2 : /=$/.test(encoded) ? 1 : 0;
    if (encoded.length % 4 || encoded.length * 3 / 4 - padding > 16 * 1024 * 1024
        || atob(encoded.slice(0,16)).slice(0,8) !== "\x89PNG\r\n\x1a\n") fail("그래프 미리보기 PNG 형식 또는 크기가 올바르지 않습니다.");
  }
  function layout(value) {
    if (!object(value)) fail("그래프 배치 정보가 올바르지 않습니다.");
    ["annotations", "shapes", "images"].forEach(function (key) {
      if (value[key] != null && (!Array.isArray(value[key]) || value[key].length > 10000)) fail("그래프 주석·도형 정보가 올바르지 않습니다.");
    });
    (value.images || []).forEach(function (image) {
      if (!object(image) || typeof image.source !== "string" || !/^data:image\/(png|jpeg|webp);base64,[A-Za-z0-9+/]+={0,2}$/.test(image.source)) fail("외부 주소 또는 SVG 이미지는 그래프 파일에서 불러올 수 없습니다.");
    });
  }
  function validate(value, kind) {
    if (!object(value) || value.format !== "rist-spectrum-graph" || value.version !== 1) fail("지원하지 않는 그래프 파일 형식/버전입니다. .ristgraph 파일을 선택하세요.");
    if (value.experiment !== kind.toUpperCase()) fail("이 파일은 " + value.experiment + " 그래프입니다. 해당 실험 페이지에서 불러오세요.");
    inspectTree(value);
    if (typeof value.title !== "string" || !value.title.trim() || value.title.length > 100) fail("그래프 이름이 올바르지 않습니다.");
    var state = value.state, report = value.report;
    if (!object(state) || state.version !== 1 || !object(state.analysisPayload) || !object(state.reportMetadata)
        || !Array.isArray(state.plotData) || !state.plotData.length || state.plotData.length > 10000) fail("그래프 편집 정보 또는 분석 결과가 없습니다.");
    if (typeof state.originStyle !== "boolean" || typeof state.sensitivity !== "number" || !isFinite(state.sensitivity)
        || state.sensitivity < 0 || state.sensitivity > 100) fail("그래프 표시 설정이 올바르지 않습니다.");
    if (kind === "ftir" && state.signalMode !== "absorbance" && state.signalMode !== "transmittance") fail("FTIR 신호 단위가 올바르지 않습니다.");
    var rawTraces = 0;
    state.plotData.forEach(function (trace) {
      if (!object(trace) || (trace.type && trace.type !== "scatter" && trace.type !== "scattergl")
          || !Array.isArray(trace.x) || !Array.isArray(trace.y) || trace.x.length !== trace.y.length) fail("스펙트럼 좌표가 올바르지 않습니다.");
      var valid = 0;
      for (var i = 0; i < trace.x.length; i++) {
        if ((trace.x[i] != null && typeof trace.x[i] !== "number") || (trace.y[i] != null && typeof trace.y[i] !== "number")) fail("스펙트럼 좌표는 숫자여야 합니다.");
        if (trace.x[i] != null && trace.y[i] != null) valid++;
      }
      if (trace.meta && trace.meta.rist_sample_parent && valid >= 2) rawTraces++;
    });
    if (!rawTraces) fail("편집 가능한 RAW 스펙트럼이 없습니다.");
    layout(state.plotLayout);
    if (state.signalOriginals != null) layout(state.signalOriginals);
    if (!object(report) || !object(report.analysis)) fail("보고서용 그래프 정보가 없습니다.");
    [report.figure, report.viewerFigure].forEach(function (figure) {
      if (!object(figure) || !Array.isArray(figure.data)) fail("보고서용 그래프가 올바르지 않습니다.");
      layout(figure.layout);
    });
    png(report.image);
    if (!Array.isArray(value.rawFiles) || !value.rawFiles.length || value.rawFiles.length > 10) fail("원본 RAW는 1~10개여야 합니다.");
    var total = 0;
    value.rawFiles.forEach(function (file) {
      if (!object(file) || typeof file.name !== "string" || !/^[^\\/\x00-\x1f]{1,255}$/.test(file.name)
          || !(kind === "ftir" ? /\.dpt$/i : /\.(txt|csv|tsv|xlsx|xlsm)$/i).test(file.name)) fail("원본 RAW 파일명이 올바르지 않습니다.");
      if (!integer(file.size) || file.size < 1 || file.size > 20 * 1024 * 1024 || !integer(file.lastModified)
          || file.lastModified < 0 || typeof file.type !== "string" || file.type.length > 100
          || !/^[a-f0-9]{8}$/.test(file.crc32)) fail("원본 RAW 크기·검증 정보가 올바르지 않습니다.");
      total += file.size;
    });
    if (total > RAW_MAX) fail("원본 RAW의 합계는 50MB 이하여야 합니다.");
    return total;
  }
  function analysis(value) {
    var result = Object.assign({}, value); delete result.reportContext; return result;
  }
  function portableState(state) {
    // No account, SSO, report job/request binding or installation-local library IDs.
    return {version:1,plotData:state.plotData,plotLayout:state.plotLayout,analysisPayload:analysis(state.analysisPayload),
      reportMetadata:state.reportMetadata || {},originStyle:state.originStyle,sensitivity:state.sensitivity,
      signalMode:state.signalMode || "absorbance",signalOriginals:state.signalOriginals || null};
  }
  function write(graph, kind, progress) {
    return Promise.resolve().then(function () {
      var files = graph.state.files || [], segments = [], value = {
        format:"rist-spectrum-graph",version:1,experiment:kind.toUpperCase(),title:graph.title,createdAt:new Date().toISOString(),
        state:portableState(graph.state),report:{analysis:analysis(graph.report.analysis),figure:graph.report.figure,
          viewerFigure:graph.report.viewerFigure,image:graph.report.image},rawFiles:[]
      };
      var chain = Promise.resolve(), total = 0;
      if (!files.length || files.length > 10) fail("원본 RAW가 없는 저장본은 내보낼 수 없습니다. RAW를 불러와 그래프를 다시 저장하세요.");
      files.forEach(function (file, index) {
        chain = chain.then(function () {
          if (!(file.blob instanceof Blob) || !file.blob.size || file.blob.size > 20 * 1024 * 1024) fail("저장본의 원본 RAW 파일이 없거나 20MB를 초과합니다.");
          total += file.blob.size; if (total > RAW_MAX) fail("원본 RAW의 합계는 50MB 이하여야 합니다.");
          if (progress) progress("원본 RAW 확인 중 " + (index + 1) + "/" + files.length);
          return checksum(file.blob).then(function (crc) {
            value.rawFiles.push({name:file.name,type:file.type || "application/octet-stream",lastModified:file.lastModified || 0,
              size:file.blob.size,crc32:hex(crc)}); segments.push(file.blob);
          });
        });
      });
      return chain.then(function () {
        validate(value,kind);
        var metadata = new Blob([JSON.stringify(value)],{type:"application/json;charset=utf-8"});
        if (metadata.size > META_MAX) fail("편집 데이터가 64MB를 초과합니다. 샘플 수를 줄여 그래프를 나누어 저장하세요.");
        return checksum(metadata).then(function (crc) {
          var header = new ArrayBuffer(HEADER), bytes = new Uint8Array(header), view = new DataView(header);
          for (var i = 0; i < MAGIC.length; i++) bytes[i] = MAGIC.charCodeAt(i);
          view.setUint32(8,metadata.size,true); view.setUint32(12,crc,true);
          return new Blob([header,metadata].concat(segments),{type:"application/octet-stream"});
        });
      });
    });
  }
  function load(file, kind, progress) {
    return Promise.resolve().then(function () {
      if (!(file instanceof Blob) || file.size < HEADER || file.size > HEADER + META_MAX + RAW_MAX) fail("그래프 파일이 비어 있거나 허용 크기를 초과합니다.");
      return read(file.slice(0,HEADER));
    }).then(function (header) {
      var bytes = new Uint8Array(header), view = new DataView(header), magic = "";
      for (var i = 0; i < 8; i++) magic += String.fromCharCode(bytes[i]);
      if (magic !== MAGIC) fail("RIST 그래프 파일이 아닙니다. DPT는 RAW 업로드, 템플릿 JSON은 템플릿 불러오기를 사용하세요.");
      var size = view.getUint32(8,true), crc = view.getUint32(12,true);
      if (!size || size > META_MAX || HEADER + size >= file.size) fail("그래프 파일이 잘렸거나 편집 데이터 크기가 올바르지 않습니다.");
      var metadata = file.slice(HEADER,HEADER+size);
      if (progress) progress("그래프 편집 정보 무결성 확인 중");
      return checksum(metadata).then(function (actual) {
        if (actual !== crc) fail("그래프 편집 정보가 손상되었습니다. 원본에서 다시 내보내세요.");
        return read(metadata,true);
      }).then(function (text) {
        var value;
        try { value = JSON.parse(text); } catch (_) { fail("그래프 편집 정보를 해석할 수 없습니다."); }
        var rawSize = validate(value,kind);
        if (HEADER + size + rawSize !== file.size) fail("RAW 데이터가 잘렸거나 알 수 없는 데이터가 덧붙은 파일입니다.");
        var offset = HEADER + size, records = [], chain = Promise.resolve();
        value.rawFiles.forEach(function (record,index) {
          var blob = file.slice(offset,offset+record.size,record.type); offset += record.size;
          chain = chain.then(function () {
            if (progress) progress("원본 RAW 무결성 확인 중 " + (index+1) + "/" + value.rawFiles.length);
            return checksum(blob).then(function (actual) {
              if (hex(actual) !== record.crc32) fail("원본 RAW가 손상되었습니다: " + record.name);
              records.push({name:record.name,type:record.type,lastModified:record.lastModified,blob:blob});
            });
          });
        });
        return chain.then(function () {
          var state = portableState(value.state);
          state.files = records; state.selectedLibraryIds = []; state.workTemplate = null;
          state.reportTransfer = {}; state.portableImport = true;
          state.statusText = "그래프 파일에서 RAW와 편집 상태를 복원했습니다.";
          return {title:value.title,state:state,report:{analysis:analysis(value.report.analysis),figure:value.report.figure,
            viewerFigure:value.report.viewerFigure,image:value.report.image}};
        });
      });
    });
  }
  window.RistSpectrumGraphFile = {write:write,load:load};
})();
