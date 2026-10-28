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
    '<div class="formGrid"><label>실험코드<input id="ristExperiment" value="XRD" maxlength="100"></label><label>실험장비 코드<input id="ristEquipment" maxlength="100"></label><label>실험자(SSO)<input id="ristOperator" maxlength="100" readonly></label></div>' +
    '<a href="/account">회원정보에서 사내 SSO 인증</a> <button type="button" id="ristReportSend">보고서 전송 큐에 등록</button><p id="ristTransferStatus" role="status"></p>';
  document.getElementById('savePane').prepend(block);
  const $ = id => document.getElementById(id), status = text => { $('ristTransferStatus').textContent = text; };
  const progress=window.RistFileProgress.monitor($('ristTransferStatus'));
  for (const id of ['ristExperiment', 'ristEquipment']) $(id).addEventListener('input', changed);
  let items = [];
  $('ristRequestLookup').onclick = async () => {
    $('ristRequestLookup').disabled = true;
    progress.start('의뢰 목록 조회 중…');
    try {
      const response = await fetch('/api/v1/requests?page=1&pageSize=200&experimentType=XRD',
        {headers:{'X-Request-Id':'xrd-workbench-' + Date.now()}});
      const body = await response.json();
      if (!response.ok) throw new Error(body.error?.message || body.detail || '의뢰 조회에 실패했습니다.');
      items = body.items || [];
      $('ristRequests').textContent = '';
      $('ristRequests').appendChild(new Option('의뢰를 선택하세요', ''));
      items.forEach((item, i) => $('ristRequests').appendChild(new Option(`${item.requestNumber} · ${item.sampleName || item.requestTitle || ''}`, String(i))));
      status(`${items.length}개 의뢰를 불러왔습니다.`);
    } catch (error) { status(error.message); }
    finally { $('ristRequestLookup').disabled = false; progress.stop(); }
  };
  $('ristRequests').onchange = () => {
    if ($('ristRequests').value === '') return;
    const item = items[Number($('ristRequests').value)];
    $('reportNumber').value = item.requestNumber || '';
    $('reportNumber').dispatchEvent(new Event('input', {bubbles:true}));
    $('ristExperiment').value = item.experimentCode || item.testMethodCode || 'XRD';
    $('ristEquipment').value = item.equipmentCode || item.deviceCode || item.instrumentCode || '';
    changed();
    status('표지 의뢰번호에 반영했습니다. 실험코드·장비코드를 확인하세요.');
  };
  $('ristReportSend').onclick = async () => {
    $('ristReportSend').disabled = true;
    progress.start('SSO·전송 권한 확인 중…');
    try {
      const info = await (await request('/api/platform')).json();
      $('ristOperator').value = info.operatorId;
      changed();
      if (Object.values(transferValues()).some(v => !v)) throw new Error('의뢰번호·실험코드·장비코드 및 SSO 인증을 확인하세요.');
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
