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


// The optional ALL marker must not participate in required-field checks.
test('report transfer identifies only missing fields instead of reporting an SSO failure', async () => {
  const {missingTransferFields} = await import('../../app/xrd_workbench/web/shared/platform.mjs');
  const complete = {requestNumber:'2022T00793', experimentCode:'A11112', equipmentCode:'DEVICE-01', operatorId:'0a0516'};
  assert.deepEqual(missingTransferFields(complete), []);
  assert.deepEqual(missingTransferFields({...complete, requestScope:'all'}), []);
  assert.deepEqual(missingTransferFields({...complete, equipmentCode:'  '}).map(f => [f.key, f.id, f.label]),
    [['equipmentCode', 'ristEquipment', '실험장비 코드']]);
  assert.deepEqual(missingTransferFields({...complete, operatorId:undefined}).map(f => f.key), ['operatorId']);
  assert.deepEqual(missingTransferFields({...complete, requestNumber:'', experimentCode:''}).map(f => f.key),
    ['requestNumber','experimentCode']);
});
