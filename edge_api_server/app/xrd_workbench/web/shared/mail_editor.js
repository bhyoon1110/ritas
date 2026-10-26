/* Shared offline mail composer: Supermium 144 native and ES5 fallback. */
(function () {
  "use strict";
  var keys = ["recipientName", "to", "cc", "bcc", "bodyHtml", "signature"];
  var maxImage=256*1024,maxHtml=1500*1024;
  function node(tag, parent, text) { var e=document.createElement(tag);if(text!=null)e.textContent=text;if(parent)parent.appendChild(e);return e; }
  function plain(text) { var d=node("div");d.textContent=text;return d.innerHTML.replace(/\n/g,"<br>"); }
  function formatting(tag,child,next){
    var styles={};(child.getAttribute("style")||"").split(";").forEach(function(part){var pair=part.split(":");if(pair.length===2)styles[pair[0].trim().toLowerCase()]=pair[1].trim().toLowerCase();});
    var align=styles["text-align"]||child.getAttribute("align"),vertical=styles["vertical-align"]||child.getAttribute("valign"),width=styles.width||child.getAttribute("width"),height=styles.height||child.getAttribute("height");
    if(/^(p|div|li|td|th)$/.test(tag)&&/^(left|center|right|justify)$/.test(align||"")){next.style.textAlign=align;next.setAttribute("align",align);}
    if(tag==="table"){
      width=/^\d+(\.\d{1,6})?%$/.test(width||"")&&parseFloat(width)>=20&&parseFloat(width)<=100?width:"100%";
      next.style.width=width;next.setAttribute("width",width);
    }
    if(tag==="td"||tag==="th"){
      vertical=/^(top|middle|bottom)$/.test(vertical||"")?vertical:"top";next.style.verticalAlign=vertical;next.setAttribute("valign",vertical);
      if(/^\d+(\.\d{1,6})?%$/.test(width||"")&&parseFloat(width)>=5&&parseFloat(width)<=100){next.style.width=width;next.setAttribute("width",width);}
      var color=styles["background-color"]||child.getAttribute("bgcolor")||"",rgb=/^rgb\(\s*(\d{1,3})\s*,\s*(\d{1,3})\s*,\s*(\d{1,3})\s*\)$/.exec(color);
      if(rgb&&rgb.slice(1).every(function(n){return Number(n)<=255;}))color="#"+rgb.slice(1).map(function(n){return ("0"+Number(n).toString(16)).slice(-2);}).join("");
      if(/^#[0-9a-f]{6}$/.test(color)){next.style.backgroundColor=color;next.setAttribute("bgcolor",color);}
    }
    if(tag==="tr"&&/^\d+(px)?$/.test(height||"")&&parseInt(height,10)>=24&&parseInt(height,10)<=400){next.style.height=parseInt(height,10)+"px";next.setAttribute("height",String(parseInt(height,10)));}
  }
  function clean(source) {
    // Parse off-document. Only bounded embedded raster images and fixed table styles.
    if(String(source).length>maxHtml)throw Error("메일 본문 용량 제한을 초과했습니다.");
    var template=node("template"),target=node("div");template.innerHTML=String(source);
    var images=0,total=0,tables=0;
    function copy(from,to,depth) {
      if(depth>40)return;
      Array.prototype.forEach.call(from.childNodes,function(child){
        if(child.nodeType===3){to.appendChild(document.createTextNode(child.nodeValue));return;}
        if(child.nodeType!==1)return;var tag=child.tagName.toLowerCase();
        if(/^(script|style|iframe|object|svg|math|form|textarea|video|audio|link|meta)$/.test(tag))return;
        if(tag==="img"){
          var src=child.getAttribute("src")||"",m=/^data:image\/(png|jpeg);base64,([A-Za-z0-9+/]*={0,2})$/.exec(src);
          if(!m)return;
          var size=m[2].length*3/4-(m[2].slice(-2)==="=="?2:m[2].slice(-1)==="="?1:0);
          images++;total+=size;
          if(size>maxImage||images>4||total>1024*1024)throw Error("이미지는 최대 4장, 장당 256KB·합계 1MB 이하입니다.");
          var img=node("img",to),width=Number(child.getAttribute("width"))||640;
          img.setAttribute("src",src);img.setAttribute("width",String(Math.max(1,Math.min(1280,Math.round(width)))));
          img.setAttribute("alt",(child.getAttribute("alt")||"메일 본문 이미지").slice(0,200));img.setAttribute("style","max-width:100%;height:auto");return;
        }
        var next=/^(p|div|br|strong|b|em|i|u|ul|ol|li|table|thead|tbody|tfoot|tr|th|td)$/.test(tag)?node(tag,to):to;
        if(tag==="table"){
          tables++;if(tables>3||child.querySelector("table")||child.rows.length>20)throw Error("표는 최대 3개, 각 20행×8열이며 중첩 표는 지원하지 않습니다.");
          Array.prototype.forEach.call(child.rows,function(row){if(row.cells.length>8)throw Error("표는 최대 8열입니다.");});
          next.setAttribute("border","1");next.setAttribute("cellpadding","6");next.setAttribute("cellspacing","0");next.setAttribute("style","border-collapse:collapse;width:100%;table-layout:fixed");
        }
        if(tag==="td"||tag==="th")next.setAttribute("style","border:1px solid #94a3b8;padding:6px;vertical-align:top;word-break:break-word");
        if(next!==to)formatting(tag,child,next);
        copy(child,next,depth+1);
      });
    }
    copy(template.content,target,0);return target.innerHTML;
  }
  function plainHtml(source){
    var t=node("template");t.innerHTML=source;var out=[];
    function walk(parent){Array.prototype.forEach.call(parent.childNodes,function(n){
      if(n.nodeType===3){out.push(n.nodeValue);return;}if(n.nodeType!==1)return;
      var tag=n.tagName.toLowerCase();if(tag==="img"){out.push("\n[이미지]\n");return;}
      if(/^(p|div|li|br)$/.test(tag))out.push("\n");walk(n);
      if(/^(p|div|li|tr|table)$/.test(tag))out.push("\n");if(tag==="td"||tag==="th")out.push("\t");
    });}walk(t.content);return out.join("").replace(/\u00a0/g," ").trim();
  }
  function styles() {
    if(document.getElementById("rist-mail-styles"))return;
    var css=node("style",document.head);css.id="rist-mail-styles";
    css.textContent=".mail-addresses{display:flex;flex-wrap:wrap;margin:0}.mail-addresses label{flex:1 1 240px;min-width:0;padding:0 6px;box-sizing:border-box}.mail-addresses input{width:100%;box-sizing:border-box}.mail-toolbar{display:flex;flex-wrap:wrap;align-items:center;margin:6px 0}.mail-toolbar button{min-height:34px;min-width:34px;margin:3px;padding:5px 9px;font-size:13px;white-space:nowrap}.mail-rich{display:block;box-sizing:border-box;min-height:260px;max-height:650px;overflow:auto;resize:vertical;padding:12px;border:1px solid #94a3b8;border-radius:5px;background:white;color:#172b4d;font:16px/1.65 Arial,sans-serif;white-space:pre-wrap;overflow-wrap:break-word;word-break:break-word}.mail-rich:focus{outline:2px solid #2563eb}.mail-rich p{margin:0 0 8px}.mail-rich[contenteditable=false]{background:#f1f5f9}.mail-signature{margin-top:12px}.mail-signature textarea{width:100%;min-height:100px;font:inherit;box-sizing:border-box}.mail-note{font-size:12px;color:#526277;margin:6px 0}.mail-compose [hidden]{display:none!important}.mail-compose input,.mail-compose textarea{max-width:100%}";
    css.textContent+=".mail-rich table{width:100%;min-width:0;table-layout:fixed;border-collapse:collapse;margin:8px 0}.mail-rich td,.mail-rich th{border:1px solid #94a3b8;padding:6px;vertical-align:top;word-break:break-word}.mail-rich img{max-width:100%;height:auto;cursor:pointer}.mail-media-options{display:flex;flex-wrap:wrap;align-items:center;max-width:100%}.mail-media-options label{display:inline-flex;align-items:center;margin:3px}.mail-media-options input{margin:0 4px}.mail-toolbar[hidden]{display:none}";
    css.textContent+=".mail-toolbar .mail-icon-button{display:inline-flex;align-items:center;justify-content:center;box-sizing:border-box;flex:0 0 36px;width:36px;height:36px;min-width:36px;min-height:36px;padding:6px;line-height:1;vertical-align:middle}.mail-icon-button svg{display:block;flex:none;width:22px;height:22px;pointer-events:none}.mail-toolbar .mail-icon-button:focus{outline:2px solid #23759f;outline-offset:1px}.mail-toolbar .mail-icon-danger{color:#b42318}.mail-toolbar .mail-icon-danger:hover{background:#fff1f2}.mail-toolbar .mail-icon-button:disabled{opacity:.45;cursor:default}.mail-tool-hint{height:20px;line-height:20px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;margin:0 3px 5px;font-size:12px;color:#526277}@media(pointer:coarse),(max-width:600px){.mail-toolbar .mail-icon-button{flex-basis:44px;width:44px;height:44px;min-width:44px;min-height:44px}}";
  }
  window.RistMailEditor=function(options){
    styles();var host=options.host,body=options.body,fields={},state={},locked=false,range=null,sourceMode=false,mediaBusy=false,selectedImage=null;
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
    var toolHint=node("p");toolHint.className="mail-tool-hint";toolHint.setAttribute("role","status");toolHint.textContent="아이콘에 마우스를 올리거나 선택하면 기능을 확인할 수 있습니다.";rich.parentNode.insertBefore(toolHint,rich);
    var mediaNote=node("p");mediaNote.className="mail-note";mediaNote.setAttribute("role","status");rich.parentNode.insertBefore(mediaNote,rich.nextSibling);
    mediaNote.textContent="이미지 4장 · 장당 256KB/합계 1MB · 긴 변 1,280px 자동 축소. 표 3개 · 각 20행×8열. 이미지나 표를 클릭하면 편집 도구가 나타납니다.";
    function notify(){if(options.changed)options.changed();}
    function button(parent,text,label,handler,icon){
      var b=node("button",parent,text);b.type="button";b.title=label;b.setAttribute("aria-label",label);b.onclick=handler;
      if(icon){window.RistMailIcons(b,icon,label);var explain=function(){toolHint.textContent=toolHint.title=b.getAttribute("aria-label");};b.addEventListener("mouseover",explain);b.addEventListener("focus",explain);b.addEventListener("click",explain);}
      return b;
    }
    function remember(){var s=window.getSelection();if(s.rangeCount&&rich.contains(s.anchorNode))range=s.getRangeAt(0).cloneRange();}
    rich.addEventListener("mouseup",remember);rich.addEventListener("keyup",remember);rich.addEventListener("blur",remember);
    function sync(){
      if(locked)return;
      try{state.bodyHtml=clean(rich.innerHTML);}catch(error){mediaNote.textContent=error.message;return;}
      body.value=plainHtml(state.bodyHtml);
      notify();
    }
    function insert(html){
      if(locked||sourceMode)return;rich.focus();
      var s=window.getSelection();
      if(range&&rich.contains(range.startContainer)){s.removeAllRanges();s.addRange(range);}
      else{range=document.createRange();range.selectNodeContents(rich);range.collapse(false);s.removeAllRanges();s.addRange(range);}
      document.execCommand("insertHTML",false,html);remember();sync();
    }
    [["B","굵게","bold","bold"],["I","기울임","italic","italic"],["U","밑줄","underline","underline"],["•","글머리 기호","insertUnorderedList","bullets"],["1.","번호 목록","insertOrderedList","numbered"],["Tx","서식 지우기","removeFormat","clear"]].forEach(function(info){
      var b=button(toolbar,info[0],info[1],function(){if(locked||sourceMode)return;rich.focus();if(range){var s=window.getSelection();s.removeAllRanges();s.addRange(range);}if(document.execCommand)document.execCommand(info[2],false,null);remember();sync();},info[3]);
      b.onmousedown=function(e){e.preventDefault();};
    });
    var file=node("input",host);file.type="file";file.accept="image/png,image/jpeg";file.hidden=true;file.setAttribute("aria-label","메일 이미지 파일");
    var imageButton=button(toolbar,"이미지","메일 이미지 삽입",function(){if(locked||sourceMode||mediaBusy)return;remember();file.click();},"image");
    function dimensions(buffer){
      var v=new DataView(buffer),i=2,m,n;
      if(v.byteLength>=24&&v.getUint32(0)===0x89504e47&&v.getUint32(4)===0x0d0a1a0a)return [v.getUint32(16),v.getUint32(20)];
      if(v.byteLength<4||v.getUint16(0)!==0xffd8)throw Error("PNG 또는 JPEG 파일만 넣을 수 있습니다.");
      while(i+8<v.byteLength){if(v.getUint8(i++)!==255)break;while(v.getUint8(i)===255)i++;m=v.getUint8(i++);if(m===0xda||m===0xd9)break;n=v.getUint16(i);
        if([0xc0,0xc1,0xc2,0xc3,0xc5,0xc6,0xc7,0xc9,0xca,0xcb,0xcd,0xce,0xcf].indexOf(m)>=0)return [v.getUint16(i+5),v.getUint16(i+3)];if(n<2)break;i+=n;}
      throw Error("이미지 크기를 확인하지 못했습니다. PNG/JPEG로 다시 저장하세요.");
    }
    function finishImage(message){mediaBusy=false;imageButton.disabled=locked;mediaNote.textContent=message;file.value="";}
    function uploadImage(f){
      if(!f||locked||sourceMode||mediaBusy)return;
      if(f.size>5*1024*1024){mediaNote.textContent="원본 이미지는 5MB 이하로 선택하세요.";return;}
      if(rich.querySelectorAll("img").length>=4){mediaNote.textContent="이미지는 최대 4장입니다. 기존 이미지를 삭제한 후 추가하세요.";return;}
      mediaBusy=true;imageButton.disabled=true;mediaNote.textContent="이미지 크기와 용량을 확인하는 중입니다…";
      var reader=new FileReader();reader.onerror=function(){finishImage("이미지 파일을 읽지 못했습니다.");};
      reader.onload=function(){
        try{var size=dimensions(reader.result);if(!size[0]||!size[1]||size[0]*size[1]>12000000||Math.max(size[0],size[1])>12000)throw Error("원본 이미지는 1,200만 화소 이하로 축소해 주세요.");}
        catch(error){finishImage(error.message);return;}
        var image=new Image(),objectURL=URL.createObjectURL(f);
        image.onerror=function(){URL.revokeObjectURL(objectURL);finishImage("이미지를 열지 못했습니다.");};
        image.onload=function(){
          URL.revokeObjectURL(objectURL);
          try{
            var w=image.naturalWidth,h=image.naturalHeight;if(w*h>12000000||!w||!h)throw Error("원본 이미지 크기 제한을 초과했습니다.");
            var scale=Math.min(1,1280/Math.max(w,h)),canvas=node("canvas"),encoded="",attempt;
            for(attempt=0;attempt<6;attempt++){
              canvas.width=Math.max(1,Math.round(w*scale));canvas.height=Math.max(1,Math.round(h*scale));var ctx=canvas.getContext("2d");
              ctx.fillStyle="white";ctx.fillRect(0,0,canvas.width,canvas.height);ctx.drawImage(image,0,0,canvas.width,canvas.height);
              encoded=canvas.toDataURL("image/jpeg",attempt===0 ? 0.9 : 0.8);
              if((encoded.length-23)*3/4<=maxImage)break;scale*=.8;
            }
            if((encoded.length-23)*3/4>maxImage)throw Error("이미지를 256KB 이하로 줄이지 못했습니다.");
            if(locked||sourceMode)throw Error("편집 상태가 바뀌어 이미지 삽입을 취소했습니다.");
            insert('<p><img src="'+encoded+'" width="'+Math.min(canvas.width,640)+'" alt="메일 본문 이미지" style="max-width:100%;height:auto"></p><p><br></p>');
            canvas.width=canvas.height=1;finishImage("이미지를 본문에 포함했습니다. 클릭하여 표시 폭을 변경하거나 삭제할 수 있습니다.");
          }catch(error){finishImage(error.message);}
        };image.src=objectURL;
      };reader.readAsArrayBuffer(f.slice(0,256*1024));
    }
    file.onchange=function(){uploadImage(file.files[0]);};
    var mediaTools=node("div");mediaTools.className="mail-toolbar";mediaTools.hidden=true;rich.parentNode.insertBefore(mediaTools,rich);
    var imageTools=node("div",mediaTools),widthLabel=node("label",imageTools,"이미지 폭(px) "),imageWidth=node("input",widthLabel);
    imageWidth.type="number";imageWidth.min=40;imageWidth.max=1280;imageWidth.style.width="85px";imageWidth.setAttribute("aria-label","메일 이미지 표시 폭");
    imageWidth.onchange=function(){if(locked||sourceMode||!selectedImage)return;var value=Number(imageWidth.value);if(!isFinite(value)||value<40||value>1280){mediaNote.textContent="이미지 표시 폭은 40~1,280px입니다.";return;}selectedImage.setAttribute("width",String(Math.round(value)));sync();};
    button(imageTools,"이미지 삭제","선택한 메일 이미지 삭제",function(){if(!locked&&selectedImage){selectedImage.parentNode.removeChild(selectedImage);selectedImage=null;mediaTools.hidden=true;sync();}},"trash");
    imageTools.className="mail-media-options";
    var tables=new window.RistMailTables({rich:rich,toolbar:toolbar,node:node,button:button,enabled:function(){return !locked&&!sourceMode;},
      changed:sync,remember:remember,note:function(text){mediaNote.textContent=text;},insert:function(html){insert(clean(html));},
      onShow:function(){selectedImage=null;mediaTools.hidden=true;},
      restore:function(){rich.focus();if(range&&rich.contains(range.startContainer)){var s=window.getSelection();s.removeAllRanges();s.addRange(range);}},
      outsideTable:function(){if(range){var owner=range.startContainer.nodeType===1?range.startContainer:range.startContainer.parentNode;if(owner.closest&&owner.closest("table"))range=null;}}
    });
    rich.addEventListener("click",function(e){selectedImage=e.target.tagName==="IMG"?e.target:null;mediaTools.hidden=!selectedImage;imageTools.hidden=!selectedImage;if(selectedImage)imageWidth.value=selectedImage.getAttribute("width")||640;});
    var toggle=button(toolbar,"일반 텍스트","메일 본문 일반 텍스트 전환",function(){
      if(locked||mediaBusy)return;if(!sourceMode&&rich.querySelector("img,table")&&!window.confirm("일반 텍스트에서 내용을 수정하면 이미지·표·서식이 사라집니다. 전환할까요?"))return;
      tables.reset();sourceMode=!sourceMode;mediaTools.hidden=true;body.hidden=!sourceMode;rich.hidden=sourceMode;window.RistMailIcons(toggle,sourceMode?"rich-text":"plain-text",sourceMode?"메일 본문 서식 편집 전환":"메일 본문 일반 텍스트 전환");toggle.setAttribute("aria-pressed",String(sourceMode));if(!sourceMode){rich.innerHTML=state.bodyHtml||plain(body.value);rich.focus();}else body.focus();},"plain-text");
    toggle.setAttribute("aria-pressed","false");
    rich.addEventListener("input",sync);
    rich.addEventListener("paste",function(e){
      e.preventDefault();if(locked)return;var data=e.clipboardData||window.clipboardData,text=data&&data.getData("text/plain");
      if(data&&data.items){for(var i=0;i<data.items.length;i++)if(data.items[i].kind==="file"){uploadImage(data.items[i].getAsFile());return;}}
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
    button(actions,"기본 서명 저장","내 기본 서명 저장",function(){if(locked)return;preference("PUT",function(){feedback.textContent="내 기본 서명을 저장했습니다. 4개 실험에서 같은 계정으로 불러올 수 있습니다.";});},"save");
    button(actions,"기본 서명 불러오기","내 기본 서명 불러오기",function(){if(locked)return;if(signature.value&&!window.confirm("현재 보고서의 서명을 기본 서명으로 바꿀까요?"))return;preference("GET",function(value){if(locked)return;signature.value=state.signature=value.signature||"";notify();feedback.textContent="기본 서명을 현재 보고서에 적용했습니다.";});},"folder");
    node("p",signatureBox,"서명은 본문과 분리해 저장하여 중복 삽입을 방지합니다. 기존 보고서는 자동으로 덮어쓰지 않습니다.").className="mail-note";
    function load(value){tables.reset();state={};keys.forEach(function(k){state[k]=String((value||{})[k]||"");if(fields[k])fields[k].value=state[k];});state.bodyHtml=clean(state.bodyHtml);rich.innerHTML=state.bodyHtml||plain(body.value);range=null;selectedImage=null;mediaTools.hidden=true;}
    load(options.value);
    return {value:function(){var result={};keys.forEach(function(k){result[k]=fields[k]?fields[k].value:state[k]||"";});return result;},load:load,
      lock:function(value){locked=!!value;tables.lock();rich.contentEditable=String(!locked);Array.prototype.forEach.call(host.querySelectorAll(".mail-addresses input,.mail-toolbar button,.mail-toolbar input,.mail-resize-handle,.mail-signature textarea"),function(e){e.disabled=locked;});imageButton.disabled=locked||mediaBusy;},
      validate:function(){if(mediaBusy)return "이미지 처리가 끝난 후 저장하세요.";if(body.value.length>4000)return "메일 본문은 4,000자 이하로 입력하세요.";try{clean(rich.innerHTML);}catch(error){return error.message;}return "";}};
  };
  window.RistMailEditor.clean=clean;
}());
