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
  block.innerHTML = '<h2>보고서 전송정보</h2><p>의뢰번호는 표지에서 직접 입력하거나 아래 조회로 선택할 수 있습니다. 전송은 사내 SSO 인증 후 가능합니다.</p>' +
    '<button type="button" id="ristRequestLookup">의뢰 조회</button><select id="ristRequests" aria-label="의뢰 선택"><option value="">의뢰를 선택하세요</option></select>' +
    '<div class="formGrid"><label>LIMS 시험방법코드<input id="ristExperiment" maxlength="100"></label><label>실험장비 코드<input id="ristEquipment" maxlength="100"></label><label>실험자(SSO)<input id="ristOperator" maxlength="100" readonly></label></div>' +
    '<a href="/account">회원정보에서 사내 SSO 인증</a> <button type="button" id="ristReportSend">보고서 전송 큐에 등록</button><p id="ristTransferStatus" role="status"></p>';
  document.getElementById('savePane').prepend(block);
  const $ = id => document.getElementById(id), status = text => { $('ristTransferStatus').textContent = text; };
  const progress=window.RistFileProgress.monitor($('ristTransferStatus'));
  for (const id of ['ristExperiment', 'ristEquipment']) $(id).addEventListener('input', changed);
  const requestSelect = $('ristRequests'), lookupButton = $('ristRequestLookup');
  let items = [], selectedItem = null;
  const filters = window.RistRequestFilters?.mount(requestSelect, {experimentType:'XRD', reload:loadRequests});

  function methodCode(item) { return (item.testMethodCode || item.experimentCode || '').trim(); }
  function clearOptions() {
    items = [];
    requestSelect.textContent = '';
    requestSelect.add(new Option('의뢰를 선택하세요', ''));
  }
  async function loadRequests() {
    if (lookupButton.disabled) return;
    lookupButton.disabled = true;
    requestSelect.disabled = true;
    filters?.setBusy(true);
    progress.start('의뢰 목록 조회 중…');
    clearOptions();
    try {
      if (!filters) throw new Error('의뢰 검색 조건을 불러오지 못했습니다. 화면을 새로 고쳐 주세요.');
      const response = await fetch(filters.url(), {headers:{'X-Request-Id':'xrd-workbench-' + Date.now()}});
      const body = await response.json();
      if (!response.ok) throw new Error(body.error?.message || body.detail || '의뢰 조회에 실패했습니다.');
      items = body.items || [];
      filters.showResult(body);
      items.forEach((item, index) => {
        const label = [item.requestNumber, item.sampleName || item.requestTitle, methodCode(item) || '시험방법코드 없음',
          item.testMethodName, window.RistRequestFilters.testStateName(item.testState)].filter(Boolean).join(' · ');
        requestSelect.add(new Option(label, String(index)));
      });
      status(`${items.length}개 시험을 불러왔습니다.`);
    } catch (error) { status(error.message); }
    finally {
      lookupButton.disabled = false;
      requestSelect.disabled = false;
      filters?.setBusy(false);
      progress.stop();
    }
  }
  lookupButton.onclick = loadRequests;
  requestSelect.onchange = () => {
    if (requestSelect.value === '') return;
    const item = items[Number(requestSelect.value)];
    selectedItem = item;
    $('reportNumber').value = item.requestNumber || '';
    $('ristExperiment').value = methodCode(item);
    const equipment = item.equipmentCode || item.deviceCode || item.instrumentCode;
    if (equipment) $('ristEquipment').value = equipment;
    $('reportNumber').dispatchEvent(new Event('input', {bubbles:true}));
    changed();
    status(methodCode(item) ? '표지 의뢰번호와 시험방법코드를 반영했습니다. 사용한 장비코드를 확인하세요.'
      : '선택한 시험에 시험방법코드가 없습니다. 전송 전 LIMS 시험방법코드를 확인하세요.');
  };
  $('reportNumber').addEventListener('input', () => {
    if (!selectedItem || $('reportNumber').value.trim() === selectedItem.requestNumber) return;
    if ($('ristExperiment').value === methodCode(selectedItem)) $('ristExperiment').value = '';
    selectedItem = null;
    requestSelect.value = '';
    changed();
  });
  $('ristReportSend').onclick = async () => {
    $('ristReportSend').disabled = true;
    progress.start('SSO·전송 권한 확인 중…');
    try {
      const info = await (await request('/api/platform')).json();
      $('ristOperator').value = info.operatorId;
      changed();
      if (Object.values(transferValues()).some(v => !v)) throw new Error('의뢰번호·LIMS 시험방법코드·장비코드 및 SSO 인증을 확인하세요.');
      await send();
    } catch (error) { status(error.message); }
    finally { $('ristReportSend').disabled = false; progress.stop(); }
  };
  try {
    const info = await (await request('/api/platform')).json();
    $('ristOperator').value = info.operatorId;
    changed();
  } catch (error) { status(error.message); }
}
