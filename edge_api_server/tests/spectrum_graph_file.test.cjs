const {test}=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const {JSDOM}=require('jsdom'),{createHash}=require('node:crypto'),acorn=require('acorn');
const source=fs.readFileSync(path.join(__dirname,'../app/spectrum_graph_file.js'),'utf8');
const assets=path.resolve(__dirname,'../app/static/chrome49');
const png='data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVQIHWP4z8DwHwAFgAI/ScLbtAAAAABJRU5ErkJggg==';
function setup(t,mode){
  const dom=new JSDOM('',{runScripts:'outside-only'}),w=dom.window;t.after(()=>w.close());
  let code=source;if(mode==='chrome49'){
    const manifest=JSON.parse(fs.readFileSync(path.join(assets,'manifest.json')));w.eval(fs.readFileSync(path.join(assets,manifest.runtime),'utf8'));
    const file=manifest.scripts[createHash('sha256').update(source.trim()).digest('hex')];assert(file,'rebuild portable graph assets');code=fs.readFileSync(path.join(assets,file),'utf8');
  }
  w.eval(code);
  const read=blob=>new Promise((resolve,reject)=>{const r=new w.FileReader();r.onload=()=>resolve(Buffer.from(r.result));r.onerror=reject;r.readAsArrayBuffer(blob);});
  const graph=(kind='ftir')=>({title:'편집한 그래프',state:{version:1,originStyle:true,signalMode:'transmittance',sensitivity:0,
    files:[{name:'C272 10%.'+(kind==='ftir'?'dpt':'txt'),type:'text/plain',lastModified:123456,blob:new w.Blob(['123456789'])}],
    reportMetadata:{instrumentModel:'test'},selectedLibraryIds:['local-id'],workTemplate:{name:'local'},reportTransfer:{requestNumber:'PRIVATE'},
    signalOriginals:kind==='ftir'?{annotations:[{text:'hidden absorbance label',x:2,y:1}],shapes:[]}:null,
    plotData:[{type:'scatter',name:'Edited',x:[1,2,3],y:[3,1,4],meta:{rist_sample_parent:true,rist_sample_group:'sample:0'}},
      {type:'scatter',name:'manual',x:[2],y:[1],meta:{rist_peak:{user:true,base_y:1,sample_group:'sample:0'}}}],
    plotLayout:{title:{text:''},annotations:[{text:'my label',x:2,y:1}],shapes:[{type:'line',x0:1,x1:2,y0:1,y1:2}],meta:{ristGrid:{x:{major:false,minor:true}}}},
    analysisPayload:{samples:[{name:'C272'}],reportContext:{requestNumber:'PRIVATE'}}},
    report:{image:png,analysis:{samples:[],reportContext:{requestNumber:'PRIVATE'}},figure:{data:[],layout:{}},viewerFigure:{data:[],layout:{}}}});
  return {w,api:w.RistSpectrumGraphFile,read,graph};
}
// Independent encoder used only to test rejection of valid-checksum hostile inputs.
function crc(bytes){let n=0xffffffff;for(const b of bytes){n^=b;for(let k=0;k<8;k++)n=n&1?0xedb88320^(n>>>1):n>>>1;}return (n^0xffffffff)>>>0;}
function repack(w,bytes,change){const size=bytes.readUInt32LE(8),meta=JSON.parse(bytes.subarray(16,16+size));change(meta);
  const text=Buffer.from(JSON.stringify(meta)),header=Buffer.from(bytes.subarray(0,16));header.writeUInt32LE(text.length,8);header.writeUInt32LE(crc(text),12);
  return new w.Blob([header,text,bytes.subarray(16+size)]);}
test('portable format source stays ES5',()=>acorn.parse(source,{ecmaVersion:5}));
for(const mode of ['native','chrome49'])for(const kind of ['ftir','raman']){
  const label=mode+'/'+kind;
  test(label+': round trip retains exact RAW bytes and scientific edits without account/request/library bindings',async t=>{
    const h=setup(t,mode),g=h.graph(kind),messages=[];
    const blob=await h.api.write(g,kind,msg=>messages.push(msg)),bytes=await h.read(blob),size=bytes.readUInt32LE(8),meta=JSON.parse(bytes.subarray(16,16+size));
    assert.equal(bytes.subarray(0,8).toString(),'RISTGRF1');assert.equal(meta.rawFiles[0].crc32,'cbf43926');assert.equal(bytes.readUInt32LE(12),crc(bytes.subarray(16,16+size)));
    assert(!bytes.includes(Buffer.from('PRIVATE')));assert(!bytes.includes(Buffer.from('local-id')));
    const loaded=await h.api.load(blob,kind);
    assert.deepEqual(JSON.parse(JSON.stringify(loaded.state.plotData)),g.state.plotData);
    assert.deepEqual(JSON.parse(JSON.stringify(loaded.state.plotLayout)),g.state.plotLayout);
    assert.deepEqual(JSON.parse(JSON.stringify(loaded.state.signalOriginals)),g.state.signalOriginals);
    assert.equal(loaded.state.files[0].name,g.state.files[0].name);assert.equal(loaded.state.files[0].lastModified,123456);
    assert.equal((await h.read(loaded.state.files[0].blob)).toString(),'123456789');assert.equal(loaded.state.sensitivity,0);
    assert.equal(loaded.state.workTemplate,null);assert.equal(loaded.state.selectedLibraryIds.length,0);assert(loaded.state.portableImport);
    assert.equal(g.state.reportTransfer.requestNumber,'PRIVATE','export never mutates the saved entry');assert(messages.length);
    const reexported=await h.api.write(loaded,kind);assert.equal((await h.read((await h.api.load(reexported,kind)).state.files[0].blob)).toString(),'123456789');
  });
  test(label+': multiple binary RAWs keep encoding, line endings, order and same-name content',async t=>{
    const h=setup(t,mode),g=h.graph(kind),raw=Buffer.alloc(700000);for(let i=0;i<raw.length;i++)raw[i]=i%256;
    g.state.files.push({...g.state.files[0],blob:new h.w.Blob([raw])});
    const imported=await h.api.load(await h.api.write(g,kind),kind);
    assert.equal(imported.state.files.length,2);assert.deepEqual(await h.read(imported.state.files[1].blob),raw);
  });
  test(label+': corruption, truncation, appended bytes and wrong experiment are rejected before restore',async t=>{
    const h=setup(t,mode),blob=await h.api.write(h.graph(kind),kind),bytes=await h.read(blob);
    for(const offset of [20,bytes.length-1]){const bad=Buffer.from(bytes);bad[offset]^=1;await assert.rejects(h.api.load(new h.w.Blob([bad]),kind),/손상/);}
    await assert.rejects(h.api.load(blob.slice(0,blob.size-1),kind),/잘렸/);
    await assert.rejects(h.api.load(new h.w.Blob([blob,'x']),kind),/덧붙/);
    await assert.rejects(h.api.load(blob,kind==='ftir'?'raman':'ftir'),/해당 실험/);
    await assert.rejects(h.api.load(new h.w.Blob(['0'.repeat(20)]),kind),/RIST 그래프/);
  });
  test(label+': malformed schemas, unsafe images, prototype keys and excessive nesting fail even with correct CRC',async t=>{
    const h=setup(t,mode),bytes=await h.read(await h.api.write(h.graph(kind),kind));
    const mutations=[m=>m.version=2,m=>m.state.plotData[0].x=['1'],m=>m.state.plotData[0].meta={},m=>m.rawFiles[0].size=-1,
      m=>m.rawFiles[0].name='../bad.dpt',m=>m.state.plotLayout.images=[{source:'https://example.com/collect'}],
      m=>m.report.image='data:image/svg+xml;base64,AAAA',m=>m.state.plotLayout.annotations=[{text:'<script>alert(1)</script>'}],
      m=>m.report.image='data:image/png;base64,AAAA',
      m=>m.state.signalOriginals={images:[{source:'https://example.com/collect'}]},
      m=>Object.defineProperty(m.state.plotLayout,'__proto__',{value:{polluted:true},enumerable:true}),
      m=>{let n=m.state.plotLayout;for(let i=0;i<70;i++)n=n.deep={};}];
    for(const mutate of mutations)await assert.rejects(h.api.load(repack(h.w,bytes,mutate),kind));
    assert.equal({}.polluted,undefined);
  });
  test(label+': unsupported/missing RAW cannot be exported',async t=>{
    const h=setup(t,mode),g=h.graph(kind);g.state.files=[];await assert.rejects(h.api.write(g,kind),/원본 RAW/);
    g.state.files=h.graph(kind).state.files;g.state.files[0].name='other.exe';await assert.rejects(h.api.write(g,kind),/파일명/);
  });
}
