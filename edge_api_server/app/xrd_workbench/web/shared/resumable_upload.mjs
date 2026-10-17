import {hashBlobPrefix} from './file_hash.mjs';

export async function transferUpload(files,mappings,{request,chunkSize,uploadId=null,onSession=()=>{},notice=()=>{},canceled=()=>false}){
  if(!files.length||files.length>100)throw Error('파일은 1~100개 선택하세요.');
  if(files.some(f=>!f.size||f.size>80*1024*1024)||files.reduce((s,f)=>s+f.size,0)>1200*1024*1024)throw Error('파일당 80MB, 전체 1200MB 이하의 비어 있지 않은 파일을 선택하세요.');
  const stop=()=>{if(canceled())throw Error('전송을 일시중지했습니다. 같은 원본을 선택해 이어서 전송할 수 있습니다.');};
  stop();
  if(!uploadId)uploadId=(await(await request('/api/upload-sessions',{method:'POST'})).json()).upload_id;
  onSession(uploadId);
  const base=`/api/upload-sessions/${uploadId}`,existing=await(await request(base)).json();
  if(existing.job)return {upload_id:uploadId,job:existing.job};
  const offsets=new Map(),byName=new Map(files.map(f=>[f.name,f]));
  for(const previous of existing.files){
    const file=byName.get(previous.name);
    if(!file||file.size!==previous.size)throw Error(`${previous.name}: 이전 작업과 같은 파일 전체를 다시 선택하세요. 크기/이름이 다르면 새 업로드를 시작하세요.`);
    stop();notice(`${file.name}: 이미 전송된 부분 대조 중…`);
    const digest=await hashBlobPrefix(file,previous.uploaded_bytes,{canceled,progress:(done,total)=>notice(`${file.name}: 원본 대조 ${Math.floor(done/total*100)}%`)});
    if(digest!==previous.prefix_sha256)throw Error(`${file.name}: 이미 전송한 내용과 다릅니다. 원본을 확인하거나 새 업로드를 시작하세요.`);
    offsets.set(file.name,previous.uploaded_bytes);
  }
  stop();
  await request(base+'/mappings',{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify(mappings)});
  const total=files.reduce((s,f)=>s+f.size,0);let completed=0;
  for(const [i,file] of files.entries()){
    for(let offset=offsets.get(file.name)||0;offset<file.size;offset+=chunkSize){
      stop();const chunk=new FormData();
      chunk.append('relative_path',file.name);chunk.append('offset',offset);chunk.append('total_size',file.size);
      chunk.append('chunk_index',Math.floor(offset/chunkSize));chunk.append('chunk_count',Math.ceil(file.size/chunkSize));
      chunk.append('file',file.slice(offset,offset+chunkSize),file.name);
      notice(`전체 ${Math.floor((completed+offset)/total*100)}% · 파일 ${i+1}/${files.length}: ${file.name} ${Math.floor(offset/file.size*100)}%`);
      for(let attempt=0;;attempt++){
        stop();try{await request(base+'/chunks',{method:'POST',body:chunk});break;}
        catch(e){if(attempt>=2||(e.status&&e.status<500&&e.status!==429))throw Error(`${file.name}: ${e.message} · 이어서 전송으로 재시도하세요.`);await new Promise(resolve=>setTimeout(resolve,1000*(attempt+1)));}
      }
    }
    completed+=file.size;
  }
  stop();notice('전송 100% · 서버에서 Raw 형식·범위를 진단하는 중…');
  const diagnostics=await(await request(base+'/preflight',{method:'POST'})).json();
  return {upload_id:uploadId,diagnostics};
}
