// Inlined in the TEM closure. No global browser policy or other experiment changes.
var TemTransfer = (function () {
  var MiB = 1024 * 1024;
  function policy(nav) {
    var memory = Number(nav.deviceMemory) || 0, cores = Number(nav.hardwareConcurrency) || 0;
    var connection = nav.connection || {}, mobile = /Android|iPhone|iPad|Mobile/i.test(nav.userAgent || '')
      || Boolean(nav.userAgentData && nav.userAgentData.mobile)
      || (/Macintosh/.test(nav.userAgent || '') && nav.maxTouchPoints > 1);
    var constrained = connection.saveData || /(^|-)2g$/.test(connection.effectiveType || '') || (memory > 0 && memory <= 1);
    var high = !mobile && memory >= 8 && cores >= 4;
    var standard = !mobile && memory >= 4 && cores >= 4;
    return {label: constrained ? '최소 자원' : high ? '성능 여유' : standard ? '일반 성능' : '자원 절약',
      initial: (constrained ? 1 : high ? 4 : 2) * MiB,
      max: (constrained ? 1 : high ? 8 : standard ? 4 : 2) * MiB,
      prefetch: !constrained && standard};
  }
  function create(options) {
    var nav = options.navigator || navigator, doc = options.document || document;
    var clock = options.now || function () { return performance.now(); };
    var budget = policy(nav), size = budget.initial, ceiling = budget.max, fast = 0, cooldown = 0;
    var worker = null, workerDisabled = false, pending = null, nextId = 0, disposed = false, slowPrep = 0;
    var timer = null, started = clock(), pausedAt = null, pausedMs = 0, epoch = 0, lastEmit = -Infinity;
    var stats = {total: 0, base: 0, committed: 0, measuredBytes: 0, retries: 0, prepMs: 0, requestMs: 0,
      responseWaitMs: 0, responseSamples: 0, serverMs: 0, serverSamples: 0, state: '준비'};
    function visible() { return doc.visibilityState !== 'hidden' && nav.onLine !== false; }
    function snapshot() {
      var elapsed = Math.max(0, (pausedAt === null ? clock() : pausedAt) - started - pausedMs);
      var rate = elapsed >= 250 && stats.measuredBytes > 0 ? stats.measuredBytes * 1000 / elapsed : 0;
      return Object.assign({}, stats, {label: budget.label, chunkBytes: size, maxBytes: ceiling,
        worker: !!worker && !workerDisabled, prefetch: canPrefetch(), paused: !visible(), activeMs: elapsed,
        bytesPerSecond: rate, remainingSeconds: rate > 0 ? Math.max(0, stats.total - stats.base - stats.committed) / rate : null});
    }
    function emit(force) {
      if (!options.render || (!force && clock() - lastEmit < 250)) return;
      lastEmit = clock(); options.render(snapshot());
    }
    function visibility() {
      epoch += 1;
      if (!visible() && pausedAt === null) pausedAt = clock();
      else if (visible() && pausedAt !== null) { pausedMs += clock() - pausedAt; pausedAt = null; }
      emit(true);
    }
    doc.addEventListener('visibilitychange', visibility);
    window.addEventListener('online', visibility);
    window.addEventListener('offline', visibility);
    function rejectWorker() {
      workerDisabled = true;
      if (worker) worker.terminate();
      worker = null;
      if (pending) { var task = pending; pending = null; clearTimeout(task.timer); task.reject(new Error('Checksum worker unavailable')); }
    }
    function workerChecksum(blob) {
      return new Promise(function (resolve, reject) {
        if (disposed || workerDisabled || typeof Worker !== 'function') { reject(new Error('No worker')); return; }
        if (pending) { reject(new Error('Checksum already active')); return; }
        try {
          if (!worker) {
            worker = new Worker('/api/v1/tem/upload-worker.js');
            worker.onerror = function (event) { if (event.preventDefault) event.preventDefault(); rejectWorker(); };
            worker.onmessage = function (event) {
              var data = event.data || {};
              if (!pending || data.id !== pending.id) return;
              if (data.error || !/^[a-f0-9]{8}$/.test(data.crc32 || '')) { rejectWorker(); return; }
              var task = pending; pending = null; clearTimeout(task.timer); task.resolve(data.crc32);
            };
          }
          pending = {id: ++nextId, resolve: resolve, reject: reject,
            timer: setTimeout(rejectWorker, 30000)};
          worker.postMessage({id: pending.id, blob: blob});
        } catch (_error) { rejectWorker(); reject(_error); }
      });
    }
    async function checksum(blob, fallback) {
      if (disposed) throw new Error('TEM upload stopped');
      var start = clock(), startEpoch = epoch, value;
      try { value = await workerChecksum(blob); }
      catch (_error) {
        if (disposed) throw new Error('TEM upload stopped');
        value = await fallback(blob);
      }
      if (disposed) throw new Error('TEM upload stopped');
      var elapsed = Math.max(0, clock() - start);
      stats.prepMs += elapsed;
      if (startEpoch === epoch && visible() && blob.size >= MiB) {
        slowPrep = elapsed > 2000 * blob.size / MiB ? slowPrep + 1 : 0;
        if (slowPrep >= 2) {
          // Coarse hardware hints can overestimate this PC's current capacity.
          budget.prefetch = false; budget.max = Math.min(budget.max, 2 * MiB);
          ceiling = Math.min(ceiling, budget.max); size = Math.min(size, ceiling);
        }
      }
      emit(false);
      return value;
    }
    function canPrefetch() { return budget.prefetch && !!worker && !workerDisabled && !disposed && visible(); }
    function retry() {
      stats.retries += 1; fast = 0; cooldown = 3;
      size = Math.max(Math.min(MiB, ceiling), size / 2);
      emit(true);
    }
    function observe(sample) {
      stats.requestMs += sample.elapsedMs;
      if (sample.responseWaitMs !== null) { stats.responseWaitMs += sample.responseWaitMs; stats.responseSamples += 1; }
      if (Number.isFinite(sample.serverMs)) { stats.serverMs += sample.serverMs; stats.serverSamples += 1; }
    }
    function ack(bytes, sample) {
      stats.committed += bytes;
      // An in-flight XHR may finish while hidden. Do not divide those bytes by
      // an active clock that excluded that hidden interval and inflate the ETA.
      if (sample.epoch === epoch) stats.measuredBytes += bytes;
      observe(sample);
      // A suspension, retry or tiny final fragment is not a bandwidth probe.
      if (sample.epoch === epoch && !sample.retried && bytes >= size * 0.9) {
        if (cooldown > 0) cooldown -= 1;
        else if (sample.elapsedMs > 8000) { size = Math.max(Math.min(MiB, ceiling), size / 2); fast = 0; cooldown = 2; }
        else if (sample.elapsedMs <= 2500 && bytes / Math.max(1, sample.elapsedMs) >= 1024) {
          fast += 1;
          if (fast >= 2) { size = Math.min(ceiling, size * 2); fast = 0; }
        } else fast = 0;
      } else fast = 0;
      emit(false);
    }
    function begin(total, base, maxBytes) {
      ceiling = Math.min(budget.max, Number(maxBytes) > 0 ? Number(maxBytes) : 8 * MiB);
      size = Math.min(size, ceiling);
      stats.total = total; stats.base = base; stats.state = '전송 중'; stats.prepMs = 0;
      started = clock(); pausedMs = 0; pausedAt = visible() ? null : started;
      timer = setInterval(function () { emit(false); }, 1000); emit(true);
    }
    function dispose(state) {
      if (disposed) return;
      stats.state = state; clearInterval(timer); timer = null;
      emit(true); disposed = true; rejectWorker();
      doc.removeEventListener('visibilitychange', visibility);
      window.removeEventListener('online', visibility); window.removeEventListener('offline', visibility);
    }
    return {checksum: checksum, begin: begin, ack: ack, observe: observe, retry: retry, dispose: dispose,
      canPrefetch: canPrefetch, snapshot: snapshot, epoch: function () { return epoch; },
      chunkBytes: function () { return size; }, now: clock};
  }
  return {create: create, policy: policy};
}());

var temTransfer = null;
function renderTemTransfer(stats) {
  var panel = document.getElementById('ahn-transfer-stats');
  panel.hidden = false;
  var rate = stats.bytesPerSecond > 0 ? (stats.bytesPerSecond / 1048576).toFixed(2) + ' MiB/s' : '속도 측정 중';
  var seconds = stats.remainingSeconds;
  var eta = seconds === null ? '남은 시간 계산 중' : seconds < 60 ? '약 ' + Math.ceil(seconds) + '초 남음'
    : '약 ' + Math.ceil(seconds / 60) + '분 남음';
  if (stats.state !== '전송 중') eta = stats.state;
  if (stats.paused && stats.state === '전송 중') { rate = '화면·연결 복귀 대기'; eta = '남은 시간 일시 정지'; }
  document.getElementById('ahn-transfer-speed').textContent = stats.label + ' · ' +
    (stats.chunkBytes / 1048576) + ' MiB/조각 · ' + rate + ' · ' + eta;
  var secondsText = function (ms) { return (ms / 1000).toFixed(2) + '초'; };
  document.getElementById('ahn-transfer-detail').textContent =
    '평균 속도는 이번 전송의 서버 확인 바이트/활성 시간 기준입니다. 이어받은 용량·화면 숨김·오프라인 대기와 그 전환에 걸친 조각의 속도 표본은 제외합니다.\n' +
    '파일 읽기·검증 ' + secondsText(stats.prepMs) + ' / 요청 처리 합계 ' + secondsText(stats.requestMs) +
    ' / 응답 대기 ' + (stats.responseSamples ? secondsText(stats.responseWaitMs) : '측정 불가') +
    ' / 서버 조각 검증·저장 ' + (stats.serverSamples ? secondsText(stats.serverMs) : '측정 불가') +
    ' / 재시도 ' + stats.retries + '회\n' +
    (stats.worker ? '별도 작업에서 검증' : '호환 검증 방식') + (stats.prefetch ? ' · 다음 1개 조각 미리 준비' : ' · 순차 준비') +
    '. 단계 시간은 병행/포함 관계이므로 합산하지 않습니다. 서버 시간은 요청 본문 수신 후부터 측정합니다.';
}

async function sendPreparedTemFiles(items, offsets, session, totalBytes, transfer) {
  var index = 0, position = 0, uploaded = Object.keys(offsets).reduce(function (sum, key) { return sum + offsets[key]; }, 0);
  function next() {
    while (index < items.length) {
      var item = items[index], path = item.path || item.file.name;
      position = Math.max(position, offsets[path] || 0);
      if (position >= item.file.size) { index += 1; position = 0; continue; }
      var offset = position, blob = item.file.slice(offset, Math.min(item.file.size, offset + transfer.chunkBytes()));
      position += blob.size;
      return {uploadId: session.uploadId, path: path, fileName: item.file.name, blob: blob, offset: offset,
        totalSize: item.file.size, chunkIndex: Math.floor(offset / 1048576),
        chunkCount: Math.max(1, Math.ceil(item.file.size / 1048576)), totalUploadBytes: totalBytes,
        fileIndex: index + 1, fileCount: items.length, transfer: transfer};
    }
    return null;
  }
  async function prepare() {
    var part = next();
    if (part) part.chunkCrc32 = await transfer.checksum(part.blob, chunkCrc32Main);
    return part;
  }
  await waitTemVisible();
  var current = await prepare();
  while (current) {
    await waitTemVisible();
    current.uploadedBefore = uploaded;
    var sending = uploadChunkWithRetry(current);
    // Attach rejection handling immediately; a failed request can abandon this
    // ONE preparation, and dispose() cancels its worker without unhandled errors.
    var prepared = transfer.canPrefetch() ? prepare().then(function (value) { return {value: value}; },
      function (error) { return {error: error}; }) : null;
    await sending;
    uploaded += current.blob.size;
    current = null;
    if (prepared) {
      var result = await prepared;
      if (result.error) throw result.error;
      current = result.value;
    } else {
      await waitTemVisible();
      current = await prepare();
    }
  }
}
