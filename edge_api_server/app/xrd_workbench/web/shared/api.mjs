// The single place where screens talk to the server. Set globalThis.XRD_API_BASE before the
// screen modules load to serve the API from another origin or path prefix.
export function apiUrl(path) {
  return `${globalThis.XRD_API_BASE ?? ''}${path}`;
}

export async function request(path, options = {}) {
  if (path === '/api/upload' && options.body instanceof FormData) return uploadFiles(options.body);
  const response = await fetch(apiUrl(path), options);
  if (response.ok) return response;
  let detail, eventId;
  try {
    const payload = await response.json();
    detail = payload.detail || payload.error?.message || payload.message;
    eventId = payload.errorEventId || payload.error?.errorEventId;
  } catch {}
  eventId = response.headers?.get('X-Error-Event-Id') || eventId;
  // Only show the public archive identifier, never raw exception details or a server path.
  eventId = typeof eventId === 'string' && /^[0-9A-Za-z_-]{8,80}$/.test(eventId) ? eventId : undefined;
  const message =
    typeof detail === 'string'
      ? detail
      : detail != null
        ? JSON.stringify(detail)
        : `요청 실패 (${response.status})`;
  const hint = eventId ? ` (오류번호: ${eventId} · 관리자 운영 관리의 오류 기록에서 확인)` : '';
  throw Object.assign(new Error(message + hint), { status: response.status, errorEventId: eventId });
}

export async function waitJob(job, onProgress = () => {}) {
  while (job.status === 'queued' || job.status === 'running') {
    onProgress(job.status === 'queued' ? '서버 처리 대기 중…' : '서버에서 자료를 처리하는 중…');
    await new Promise(resolve => setTimeout(resolve, 1200));
    job = await (await request(`/api/jobs/${job.job_id}`)).json();
  }
  if (job.status !== 'completed') throw new Error(job.error || '서버 처리를 완료하지 못했습니다.');
  return job;
}

export function chunkBytes(resources = globalThis.navigator || {}) {
  const memory = Number(resources.deviceMemory || 0), cores = Number(resources.hardwareConcurrency || 0);
  return memory >= 8 && cores >= 4 ? 4 * 1024 * 1024 : memory >= 4 ? 2 * 1024 * 1024 : 512 * 1024;
}

async function uploadFiles(form) {
  const files = form.getAll('files');
  if (!files.length || files.length > 100) throw new Error('파일은 1~100개 선택하세요.');
  if (files.some(f => !f.size || f.size > 80 * 1024 * 1024) || files.reduce((s, f) => s + f.size, 0) > 1200 * 1024 * 1024)
    throw new Error('파일당 80MB, 전체 1200MB 이하의 비어 있지 않은 파일을 선택하세요.');
  const session = await (await request('/api/upload-sessions', {method:'POST'})).json();
  const size = chunkBytes(), notice = message => {
    const element = document.getElementById('uploadError');
    if (element) element.textContent = message;
  };
  for (const [i, file] of files.entries()) {
    for (let offset = 0, index = 0; offset < file.size; offset += size, index++) {
      const chunk = new FormData();
      chunk.append('relative_path', file.name);
      chunk.append('offset', offset);
      chunk.append('total_size', file.size);
      chunk.append('chunk_index', index);
      chunk.append('chunk_count', Math.ceil(file.size / size));
      chunk.append('file', file.slice(offset, offset + size), file.name);
      notice(`파일 ${i + 1}/${files.length}: ${file.name} · ${Math.floor(offset / file.size * 100)}%`);
      for (let attempt = 0; ; attempt++) {
        try {
          await request(`/api/upload-sessions/${session.upload_id}/chunks`, {method:'POST', body:chunk});
          break;
        } catch (error) {
          if (attempt >= 2 || (error.status && error.status < 500 && error.status !== 429)) throw error;
          await new Promise(resolve => setTimeout(resolve, 1000 * (attempt + 1)));
        }
      }
    }
  }
  const job = await waitJob(await (await request(`/api/upload-sessions/${session.upload_id}/complete`, {
    method:'POST', headers:{'Content-Type':'application/json'}, body:form.get('mappings') || '{}'
  })).json(), notice);
  return request(`/api/projects/${job.storage_id}`);
}
