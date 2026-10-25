/* Measured-peak recommendations. ES5 and XHR for laboratory browsers. */
(function (root, factory) {
  var api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  else root.RistPeakRecommendations = api;
})(this, function () {
  'use strict';
  function finite(v) { return v != null && v !== '' && isFinite(Number(v)); }
  function group(t) { var m = t.meta || {}; return String(m.rist_sample_group || (m.rist_peak && m.rist_peak.sample_group) || ''); }
  function plain(v) { return String(v || '').replace(/<[^>]*>/g, '').trim(); }
  function candidates(trace) {
    var m = trace.meta || {}, signal = m.ftir_signal_toggle;
    var xs = trace.x || [], ys = signal ? signal.absorbance_y : trace.y || [];
    var offset = Number(signal ? m.rist_ftir_stack_offset_absorbance || 0 : m.rist_raman_stack_offset || 0);
    var top = [], length = Math.min(xs.length, ys.length);
    function trim() { top.sort(function (a,b) { return b.score-a.score || a.id-b.id; }); top = top.slice(0,120); }
    // Include plateau maxima once, exclude endpoints, gaps and nonfinite samples.
    // Keep only 120 prominent candidates: never transmit the full RAW arrays.
    for (var i=1; i<length-1; i++) {
      if (!finite(xs[i]) || !finite(ys[i]) || !finite(ys[i-1]) || Number(ys[i]) <= Number(ys[i-1])) continue;
      var end=i;
      while (end+1<length && finite(ys[end+1]) && Number(ys[end+1])===Number(ys[i])) end++;
      if (end+1>=length || !finite(ys[end+1]) || Number(ys[end+1])>=Number(ys[i])) { i=end; continue; }
      var index=Math.floor((i+end)/2), value=Number(ys[index]);
      var left=value, right=value;
      for (var j=Math.max(0,i-12); j<i; j++) if (finite(ys[j])) left=Math.min(left,Number(ys[j]));
      for (var k=end+1; k<Math.min(length,end+13); k++) if (finite(ys[k])) right=Math.min(right,Number(ys[k]));
      if (finite(xs[index])) top.push({id:index,x:Number(xs[index]),intensity:value-offset,score:Math.min(value-left,value-right)});
      if (top.length>=240) trim();
      i=end;
    }
    trim();
    return top.sort(function(a,b){return a.x-b.x;}).map(function(p){return {id:p.id,x:p.x,intensity:p.intensity};});
  }
  function tolerance(trace, id) {
    var xs=trace.x || [], x=Number(xs[id]), delta=Infinity;
    [id-1,id+1].forEach(function(i){if(finite(xs[i]) && Math.abs(Number(xs[i])-x)>0) delta=Math.min(delta,Math.abs(Number(xs[i])-x));});
    return isFinite(delta) ? Math.max(1e-7, delta/2) : 1e-7;
  }
  function duplicate(data, trace, candidate, sensitivity) {
    return data.some(function(t) {
      var p=t.meta && t.meta.rist_peak;
      if (!p || group(t)!==group(trace) || !finite(p.x)) return false;
      var manual=p.user || p.source==='user';
      // Sensitivity-hidden detections may be promoted to persistent manual peaks.
      // Already present manual peaks (including intentionally hidden ones) are never duplicated.
      var detected=p.source==='detected' && sensitivity>0 && p.user_visible!==false
        && (finite(p.sensitivity_min) ? Number(p.sensitivity_min)<=sensitivity : t.visible!==false && t.visible!=='legendonly');
      return (manual || detected) && Math.abs(Number(p.x)-candidate.x)<=tolerance(trace,candidate.id);
    });
  }
  function install(api) {
    var gd=api.gd, kind=api.kind, row=gd.querySelector('.rist-plot-control-row');
    if (!row || gd._ristPeakRecommendationsInstalled || !gd._ristPeakCoordinates) return;
    gd._ristPeakRecommendationsInstalled=true;
    var button=document.createElement('button');button.type='button';button.className='rist-peak-edit-button rist-peak-recommend-button';
    button.textContent='LLM 피크 추천';button.title='물질 힌트로 실제 측정 피크를 추천받아 추가';row.appendChild(button);
    var overlay=document.createElement('div');overlay.className='rist-peak-recommend-overlay';
    overlay.innerHTML='<form class="rist-peak-recommend-dialog" role="dialog" aria-modal="true" aria-label="LLM 피크 추천">'
      + '<h3>LLM 피크 추천</h3><label>Raw 시료<select aria-label="추천 대상 시료"></select></label>'
      + '<label>물질·조성 힌트<textarea aria-label="물질 힌트" maxlength="1000" rows="3" placeholder="예: 탄소 코팅 LFP, D/G 밴드 확인 또는 PVDF 바인더의 주요 결합"></textarea></label>'
      + '<p>현재 피크 민감도와 무관하게 실제 스펙트럼의 주요 극대점(FTIR: 흡광도)을 검토합니다. 추천 피크만 추가하며 기존 피크·이름은 유지합니다. 같은 시료의 중복 위치는 건너뜁니다.</p>'
      + '<p>한 번에 최대 12개를 추가하며, 민감도를 0으로 내려도 유지됩니다. 실행취소로 이번 추천 전체를 되돌릴 수 있습니다. FTIR 투과도 화면은 추천 적용 시 흡광도로 전환됩니다.</p>'
      + '<p class="rist-peak-recommend-warning">LLM 추천은 검토용 후보이며 물질 확정 결과가 아닙니다. 표준 스펙트럼·문헌과 대조하세요.</p>'
      + '<p role="status" aria-live="polite"></p><ul class="rist-peak-recommend-results"></ul>'
      + '<div class="rist-peak-recommend-actions"><button type="button">닫기 (Esc)</button><button type="submit">추천받아 추가</button></div></form>';
    document.body.appendChild(overlay);
    var form=overlay.querySelector('form'), sample=overlay.querySelector('select'), hint=overlay.querySelector('textarea');
    var status=overlay.querySelector('[role=status]'), results=overlay.querySelector('ul'), closeButton=overlay.querySelector('button[type=button]');
    var submit=overlay.querySelector('button[type=submit]'), xhr=null, sequence=0, generation=0, busy=false, applying=false, previous=null;
    function currentParent() {
      var t=sample.value==='' ? null : (gd.data || [])[Number(sample.value)];
      return t && t.meta && t.meta.rist_sample_parent && t.visible!==false && t.visible!=='legendonly' ? t : null;
    }
    function update() { submit.disabled=busy || button.disabled || !hint.value.trim() || !currentParent(); sample.disabled=hint.disabled=busy; closeButton.disabled=applying; }
    function stop(message) {
      if (applying) return;
      sequence++;var pending=xhr;xhr=null;if(pending) pending.abort();busy=false;update();
      if(message) status.textContent=message;
    }
    function close(restore) {
      if(applying) return;
      stop();overlay.style.display='none';gd._ristPeakRecommendationOpen=false;
      gd.dispatchEvent(new CustomEvent('rist-interaction-mode-change'));
      if(restore!==false) { if(gd._ristRestoreNavigation) gd._ristRestoreNavigation(); if(previous && previous.getClientRects().length) previous.focus(); }
    }
    button.addEventListener('click',function(ev) {
      ev.preventDefault();ev.stopPropagation();if(button.disabled || busy) return;
      previous=document.activeElement;
      gd.dispatchEvent(new CustomEvent('rist-exclusive-interaction-start',{detail:{mode:'peak-recommend'}}));
      sample.innerHTML='';results.textContent='';status.textContent='';
      (gd.data || []).forEach(function(t,index) {
        if(!t.meta || !t.meta.rist_sample_parent || t.visible===false || t.visible==='legendonly') return;
        var option=document.createElement('option');option.value=String(index);option.textContent=plain(t.name) || 'Raw '+(sample.options.length+1);sample.appendChild(option);
      });
      gd._ristPeakRecommendationOpen=true;overlay.style.display='flex';update();
      if(!sample.options.length) status.textContent='표시 중인 Raw 시료가 없습니다. 먼저 업로드하세요.';
      gd.dispatchEvent(new CustomEvent('rist-interaction-mode-change'));hint.focus();
    });
    hint.addEventListener('input',update);sample.addEventListener('change',update);
    closeButton.addEventListener('click',function(){close();});
    form.addEventListener('submit',function(ev) {
      ev.preventDefault();if(submit.disabled || !gd._ristPeakRecommendationOpen) return;
      var trace=currentParent(), curve=Number(sample.value), pool=candidates(trace), material=hint.value.trim();
      results.textContent='';
      if(!pool.length) { status.textContent='실제 스펙트럼에서 추천할 극대점을 찾지 못했습니다. X좌표 추가로 직접 지정할 수 있습니다.';return; }
      busy=true;update();var token=++sequence, revision=generation;
      status.textContent='실제 측정 피크 '+pool.length+'개를 로컬 LLM으로 검토 중입니다… (최대 약 90초)';
      var request=new XMLHttpRequest();xhr=request;request.open('POST','/api/v1/'+kind+'/peaks/recommend');request.timeout=90000;
      request.setRequestHeader('Content-Type','application/json');
      function fail(message) { if(token!==sequence)return;xhr=null;busy=false;applying=false;update();status.textContent=message; }
      request.onerror=function(){fail('서버에 연결하지 못했습니다. 그래프는 변경하지 않았습니다.');};
      request.ontimeout=function(){fail('추천 응답 시간이 초과되었습니다. 그래프는 변경하지 않았습니다.');};
      request.onload=function() {
        if(token!==sequence) return;
        xhr=null;
        if(currentParent()!==trace) {fail('시료가 변경되어 추천 적용을 취소했습니다. 다시 요청하세요.');return;}
        var response;
        try {response=JSON.parse(request.responseText);} catch(ignore) {fail('추천 응답을 읽지 못했습니다. 그래프는 변경하지 않았습니다.');return;}
        if(request.status!==200 || !response || !Array.isArray(response.peaks)) {fail(response && response.message || '피크 추천 요청에 실패했습니다. 그래프는 변경하지 않았습니다.');return;}
        var points=[], skipped=0, seen={}, valid=0;
        applying=true;update();
        Promise.resolve().then(function() {
          // FTIR deliberately hides absorbance annotations in transmittance view.
          // Restore those originals BEFORE appending new labels and batch indices.
          if(kind==='ftir' && gd._ristFtirSignalMode==='transmittance' && response.peaks.length) return gd._ristSetSignalMode('absorbance');
        }).then(function(){return gd._ristStackReady;}).then(function() {
          if(revision!==generation || !currentParent() || group(currentParent())!==group(trace)
              || document.body.getAttribute('data-rist-spectrum-step')!=='2') {
            throw new Error('stale-recommendation');
          }
          response.peaks.slice(0,12).forEach(function(p) {
            var candidate=pool.filter(function(c){return c.id===p.id;})[0];
            if(!candidate || seen[p.id] || typeof p.name!=='string') return;
            seen[p.id]=true;valid++;
            var isDuplicate=duplicate(gd.data,trace,candidate,Number(gd._ristPeakSensitivityValue));
            var item=document.createElement('li');item.textContent=(isDuplicate?'기존 유지 · ':'추가 · ')+candidate.x+' cm⁻¹ · '+p.name.slice(0,120)+(p.reason?' — '+String(p.reason).slice(0,500):'');results.appendChild(item);
            if(isDuplicate) {skipped++;return;}
            var point=gd._ristPeakCoordinates.pointAt(curve,candidate.x);
            if(!point) return;
            point.recommendation={name:p.name.slice(0,120),reason:String(p.reason || '').slice(0,500),material:material};
            // Override sensitivity-hidden detections at exactly the same measured position.
            var maximum=gd._ristMaximumSensitivityPeakForSample(group(trace),candidate.x);
            if(maximum && maximum.distance<=tolerance(trace,candidate.id)) point.maximumSensitivityPeakCurve=maximum.curve;
            points.push(point);
          });
          return gd._ristAddRecommendedPeaks(points);
        }).then(function(){return gd._ristStackReady;}).then(function(){return gd._ristPeakDisplayReady;}).then(function(){
          applying=false;busy=false;update();
          status.textContent=valid ? points.length+'개 추가 · 중복 '+skipped+'개 유지. 추천 이름과 근거를 검토하세요.' : '힌트에 적합한 측정 피크를 추천하지 못했습니다. 기존 그래프는 유지됩니다.';
        }).catch(function(error){applying=false;fail(error.message==='stale-recommendation'
          ? '시료 또는 작업 단계가 변경되어 추천 피크를 적용하지 않았습니다. 다시 요청하세요.'
          : '추천 표시 중 오류가 발생했습니다. 그래프를 확인하고 필요하면 실행취소하세요.');});
      };
      request.send(JSON.stringify({material:material,sample:plain(trace.name).slice(0,200),candidates:pool}));
    });
    overlay.addEventListener('keydown',function(ev) {
      if(ev.key==='Escape') {ev.preventDefault();ev.stopPropagation();close();}
      if(ev.key==='Tab') {
        var nodes=Array.prototype.slice.call(overlay.querySelectorAll('button,input,select,textarea')).filter(function(n){return !n.disabled;});
        var first=nodes[0],last=nodes[nodes.length-1];
        if(ev.shiftKey && document.activeElement===first) {ev.preventDefault();last.focus();}
        else if(!ev.shiftKey && document.activeElement===last) {ev.preventDefault();first.focus();}
      }
    });
    ['rist-plot-data-replaced','rist-history-restored'].forEach(function(type){gd.addEventListener(type,function(){generation++;if(!applying)close(false);});});
    gd.addEventListener('rist-plot-structure-changed',function(){if(!applying)close(false);});
    gd.on('plotly_restyle',function(){if(busy && !applying) stop('그래프 표시가 변경되어 추천 적용을 취소했습니다. 다시 요청하세요.');});
    gd.addEventListener('rist-exclusive-interaction-start',function(ev){if(!ev.detail || ev.detail.mode!=='peak-recommend')close(false);});
    gd.addEventListener('rist-peak-actions-disabled',function(ev){button.disabled=!!(ev.detail && ev.detail.disabled);if(button.disabled)close(false);});
    new MutationObserver(function(){if(document.body.getAttribute('data-rist-spectrum-step')!=='2') close(false);}).observe(document.body,{attributes:true,attributeFilter:['data-rist-spectrum-step']});
    button.disabled=!!gd.querySelector('.rist-peak-add-button').disabled;
    gd._ristPeakRecommendations={candidates:candidates};
  }
  return {candidates:candidates,duplicate:duplicate,tolerance:tolerance,install:install};
});
