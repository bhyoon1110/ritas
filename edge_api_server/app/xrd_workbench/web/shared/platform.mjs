import { request, waitJob, apiUrl } from './api.mjs';

export function transferValues() {
  const value = id => (document.getElementById(id)?.value || '').trim();
  return {requestNumber:value('reportNumber'), experimentCode:value('ristExperiment'),
    equipmentCode:value('ristEquipment'), operatorId:value('ristOperator')};
}

export async function startExport(storageId, revision, kind, extra = {}, onProgress) {
  const job = await (await request(`/api/reports/${storageId}/exports`, {
    method:'POST', headers:{'Content-Type':'application/json'},
    body:JSON.stringify({...extra, kind, revision, transfer:transferValues()})
  })).json();
  return waitJob(job, onProgress);
}

export function downloadJob(job) {
  // Native downloads stream to disk; do not materialize a large ZIP Blob on XP.
  const a = document.createElement('a');
  a.href = apiUrl(`/api/jobs/${job.job_id}/file`);
  a.download = `XRD-report.${job.kind}`;
  document.body.appendChild(a); a.click(); a.remove();
}

export async function mountReportPlatform(send, changed) {
  // Model endpoint/credentials belong to the server configuration, not a browser form.
  for (const id of ['provider','baseUrl','model']) document.getElementById(id).closest('label').hidden = true;
  const hint = document.querySelector('#settingsDialog > p:last-child');
  if (hint) hint.textContent = '기존 RIST 서버에 설정된 로컬 LLM을 사용합니다. 모델 연결·검증 실패 시 근거 기반 문장을 보존합니다.';
  const block = document.createElement('section');
  block.className = 'ristTransfer';
  block.innerHTML = '<h2>의뢰 정보</h2><p>의뢰를 조회하거나 번호를 직접 입력한 뒤, 아래에서 안내 메일을 확인하세요.</p>' +
    '<label>의뢰번호<input id="ristRequestNumber" maxlength="80" placeholder="직접 입력 또는 의뢰 조회"></label>' +
    '<button type="button" id="ristRequestLookup">의뢰 조회</button><div id="ristRequests"></div>' +
    '<div class="formGrid"><label>LIMS 시험방법코드<input id="ristExperiment" maxlength="100"></label><label>실험장비 코드<input id="ristEquipment" maxlength="100"></label><label>실험자(SSO)<input id="ristOperator" maxlength="100" readonly></label></div>' +
    '<p id="ristTransferStatus" role="status"></p>';
  const fields = document.getElementById('deliveryFields');
  fields.prepend(block);
  const actions = document.createElement('section');
  actions.className = 'ristSendActions';
  actions.innerHTML = '<h2>보고서 전송</h2><p>의뢰 정보와 안내 메일을 확인한 뒤 전송하세요.</p>' +
    '<div class="generationActions"><button type="button" id="saveDelivery">변경 내용 저장</button>' +
    '<button type="button" id="ristReportSend" class="primary">보고서 전송 큐에 등록</button></div>' +
    '<a href="/account">회원정보에서 사내 SSO 인증</a><p id="ristSendStatus" role="status"></p>';
  fields.appendChild(actions);
  const $ = id => document.getElementById(id), status = text => { $('ristTransferStatus').textContent = text; };
  const sendProgress=window.RistFileProgress.monitor($('ristSendStatus'));
  $('ristRequestNumber').value = $('reportNumber').value;
  $('ristRequestNumber').addEventListener('input', () => {
    $('reportNumber').value = $('ristRequestNumber').value;
    $('reportNumber').dispatchEvent(new Event('input', {bubbles:true}));
  });
  for (const id of ['ristExperiment', 'ristEquipment']) $(id).addEventListener('input', changed);
  function methodCode(item) { return (item.testMethodCode || item.experimentCode || '').trim(); }
  const picker = window.RistRequestPicker.mount($('ristRequests'), {id:'rist-request', experimentType:'XRD',
    loadButton:$('ristRequestLookup'),
    fetch:async (url, callback) => {
      try {
        const response = await fetch(url, {headers:{'X-Request-Id':'xrd-workbench-' + Date.now()}});
        const body = await response.json();
        if (!response.ok) throw new Error(body.message || body.error?.message || body.detail || '의뢰 조회에 실패했습니다.');
        callback(null, body);
      } catch (error) { callback(error); }
    },
    onSelect:item => {
      $('reportNumber').value = item.requestNumber || '';
      $('ristExperiment').value = methodCode(item);
      const equipment = item.equipmentCode || item.deviceCode || item.instrumentCode;
      if (equipment) $('ristEquipment').value = equipment;
      $('reportNumber').dispatchEvent(new Event('input', {bubbles:true}));
      changed();
      status(methodCode(item) ? '표지 의뢰번호와 시험방법코드를 반영했습니다. 사용한 장비코드를 확인하세요.'
        : '선택한 시험에 시험방법코드가 없습니다. 전송 전 LIMS 시험방법코드를 확인하세요.');
    }
  });
  $('reportNumber').addEventListener('input', () => {
    $('ristRequestNumber').value = $('reportNumber').value;
    const selectedItem = picker.selected();
    if (!selectedItem || $('reportNumber').value.trim() === selectedItem.requestNumber) return;
    if ($('ristExperiment').value === methodCode(selectedItem)) $('ristExperiment').value = '';
    picker.clear(); changed();
  });
  $('ristReportSend').onclick = async () => {
    $('ristReportSend').disabled = true;
    sendProgress.start('SSO·전송 권한 확인 중…');
    try {
      const info = await (await request('/api/platform')).json();
      $('ristOperator').value = info.operatorId;
      changed();
      if (Object.values(transferValues()).some(v => !v)) throw new Error('의뢰번호·LIMS 시험방법코드·장비코드 및 SSO 인증을 확인하세요.');
      await send();
    } catch (error) { $('ristSendStatus').textContent = error.message; }
    finally { $('ristReportSend').disabled = false; sendProgress.stop(); }
  };
  try {
    const info = await (await request('/api/platform')).json();
    $('ristOperator').value = info.operatorId;
    changed();
  } catch (error) { status(error.message); }
}
