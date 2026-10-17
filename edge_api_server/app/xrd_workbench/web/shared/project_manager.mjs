import { apiUrl, request, waitJob } from './api.mjs';
import { escapeHTML as esc } from './dom.mjs';

export function projectRowsHTML(rows, archived, current) {
  return rows.map(p => {
    const id = esc(p.storage_id), readonly = p.storage_id === 'example';
    const date = new Date(archived ? p.archived_at : p.created_at);
    const when = Number.isNaN(date.getTime()) ? '' : date.toLocaleDateString('ko-KR');
    return `<div class="projectRow" data-project-id="${id}">
      <input type="checkbox" data-select aria-label="${esc(p.label)} 선택" ${readonly ? 'disabled' : ''}>
      <div class="projectRowInfo"><label for="project-name-${id}" class="projectNameLabel">${p.storage_id === current ? '현재 작업' : '프로젝트 이름'}</label>
      <input id="project-name-${id}" data-name value="${esc(p.label)}" title="${esc(p.label)}" maxlength="100" ${readonly || archived ? 'readonly' : ''}>
      <p>${p.storage_id === current ? '<strong>현재 작업</strong> · ' : ''}${archived ? '삭제' : '등록'} ${esc(when)} · 시료 ${Number(p.sample_count) || 0} · 파일 ${Number(p.file_count) || 0} · ${((Number(p.total_bytes) || 0) / 1048576).toFixed(1)} MB</p></div>
      <div class="projectRowActions">${archived ? '<button type="button" data-action="restore">복원</button><button type="button" data-action="purge" class="projectDanger">영구 삭제</button>' :
        `<button type="button" data-action="open">열기</button>${readonly ? '' : '<button type="button" data-action="rename">이름 저장</button><button type="button" data-action="archive" class="projectDanger">삭제</button>'}`}</div>
    </div>`;
  }).join('');
}

// Custom modal rather than <dialog>: Supermium 144 and the Chrome 49 fallback.
export function mountProjectManager({ button, getCurrent, refreshProjects,
  host = window, navigate = url => { location.href = url; } }) {
  const overlay = document.createElement('div');
  overlay.className = 'xrdProjectManager';
  overlay.hidden = true;
  overlay.innerHTML = `<section class="projectPanel" role="dialog" aria-modal="true" aria-labelledby="projectManagerTitle">
    <div class="projectHeading"><h2 id="projectManagerTitle">프로젝트 관리</h2><button type="button" data-close>닫기</button></div>
    <p class="projectHelp">본인 XRD 프로젝트만 표시합니다. 목록 이름을 바꿔도 시료명·보고서 제목은 유지됩니다.</p>
    <div class="projectFilters"><div><button type="button" data-tab="active" aria-pressed="true">사용 중</button><button type="button" data-tab="trash" aria-pressed="false">휴지통</button></div>
    <label>검색<input type="search" data-search maxlength="100" placeholder="전체 프로젝트 이름 검색" aria-label="프로젝트 이름 검색"></label><button type="button" data-refresh>새로고침</button></div>
    <div class="projectSort"><label>정렬 <select data-sort><option value="newest">최신순</option><option value="oldest">오래된순</option><option value="name">이름순</option><option value="size">원본 용량순</option></select></label>
    <label>페이지당 <select data-per-page><option>10</option><option selected>20</option><option>50</option></select>개</label></div>
    <div class="projectBulk"><label><input type="checkbox" data-select-all>현재 페이지 선택</label><span data-selected>0개 선택</span>
    <button type="button" data-bulk>선택 삭제</button><button type="button" data-purge-selected class="projectDanger" hidden>선택 영구 삭제</button><button type="button" data-empty class="projectDanger" hidden>휴지통 비우기</button></div>
    <p class="projectNotice" data-notice role="status" aria-live="polite"></p>
    <div class="projectRows" data-rows></div>
    <div class="projectPager"><button type="button" data-prev>이전</button><span data-page>1 / 1 페이지</span><button type="button" data-next>다음</button></div>
    <p class="projectHelp" data-help></p>
  </section>`;
  document.body.appendChild(overlay);
  const el = selector => overlay.querySelector(selector);
  let rows = [], archived = false, busy = false, loading = false, page = 1, pages = 1, totalInTab = 0;
  let version = 0, timer, returnFocus = null;
  const message = (text, error = false) => {
    el('[data-notice]').textContent = text;
    el('[data-notice]').classList.toggle('error', error);
  };
  const selection = () => rows.filter(p => {
    const box = el(`[data-project-id="${p.storage_id}"] [data-select]`);
    return box && box.checked;
  });
  function controls() {
    overlay.setAttribute('aria-busy', String(busy || loading));
    overlay.querySelectorAll('button,input,select').forEach(node => { node.disabled = busy || (loading && !node.matches('[data-search],[data-close],[data-tab],[data-sort],[data-per-page]')); });
    const eligible = rows.filter(p => p.storage_id !== 'example'), count = selection().length;
    const example = el('[data-project-id="example"] [data-select]');
    if (example) example.disabled = true;
    el('[data-selected]').textContent = `${count}개 선택`;
    el('[data-select-all]').checked = !!count && count === eligible.length;
    el('[data-select-all]').indeterminate = !!count && count < eligible.length;
    el('[data-select-all]').disabled = busy || loading || !eligible.length;
    el('[data-bulk]').disabled = el('[data-purge-selected]').disabled = busy || loading || !count;
    el('[data-empty]').disabled = busy || loading || !totalInTab;
    el('[data-prev]').disabled = busy || loading || page <= 1;
    el('[data-next]').disabled = busy || loading || page >= pages;
  }
  function setBusy(value) { busy = value; controls(); }
  async function load() {
    if (busy) return;
    clearTimeout(timer);
    const requestVersion = ++version;
    loading = true;
    rows = [];
    el('[data-rows]').innerHTML = '';
    controls();
    message('프로젝트 목록을 불러오는 중…');
    overlay.querySelectorAll('[data-tab]').forEach(b => b.setAttribute('aria-pressed', String((b.dataset.tab === 'trash') === archived)));
    el('[data-bulk]').textContent = archived ? '선택 복원' : '선택 삭제';
    el('[data-purge-selected]').hidden = el('[data-empty]').hidden = !archived;
    el('[data-help]').textContent = archived
      ? '영구 삭제하면 원본·검토·편집본을 복원할 수 없습니다. 이미 내보낸 보고서와 LIMS 전송 큐는 보존합니다.'
      : '삭제하면 휴지통으로 이동하며 복원할 수 있습니다. 체크한 항목은 현재 페이지에서만 선택됩니다.';
    try {
      const query = encodeURIComponent(el('[data-search]').value.trim());
      const data = await (await request(`/api/project-catalog?archived=${archived}&q=${query}&sort=${el('[data-sort]').value}&page=${page}&per_page=${el('[data-per-page]').value}`)).json();
      if (requestVersion !== version) return;
      rows = data.items; page = data.page; pages = data.pages; totalInTab = data.total_in_tab;
      el('[data-rows]').innerHTML = rows.length ? projectRowsHTML(rows, archived, getCurrent()) : '<p class="projectEmpty">해당 프로젝트가 없습니다.</p>';
      el('[data-page]').textContent = `${page} / ${pages} 페이지`;
      message(`${archived ? '휴지통' : '사용 중'} 전체 ${totalInTab}개 · 검색 결과 ${data.total}개 · ${rows.length}개 표시`);
    } catch (error) {
      if (requestVersion !== version) return;
      rows = []; totalInTab = 0; pages = 1;
      el('[data-rows]').innerHTML = '';
      message(error.message, true);
    } finally { if (requestVersion === version) { loading = false; controls(); } }
  }
  async function mayLeave() {
    if (host.xrdCanReset && !(await host.xrdCanReset())) return false;
    return !host.xrdBeforeLeave || await host.xrdBeforeLeave();
  }
  const itemsFor = projects => projects.map(p => ({storage_id:p.storage_id, expected_revision:p.managed_revision}));
  const post = async (url, body) => (await request(url, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(body)})).json();
  async function purge(projects, all = false) {
    const plan = await post('/api/project-purges/plan', all ? {all_trash:true} : {items:itemsFor(projects)});
    const answer = host.prompt(`${all ? '검색 조건과 관계없이 전체 휴지통' : '선택한 휴지통 프로젝트'} ${plan.count}개 (${(plan.total_bytes / 1048576).toFixed(1)} MB)를 영구 삭제합니다.\n원본·검토·편집본은 복원할 수 없습니다. 이미 내보낸 보고서와 LIMS 전송 큐는 보존합니다.\n계속하려면 “영구 삭제”를 입력하세요.`, '');
    if (answer !== '영구 삭제') { message('영구 삭제를 취소했습니다.'); return false; }
    try {
      const job = await post('/api/project-purges', {plan_id:plan.plan_id, expected_count:plan.count, confirmation:answer});
      await waitJob(job, message);
    } catch (error) {
      throw new Error(`${error.message} (삭제 확인 번호: ${plan.plan_id})`);
    }
    return true;
  }
  async function perform(kind, projects, row = null) {
    if (busy || loading) return;
    setBusy(true);
    try {
      if (kind === 'open') {
        if (await mayLeave()) navigate(apiUrl('/?project=' + encodeURIComponent(projects[0].storage_id)));
        return;
      }
      const currentSelected = projects.some(p => p.storage_id === getCurrent());
      if (kind === 'archive') {
        if (!host.confirm(`${projects.length}개 프로젝트를 휴지통으로 이동할까요?\n원본·편집 내용은 보존되며 복원할 수 있습니다. 기존 보고서와 전송 큐는 변경하지 않습니다.`)) return;
        if (currentSelected && !(await mayLeave())) return;
      }
      if (kind === 'purge' || kind === 'empty') {
        if (!(await purge(projects, kind === 'empty'))) return;
      } else if (kind === 'rename') {
        const label = row.querySelector('[data-name]').value.trim();
        if (!label || label.length > 100) throw new Error('프로젝트 이름은 1~100자로 입력하세요.');
        await request('/api/projects/' + encodeURIComponent(projects[0].storage_id), {method:'PATCH', headers:{'Content-Type':'application/json'}, body:JSON.stringify({...itemsFor(projects)[0], label})});
      } else {
        await post('/api/projects/manage-batch', {action:kind, items:itemsFor(projects)});
      }
      await refreshProjects();
      if (kind === 'archive' && currentSelected) { navigate(apiUrl('/?step=upload&fresh=1')); return; }
      setBusy(false);
      await load();
      message(kind === 'archive' ? '휴지통으로 이동했습니다. 복원할 수 있습니다.' : kind === 'restore' ? '프로젝트를 복원했습니다.' : kind === 'rename' ? '목록 이름을 저장했습니다. 시료명과 보고서 제목은 그대로입니다.' : '영구 삭제했습니다. 원본·검토·편집본은 복원할 수 없으며, 기존 내보내기와 전송 큐는 보존했습니다.');
    } catch (error) {
      if (host.xrdLeaveCanceled) host.xrdLeaveCanceled();
      message(error.message, true);
    } finally { setBusy(false); }
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
    clearTimeout(timer);
    overlay.hidden = true;
    (returnFocus || button).focus();
  }
  el('[data-close]').onclick = close;
  el('[data-refresh]').onclick = load;
  el('[data-search]').oninput = () => {
    page = 1; clearTimeout(timer);
    // Invalidate an old query immediately, not after the debounce expires.
    version++; loading = true; controls();
    timer = setTimeout(load, 250);
  };
  ['[data-sort]', '[data-per-page]'].forEach(s => { el(s).onchange = () => { page = 1; load(); }; });
  el('[data-prev]').onclick = () => { page--; load(); };
  el('[data-next]').onclick = () => { page++; load(); };
  el('[data-select-all]').onchange = () => {
    overlay.querySelectorAll('[data-select]:not(:disabled)').forEach(box => { box.checked = el('[data-select-all]').checked; });
    controls();
  };
  el('[data-rows]').onchange = controls;
  el('[data-bulk]').onclick = () => perform(archived ? 'restore' : 'archive', selection());
  el('[data-purge-selected]').onclick = () => perform('purge', selection());
  el('[data-empty]').onclick = () => perform('empty', []);
  overlay.querySelectorAll('[data-tab]').forEach(b => { b.onclick = () => {
    if (busy || archived === (b.dataset.tab === 'trash')) return;
    archived = b.dataset.tab === 'trash'; page = 1; load();
  }; });
  overlay.addEventListener('keydown', event => {
    event.stopPropagation();
    if (event.key === 'Escape') { event.preventDefault(); close(); }
    if (event.key !== 'Tab') return;
    const nodes = Array.from(overlay.querySelectorAll('button:not(:disabled),input:not(:disabled),select:not(:disabled)')).filter(n => !n.hidden);
    if (!nodes.length) { event.preventDefault(); return; }
    if (event.shiftKey && document.activeElement === nodes[0]) { event.preventDefault(); nodes[nodes.length - 1].focus(); }
    else if (!event.shiftKey && document.activeElement === nodes[nodes.length - 1]) { event.preventDefault(); nodes[0].focus(); }
  });
  el('[data-rows]').onclick = event => {
    const action = event.target.closest('[data-action]');
    if (!action) return;
    const row = action.closest('[data-project-id]'), project = rows.find(p => p.storage_id === row.dataset.projectId);
    if (project) perform(action.dataset.action, [project], row);
  };
  return overlay;
}
