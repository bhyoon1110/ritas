const assert=require('node:assert/strict'),{test}=require('node:test');
const {candidates,duplicate,reviewItems}=require('../app/peak_recommendations.js');
const trace=(ys,meta={})=>({x:ys.map((_,i)=>i*10),y:ys,meta:{rist_sample_parent:true,rist_sample_group:'a',...meta}});
test('actual maxima and plateau centers; no endpoints or missing-data bridges',()=>{
  assert.deepEqual(candidates(trace([9,1,4,4,4,1,NaN,6,1,9])),[{id:3,x:30,intensity:4}]);
  assert.deepEqual(candidates(trace([1,1,1,1])),[]);
});
test('absorbance candidates independent of transmittance display and stack offsets',()=>{
  const t=trace([70,30,75,10,80],{ftir_signal_toggle:{absorbance_y:[2,4,2,3,2]},rist_ftir_stack_offset_absorbance:2});
  assert.deepEqual(candidates(t),[{id:1,x:10,intensity:2},{id:3,x:30,intensity:1}]);
  assert.deepEqual(candidates(trace([5,9,5],{rist_raman_stack_offset:5})),[{id:1,x:10,intensity:4}]);
});
test('bounded candidate request for noisy large RAW',()=>{
  const data=candidates(trace(Array.from({length:100000},(_,i)=>i%2 ? i:0)));
  assert.equal(data.length,120);assert(data.every(p=>p.id>99000));
});
test('dedupe within sample only; sensitivity zero preserves manual peaks',()=>{
  const raw=trace([0,1,0]),c={id:1,x:10};
  const peak=(source,group='a',visible=true)=>({visible,meta:{rist_peak:{source,x:10,sample_group:group,sensitivity_min:25}}});
  assert.equal(duplicate([peak('detected')],raw,c,0),false);
  assert.equal(duplicate([peak('detected')],raw,c,25),true);
  assert.equal(duplicate([peak('user','a',false)],raw,c,0),true);
  assert.equal(duplicate([peak('user','b')],raw,c,100),false);
  assert.equal(duplicate([{meta:{rist_peak:{source:'user',x:20,sample_group:'a'}}}],raw,c,100),false);
});

test('review list uses measured candidates, ignores invalid IDs and keeps the original name',()=>{
  const pool=[{id:1,x:10,intensity:2}];
  const rows=reviewItems(pool,[null,{id:99,name:'invented'}, {id:1,name:'<b>추천명</b>',reason:'근거',x:999}, {id:1,name:'duplicate'}]);
  assert.equal(rows.length,1);assert.equal(rows[0].candidate.x,10);
  rows[0].name='수정명';assert.equal(rows[0].originalName,'<b>추천명</b>');assert.deepEqual(pool,[{id:1,x:10,intensity:2}]);
});

const {JSDOM}=require('jsdom'),fs=require('node:fs'),path=require('node:path'),{createHash}=require('node:crypto');
const source=fs.readFileSync(path.join(__dirname,'../app/peak_recommendations.js'),'utf8');
const tick=()=>new Promise(resolve=>setTimeout(resolve,5));
function setup(t,kind,mode) {
  const dom=new JSDOM('<body data-rist-spectrum-step="2"><div id="graph"><div class="rist-plot-control-row"><button class="rist-peak-add-button"></button></div></div></body>',{runScripts:'outside-only',pretendToBeVisual:true,url:'http://localhost/'+kind});
  t.after(()=>dom.window.close());const w=dom.window,g=w.document.getElementById('graph'),events={},requests=[],batches=[];
  g.data=[trace([0,3,0,4,0]),trace([0,2,0,1,0],{rist_sample_group:'b'})];g._ristPeakSensitivityValue=0;
  g.on=(type,fn)=>{(events[type]||=[]).push(fn);};g._ristMaximumSensitivityPeakForSample=()=>null;
  g._ristPeakCoordinates={pointAt:(curve,x)=>({curve,x,y:g.data[curve].y[g.data[curve].x.indexOf(x)],coordinateInput:true})};
  g._ristAddRecommendedPeaks=async points=>{batches.push(points);for(const p of points)g.data.push({meta:{rist_peak:{source:'user',x:p.x,sample_group:g.data[p.curve].meta.rist_sample_group}}});};
  let unitChanges=0;g._ristFtirSignalMode='transmittance';g._ristSetSignalMode=async()=>{unitChanges++;g._ristFtirSignalMode='absorbance';};
  class XHR {open(method,url){this.url=url;}setRequestHeader(){}send(body){this.body=JSON.parse(body);requests.push(this);}abort(){this.aborted=true;}}
  w.XMLHttpRequest=XHR;
  if(mode==='chrome49'){
    const root=path.join(__dirname,'../app/static/chrome49'),manifest=JSON.parse(fs.readFileSync(path.join(root,'manifest.json')));
    w.eval(fs.readFileSync(path.join(root,manifest.runtime),'utf8'));
    const asset=manifest.scripts[createHash('sha256').update(source.trim()).digest('hex')];assert(asset,'rebuild compatibility assets');w.eval(fs.readFileSync(path.join(root,asset),'utf8'));
  }else w.eval(source);
  w.RistPeakRecommendations.install({gd:g,kind});
  const q=s=>w.document.querySelector(s),change=(node,value)=>{node.value=value;node.dispatchEvent(new w.Event(node.tagName==='SELECT'?'change':'input',{bubbles:true}));};
  const open=()=>{q('.rist-peak-recommend-button').click();change(q('[aria-label="물질 힌트"]'),'탄소 시료');};
  const request=()=>q('button[type=submit]').click();
  const respond=(peaks,status=200)=>{const r=requests.at(-1);r.status=status;r.responseText=JSON.stringify(peaks);r.onload();};
  const recommend=()=>{request();respond({peaks:requests.at(-1).body.candidates.map((c,i)=>({...c,name:'추천 '+(i+1),reason:'검토 근거'}))});};
  const dispatch=type=>g.dispatchEvent(new w.CustomEvent(type));
  return {w,g,q,change,open,request,respond,recommend,requests,batches,dispatch,emit:type=>(events[type]||[]).forEach(fn=>fn()),unitChanges:()=>unitChanges};
}
for(const kind of ['ftir','raman'])for(const mode of ['native','chrome49']){
  test(kind+'/'+mode+': bulk selection changes only checks, preserves edited names and skips applied rows',async t=>{
    const h=setup(t,kind,mode);h.open();
    const all=h.q('.rist-peak-recommend-select-all'),none=h.q('.rist-peak-recommend-clear-all'),add=h.q('.rist-peak-recommend-apply');
    assert.equal(all.disabled,true);assert.equal(none.disabled,true);
    h.recommend();assert.equal(all.disabled,true);assert.equal(none.disabled,false);
    h.change(h.q('[aria-label="추천 피크 1 이름"]'),'수정한 이름');h.change(h.q('[aria-label="추천 피크 2 이름"]'),'');
    none.click();assert.equal(add.disabled,true);assert.equal(none.disabled,true);assert.equal(all.disabled,false);
    assert.match(h.q('.rist-peak-recommend-summary').textContent,/선택 0개/);
    all.click();assert.match(h.q('.rist-peak-recommend-summary').textContent,/선택 2개/);
    assert.equal(h.q('[aria-label="추천 피크 1 이름"]').value,'수정한 이름');assert.equal(h.q('[aria-label="추천 피크 2 이름"]').value,'');
    assert.equal(h.batches.length,0);assert.equal(h.unitChanges(),0);assert.equal(h.requests.length,1);
    h.q('[aria-label="추천 피크 2 선택"]').click();add.click();
    assert.equal(all.disabled,true);assert.equal(none.disabled,true);await tick();
    all.click();assert.equal(h.q('[aria-label="추천 피크 1 선택"]').checked,false);assert.equal(h.q('[aria-label="추천 피크 2 선택"]').checked,true);
    add.click();await tick();assert.equal(h.batches.length,2);assert.equal(h.batches[1].length,1);
    assert.equal(h.batches[1][0].recommendation.name,'');assert.equal(all.disabled,true);assert.equal(none.disabled,true);
  });
  test(kind+'/'+mode+': bulk selection excludes existing duplicates and is disabled during requests',async t=>{
    const h=setup(t,kind,mode);h.g.data.push({name:'기존 피크',meta:{rist_peak:{source:'user',sample_group:'a',x:10}}});h.open();h.recommend();
    const all=h.q('.rist-peak-recommend-select-all'),none=h.q('.rist-peak-recommend-clear-all');
    none.click();all.click();
    assert.equal(h.q('[aria-label="추천 피크 1 선택"]').checked,false);assert.equal(h.q('[aria-label="추천 피크 1 선택"]').disabled,true);
    assert.equal(h.q('[aria-label="추천 피크 2 선택"]').checked,true);assert.match(h.q('.rist-peak-recommend-summary').textContent,/선택 1개 · 기존 중복 1개/);
    h.request();assert.equal(all.disabled,true);assert.equal(none.disabled,true);all.click();none.click();assert.equal(h.batches.length,0);
    h.respond({peaks:[]});assert.equal(all.disabled,true);assert.equal(none.disabled,true);
  });
  test(kind+'/'+mode+': recommendations are drafts; selection and name edits apply only on explicit Add',async t=>{
    const h=setup(t,kind,mode);h.open();h.recommend();
    assert.equal(h.batches.length,0);assert.equal(h.unitChanges(),0);
    assert.match(h.q('[role=status]').textContent,/아직 그래프에는 추가하지 않았습니다/);
    h.q('[aria-label="추천 피크 1 선택"]').click();h.change(h.q('[aria-label="추천 피크 2 이름"]'),'사용자 수정명');
    assert.equal(h.batches.length,0);
    h.q('.rist-peak-recommend-apply').click();await tick();
    assert.equal(h.batches.length,1);assert.equal(h.batches[0].length,1);
    assert.equal(h.batches[0][0].recommendation.name,'사용자 수정명');assert.equal(h.batches[0][0].recommendation.originalName,'추천 2');
    assert.equal(h.unitChanges(),kind==='ftir'?1:0);assert.equal(h.q('.rist-peak-recommend-apply').disabled,true);
    // The unchecked row remains available. Blank names intentionally show position only.
    h.change(h.q('[aria-label="추천 피크 1 이름"]'),'');h.q('[aria-label="추천 피크 1 선택"]').click();
    h.q('.rist-peak-recommend-apply').click();await tick();assert.equal(h.batches.length,2);assert.equal(h.batches[1][0].recommendation.name,'');
    assert.equal(h.requests.length,1);assert.equal(h.q('.rist-peak-recommend-apply').disabled,true);
  });
  test(kind+'/'+mode+': close/Esc discards drafts, and Enter while editing never resubmits',async t=>{
    const h=setup(t,kind,mode);h.open();h.recommend();
    const input=h.q('[aria-label="추천 피크 1 이름"]');h.change(input,'<img src=x onerror=alert(1)>');
    const ev=new h.w.KeyboardEvent('keydown',{key:'Enter',bubbles:true,cancelable:true});input.dispatchEvent(ev);
    assert.equal(ev.defaultPrevented,true);assert.equal(h.requests.length,1);assert.equal(h.q('.rist-peak-recommend-results img'),null);
    input.dispatchEvent(new h.w.KeyboardEvent('keydown',{key:'Escape',bubbles:true,cancelable:true}));
    assert.equal(h.batches.length,0);assert.equal(h.unitChanges(),0);assert.equal(h.g._ristPeakRecommendationOpen,false);
    h.open();assert.equal(h.q('.rist-peak-recommend-results').children.length,0);assert.equal(h.q('.rist-peak-recommend-apply').disabled,true);
  });
  test(kind+'/'+mode+': existing duplicates cannot be selected or renamed',async t=>{
    const h=setup(t,kind,mode);h.g.data.push({name:'기존 이름',meta:{rist_peak:{source:'user',sample_group:'a',x:10}}});h.open();h.recommend();
    assert.equal(h.q('[aria-label="추천 피크 1 선택"]').disabled,true);
    assert.equal(h.q('[aria-label="추천 피크 1 이름"]').disabled,true);
    assert.match(h.q('.rist-peak-recommend-state').textContent,/기존 피크 유지/);
    h.q('.rist-peak-recommend-apply').click();await tick();assert.equal(h.batches[0].length,1);assert.equal(h.g.data[2].name,'기존 이름');
  });
  for(const invalidator of ['hint','sample','restyle','replace','stage'])test(kind+'/'+mode+': '+invalidator+' invalidates a reviewed list',async t=>{
    const h=setup(t,kind,mode);h.open();h.recommend();
    if(invalidator==='hint')h.change(h.q('[aria-label="물질 힌트"]'),'새 힌트');
    else if(invalidator==='sample')h.change(h.q('select'),'1');
    else if(invalidator==='restyle')h.emit('plotly_restyle');
    else if(invalidator==='replace')h.dispatch('rist-plot-data-replaced');
    else h.w.document.body.setAttribute('data-rist-spectrum-step','3');
    // Applying also checks the stage, even before MutationObserver delivers.
    if(invalidator!=='stage')assert.equal(h.q('.rist-peak-recommend-apply').disabled,true);
    h.q('.rist-peak-recommend-apply').click();await tick();
    assert.equal(h.batches.length,0);assert.equal(h.unitChanges(),0);
  });
  test(kind+'/'+mode+': empty/errors/cancelled late responses never create an applicable list',t=>{
    const h=setup(t,kind,mode);h.open();h.request();h.respond({peaks:[]});assert.equal(h.q('.rist-peak-recommend-apply').disabled,true);
    h.request();h.respond({message:'모델 실패'},502);assert.match(h.q('[role=status]').textContent,/모델 실패/);
    h.request();h.q('.rist-peak-recommend-close').click();assert.equal(h.requests.at(-1).aborted,true);
    h.respond({peaks:[{id:1,name:'늦은 응답'}]});assert.equal(h.q('.rist-peak-recommend-results').children.length,0);assert.equal(h.batches.length,0);
  });
}
