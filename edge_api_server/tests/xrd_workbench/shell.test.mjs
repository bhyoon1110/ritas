import test from 'node:test';
import assert from 'node:assert/strict';
import { STEPS, projectOptionsHTML, stepHref, stepsHTML } from '../../app/xrd_workbench/web/shared/shell.mjs';

test('the five steps are in order and point at their pages with the project', () => {
  assert.deepEqual(
    STEPS.map((s) => s.id),
    ['upload', 'viewer', 'analysis', 'report', 'save'],
  );
  assert.equal(stepHref('upload', ''), '/?step=upload');
  assert.equal(stepHref('upload', 'abc'), '/?project=abc&step=upload');
  assert.equal(stepHref('viewer', 'abc'), '/?project=abc');
  assert.equal(stepHref('analysis', 'abc'), '/analysis.html?project=abc');
  assert.equal(stepHref('report', 'abc'), '/report.html?project=abc');
  assert.equal(stepHref('save', 'abc'), '/report.html?project=abc&step=save');
  assert.equal(stepHref('report', 'a b'), '/report.html?project=a%20b');
  assert.equal(stepHref('viewer', ''), '/');
});

test('the current step is marked and the others are links', () => {
  const html = stepsHTML('analysis', 'p1', {});
  assert.ok(html.includes('class="shellStep current"') && html.includes('aria-current="step"'));
  assert.equal((html.match(/aria-current/g) || []).length, 1);
  assert.ok(html.includes('href="/report.html?project=p1"'));
  assert.ok(html.includes('② 분석 자료 검토') || html.includes('분석 자료 검토'));
});

test('without a project only data upload is usable', () => {
  const html = stepsHTML('upload', '', {});
  assert.equal((html.match(/aria-disabled="true"/g) || []).length, 4);
  assert.ok(html.includes('데이터 업로드') && html.indexOf('데이터 업로드') < html.indexOf('뷰어'));
});

test('unsaved changes and open checklist items show on their steps', () => {
  const html = stepsHTML('report', 'p1', { dirty: true, issues: 3, blocked: true });
  assert.ok(html.includes('class="shellDirty"'));
  assert.ok(html.includes('class="shellBadge blocked">3<'));
  assert.ok(!stepsHTML('report', 'p1', { issues: 0 }).includes('shellBadge'));
  assert.ok(stepsHTML('report', 'p1', { issues: 2 }).includes('class="shellBadge">2<'));
});

test('the project list is escaped, marks the current one and names the example', () => {
  const html = projectOptionsHTML(
    [
      { storage_id: 'example', label: '<예제>' },
      { storage_id: 'abc', label: 'A, B' },
    ],
    'abc',
  );
  assert.ok(html.includes('&lt;예제&gt;') && !html.includes('<예제>'));
  assert.ok(html.includes('<option value="abc" selected>'));
  assert.ok(projectOptionsHTML([], '').includes('등록된 프로젝트 없음'));
  assert.ok(projectOptionsHTML([{ storage_id: 'x', label: 'X' }], '').includes('프로젝트 선택'));
});

test('a project that is not in the list is not shown as if another one were selected', () => {
  const html = projectOptionsHTML([{ storage_id: 'a', label: 'A' }], 'gone');
  assert.ok(html.startsWith('<option value="gone" selected>현재 프로젝트 (목록 갱신 중)</option>'));
  assert.ok(!html.includes('<option value="a" selected>'));
  assert.ok(projectOptionsHTML([], 'new').includes('value="new" selected'));
  assert.ok(projectOptionsHTML([], '"><script>').includes('&quot;&gt;&lt;script&gt;'));
});
