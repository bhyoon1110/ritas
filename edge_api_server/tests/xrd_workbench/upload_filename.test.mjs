import test from 'node:test';
import assert from 'node:assert/strict';
import {classifyUploadFilename, summarizeUpload} from '../../app/xrd_workbench/web/viewer/upload_summary.mjs';

test('both quantity PDF naming conventions connect to WPPF without manual mapping', () => {
  for (const name of ['1_정량_Report.pdf', '1_정량(WPPF)_Report.pdf', '1_정량_report.PDF', '1_정량_Report.pdf'.normalize('NFD')]) {
    assert.deepEqual(classifyUploadFilename(name), {sample:'1', role:'wppf_report'});
  }
});

test('extension checks and existing phase/image/RAW naming stay consistent', () => {
  for (const [name, role] of [['1.txt','measurement'], ['1_상매칭_Report.pdf','phase_report'], ['1_상매칭.png','phase_image'], ['1_정량.png','quantity_image'], ['1_정량_Report.png','ignore'], ['1_상매칭_Report.txt','measurement']]) {
    assert.equal(classifyUploadFilename(name).role, role);
  }
  assert.deepEqual(classifyUploadFilename('C:\\data\\1_정량_Report.pdf'),{sample:'1',role:'wppf_report'});
});

test('short-name quantity PDFs appear in the upload summary and duplicate aliases require a choice', () => {
  const files=['1.txt','1_상매칭_Report.pdf','1_정량_Report.pdf'].map(name=>({name}));
  const mappings=Object.fromEntries(files.map(f=>[f.name,classifyUploadFilename(f.name)]));
  const result=summarizeUpload(files,mappings);
  assert.equal(result.ignored,0);
  assert.deepEqual(result.errors,[]);
  assert.deepEqual(result.groups[0].roles.wppf_report,['1_정량_Report.pdf']);
  files.push({name:'1_정량(WPPF)_Report.pdf'});
  mappings[files[3].name]=classifyUploadFilename(files[3].name);
  assert.match(summarizeUpload(files,mappings).errors.join(),/wppf_report 역할 중복/);
});
