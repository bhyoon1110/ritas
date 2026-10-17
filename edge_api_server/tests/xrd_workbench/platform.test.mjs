import test from 'node:test';
import assert from 'node:assert/strict';
import {chunkBytes, apiUrl} from '../../app/xrd_workbench/web/shared/api.mjs';

test('unknown and low-resource clients use conservative sequential chunks', () => {
  assert.equal(chunkBytes({}), 512*1024);
  assert.equal(chunkBytes({deviceMemory:2,hardwareConcurrency:2}),512*1024);
  assert.equal(chunkBytes({deviceMemory:4,hardwareConcurrency:4}),2*1024*1024);
  assert.equal(chunkBytes({deviceMemory:8,hardwareConcurrency:8}),4*1024*1024);
  assert.equal(chunkBytes({deviceMemory:8,hardwareConcurrency:1}),2*1024*1024);
});
test('all API navigation stays in the authorized XRD namespace', () => {
  globalThis.XRD_API_BASE = '/api/v1/xrd/workbench';
  assert.equal(apiUrl('/report.html?project=id'), '/api/v1/xrd/workbench/report.html?project=id');
  delete globalThis.XRD_API_BASE;
});
