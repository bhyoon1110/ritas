/* Current author-reviewed facts only; no model calls or inferred measurement values. */
(function(){
  "use strict";
  function text(v){return v==null?"":String(v);}
  function lines(s){return (s.paragraphs||[]).concat(s.bullets||[]).filter(Boolean).join("\n");}
  function unique(values){return values.filter(function(v,i){return v&&values.indexOf(v)===i;});}
  function report(doc,experiment,variables){
    var out=Object.create(null),transfer=doc.transfer||{},conditions=[],environment=[],summaries=[],findings=[],names=[],facts={};
    out["실험종류"]=experiment;out["보고서제목"]=doc.title||"";out["완료일"]=doc.completionDate||"";
    [["requestNumber","의뢰번호"],["equipmentCode","실험장비"],["operatorId","실험자"],["limsExperimentCode","실험코드"]].forEach(function(p){out[p[1]]=transfer[p[0]]||"";});
    (doc.sections||[]).forEach(function(s){
      var id=s.sectionId||"",value=lines(s);
      if(id==="report_traceability")(s.rows||[]).forEach(function(r){facts[r[0]]=r[1]||"";});
      if((s.heading||"").indexOf("표지 · ")===0)facts[s.heading.slice(5)]=value;
      if(id==="experiment_conditions")(s.rows||[]).forEach(function(r){
        if(r.length!==3)return;var label=r[1],v=r[2];
        if(v)conditions.push((r[0]==="공통"?"":r[0]+" · ")+label+": "+v);
        if(/환경|온도|습도/.test(label)&&v)environment.push(label+": "+v);
        if(r[0]==="공통")out["공통."+label]=v;
        else if(r[0])names.push(r[0]);
        if(/장비.*모델|장비명|모델명/.test(label)&&v)facts["장비명 / 모델"]=v;
      });
      if(/(^|-)summary$/.test(id)&&value)summaries.push(value);
      if(/(^|-)(key_findings|interpretation|verdict)$/.test(id)&&value)findings.push(value);
      if(id==="raman_samples")(s.rows||[]).forEach(function(r){names.push(r[0]);});
      if(id==="sample_info")(s.bullets||[]).forEach(function(v){var m=/^(?:시료명|시료|샘플명|샘플)\s*:\s*(.+)$/.exec(v);if(m)names.push(m[1]);});
      // TEM shapes have generated IDs. Only explicitly labelled analysis prose is a summary.
      if(experiment==="TEM"&&!/^표지/.test(s.heading||"")&&/분석 결과|분석 요약|해석|판정/.test(s.heading||"")&&value)summaries.push(value);
    });
    Object.keys(facts).forEach(function(k){out["공통."+k]=facts[k];});
    out["의뢰기관"]=facts["의뢰기관"]||"";out["의뢰자"]=facts["의뢰자"]||"";
    out["장비모델"]=facts["장비명 / 모델"]||"";
    out["실험환경"]=facts["측정 환경"]||environment.join("\n");
    out["실험조건"]=conditions.join("\n")||facts["핵심 측정 조건"]||"";
    out["시료명"]=unique(names).join(", ");out["분석결과"]=summaries.join("\n\n");out["핵심관찰"]=findings.join("\n\n");
    (variables||[]).forEach(function(v){out[v.name]=v.value;});
    return out;
  }
  function xrd(doc,project){
    doc=doc||{};var m=doc.metadata||{},out=Object.create(null),samples=(doc.samples||[]).filter(function(s){return s.included;}),details=[];
    [["title","보고서제목"],["report_number","의뢰번호"],["equipment_code","실험장비"],["equipment_name","장비모델"],["environment","실험환경"],["conditions","실험조건"],["date","완료일"],["operator_id","실험자"],["experiment_code","실험코드"],["client","의뢰기관"],["requester","의뢰자"]].forEach(function(p){out[p[1]]=m[p[0]]||"";});
    out["실험종류"]="XRD";out["시료명"]=samples.map(function(s){return s.display_name;}).join(", ");out["분석결과"]=doc.summary||"";
    samples.forEach(function(s){
      var rows=[],source=((project||{}).samples||[]).filter(function(p){return p.id===s.sample_id;})[0];
      (s.notes||[]).filter(function(n){return n.selected;}).forEach(function(n){rows.push(n.text);});
      if(s.manual_note)rows.push(s.manual_note);
      if(source)(source.quantities||[]).forEach(function(q){rows.push("WPPF: "+q.phase+" "+q.raw+" "+(q.unit||""));});
      if(rows.length)details.push(s.display_name+"\n"+rows.join("\n"));
    });
    out["핵심관찰"]=details.join("\n\n");
    [["purpose","분석 목적"],["preparation","시료 전처리"],["measured_date","측정일 / 기간"],["analysis_period","분석 기간"],["reviewer","검토자"]].forEach(function(p){out["공통."+p[1]]=m[p[0]]||"";});
    return out;
  }
  function expand(subject,html,definitions,clean){
    var values=Object.create(null),cache=Object.create(null),missing=[],token=/\{\{\s*([^{}\n]{1,100}?)\s*\}\}/g;
    Object.keys(definitions||{}).forEach(function(k){values[k]=text(definitions[k]);});
    function resolve(value,trail){var output=text(value).replace(token,function(_,raw){var name=raw.trim();
      if(!Object.prototype.hasOwnProperty.call(values,name))throw Error("등록되지 않은 항목: "+name);
      if(trail.indexOf(name)>=0||trail.length>=10)throw Error("공통 항목이 서로를 반복 참조합니다: "+name);
      if(!Object.prototype.hasOwnProperty.call(cache,name)){cache[name]=resolve(values[name],trail.concat(name));if(cache[name].length>4000)throw Error("공통 항목 내용이 너무 깁니다: "+name);}
      if(!cache[name].trim()&&missing.indexOf(name)<0)missing.push(name);return cache[name];
    });if(output.length>4000)throw Error("공통항목 반영 후 문단이 4,000자를 초과합니다.");return output;}
    var title=resolve(subject,[]);if(title.length>180||/[\r\n\x00]/.test(title))throw Error("반영된 메일 제목은 줄바꿈 없이 180자 이하여야 합니다.");
    var root=document.createElement("div");root.innerHTML=clean(html);
    function visit(parent){Array.prototype.slice.call(parent.childNodes).forEach(function(n){
      if(n.nodeType===3){var replacement=resolve(n.nodeValue,[]),parts=replacement.split("\n");parts.forEach(function(v,i){if(i)parent.insertBefore(document.createElement("br"),n);parent.insertBefore(document.createTextNode(v),n);});parent.removeChild(n);}
      else if(n.nodeType===1)visit(n);
    });}visit(root);
    var result=clean(root.innerHTML),body=window.RistMailEditor.plainText(result);
    if(/\{\{|\}\}/.test(title+body))throw Error("완성되지 않은 공통항목이 있습니다. {{항목명}} 전체를 같은 서식으로 다시 넣으세요.");
    if(body.length>4000)throw Error("반영된 메일 본문이 4,000자를 초과합니다. 분석결과·공통항목을 줄이세요.");
    return {subject:title,body:body,bodyHtml:result,missing:missing};
  }
  window.RistMailTemplateContext={report:report,xrd:xrd,expand:expand};
}());
