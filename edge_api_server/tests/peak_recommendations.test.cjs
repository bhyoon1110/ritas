const assert=require('node:assert/strict'),{test}=require('node:test');
const {candidates,duplicate}=require('../app/peak_recommendations.js');
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
