// One in-flight request, debounce, newest-draft wins. No saving/exporting here.
export function createLivePreview({render, ready, status, delay=900, clock=globalThis}) {
  let timer=0, version=0, running=false, disposed=false, pending=false;
  async function run() {
    timer=0;
    if (disposed || running) return;
    const token=version; running=true; pending=false; status('미리보기 생성 중…');
    try {
      const value=await render();
      if (!disposed && token===version) ready(value);
    } catch (error) {
      if (!disposed && token===version) status('미리보기 갱신 실패: '+error.message);
    } finally {
      running=false;
      if (!disposed && pending && !timer) timer=clock.setTimeout(run,delay);
    }
  }
  function schedule(immediate=false) {
    if (disposed) return;
    version++; pending=true; clock.clearTimeout(timer);
    status('입력 반영 대기 · 미저장 초안');
    timer=clock.setTimeout(run,immediate ? 0 : delay);
  }
  return {schedule, invalidate(){version++; pending=false; clock.clearTimeout(timer);timer=0;},
    dispose(){disposed=true;version++;pending=false;clock.clearTimeout(timer);timer=0;}};
}
