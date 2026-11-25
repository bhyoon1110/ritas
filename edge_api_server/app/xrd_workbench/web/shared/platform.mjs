import { request, waitJob, apiUrl } from './api.mjs';

let requestSelection = null;
const transferFields = [
  {key:'requestNumber', id:'ristRequestNumber', label:'의뢰번호'},
  {key:'experimentCode', id:'ristExperiment', label:'LIMS 시험방법코드'},
  {key:'equipmentCode', id:'ristEquipment', label:'실험장비 코드'},
  {key:'operatorId', id:'ristOperator', label:'실험자 정보'},
];

export function missingTransferFields(values) {
  return transferFields.filter(field => typeof values[field.key] !== 'string' || !values[field.key].trim());
}

export function transferValues() {
  const value = id => (document.getElementById(id)?.value || '').trim();
  const transfer = {requestNumber:value('reportNumber'), experimentCode:value('ristExperiment'),
    equipmentCode:value('ristEquipment'), operatorId:value('ristOperator')};
  if (requestSelection?.scope === 'all' && requestSelection.requestNumber === transfer.requestNumber
      && requestSelection.experimentCode === transfer.experimentCode) transfer.requestScope = 'all';
  return transfer;
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
  requestSelection = null;
  let autoEquipment = '';
  // Model endpoint/credentials belong to the server configuration, not a browser form.
  for (const id of ['provider','baseUrl','model']) document.getElementById(id).closest('label').hidden = true;
  const hint = document.getElementById('modelConnectionHint');
  if (hint) hint.textContent = '기존 RIST 서버에 설정된 로컬 LLM을 사용합니다. 모델 연결·검증 실패 시 근거 기반 문장을 보존합니다.';
  const block = document.createElement('section');
  block.className = 'ristTransfer';
  block.innerHTML = '<h2>의뢰 정보</h2><p>의뢰를 조회하거나 번호를 직접 입력한 뒤, 아래에서 안내 메일을 확인하세요.</p>' +
    '<label>의뢰번호<input id="ristRequestNumber" maxlength="80" placeholder="직접 입력 또는 의뢰 조회"></label>' +
    '<button type="button" id="ristRequestLookup">의뢰 조회</button><div id="ristRequests"></div>' +
    '<div class="formGrid"><label>LIMS 시험방법코드<input id="ristExperiment" maxlength="100"></label><label>실험장비 코드<input id="ristEquipment" maxlength="100" aria-describedby="ristEquipmentHelp"></label><label>실험자(SSO)<input id="ristOperator" maxlength="100" readonly></label></div>' +
    '<p id="ristEquipmentHelp">의뢰에서 장비코드가 제공되면 자동 입력합니다. 비어 있으면 실제 사용한 장비의 등록 코드를 입력하세요. 검색 조건의 장비 자산번호는 자동 복사하지 않습니다.</p>' +
    '<p id="ristTransferStatus" role="status"></p>';
  const fields = document.getElementById('deliveryFields');
  fields.prepend(block);
  const actions = document.createElement('section');
  actions.className = 'ristSendActions';
  actions.innerHTML = '<h2>보고서 전송</h2><p>의뢰 정보와 안내 메일을 확인한 뒤 전송하세요.</p>' +
    '<div class="generationActions"><button type="button" id="saveDelivery">변경 내용 저장</button>' +
    '<button type="button" id="ristReportSend" class="primary">보고서 전송 큐에 등록</button></div>' +
    '<a href="/account">회원정보·SSO 인증 상태 보기</a><p id="ristSendStatus" role="status"></p>';
  fields.appendChild(actions);
  const $ = id => document.getElementById(id), status = text => { $('ristTransferStatus').textContent = text; };
  const sendProgress=window.RistFileProgress.monitor($('ristSendStatus'));
  $('ristRequestNumber').value = $('reportNumber').value;
  $('ristRequestNumber').addEventListener('input', () => {
    $('reportNumber').value = $('ristRequestNumber').value;
    $('reportNumber').dispatchEvent(new Event('input', {bubbles:true}));
  });
  for (const field of transferFields) $(field.id).addEventListener('input', () => $(field.id).removeAttribute('aria-invalid'));
  for (const id of ['ristExperiment', 'ristEquipment']) $(id).addEventListener('input', changed);
  $('ristEquipment').addEventListener('input', () => { autoEquipment = ''; });
  function clearAutoEquipment() {
    if (autoEquipment && $('ristEquipment').value.trim() === autoEquipment) $('ristEquipment').value = '';
    autoEquipment = '';
  }
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
    onClear:() => {
      requestSelection = null;
      clearAutoEquipment();
      changed();
      status('의뢰 선택을 해제했습니다. 전체 범위의 의뢰를 연결하려면 다시 조회해 선택하세요.');
    },
    onSelect:(item, selection) => {
      requestSelection = {scope:selection.scope, requestNumber:(item.requestNumber || '').trim(), experimentCode:methodCode(item)};
      $('reportNumber').value = item.requestNumber || '';
      $('ristExperiment').value = methodCode(item);
      const equipment = [item.equipmentCode, item.deviceCode, item.instrumentCode]
        .find(code => typeof code === 'string' && code.trim());
      clearAutoEquipment();
      if (equipment) {
        autoEquipment = equipment.trim();
        $('ristEquipment').value = autoEquipment;
        $('ristEquipment').removeAttribute('aria-invalid');
      }
      $('reportNumber').dispatchEvent(new Event('input', {bubbles:true}));
      changed();
      status(methodCode(item)
        ? (selection.scope === 'all'
          ? '전체 의뢰에서 선택했습니다. XRD 보고서를 선택한 의뢰와 실제 시험방법코드에 연결합니다. 의뢰·시험·사용한 장비를 확인하세요.'
          : '표지 의뢰번호와 시험방법코드를 반영했습니다. 사용한 장비코드를 확인하세요.')
        : '선택한 시험에 시험방법코드가 없습니다. 전송 전 LIMS 시험방법코드를 확인하세요.');
    }
  });
  $('ristExperiment').addEventListener('input', () => {
    const selectedItem = picker.selected();
    if (selectedItem && $('ristExperiment').value.trim() !== methodCode(selectedItem)) picker.clear();
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
    sendProgress.start('전송 정보 확인 중…');
    try {
      const info = await (await request('/api/platform')).json();
      $('ristOperator').value = typeof info.operatorId === 'string' ? info.operatorId.trim() : '';
      changed();
      const missing = missingTransferFields(transferValues());
      for (const field of transferFields) $(field.id).removeAttribute('aria-invalid');
      if (missing.length) {
        for (const field of missing) $(field.id).setAttribute('aria-invalid', 'true');
        $(missing[0].id).focus();
        if (missing.length === 1 && missing[0].key === 'equipmentCode') {
          throw new Error('실험장비 코드를 입력해 주세요. 의뢰 정보의 실험장비 코드 칸에서 실제 사용한 장비를 확인하세요.');
        }
        if (missing.length === 1 && missing[0].key === 'operatorId') {
          throw new Error('실험자 정보를 확인할 수 없습니다. 회원정보의 로그인·SSO 상태를 확인하세요.');
        }
        throw new Error('다음 전송 정보를 확인해 주세요: ' + missing.map(field => field.label).join(', ') + '.');
      }
      await send();
    } catch (error) { $('ristSendStatus').textContent = error.message; status(error.message); }
    finally { $('ristReportSend').disabled = false; sendProgress.stop(); }
  };
  try {
    const info = await (await request('/api/platform')).json();
    $('ristOperator').value = typeof info.operatorId === 'string' ? info.operatorId.trim() : '';
    changed();
  } catch (error) { status(error.message); }
}
