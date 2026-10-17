import test from 'node:test';
import assert from 'node:assert/strict';
import {summarizeUpload,uploadSummaryHTML} from '../../app/xrd_workbench/web/viewer/upload_summary.mjs';
import {createLivePreview} from '../../app/xrd_workbench/web/report/live_preview.mjs';

function fakeClock() {
  let now=0,id=0; const timers=new Map();
  return {setTimeout(fn,ms){timers.set(++id,{fn,time:now+ms});return id;},clearTimeout(id){timers.delete(id);},
    advance(ms){now+=ms;for(const [id,t] of [...timers])if(t.time<=now){timers.delete(id);t.fn();}},count:()=>timers.size};
}
const flush=()=>new Promise(resolve=>setImmediate(resolve));
test('upload cards distinguish optional WPPF, absent TXT, duplicates and ignored sources',()=>{
  const files=['A.txt','A.pdf','B.txt','unused.pdf'].map(name=>({name}));
  const map={'A.txt':{sample:'A',role:'measurement'},'A.pdf':{sample:'A',role:'phase_report'},'B.txt':{sample:'B',role:'measurement'},'unused.pdf':{role:'ignore'}};
  const r=summarizeUpload(files,map);
  assert.equal(r.groups.length,2);assert.equal(r.ignored,1);assert.deepEqual(r.errors,[]);
  assert.equal(r.groups[1].warnings.length,1);assert.match(uploadSummaryHTML(r),/WPPF 선택 없음/);
  map['A.pdf'].sample='Orphan';assert.match(summarizeUpload(files,map).errors.join(),/TXT 없음/);
  map['A.pdf']={sample:'A',role:'measurement'};assert.match(summarizeUpload(files,map).errors.join(),/역할.*중복/);
  assert.match(summarizeUpload([...files,files[0]],map).errors.join(),/파일명 중복/);
  assert.match(summarizeUpload(files,map).errors.join(),/확장자 불일치/);
});
test('upload summary escapes names and groups NFC-equivalent names without special object keys',()=>{
  const files=[{name:'1.txt'},{name:'2.pdf'}],m={'1.txt':{sample:'가',role:'measurement'},'2.pdf':{sample:'가',role:'phase_report'}};
  assert.equal(summarizeUpload(files,m).groups.length,1);
  m['1.txt'].sample=m['2.pdf'].sample='<img onerror=alert(1)>';
  assert(!uploadSummaryHTML(summarizeUpload(files,m)).includes('<img'));
  m['1.txt'].sample=m['2.pdf'].sample='__proto__';assert.equal(summarizeUpload(files,m).groups.length,1);
});
test('live preview debounces, serializes requests and drops stale replies without saving',async()=>{
  const clock=fakeClock(), shown=[], pending=[],statuses=[];
  const p=createLivePreview({clock,delay:900,render:()=>new Promise(resolve=>pending.push(resolve)),ready:x=>shown.push(x),status:s=>statuses.push(s)});
  p.schedule();clock.advance(800);p.schedule();clock.advance(899);assert.equal(pending.length,0);
  clock.advance(1);assert.equal(pending.length,1);
  p.schedule();clock.advance(900);assert.equal(pending.length,1);
  pending[0]('old');await flush();assert.deepEqual(shown,[]);
  clock.advance(900);assert.equal(pending.length,2);pending[1]('latest');await flush();assert.deepEqual(shown,['latest']);
  assert.equal(clock.count(),0);p.dispose();
});
test('failed preview leaves previous result intact, explicit retry works, disposal blocks late work',async()=>{
  const clock=fakeClock(),shown=[],status=[];let fail=false, resolve;
  const p=createLivePreview({clock,render:async()=>{if(fail)throw Error('invalid draft');return 'good';},ready:x=>shown.push(x),status:s=>status.push(s)});
  p.schedule(true);clock.advance(0);await flush();fail=true;p.schedule(true);clock.advance(0);await flush();
  assert.deepEqual(shown,['good']);assert.match(status.at(-1),/invalid draft/);
  fail=false;p.schedule(true);clock.advance(0);await flush();assert.deepEqual(shown,['good','good']);
  p.schedule();p.invalidate();clock.advance(3000);assert.equal(shown.length,2);
  const q=createLivePreview({clock,render:()=>new Promise(r=>{resolve=r;}),ready:x=>shown.push(x),status:()=>{}});
  q.schedule(true);clock.advance(0);q.dispose();resolve('late');await flush();assert.equal(shown.length,2);
});
