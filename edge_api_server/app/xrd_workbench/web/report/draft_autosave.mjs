// Serialize writes: even a stale response must advance the optimistic draft revision.
export function createDraftAutosave({snapshot,write,clear,status,delay=1500,clock=globalThis}) {
  let revision=0,enabled=false,pending=false,running=null,timer=0,stopped=false;
  function cancelTimer(){clock.clearTimeout(timer);timer=0;}
  async function run() {
    cancelTimer();
    if(!enabled||!pending||running)return;
    pending=false;const value=snapshot();
    if(!value)return;
    status('초안 임시저장 중…');
    running=Promise.resolve().then(async()=>{
      try{const r=await write(value,revision);revision=r.revision;status('초안 임시저장 완료 · 최종 저장은 별도');}
      catch(e){if(e.status===409||e.status===401||e.status===403)enabled=false;status('초안 임시저장 실패: '+e.message);}
      finally{running=null;if(pending&&enabled&&!stopped)timer=clock.setTimeout(run,delay);}
    });
    await running;
  }
  return {
    initialize(value,allow=true){revision=value;enabled=allow;},
    schedule(){if(!enabled||stopped)return;pending=true;cancelTimer();timer=clock.setTimeout(run,delay);status('초안 임시저장 대기');},
    pause(){cancelTimer();pending=false;},
    async flush(){cancelTimer();if(running)await running;if(pending)await run();},
    async clear(){cancelTimer();pending=false;enabled=false;if(running)await running;try{const r=await clear(revision);revision=r.revision;enabled=true;status('저장된 보고서 기준 · 복구 초안 없음');}catch(e){status('초안 정리 실패: '+e.message);throw e;}},
    dispose(){stopped=true;cancelTimer();pending=false;}
  };
}
