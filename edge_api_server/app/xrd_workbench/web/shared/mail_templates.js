/* Personal mail templates; all changes remain drafts until explicitly applied. */
(function(){
  "use strict";
  window.RistMailTemplates=function(options){
    var host=options.host,clean=options.clean,locked=false,busy=false,revision=0,items=[],selected=null,loaded=false,dirty=false,target=null,savedRange=null;
    function node(tag,parent,label){var n=document.createElement(tag);if(label)n.textContent=label;if(parent)parent.appendChild(n);return n;}
    function button(parent,label,icon,fn){var b=node("button",parent);b.type="button";window.RistMailIcons(b,icon,label);b.onclick=function(){if(!locked&&!busy)fn();};return b;}
    function field(parent,label,tag){var l=node("label",parent,label),n=node(tag||"input",l);n.setAttribute("aria-label",label);return n;}
    var header=node("div");header.className="mail-template-entry";host.insertBefore(header,options.before);
    var toggle=node("button",header,"메일 템플릿");toggle.type="button";toggle.setAttribute("aria-expanded","false");
    var panel=node("section");panel.className="mail-template-panel";panel.hidden=true;host.insertBefore(panel,options.before);
    var style=node("style",panel);style.textContent=".mail-template-entry{margin:10px 0}.mail-template-panel{border:1px solid #cbd5e1;border-radius:6px;padding:12px;margin:8px 0;background:#f8fafc;min-width:0}.mail-template-panel[hidden]{display:none}.mail-template-panel label{display:block;margin:8px 0}.mail-template-panel input,.mail-template-panel select{width:100%;min-height:38px;box-sizing:border-box;font:inherit;padding:6px}.mail-template-fields{display:flex;flex-wrap:wrap;gap:8px}.mail-template-fields>label{flex:1 1 180px;min-width:0}.mail-template-body,.mail-template-preview{background:white;border:1px solid #cbd5e1;padding:10px;min-height:160px;max-height:440px;overflow:auto;overflow-wrap:break-word}.mail-template-body img,.mail-template-preview img{max-width:100%;height:auto}.mail-template-body table,.mail-template-preview table{max-width:100%;table-layout:fixed;border-collapse:collapse}.mail-template-preview{min-height:60px}.mail-template-note{white-space:pre-wrap;font-size:13px;color:#526277}.mail-template-variables{display:flex;align-items:end;gap:6px}.mail-template-variables label{flex:1;min-width:0}.mail-template-variables .mail-toolbar{flex:none}.mail-template-panel .mail-toolbar{display:flex;flex-wrap:wrap;gap:4px}@media(max-width:600px){.mail-template-panel input,.mail-template-panel select{min-height:44px}}";
    node("h3",panel,"메일 템플릿 관리");
    node("p",panel,"내 계정의 템플릿입니다. 제목·본문에 공통항목과 현재 분석결과를 넣고 미리보기 후 적용하세요. 수신자·참조·서명은 템플릿에 저장하지 않습니다.").className="mail-template-note";
    var list=field(panel,"저장된 메일 템플릿","select"),actions=node("div",panel);actions.className="mail-toolbar";
    var newButton=button(actions,"새 메일 템플릿","plus",function(){if(discard()){selected=null;list.value="";name.value="";scope.value=options.experiment;subject.value="[RIST] {{실험종류}} 분석 결과 · {{의뢰번호}}";editor.innerHTML="<p>안녕하십니까.</p><p>{{시료명}}의 {{실험종류}} 분석 결과를 안내드립니다.</p><p>실험장비: {{실험장비}}<br>장비모델: {{장비모델}}<br>실험조건: {{실험조건}}<br>실험환경: {{실험환경}}</p><p>{{분석결과}}</p><p>{{핵심관찰}}</p><p>상세 내용은 첨부 보고서를 확인해 주십시오.</p>";changed();controls();}});
    var loadButton=button(actions,"선택한 메일 템플릿 불러오기","folder",function(){if(!list.value||!discard())return;request("GET","/"+encodeURIComponent(list.value),null,function(v){selected=v.id;revision=v.revision;fill(v.content);dirty=false;controls();status("템플릿을 불러왔습니다. 현재 메일은 아직 변경되지 않았습니다.");});});
    button(actions,"현재 메일을 템플릿으로 가져오기","rich-text",function(){if(!discard())return;var v=options.current();subject.value=v.subject;editor.innerHTML=clean(v.bodyHtml);changed();status("현재 제목·본문을 가져왔습니다. 보고서마다 달라질 값은 공통항목으로 바꿔 저장하세요.");});
    var addButton=button(actions,"메일 템플릿 새 이름으로 저장","save",function(){save(false);});
    var updateButton=button(actions,"선택한 메일 템플릿 수정 저장","edit",function(){save(true);});
    var deleteButton=button(actions,"선택한 메일 템플릿 삭제","trash",function(){if(!list.value||!window.confirm("선택한 템플릿을 삭제할까요? 작성 중인 메일은 유지됩니다."))return;request("DELETE","/"+encodeURIComponent(list.value),{revision:revision},function(v){selected=null;updateList(v);status("템플릿을 삭제했습니다. 현재 입력과 메일은 유지됩니다.");});});
    button(actions,"메일 템플릿 목록 새로고침","refresh",refresh);
    var fields=node("div",panel);fields.className="mail-template-fields";
    var name=field(fields,"메일 템플릿 이름"),scope=field(fields,"템플릿 적용 실험","select");name.maxLength=80;
    ["ALL","FTIR","RAMAN","TEM","XRD"].forEach(function(k){var o=node("option",scope,k==="ALL"?"모든 실험":k);o.value=k;});scope.value=options.experiment;
    var subject=field(panel,"템플릿 메일 제목");subject.maxLength=180;
    var vars=node("div",panel);vars.className="mail-template-variables";
    var variable=field(vars,"템플릿 공통항목","select"),varActions=node("div",vars);varActions.className="mail-toolbar";
    button(varActions,"선택한 공통항목 삽입","plus",insertVariable);
    button(varActions,"현재 보고서 공통항목 새로고침","refresh",refreshVariables);
    node("p",panel,"제목 또는 본문의 삽입 위치를 클릭한 뒤 항목을 선택하세요. 공통항목은 {{항목명}}으로 저장되며 적용 시 현재 보고서 값으로 채워집니다.").className="mail-template-note";
    var format=node("div",panel);format.className="mail-toolbar";
    [["bold","굵게"],["italic","기울임"],["underline","밑줄"],["insertUnorderedList","목록"]].forEach(function(p){var b=button(format,"템플릿 본문 "+p[1],p[0]==="insertUnorderedList"?"bullets":p[0],function(){editor.focus();restoreRange();document.execCommand(p[0],false,null);remember();changed();});b.onmousedown=function(e){e.preventDefault();};});
    node("label",panel,"템플릿 메일 본문");var editor=node("div",panel);editor.className="mail-template-body";editor.contentEditable="true";editor.setAttribute("role","textbox");editor.setAttribute("aria-label","템플릿 메일 본문");editor.setAttribute("aria-multiline","true");
    var previewActions=node("div",panel);previewActions.className="mail-toolbar";
    button(previewActions,"공통항목 반영 미리보기","select-range",preview);
    var applyButton=button(previewActions,"미리본 템플릿을 현재 메일에 적용","next",apply);
    var message=node("p",panel);message.className="mail-template-note";message.setAttribute("role","status");message.setAttribute("aria-live","polite");
    var previewTitle=node("strong",panel),previewBody=node("div",panel);previewBody.className="mail-template-preview";previewBody.hidden=true;
    function status(v){message.textContent=v;}
    function changed(){dirty=true;previewBody.hidden=true;previewTitle.textContent="";}
    function discard(){return !dirty||window.confirm("저장하지 않은 템플릿 입력을 바꿀까요? 현재 보고서 메일은 유지됩니다.");}
    function controls(){toggle.disabled=locked;Array.prototype.forEach.call(panel.querySelectorAll("button,input,select"),function(n){n.disabled=locked||busy;});editor.contentEditable=String(!locked&&!busy);loadButton.disabled=deleteButton.disabled=locked||busy||!list.value;updateButton.disabled=locked||busy||!selected||selected!==list.value;addButton.disabled=locked||busy||!loaded||items.length>=20;applyButton.disabled=locked||busy;}
    function fill(v){name.value=v.name;scope.value=v.experiment;subject.value=v.subject;editor.innerHTML=clean(v.bodyHtml||plain(v.body));previewBody.hidden=true;previewTitle.textContent="";savedRange=null;}
    function plain(v){var n=document.createElement("div");n.textContent=v||"";return n.innerHTML.replace(/\n/g,"<br>");}
    function data(){var html=clean(editor.innerHTML);return {name:name.value.trim(),experiment:scope.value,subject:subject.value,body:window.RistMailEditor.plainText(html),bodyHtml:html};}
    function save(update){if(update&&(!selected||selected!==list.value))return;var v;try{v=data();if(!v.name)throw Error("템플릿 이름을 입력하세요.");if(v.body.length>4000)throw Error("템플릿 본문은 4,000자 이하로 입력하세요.");}catch(e){status(e.message);return;}
      request(update?"PUT":"POST",update?"/"+encodeURIComponent(selected):"",{revision:revision,content:v},function(result){selected=result.selectedId;updateList(result);list.value=selected;dirty=false;controls();status(update?"메일 템플릿을 수정했습니다.":"메일 템플릿을 저장했습니다.");});
    }
    function updateList(v){revision=v.revision;items=v.items;loaded=true;var keep=list.value;list.textContent="";node("option",list,"템플릿을 선택하세요").value="";items.forEach(function(i){var o=node("option",list,i.name+" · "+(i.experiment==="ALL"?"공통":i.experiment));o.value=i.id;});list.value=items.some(function(i){return i.id===keep;})?keep:"";controls();}
    function refresh(){request("GET","",null,function(v){updateList(v);status("메일 템플릿 목록을 불러왔습니다. 최대 20개까지 관리할 수 있습니다.");});}
    function request(method,suffix,value,done){if(locked||busy)return;busy=true;controls();status("메일 템플릿을 처리하는 중입니다…");var xhr=new XMLHttpRequest();xhr.open(method,"/api/v1/report-mail/templates"+suffix,true);xhr.timeout=15000;if(value){xhr.setRequestHeader("Content-Type","application/json");xhr.setRequestHeader("X-Requested-With","RIST-Mail-Editor");}
      function end(error,v){busy=false;controls();if(error)status(error);else done(v);}
      xhr.onerror=xhr.ontimeout=function(){end("템플릿 연결 실패. 현재 입력은 유지됩니다.");};xhr.onload=function(){var v;try{v=JSON.parse(xhr.responseText);if(!v)throw Error();}catch(_){end("템플릿 응답을 확인할 수 없습니다.");return;}if(xhr.status<200||xhr.status>=300){end(v.message||"템플릿 요청에 실패했습니다.");return;}try{end(null,v);}catch(e){status(e.message);}};xhr.send(value?JSON.stringify(value):null);
    }
    function definitions(){return options.context()||{};}
    function refreshVariables(){var keep=variable.value;variable.textContent="";var values=definitions();Object.keys(values).forEach(function(k){var o=node("option",variable,k+(String(values[k]||"").trim()?"":" · 미입력"));o.value=k;});if(Object.prototype.hasOwnProperty.call(values,keep))variable.value=keep;}
    function remember(){target=editor;var s=window.getSelection();if(s&&s.rangeCount&&editor.contains(s.anchorNode))savedRange=s.getRangeAt(0).cloneRange();}
    function restoreRange(){if(savedRange&&editor.contains(savedRange.commonAncestorContainer)){var s=window.getSelection();s.removeAllRanges();s.addRange(savedRange);return true;}return false;}
    function insertVariable(){if(!variable.value)return;var token="{{"+variable.value+"}}";if(target===subject){var a=subject.selectionStart||0,b=subject.selectionEnd||a;subject.value=subject.value.slice(0,a)+token+subject.value.slice(b);subject.focus();subject.setSelectionRange(a+token.length,a+token.length);}else{editor.focus();if(!restoreRange()){var r=document.createRange();r.selectNodeContents(editor);r.collapse(false);var s=window.getSelection();s.removeAllRanges();s.addRange(r);}document.execCommand("insertText",false,token);remember();}changed();}
    function resolved(){if(scope.value!=="ALL"&&scope.value!==options.experiment)throw Error("이 템플릿은 "+scope.value+"용입니다. 적용 실험을 확인하세요.");var v=window.RistMailTemplateContext.expand(subject.value,editor.innerHTML,definitions(),clean);options.validate(v);return v;}
    function preview(){try{refreshVariables();var v=resolved();previewTitle.textContent=v.subject;previewBody.innerHTML=v.bodyHtml;previewBody.hidden=false;status(v.missing.length?"미입력 항목은 빈칸으로 반영됩니다: "+v.missing.join(", "):"현재 보고서의 공통항목과 분석결과를 반영했습니다. 확인 후 적용하세요.");return v;}catch(e){previewBody.hidden=true;previewTitle.textContent="";status(e.message);return null;}}
    function apply(){var v=preview();if(!v)return;if(!window.confirm((v.missing.length?"미입력 항목("+v.missing.join(", ")+")은 빈칸입니다. ":"")+"현재 메일 제목·본문을 이 내용으로 바꿀까요?"))return;options.apply(v);status("현재 메일에 적용했습니다. 입력 내용은 직접 수정할 수 있습니다.");}
    list.onchange=controls;[name,scope,subject].forEach(function(n){n.addEventListener("input",changed);});scope.onchange=changed;subject.onfocus=function(){target=subject;};editor.oninput=changed;editor.onmouseup=editor.onkeyup=editor.onfocus=remember;
    editor.addEventListener("paste",function(e){e.preventDefault();if(locked||busy)return;var c=e.clipboardData||window.clipboardData;if(!c)return;try{var html=c.getData("text/html"),value=html?clean(html):plain(c.getData("text/plain"));editor.focus();restoreRange();document.execCommand("insertHTML",false,value);remember();changed();}catch(error){status(error.message);}});
    toggle.onclick=function(){panel.hidden=!panel.hidden;toggle.setAttribute("aria-expanded",String(!panel.hidden));if(!panel.hidden){refreshVariables();if(!loaded)refresh();}};
    window.addEventListener("beforeunload",function(e){if(dirty&&document.documentElement.contains(panel)){e.preventDefault();e.returnValue="";}});
    controls();return {lock:function(v){locked=!!v;controls();},dirty:function(){return dirty;}};
  };
}());
