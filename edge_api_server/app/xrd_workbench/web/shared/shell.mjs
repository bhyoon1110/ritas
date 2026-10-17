// The app shell: one compact top workflow shared by every screen (project, file registration, the four
// steps in order, their status and settings). A page opts in with <body data-step="...">; pages
// talk to it with events so the page modules do not depend on it:
//   window.dispatchEvent(new CustomEvent('xrd-shell', { detail: { project, dirty, issues, blocked } }))
//   window.xrdBeforeLeave = async () => boolean   // false cancels leaving (e.g. unsaved changes)
//   window.xrdLocalStep = (step) => boolean       // true when the page shows that step itself
//   window 'xrd-settings' event                   // the page opens its settings dialog
import { apiUrl, request } from './api.mjs';
import { $, escapeHTML } from './dom.mjs';

export const STEPS = [
  { id: 'upload', number: '①', label: '데이터 업로드', page: '/', extra: 'step=upload' },
  { id: 'viewer', number: '②', label: '뷰어', page: '/' },
  { id: 'analysis', number: '③', label: '분석 자료 검토', page: '/analysis.html' },
  { id: 'report', number: '④', label: '보고서 편집', page: '/report.html' },
  { id: 'save', number: '⑤', label: '보고서 저장', page: '/report.html', extra: 'step=save' },
];

export function stepHref(step, projectId) {
  const s = STEPS.find((x) => x.id === step);
  if (!projectId) return s.extra ? `${s.page}?${s.extra}` : s.page;
  return `${s.page}?project=${encodeURIComponent(projectId)}${s.extra ? '&' + s.extra : ''}`;
}

export function stepsHTML(current, projectId, status) {
  return STEPS.map((s) => {
    const usable = s.id === 'upload' || !!projectId;
    const here = s.id === current;
    const dirty = s.id === 'report' && status.dirty ? '<span class="shellDirty">•</span>' : '';
    const badge =
      s.id === 'save' && status.issues
        ? `<span class="shellBadge${status.blocked ? ' blocked' : ''}">${status.issues}</span>`
        : '';
    const attrs = here
      ? 'class="shellStep current" aria-current="step"'
      : usable
        ? 'class="shellStep"'
        : 'class="shellStep disabled" aria-disabled="true"';
    const href = usable ? ` href="${escapeHTML(apiUrl(stepHref(s.id, projectId)))}"` : '';
    return `<a ${attrs}${href} data-step="${s.id}"><span class="shellNumber">${s.number}</span><span>${s.label}</span>${dirty}${badge}</a>`;
  }).join('');
}

export function projectOptionsHTML(list, currentId) {
  if (!list.length) return '<option value="">등록된 프로젝트 없음</option>';
  const known = list.some((p) => p.storage_id === currentId);
  const head = known ? '' : '<option value="">프로젝트 선택…</option>';
  return (
    head +
    list
      .map(
        (p) =>
          `<option value="${escapeHTML(p.storage_id)}" ${p.storage_id === currentId ? 'selected' : ''}>${escapeHTML(p.label)}</option>`,
      )
      .join('')
  );
}

// ---- DOM (browser only)
const state = { step: '', project: '', list: [], status: {} };

function render() {
  $('shellSteps').innerHTML = stepsHTML(state.step, state.project, state.status);
  $('shellProject').innerHTML = projectOptionsHTML(state.list, state.project);
  $('shellState').textContent = state.status.dirty ? '미저장 변경 있음' : state.status.blocked ? '출력 전 검토 필요' : '원자료 보존 · 단계별 작업';
}

async function go(step) {
  if (window.xrdLocalStep?.(step)) {
    state.step = step;
    render();
    return;
  }
  if (window.xrdBeforeLeave && !(await window.xrdBeforeLeave())) return;
  location.href = apiUrl(stepHref(step, state.project));
}

async function loadIssues() {
  if (!state.project) return;
  try {
    const value = await (await request(`/api/reports/${encodeURIComponent(state.project)}`)).json();
    const revision = value.document.revision;
    if (!revision) return;
    const summary = (
      await (
        await request(
          `/api/reports/${encodeURIComponent(state.project)}/checklist?revision=${revision}`,
        )
      ).json()
    ).summary;
    state.status = {
      ...state.status,
      issues: summary.blockers + summary.warnings,
      blocked: summary.blockers > 0,
    };
    render();
  } catch {
    // The badge is a hint only; the save step shows the full checklist.
  }
}

export async function mountShell() {
  const body = document.body;
  state.step = body.dataset.step;
  // A page can show more than one step (the viewer page also uploads; the report page also saves).
  const asked = new URLSearchParams(location.search).get('step');
  const page = STEPS.find((s) => s.id === state.step)?.page;
  if (STEPS.some((s) => s.id === asked && s.page === page)) state.step = asked;
  state.project = new URLSearchParams(location.search).get('project') || '';
  const main = document.createElement('div');
  main.className = 'shellMain';
  while (body.firstChild) main.appendChild(body.firstChild);
  const nav = document.createElement('nav');
  nav.id = 'appShell';
  nav.setAttribute('aria-label', '작업 단계');
  nav.innerHTML =
    '<div class="shellTop"><div class="shellBrand">XRD 분석·보고서</div>' +
    '<label class="shellLabel">프로젝트<select id="shellProject" aria-label="프로젝트"></select></label>' +
    '<span id="shellState" class="shellState"></span><button id="shellSettings" class="shellSettings">설정</button>' +
    '<button id="shellAccountToggle" aria-controls="shellAccount" aria-expanded="false">회원·지원</button></div>' +
    '<div id="shellSteps" class="shellSteps"></div>';
  body.append(nav, main);
  const account = document.createElement('div');
  account.className = 'ristAccount';
  account.id = 'shellAccount';
  account.hidden = true;
  account.innerHTML = '<a href="/">실험 선택</a><a href="/account">회원정보·SSO 인증</a><a href="/voc?project=XRD">VOC 게시판</a><button type="button" id="ristLogout">로그아웃</button>';
  nav.appendChild(account);
  $('shellAccountToggle').onclick = () => {
    account.hidden = !account.hidden;
    $('shellAccountToggle').setAttribute('aria-expanded', String(!account.hidden));
    window.dispatchEvent(new Event('resize'));
  };
  const footer = main.querySelector('footer, .statusbar'), next = $('nextStep');
  if (footer && next) footer.appendChild(next);
  $('ristLogout').onclick = async () => {
    try {
      const response = await fetch('/api/v1/auth/logout', {method:'POST', credentials:'same-origin', headers:{'X-Requested-With':'RIST-XRD'}});
      if (!response.ok) throw new Error('로그아웃하지 못했습니다.');
      location.href = '/login';
    } catch (error) { window.alert(error.message); }
  };
  body.classList.add('withShell');
  render();
  $('shellSteps').onclick = (event) => {
    const link = event.target.closest('[data-step]');
    if (!link) return;
    event.preventDefault();
    if (link.getAttribute('aria-disabled') === 'true' || link.dataset.step === state.step) return;
    go(link.dataset.step);
  };
  $('shellProject').onchange = async () => {
    const next = $('shellProject').value;
    if (!next || next === state.project) return;
    if (window.xrdBeforeLeave && !(await window.xrdBeforeLeave())) {
      $('shellProject').value = state.project;
      return;
    }
    location.href = apiUrl(stepHref(state.step, next));
  };
  $('shellSettings').onclick = () => {
    if (state.step === 'report' || state.step === 'save')
      window.dispatchEvent(new Event('xrd-settings'));
    else if (state.project)
      location.href = apiUrl(stepHref('report', state.project) + '&settings=1');
    else window.alert('설정은 프로젝트를 연 뒤 보고서 편집에서 바꿀 수 있습니다.');
  };
  window.addEventListener('xrd-shell', (event) => {
    const d = event.detail || {};
    if (d.project !== undefined && d.project !== state.project) {
      state.project = d.project;
      history.replaceState(null, '', apiUrl(stepHref(d.step || state.step, d.project)));
      loadIssues();
    }
    if (d.step) state.step = d.step;
    for (const key of ['dirty', 'issues', 'blocked'])
      if (d[key] !== undefined) state.status[key] = d[key];
    render();
  });
  try {
    state.list = await (await request('/api/projects')).json();
  } catch {
    state.list = [];
  }
  render();
  loadIssues();
}

if (typeof document !== 'undefined' && document.body?.dataset.step) mountShell();
