import test from 'node:test';
import assert from 'node:assert/strict';
import { apiUrl, request } from '../../app/xrd_workbench/web/shared/api.mjs';
import { escapeHTML } from '../../app/xrd_workbench/web/shared/dom.mjs';

function respond(status, body) {
  globalThis.fetch = async (url, options) => {
    respond.last = { url, options };
    return new Response(typeof body === 'string' ? body : JSON.stringify(body), { status });
  };
}

test('api base is configurable and defaults to same origin', () => {
  delete globalThis.XRD_API_BASE;
  assert.equal(apiUrl('/api/projects'), '/api/projects');
  globalThis.XRD_API_BASE = 'https://intranet.example/xrd';
  assert.equal(apiUrl('/api/projects'), 'https://intranet.example/xrd/api/projects');
  delete globalThis.XRD_API_BASE;
});

test('successful requests return the response and pass options through', async () => {
  respond(200, { ok: true });
  const response = await request('/api/reports/x', { method: 'PUT' });
  assert.deepEqual(await response.json(), { ok: true });
  assert.equal(respond.last.url, '/api/reports/x');
  assert.equal(respond.last.options.method, 'PUT');
});

test('error messages: string detail, structured detail, and non-JSON bodies', async () => {
  respond(409, { detail: '다른 저장 버전이 있습니다.' });
  await assert.rejects(request('/a'), { message: '다른 저장 버전이 있습니다.', status: 409 });
  respond(422, { detail: [{ loc: ['body'], msg: 'bad' }] });
  await assert.rejects(request('/a'), { message: '[{"loc":["body"],"msg":"bad"}]' });
  respond(500, 'Internal Server Error');
  await assert.rejects(request('/a'), { message: '요청 실패 (500)' });
});

test('escapeHTML escapes markup and quotes and treats null as empty', () => {
  assert.equal(escapeHTML('<a href="x">\'&'), '&lt;a href=&quot;x&quot;&gt;&#39;&amp;');
  assert.equal(escapeHTML(null), '');
});

test('server archive number is shown without leaking traceback details', async () => {
  const id='20261017-120000-abcdef1234';
  respond(500,{message:'서버 내부 오류가 발생했습니다.',errorEventId:id,details:{traceback:'private server path'}});
  await assert.rejects(request('/a'), error => {
    assert.equal(error.errorEventId,id);
    assert.equal(error.status,500);
    assert.match(error.message,new RegExp(id));
    assert.match(error.message,/오류 기록/);
    assert.doesNotMatch(error.message,/private server path/);
    return true;
  });
  globalThis.fetch=async()=>new Response('Internal Server Error',{status:500,headers:{'X-Error-Event-Id':id}});
  await assert.rejects(request('/a'),error=>error.errorEventId===id);
  respond(500,{message:'server error',errorEventId:'<script>private</script>'});
  await assert.rejects(request('/a'),{message:'server error',errorEventId:undefined});
});
