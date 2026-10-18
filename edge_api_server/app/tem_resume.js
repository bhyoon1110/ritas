// Inlined inside the TEM page closure; also compiled into the Chrome 49 bundle.
// Persist identifiers only, never file bytes, passwords or authentication tokens.
var temRecovery = {
  key: null, record: null, restoring: false, uploadActive: false,
  wake: null, wakePending: false, retryOnReturn: false, restoreOnReturn: false
};
var recoveryMessage = document.getElementById("ahn-recovery-message");
var recoveryButton = document.getElementById("ahn-recovery-resume");

function recoveryNotice(message) {
  recoveryMessage.textContent = message;
}
function persistTemWork(record) {
  temRecovery.record = record;
  if (!temRecovery.key) return;
  try {
    if (record) window.localStorage.setItem(temRecovery.key, JSON.stringify(record));
    else window.localStorage.removeItem(temRecovery.key);
  } catch (_error) {
    recoveryNotice("이 브라우저는 진행 상태 저장을 허용하지 않습니다. 업로드가 끝날 때까지 이 탭을 유지하세요.");
  }
}
function loadTemWork(userId) {
  // Do not restore a different user's identifiers on a shared laboratory PC.
  temRecovery.record = null;
  temRecovery.key = userId ? "rist.tem.resume.v1." + userId : null;
  if (!temRecovery.key) return;
  try {
    var saved = JSON.parse(window.localStorage.getItem(temRecovery.key) || "null");
    var validId = /^[a-f0-9]{32}$/;
    if (saved && saved.version === 1
        && (validId.test(saved.uploadId || "") || validId.test(saved.jobId || ""))) {
      temRecovery.record = saved;
    }
  } catch (_error) {}
}
function rememberTemUpload(uploadId) {
  persistTemWork({version: 1, uploadId: uploadId, jobId: null});
}
function rememberTemJob(payload) {
  if (!payload || !payload.jobId) return;
  persistTemWork({version: 1, uploadId: temRecovery.record && temRecovery.record.uploadId,
    jobId: payload.jobId});
}
function syncRecoveryControls() {
  var record = temRecovery.record;
  recoveryButton.hidden = !record;
  recoveryButton.disabled = !record || operationBusy || collectingFiles || temRecovery.restoring
    || (!record.jobId && !bundleItems.length);
  recoveryButton.textContent = record && record.jobId ? "진행 상태 확인" : "업로드 이어받기";
}
function canRunTemTransfer() {
  return document.visibilityState !== "hidden" && navigator.onLine !== false;
}
async function waitTemVisible() {
  if (canRunTemTransfer()) return;
  recoveryNotice("화면 꺼짐 또는 연결 끊김으로 대기 중입니다. 다시 열고 연결되면 이어서 진행합니다.");
  await new Promise(function(resolve) {
    function resume() {
      if (!canRunTemTransfer()) return;
      document.removeEventListener("visibilitychange", resume);
      window.removeEventListener("pageshow", resume);
      window.removeEventListener("online", resume);
      resolve();
    }
    document.addEventListener("visibilitychange", resume);
    window.addEventListener("pageshow", resume);
    window.addEventListener("online", resume);
    resume();
  });
}
function temFetch(url, options) {
  // A suspended mobile connection must not leave the workspace busy forever.
  return new Promise(function(resolve, reject) {
    var controller = typeof AbortController === "function" ? new AbortController() : null;
    options = options || {};
    if (controller) options.signal = controller.signal;
    var timer = setTimeout(function() {
      if (controller) controller.abort();
      reject(new Error("서버 응답 대기 시간이 초과되었습니다."));
    }, 30000);
    fetch(url, options).then(function(value) {
      clearTimeout(timer); resolve(value);
    }, function(error) { clearTimeout(timer); reject(error); });
  });
}
async function releaseTemWake() {
  var lock = temRecovery.wake;
  temRecovery.wake = null;
  if (lock) try { await lock.release(); } catch (_error) {}
}
async function requestTemWake() {
  if (!temRecovery.uploadActive || !canRunTemTransfer() || !window.isSecureContext
      || !navigator.wakeLock || temRecovery.wake || temRecovery.wakePending) return;
  temRecovery.wakePending = true;
  try {
    var lock = await navigator.wakeLock.request("screen");
    if (!temRecovery.uploadActive || !canRunTemTransfer()) {
      await lock.release();
    } else {
      temRecovery.wake = lock;
      lock.addEventListener("release", function() {
        if (temRecovery.wake === lock) temRecovery.wake = null;
      });
    }
  } catch (_error) {
    // Battery policy, unsupported OS and denied permission never block uploads.
  } finally { temRecovery.wakePending = false; }
}
function temSelectionManifest(items) {
  return items.filter(function(item) { return item.file.size > 0; }).map(function(item) {
    return {path: item.path || item.file.name, size: item.file.size,
      lastModified: Number(item.file.lastModified || 0)};
  });
}
async function getTemResumeState(uploadId) {
  for (var attempt = 0; attempt < 4; attempt += 1) {
    await waitTemVisible();
    try { return await requestReport("/api/v1/tem/upload-sessions/" + encodeURIComponent(uploadId)); }
    catch (error) {
      if (!(error.isNetworkError || error.isTransientError) || attempt === 3) throw error;
      await sleep(650 * (attempt + 1));
    }
  }
}
async function checkTemResumeSelection(items, state) {
  var manifest = temSelectionManifest(items);
  var files = state.files || [];
  if (!state.resumable || files.length !== manifest.length) {
    throw new Error("이전 업로드와 같은 전체 파일/폴더를 다시 선택하세요. 새 작업은 초기화 후 시작하세요.");
  }
  var lookup = Object.create(null);
  var nonempty = items.filter(function(item) { return item.file.size > 0; });
  manifest.forEach(function(row, index) { lookup[row.path] = {meta: row, item: nonempty[index]}; });
  for (var i = 0; i < files.length; i += 1) {
    var saved = files[i], selected = lookup[saved.path];
    if (!selected || saved.size !== selected.meta.size || saved.lastModified !== selected.meta.lastModified) {
      throw new Error("파일 이름·경로·크기·수정일이 이전 선택과 다릅니다. 같은 원본 파일을 선택하세요.");
    }
    var verified = 0;
    for (var c = 0; c < saved.chunks.length; c += 1) {
      await waitTemVisible();
      var chunk = saved.chunks[c];
      if (chunk.offset !== verified || chunk.size <= 0 || verified + chunk.size > saved.size) {
        throw new Error("서버의 이어받기 정보가 올바르지 않습니다. 초기화 후 다시 업로드하세요.");
      }
      recoveryNotice("이미 전송한 파일의 내용을 확인 중입니다 (" + (i + 1) + "/" + files.length + ").");
      if (await chunkCrc32(selected.item.file.slice(chunk.offset, chunk.offset + chunk.size)) !== chunk.crc32) {
        throw new Error("이미 전송한 부분과 파일 내용이 다릅니다: " + saved.path + ". 같은 원본 파일로 이어받으세요.");
      }
      verified += chunk.size;
    }
    if (verified !== saved.uploadedBytes) throw new Error("서버의 저장 용량 검증에 실패했습니다.");
  }
  return files;
}
function temRecoveryError(error) {
  temRecovery.retryOnReturn = Boolean(error.isNetworkError || error.isTransientError);
  if (error.status === 404 || error.status === 410) {
    persistTemWork(null);
    recoveryNotice("작업이 만료되었거나 서버가 재시작되었습니다. 파일을 다시 선택해 새로 업로드하세요.");
  } else if (error.status === 401 || error.status === 403) {
    recoveryNotice("로그인 또는 TEM 작업 권한을 확인하세요. 원래 계정으로 다시 로그인하면 저장된 작업을 확인할 수 있습니다.");
  } else {
    recoveryNotice((error.message || "연결이 중단되었습니다.") + " 진행 상태는 유지됩니다. ‘이어받기/진행 상태 확인’을 눌러 재시도하세요.");
  }
  syncRecoveryControls();
}
async function restoreTemWork(openCompleted) {
  if (!temRecovery.record || operationBusy || collectingFiles || temRecovery.restoring) return;
  if (!canRunTemTransfer()) { temRecovery.restoreOnReturn = true; return; }
  temRecovery.restoreOnReturn = false;
  temRecovery.restoring = true;
  temRecovery.retryOnReturn = false;
  syncActionState();
  try {
    var record = temRecovery.record;
    if (!record.jobId) {
      var state = await getTemResumeState(record.uploadId);
      if (state.job) { rememberTemJob(state.job); record = temRecovery.record; }
      else {
        recoveryNotice("이전 업로드 " + formatBytes(state.uploadedBytes || 0) + "가 서버에 보관되어 있습니다. 같은 전체 파일/폴더를 다시 선택하고 ‘업로드 이어받기’를 누르세요. 마지막 전송 후 2시간까지 복원할 수 있습니다.");
        return;
      }
    }
    setBusy(true);
    var payload = await requestReport("/api/v1/tem/report/jobs/" + encodeURIComponent(record.jobId));
    if (payload.status === "completed" && !openCompleted) {
      lastReportJob = payload;
      renderSummary(payload);
      finishProgress("TEM 보고서가 생성되었습니다.");
      recoveryNotice("이전 보고서가 완성되었습니다. ‘진행 상태 확인’으로 미리보기를 열거나 ‘초기화’로 새 작업을 시작하세요.");
    } else await finishTemReport(payload);
  } catch (error) { temRecoveryError(error); }
  finally { temRecovery.restoring = false; setBusy(false); }
}
async function finishTemReport(payload) {
  rememberTemJob(payload); // Persist BEFORE polling, not only when finished.
  recoveryNotice("업로드가 완료되어 서버에서 보고서를 생성합니다. 화면을 끄거나 나갔다 돌아와도 진행 상태를 복원합니다.");
  payload = await waitForReportJob(payload);
  lastReportJob = payload;
  renderSummary(payload);
  finishProgress("TEM 보고서가 생성되었습니다.");
  recoveryNotice("보고서가 완성되었습니다. 미리보기로 이동합니다.");
  // Keep the completed id, so returning to /tem can recover a lost navigation.
  window.location.assign(payload.editUrl || ("/api/v1/tem/report/jobs/" + encodeURIComponent(payload.jobId) + "/edit"));
}
async function runTemUpload() {
  if (operationBusy || collectingFiles || temRecovery.restoring) return;
  if (temRecovery.record && temRecovery.record.jobId) { await restoreTemWork(true); return; }
  setBusy(true);
  temRecovery.retryOnReturn = false;
  temRecovery.uploadActive = true;
  requestTemWake();
  stopProgressTimer();
  setUploadProgress(0, "raw 파일 업로드를 준비하는 중입니다.", true, false);
  setProgress(0, "업로드 완료 후 보고서 작업을 시작합니다.", true, false);
  try {
    var payload = await uploadBundleWithSession();
    temRecovery.uploadActive = false;
    await releaseTemWake();
    await finishTemReport(payload);
  } catch (error) {
    temRecoveryError(error);
    setStatus(error.message || "작업이 중단되었습니다.", true);
    failProgress(error.message || "작업이 중단되었습니다.", error.errorFeedbackUrl || "");
  } finally {
    temRecovery.uploadActive = false;
    await releaseTemWake();
    setBusy(false);
  }
}
function temReturned() {
  if (!canRunTemTransfer()) { releaseTemWake(); return; }
  requestTemWake();
  if (temRecovery.restoreOnReturn) { restoreTemWork(); return; }
  if (!operationBusy && !temRecovery.restoring && temRecovery.retryOnReturn && temRecovery.record) {
    if (temRecovery.record.jobId) restoreTemWork();
    else if (bundleItems.length) runTemUpload();
  }
}
document.addEventListener("visibilitychange", temReturned);
window.addEventListener("pageshow", temReturned);
window.addEventListener("online", temReturned);
window.addEventListener("pagehide", releaseTemWake);
recoveryButton.addEventListener("click", function() {
  if (temRecovery.record && temRecovery.record.jobId) restoreTemWork(true);
  else runTemUpload();
});
