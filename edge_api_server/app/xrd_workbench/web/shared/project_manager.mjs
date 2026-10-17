import { apiUrl, request } from './api.mjs';
import { escapeHTML as esc } from './dom.mjs';

export function projectRowsHTML(rows, archived, current) {
  return rows.map(p => {
    const id = esc(p.storage_id), readonly = p.storage_id === 'example';
    const date = new Date(archived ? p.archived_at : p.created_at);
    const when = Number.isNaN(date.getTime()) ? '' : date.toLocaleString('ko-KR');
    return `<div class="projectRow" data-project-id="${id}">
      <div class="projectRowInfo"><label for="project-name-${id}">프로젝트 이름${p.storage_id === current ? ' · 현재 작업' : ''}</label>
      <input id="project-name-${id}" data-name value="${esc(p.label)}" title="${esc(p.label)}" maxlength="100" ${readonly || archived ? 'readonly' : ''}>
      <p>${archived ? '삭제' : '등록'} ${esc(when)} · 시료 ${Number(p.sample_count) || 0}개 · 파일 ${Number(p.file_count) || 0}개 · ${((Number(p.total_bytes) || 0) / 1048576).toFixed(1)} MB</p></div>
      <div class="projectRowActions">${archived ? '<button type="button" data-action="restore">복원</button>' :
        `<button type="button" data-action="open">열기</button>${readonly ? '' : '<button type="button" data-action="rename">이름 저장</button><button type="button" data-action="archive" class="projectDanger">삭제</button>'}`}</div>
    </div>`;
  }).join('');
}

// Custom modal rather than <dialog>: native Supermium 144 and the existing Chrome 49 fallback.
export function mountProjectManager({ button, getCurrent, refreshProjects,
  host = window, navigate = url => { location.href = url; } }) {
  const overlay = document.createElement('div');
  overlay.className = 'xrdProjectManager';
  overlay.hidden = true;
  overlay.innerHTML = `<section class="projectPanel" role="dialog" aria-modal="true" aria-labelledby="projectManagerTitle">
    <div class="projectHeading"><h2 id="projectManagerTitle">프로젝트 관리</h2><button type="button" data-close>닫기</button></div>
    <p class="projectHelp">본인이 등록한 XRD 프로젝트를 관리합니다. 이름 변경은 목록 이름만 바꾸며 시료명·보고서 제목은 유지됩니다. 삭제한 프로젝트는 휴지통에서 복원할 수 있습니다.</p>
    <div class="projectFilters"><div><button type="button" data-tab="active" aria-pressed="true">사용 중</button><button type="button" data-tab="trash" aria-pressed="false">휴지통</button></div>
    <label>검색<input type="search" data-search placeholder="프로젝트 이름 검색" aria-label="프로젝트 이름 검색"></label><button type="button" data-refresh>새로고침</button></div>
    <p class="projectNotice" data-notice role="status" aria-live="polite"></p>
    <div class="projectRows" data-rows></div><button type="button" data-more hidden>50개 더 보기</button>
    <p class="projectHelp">휴지통은 원본·검토·초안을 보존합니다. 디스크 공간은 줄어들지 않으며, 이미 생성·전송된 보고서와 전송 큐는 변경하지 않습니다.</p>
  </section>`;
  document.body.appendChild(overlay);
  const el = selector => overlay.querySelector(selector);
  let rows = [], archived = false, busy = false, limit = 50, returnFocus = null;
  const message = (text, error = false) => {
    el('[data-notice]').textContent = text;
    el('[data-notice]').classList.toggle('error', error);
  };
  function setBusy(value) {
    busy = value;
    overlay.setAttribute('aria-busy', String(value));
    overlay.querySelectorAll('button,input').forEach(node => { node.disabled = value; });
  }
  function render() {
    const query = el('[data-search]').value.trim().toLocaleLowerCase();
    const visible = rows.filter(p => String(p.label).toLocaleLowerCase().includes(query));
    el('[data-rows]').innerHTML = visible.length ? projectRowsHTML(visible.slice(0, limit), archived, getCurrent()) : '<p class="projectEmpty">해당 프로젝트가 없습니다.</p>';
    el('[data-more]').hidden = visible.length <= limit;
    overlay.querySelectorAll('[data-tab]').forEach(b => b.setAttribute('aria-pressed', String((b.dataset.tab === 'trash') === archived)));
    message(`${archived ? '휴지통' : '사용 중'} ${visible.length}개${visible.length > limit ? ` · ${limit}개 표시` : ''}`);
  }
  async function load() {
    if (busy) return;
    setBusy(true);
    message('프로젝트 목록을 불러오는 중…');
    try {
      rows = await (await request('/api/projects' + (archived ? '?archived=true' : ''))).json();
      limit = 50;
      render();
    } catch (error) {
      // A failed refresh must not leave actions attached to a different tab/revision.
      rows = [];
      el('[data-rows]').innerHTML = '';
      el('[data-more]').hidden = true;
      message(error.message, true);
    } finally { setBusy(false); }
  }
  async function mayLeave() {
    if (host.xrdCanReset && !(await host.xrdCanReset())) return false;
    return !host.xrdBeforeLeave || await host.xrdBeforeLeave();
  }
  button.onclick = async () => {
    if (!overlay.hidden) return;
    returnFocus = document.activeElement;
    overlay.hidden = false;
    await load();
    el('[data-search]').focus();
  };
  function close() {
    if (busy) return;
    overlay.hidden = true;
    (returnFocus || button).focus();
  }
  el('[data-close]').onclick = close;
  el('[data-refresh]').onclick = load;
  el('[data-search]').oninput = () => { limit = 50; render(); };
  el('[data-more]').onclick = () => { limit += 50; render(); };
  overlay.querySelectorAll('[data-tab]').forEach(b => { b.onclick = async () => {
    if (busy || archived === (b.dataset.tab === 'trash')) return;
    archived = b.dataset.tab === 'trash';
    overlay.querySelectorAll('[data-tab]').forEach(tab => tab.setAttribute('aria-pressed', String(tab === b)));
    await load();
  }; });
  overlay.addEventListener('keydown', event => {
    event.stopPropagation();
    if (event.key === 'Escape') { event.preventDefault(); close(); }
    if (event.key !== 'Tab') return;
    const nodes = Array.from(overlay.querySelectorAll('button:not(:disabled),input:not(:disabled)')).filter(n => !n.hidden);
    if (!nodes.length) { event.preventDefault(); return; }
    if (event.shiftKey && document.activeElement === nodes[0]) { event.preventDefault(); nodes[nodes.length - 1].focus(); }
    else if (!event.shiftKey && document.activeElement === nodes[nodes.length - 1]) { event.preventDefault(); nodes[0].focus(); }
  });
  el('[data-rows]').onclick = async event => {
    const action = event.target.closest('[data-action]');
    if (busy || !action) return;
    const row = action.closest('[data-project-id]');
    const project = rows.find(p => p.storage_id === row.dataset.projectId);
    if (!project) return;
    const id = project.storage_id, kind = action.dataset.action;
    setBusy(true);
    try {
      if (kind === 'open') {
        if (await mayLeave()) navigate(apiUrl('/?project=' + encodeURIComponent(id)));
        return;
      }
      if (kind === 'archive') {
        if (!host.confirm(`“${project.label}” 프로젝트를 휴지통으로 이동할까요?\n원본·편집 내용은 보존되며 복원할 수 있습니다. 기존 보고서와 전송 큐는 변경하지 않습니다.`)) return;
        if (id === getCurrent() && !(await mayLeave())) return;
      }
      const payload = { expected_revision: project.managed_revision };
      if (kind === 'rename') {
        payload.label = row.querySelector('[data-name]').value.trim();
        if (!payload.label || payload.label.length > 100) throw new Error('프로젝트 이름은 1~100자로 입력하세요.');
      }
      const suffix = kind === 'restore' ? '/restore' : '';
      await request('/api/projects/' + encodeURIComponent(id) + suffix, {
        method: kind === 'archive' ? 'DELETE' : kind === 'restore' ? 'POST' : 'PATCH',
        headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload),
      });
      await refreshProjects();
      if (kind === 'archive' && id === getCurrent()) {
        navigate(apiUrl('/?step=upload&fresh=1'));
        return;
      }
      setBusy(false);
      await load();
      message(kind === 'archive' ? '휴지통으로 이동했습니다. 복원할 수 있습니다.' : kind === 'restore' ? '프로젝트를 복원했습니다. 사용 중 목록에서 열 수 있습니다.' : '목록 이름을 저장했습니다. 시료명과 보고서 제목은 그대로입니다.');
    } catch (error) {
      if (host.xrdLeaveCanceled) host.xrdLeaveCanceled();
      message(error.message, true);
    }
    finally { setBusy(false); }
  };
  return overlay;
}
