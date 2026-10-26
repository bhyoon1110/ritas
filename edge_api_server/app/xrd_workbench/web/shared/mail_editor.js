/* Shared offline mail composer: Supermium 144 native and ES5 fallback. */
(function () {
  "use strict";
  var keys = ["recipientName", "to", "cc", "bcc", "bodyHtml", "signature"];
  function node(tag, parent, text) { var e=document.createElement(tag);if(text!=null)e.textContent=text;if(parent)parent.appendChild(e);return e; }
  function plain(text) { var d=node("div");d.textContent=text;return d.innerHTML.replace(/\n/g,"<br>"); }
  function clean(source) {
    // Parse off-document. Never accept URLs, images, CSS or event attributes.
    var template=node("template"),target=node("div");template.innerHTML=String(source);
    function copy(from,to,depth) {
      if(depth>40)return;
      Array.prototype.forEach.call(from.childNodes,function(child){
        if(child.nodeType===3){to.appendChild(document.createTextNode(child.nodeValue));return;}
        if(child.nodeType!==1)return;var tag=child.tagName.toLowerCase();
        if(/^(script|style|iframe|object|svg|math|form|textarea|img|video|audio|link|meta)$/.test(tag))return;
        var next=/^(p|div|br|strong|b|em|i|u|ul|ol|li)$/.test(tag)?node(tag,to):to;
        copy(child,next,depth+1);
      });
    }
    copy(template.content,target,0);return target.innerHTML;
  }
  function styles() {
    if(document.getElementById("rist-mail-styles"))return;
    var css=node("style",document.head);css.id="rist-mail-styles";
    css.textContent=".mail-addresses{display:flex;flex-wrap:wrap;margin:0}.mail-addresses label{flex:1 1 240px;min-width:0;padding:0 6px;box-sizing:border-box}.mail-addresses input{width:100%;box-sizing:border-box}.mail-toolbar{display:flex;flex-wrap:wrap;align-items:center;margin:6px 0}.mail-toolbar button{min-height:34px;min-width:34px;margin:3px;padding:5px 9px;font-size:13px;white-space:nowrap}.mail-rich{display:block;box-sizing:border-box;min-height:260px;max-height:650px;overflow:auto;resize:vertical;padding:12px;border:1px solid #94a3b8;border-radius:5px;background:white;color:#172b4d;font:16px/1.65 Arial,sans-serif;white-space:pre-wrap;overflow-wrap:break-word;word-break:break-word}.mail-rich:focus{outline:2px solid #2563eb}.mail-rich p{margin:0 0 8px}.mail-rich[contenteditable=false]{background:#f1f5f9}.mail-signature{margin-top:12px}.mail-signature textarea{width:100%;min-height:100px;font:inherit;box-sizing:border-box}.mail-note{font-size:12px;color:#526277;margin:6px 0}.mail-compose [hidden]{display:none!important}.mail-compose input,.mail-compose textarea{max-width:100%}";
  }
  window.RistMailEditor=function(options){
    styles();var host=options.host,body=options.body,fields={},state={},locked=false,range=null,sourceMode=false;
    host.classList.add("mail-compose");
    var addresses=node("div");addresses.className="mail-addresses";host.insertBefore(addresses,options.before||body.parentNode);
    [["recipientName","수신자 이름",200],["to","수신자 이메일 주소",2000],["cc","참조 (CC)",2000],["bcc","비밀참조 (BCC)",2000]].forEach(function(info){
      var label=node("label",addresses,info[1]),input=node("input",label);input.type="text";input.maxLength=info[2];input.setAttribute("aria-label",info[1]);input.autocomplete="off";
      if(info[0]!=="recipientName"){input.placeholder="name@example.com (여러 명: 쉼표로 구분)";input.setAttribute("inputmode","email");}
      fields[info[0]]=input;input.oninput=function(){state[info[0]]=input.value;notify();};
    });
    var note=node("p");note.className="mail-note";note.textContent="비밀참조는 보고서·ZIP에 표시하지 않습니다. 수신 주소가 비어 있으면 기존 LIMS 수신자 정책을 사용합니다.";addresses.parentNode.insertBefore(note,addresses.nextSibling);
    var toolbar=node("div"),rich=node("div");toolbar.className="mail-toolbar";toolbar.setAttribute("role","toolbar");toolbar.setAttribute("aria-label","메일 본문 편집 도구");
    rich.className="mail-rich";rich.contentEditable="true";rich.setAttribute("role","textbox");rich.setAttribute("aria-label","메일 본문 서식 편집");rich.setAttribute("aria-multiline","true");
    body.parentNode.insertBefore(toolbar,body);body.parentNode.insertBefore(rich,body);body.hidden=true;
    function notify(){if(options.changed)options.changed();}
    function button(parent,text,label,handler){var b=node("button",parent,text);b.type="button";b.title=label;b.setAttribute("aria-label",label);b.onclick=handler;return b;}
    function remember(){var s=window.getSelection();if(s.rangeCount&&rich.contains(s.anchorNode))range=s.getRangeAt(0).cloneRange();}
    rich.addEventListener("mouseup",remember);rich.addEventListener("keyup",remember);rich.addEventListener("blur",remember);
    function sync(){
      if(locked)return;
      state.bodyHtml=clean(rich.innerHTML);
      body.value=(typeof rich.innerText==="string"?rich.innerText:rich.textContent).replace(/\u00a0/g," ");
      notify();
    }
    [["B","굵게","bold"],["I","기울임","italic"],["U","밑줄","underline"],["•","글머리 기호","insertUnorderedList"],["1.","번호 목록","insertOrderedList"],["Tx","서식 지우기","removeFormat"]].forEach(function(info){
      var b=button(toolbar,info[0],info[1],function(){if(locked||sourceMode)return;rich.focus();if(range){var s=window.getSelection();s.removeAllRanges();s.addRange(range);}if(document.execCommand)document.execCommand(info[2],false,null);remember();sync();});
      b.onmousedown=function(e){e.preventDefault();};
    });
    var toggle=button(toolbar,"일반 텍스트","메일 본문 일반 텍스트 전환",function(){sourceMode=!sourceMode;body.hidden=!sourceMode;rich.hidden=sourceMode;toggle.textContent=sourceMode?"서식 편집":"일반 텍스트";toggle.setAttribute("aria-pressed",String(sourceMode));if(!sourceMode){rich.innerHTML=state.bodyHtml||plain(body.value);rich.focus();}else body.focus();});
    rich.addEventListener("input",sync);
    rich.addEventListener("paste",function(e){
      e.preventDefault();if(locked)return;var data=e.clipboardData||window.clipboardData,text=data&&data.getData("text/plain");
      if(text&&document.execCommand){document.execCommand("insertText",false,text);sync();}
    });
    rich.addEventListener("drop",function(e){e.preventDefault();});
    body.addEventListener("input",function(){state.bodyHtml="";rich.innerHTML=plain(body.value);notify();});
    var signatureBox=node("div",host);signatureBox.className="mail-signature";
    var signatureLabel=node("label",signatureBox,"서명 (본문 끝에 별도 포함)"),signature=node("textarea",signatureLabel);signature.rows=4;signature.maxLength=2000;signature.setAttribute("aria-label","메일 서명");fields.signature=signature;
    signature.oninput=function(){state.signature=signature.value;notify();};
    var actions=node("div",signatureBox);actions.className="mail-toolbar";
    var feedback=node("p",signatureBox);feedback.className="mail-note";feedback.setAttribute("role","status");
    function preference(method,done){var xhr=new XMLHttpRequest();xhr.open(method,"/api/v1/report-mail/signature",true);xhr.timeout=15000;xhr.setRequestHeader("X-Requested-With","RIST-Mail-Editor");if(method==="PUT")xhr.setRequestHeader("Content-Type","application/json");
      xhr.onload=function(){var value;try{value=JSON.parse(xhr.responseText);}catch(_){value={};}if(xhr.status>=200&&xhr.status<300)done(value);else feedback.textContent=value.message||"기본 서명은 로그인한 회원만 저장·불러올 수 있습니다.";};
      xhr.onerror=xhr.ontimeout=function(){feedback.textContent="서명 연결 실패. 현재 입력한 서명은 유지됩니다.";};xhr.send(method==="PUT"?JSON.stringify({signature:signature.value}):null);
    }
    button(actions,"기본 서명 저장","내 기본 서명 저장",function(){if(locked)return;preference("PUT",function(){feedback.textContent="내 기본 서명을 저장했습니다. 4개 실험에서 같은 계정으로 불러올 수 있습니다.";});});
    button(actions,"기본 서명 불러오기","내 기본 서명 불러오기",function(){if(locked)return;if(signature.value&&!window.confirm("현재 보고서의 서명을 기본 서명으로 바꿀까요?"))return;preference("GET",function(value){if(locked)return;signature.value=state.signature=value.signature||"";notify();feedback.textContent="기본 서명을 현재 보고서에 적용했습니다.";});});
    node("p",signatureBox,"서명은 본문과 분리해 저장하여 중복 삽입을 방지합니다. 기존 보고서는 자동으로 덮어쓰지 않습니다.").className="mail-note";
    function load(value){state={};keys.forEach(function(k){state[k]=String((value||{})[k]||"");if(fields[k])fields[k].value=state[k];});state.bodyHtml=clean(state.bodyHtml);rich.innerHTML=state.bodyHtml||plain(body.value);range=null;}
    load(options.value);
    return {value:function(){var result={};keys.forEach(function(k){result[k]=fields[k]?fields[k].value:state[k]||"";});return result;},load:load,
      lock:function(value){locked=!!value;rich.contentEditable=String(!locked);Array.prototype.forEach.call(host.querySelectorAll(".mail-addresses input,.mail-toolbar button,.mail-signature textarea"),function(e){e.disabled=locked;});},
      validate:function(){if(body.value.length>4000){return "메일 본문은 4,000자 이하로 입력하세요.";}return "";}};
  };
  window.RistMailEditor.clean=clean;
}());
