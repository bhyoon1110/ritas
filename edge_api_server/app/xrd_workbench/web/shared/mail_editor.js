/* Shared offline mail composer: Supermium 144 native and ES5 fallback. */
(function () {
  "use strict";
  var keys = ["recipientName", "to", "cc", "bcc", "bodyHtml", "signature", "signatureImage", "signatureImageWidth"];
  var copyDomain = "rist.re.kr";
  function copyAddresses(value) {
    return String(value || "").split(/[,;]/).map(function(part){
      var item=part.trim();return item && item.indexOf("@")<0 ? item+"@"+copyDomain : item;
    }).filter(Boolean).join(", ");
  }
  function copyIds(value) {
    return String(value || "").split(/[,;]/).map(function(part){
      return part.trim().replace(/@rist\.re\.kr$/i, "");
    }).filter(Boolean).join(", ");
  }
  function copyError(value) {
    if(/[\r\n\x00-\x1f\x7f]/.test(value))return "참조·비밀참조 주소에 줄바꿈을 넣을 수 없습니다.";
    var items=copyAddresses(value).split(/,\s*/).filter(Boolean);
    if(items.length>50)return "참조·비밀참조는 각각 50명 이하로 입력하세요.";
    for(var i=0;i<items.length;i++){
      var parts=items[i].split("@"),local=parts[0];
      if(parts.length!==2 || parts[1].toLowerCase()!==copyDomain)return "참조·비밀참조는 @rist.re.kr 이메일 주소만 사용할 수 있습니다.";
      if(!/^[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+$/.test(local)||local.length>64||/^\.|\.$|\.\./.test(local))return "참조·비밀참조의 RIST 이메일 아이디를 확인하세요.";
    }
    return "";
  }
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
    css.textContent+=".mail-copy-address{display:flex;align-items:center;min-width:0}.mail-copy-address input{flex:1;min-width:0}.mail-copy-domain{flex:none;padding:0 6px;white-space:nowrap;font-size:13px}.mail-addresses input[readonly]{background:#f1f5f9;color:#526277}";
    css.textContent+=".mail-signature-preview{padding:12px;margin:8px 0;border:1px solid #cbd5e1;border-radius:5px;background:white;max-width:100%;overflow-wrap:break-word}.mail-signature-preview p{white-space:pre-wrap;margin:0 0 12px}.mail-signature-preview img{display:block;max-width:100%;height:auto}.mail-signature-size{display:flex;flex-wrap:wrap;align-items:center;margin:8px 0}.mail-signature-size label{display:flex;align-items:center;margin:4px}.mail-signature-size input[type=number]{width:90px;margin-left:6px;padding:6px;min-height:36px;box-sizing:border-box}.mail-signature-size input[type=range]{width:180px;max-width:100%;min-height:36px}.mail-signature-size span{font-size:12px;color:#526277;margin:4px}";
    css.textContent+=".mail-signature-image-wrap{display:inline-block;position:relative;max-width:100%;vertical-align:top}.mail-signature-resize{position:absolute;right:0;bottom:0;width:24px!important;height:24px!important;min-width:24px!important;min-height:24px!important;padding:0!important;background:#23759f!important;color:white!important;border:1px solid white!important;border-radius:3px;cursor:nwse-resize;touch-action:none}.mail-signature-preview:focus{outline:2px solid #23759f}.mail-signature-library{padding:10px;margin:8px 0;border:1px solid #cbd5e1;background:#f8fafc;border-radius:5px}.mail-signature-library label{display:block;margin:6px 0}.mail-signature-library select,.mail-signature-library input{display:block;width:100%;min-height:38px;box-sizing:border-box;padding:6px;font:inherit}.mail-signature-library .mail-toolbar{margin:0}@media(pointer:coarse),(max-width:600px){.mail-signature-resize{width:32px!important;height:32px!important}.mail-signature-library select,.mail-signature-library input{min-height:44px}}";
    css.textContent+=".mail-toolbar .mail-icon-button{display:inline-flex;align-items:center;justify-content:center;box-sizing:border-box;flex:0 0 36px;width:36px;height:36px;min-width:36px;min-height:36px;padding:6px;line-height:1;vertical-align:middle}.mail-icon-button svg{display:block;flex:none;width:22px;height:22px;pointer-events:none}.mail-toolbar .mail-icon-button:focus{outline:2px solid #23759f;outline-offset:1px}.mail-toolbar .mail-icon-danger{color:#b42318}.mail-toolbar .mail-icon-danger:hover{background:#fff1f2}.mail-toolbar .mail-icon-button:disabled{opacity:.45;cursor:default}.mail-tool-hint{height:20px;line-height:20px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;margin:0 3px 5px;font-size:12px;color:#526277}@media(pointer:coarse),(max-width:600px){.mail-toolbar .mail-icon-button{flex-basis:44px;width:44px;height:44px;min-width:44px;min-height:44px}}";
  }
  window.RistMailEditor=function(options){
    styles();var host=options.host,body=options.body,fields={},state={},locked=false,range=null,sourceMode=false,mediaBusy=false,selectedImage=null;
    var recipientRequest=null,recipientSequence=0,recipientTimer=null,recipientXhr=null,mediaTarget=false,preferenceBusy=false,loadVersion=0,signatureDrag=null;
    host.classList.add("mail-compose");
    var addresses=node("div");addresses.className="mail-addresses";host.insertBefore(addresses,options.before||body.parentNode);
    [["recipientName","수신자 이름",200],["to","수신자 이메일 주소",2000],["cc","참조 (CC)",2000],["bcc","비밀참조 (BCC)",2000]].forEach(function(info){
      var label=node("label",addresses,info[1]),copy=info[0]==="cc"||info[0]==="bcc",wrap=copy?node("span",label):label;
      if(copy)wrap.className="mail-copy-address";
      var input=node("input",wrap);input.type="text";input.maxLength=info[2];input.setAttribute("aria-label",info[1]);input.autocomplete="off";
      fields[info[0]]=input;
      if(copy){node("span",wrap,"@"+copyDomain).className="mail-copy-domain";input.placeholder="RIST 아이디 (여러 명: 쉼표로 구분)";input.oninput=function(){state[info[0]]=copyAddresses(input.value);notify();};}
      else{input.readOnly=true;input.placeholder=info[0]==="to"?"의뢰자 이메일은 LIMS에서 자동 조회":"의뢰번호 입력 후 자동 조회";}
    });
    var note=node("p");note.className="mail-note";note.textContent="수신자는 해당 의뢰의 의뢰자로 고정되며 직접 변경·추가할 수 없습니다. 참조·비밀참조는 RIST 이메일만 사용합니다. 비밀참조는 보고서·ZIP에 표시하지 않습니다.";addresses.parentNode.insertBefore(note,addresses.nextSibling);
    var recipientStatus=node("p");recipientStatus.className="mail-note";recipientStatus.setAttribute("role","status");note.parentNode.insertBefore(recipientStatus,note.nextSibling);
    var addressBook=new window.RistMailAddressBook({host:host,before:note,add:function(target,emails){
      if(locked)return "보고서 처리 후 다시 선택하세요.";
      var invalid=copyError(fields[target].value);if(invalid)return invalid;
      var merged=copyAddresses(fields[target].value).split(/,\s*/).filter(Boolean),seen={};
      merged.forEach(function(email){seen[email.toLowerCase()]=true;});
      emails.forEach(function(email){if(!seen[email.toLowerCase()]){seen[email.toLowerCase()]=true;merged.push(email);}});
      var value=merged.join(", ");invalid=copyError(value);if(invalid)return invalid;
      if(value.length>2000)return "참조·비밀참조 입력 길이를 초과했습니다. 선택 인원을 줄이세요.";
      fields[target].value=copyIds(value);state[target]=value;notify();return "";
    }});
    function refreshRecipient(){
      var number=String(typeof options.requestNumber==="function"?options.requestNumber():options.requestNumber||"").trim();
      if(number===recipientRequest)return;
      recipientRequest=number;var seq=++recipientSequence;window.clearTimeout(recipientTimer);
      if(recipientXhr){recipientXhr.abort();recipientXhr=null;}
      fields.recipientName.value=fields.to.value="";
      recipientStatus.textContent=number?"의뢰자 정보를 확인하는 중입니다…":"의뢰번호를 입력하거나 조회해 선택하세요.";
      if(!number)return;
      recipientTimer=window.setTimeout(function(){
        if(!document.documentElement.contains(host))return;
        var xhr=new XMLHttpRequest();recipientXhr=xhr;
        xhr.open("GET","/api/v1/report-mail/recipient?requestNumber="+encodeURIComponent(number),true);xhr.timeout=10000;
        function failed(message){if(seq!==recipientSequence)return;recipientXhr=null;recipientStatus.textContent=message+" 수신자는 발송 시 LIMS 의뢰자 정보로 확인합니다.";}
        xhr.onload=function(){
          if(seq!==recipientSequence)return;
          var value;try{value=JSON.parse(xhr.responseText);}catch(_){failed("의뢰자 조회 응답을 확인할 수 없습니다.");return;}
          if(xhr.status<200||xhr.status>=300){failed(value.message||"의뢰자 조회에 실패했습니다.");return;}
          recipientXhr=null;fields.recipientName.value=value.recipientName||"";fields.to.value=value.email||"";
          recipientStatus.textContent=value.email?"조회된 의뢰자 이메일로만 발송됩니다.":"의뢰자 이메일은 발송 시 LIMS에서 조회합니다. 직접 입력할 수 없습니다.";
        };
        xhr.onerror=xhr.ontimeout=function(){failed("의뢰자 조회 서버에 연결하지 못했습니다.");};xhr.send();
      },300);
    }
    var toolbar=node("div"),rich=node("div");toolbar.className="mail-toolbar";toolbar.setAttribute("role","toolbar");toolbar.setAttribute("aria-label","메일 본문 편집 도구");
    rich.className="mail-rich";rich.contentEditable="true";rich.setAttribute("role","textbox");rich.setAttribute("aria-label","메일 본문 서식 편집");rich.setAttribute("aria-multiline","true");
    body.parentNode.insertBefore(toolbar,body);body.parentNode.insertBefore(rich,body);body.hidden=true;
    var toolHint=node("p");toolHint.className="mail-tool-hint";toolHint.setAttribute("role","status");toolHint.textContent="아이콘에 마우스를 올리거나 선택하면 기능을 확인할 수 있습니다.";rich.parentNode.insertBefore(toolHint,rich);
    var mediaNote=node("p");mediaNote.className="mail-note";mediaNote.setAttribute("role","status");rich.parentNode.insertBefore(mediaNote,rich.nextSibling);
    mediaNote.textContent="본문·서명 이미지 합계 4장 · 장당 256KB/합계 1MB · 긴 변 1,280px 자동 축소. 표 3개 · 각 20행×8열. 이미지나 표를 클릭하면 편집 도구가 나타납니다.";
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
    function finishImage(message){mediaBusy=false;imageButton.disabled=locked;(mediaTarget?feedback:mediaNote).textContent=message;file.value="";signatureFile.value="";signatureControls();}
    function uploadImage(f,forSignature){
      if(!f||locked||(!forSignature&&sourceMode)||mediaBusy||preferenceBusy||signatureDrag)return;
      var target=forSignature?feedback:mediaNote,version=loadVersion;
      if(f.size>5*1024*1024){target.textContent="원본 이미지는 5MB 이하로 선택하세요.";return;}
      if(rich.querySelectorAll("img").length+(forSignature?0:state.signatureImage?1:0)>=4){target.textContent="본문·서명 이미지는 합계 4장입니다. 기존 이미지를 삭제한 후 추가하세요.";return;}
      mediaTarget=!!forSignature;mediaBusy=true;imageButton.disabled=true;signatureControls();target.textContent="이미지 크기와 용량을 확인하는 중입니다…";
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
            if(locked||version!==loadVersion||(!forSignature&&sourceMode))throw Error("편집 상태가 바뀌어 이미지 삽입을 취소했습니다.");
            if(forSignature){
              var candidate={signatureImage:encoded,signatureImageWidth:String(Math.max(40,Math.min(canvas.width,320)))};
              clean((sourceMode?plain(body.value):rich.innerHTML)+signatureMarkup(candidate));
              state.signatureImage=candidate.signatureImage;state.signatureImageWidth=candidate.signatureImageWidth;renderSignature();
            }else{
              clean(rich.innerHTML+'<img src="'+encoded+'">'+signatureMarkup(state));
              insert('<p><img src="'+encoded+'" width="'+Math.min(canvas.width,640)+'" alt="메일 본문 이미지" style="max-width:100%;height:auto"></p><p><br></p>');
            }
            canvas.width=canvas.height=1;finishImage(forSignature?"서명 이미지를 추가했습니다. 폭을 조절한 후 기본 서명으로 저장할 수 있습니다.":"이미지를 본문에 포함했습니다. 클릭하여 표시 폭을 변경하거나 삭제할 수 있습니다.");
            if(forSignature)notify();
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
    signature.oninput=function(){state.signature=signature.value;renderSignature();notify();};
    var actions=node("div",signatureBox);actions.className="mail-toolbar";
    var feedback=node("p",signatureBox);feedback.className="mail-note";feedback.setAttribute("role","status");
    var signatureFile=node("input",signatureBox);signatureFile.type="file";signatureFile.accept="image/png,image/jpeg";signatureFile.hidden=true;signatureFile.setAttribute("aria-label","서명 이미지 파일");
    signatureFile.onchange=function(){uploadImage(signatureFile.files[0],true);};
    button(actions,"이미지","서명 이미지 추가·교체",function(){if(!locked&&!mediaBusy&&!preferenceBusy)signatureFile.click();},"image");
    var removeSignatureImage=button(actions,"삭제","서명 이미지 삭제",function(){if(locked||mediaBusy||preferenceBusy)return;state.signatureImage="";renderSignature();notify();},"trash");
    var signatureSize=node("div",signatureBox);signatureSize.className="mail-signature-size";
    var signatureWidthLabel=node("label",signatureSize,"이미지 폭(px)"),signatureWidth=node("input",signatureWidthLabel);signatureWidth.type="number";signatureWidth.min=40;signatureWidth.max=1280;signatureWidth.setAttribute("aria-label","서명 이미지 표시 폭");
    var signatureSlider=node("input",signatureSize);signatureSlider.type="range";signatureSlider.min=40;signatureSlider.max=1280;signatureSlider.step=1;signatureSlider.setAttribute("aria-label","서명 이미지 크기 조절");
    node("span",signatureSize,"가로세로 비율 유지");
    function resizeSignature(input){if(locked||mediaBusy||preferenceBusy)return;var width=Number(input.value);if(!isFinite(width)||width<40||width>1280){feedback.textContent="서명 이미지 표시 폭은 40~1,280px입니다.";return;}state.signatureImageWidth=String(Math.round(width));renderSignature();notify();}
    signatureWidth.onchange=function(){resizeSignature(signatureWidth);};signatureSlider.oninput=signatureSlider.onchange=function(){resizeSignature(signatureSlider);};
    var signaturePreview=node("div",signatureBox);signaturePreview.className="mail-signature-preview";signaturePreview.setAttribute("aria-label","서명 미리보기");
    signaturePreview.tabIndex=0;
    var signatureText=node("p",signaturePreview),signatureImageWrap=node("span",signaturePreview),signaturePicture=node("img",signatureImageWrap);signatureImageWrap.className="mail-signature-image-wrap";signaturePicture.alt="메일 서명 이미지";signaturePicture.draggable=false;
    var signatureHandle=button(signatureImageWrap,"↘","서명 이미지 모서리 드래그 조절",function(){});signatureHandle.className="mail-signature-resize";
    var signatureListenerOptions=false;
    try{var passiveProbe=Object.defineProperty({},"passive",{get:function(){signatureListenerOptions={passive:false};return false;}}),passiveNoop=function(){};window.addEventListener("rist-signature-options",passiveNoop,passiveProbe);window.removeEventListener("rist-signature-options",passiveNoop,passiveProbe);}catch(_){}
    function endSignatureDrag(commit){
      if(!signatureDrag)return;var d=signatureDrag;signatureDrag=null;
      document.removeEventListener(d.move,moveSignatureDrag);document.removeEventListener(d.up,finishSignatureDrag);document.removeEventListener("pointercancel",cancelSignatureDrag);document.removeEventListener("touchcancel",cancelSignatureDrag);document.removeEventListener("keydown",signatureDragKey);window.removeEventListener("blur",cancelSignatureDrag);window.removeEventListener("resize",cancelSignatureDrag);
      if(d.pointer&&signatureHandle.releasePointerCapture){try{signatureHandle.releasePointerCapture(d.id);}catch(_){}}
      signaturePreview.style.userSelect=d.userSelect;if(!commit)state.signatureImageWidth=d.original;renderSignature();
      if(commit&&state.signatureImageWidth!==d.original)notify();
    }
    function signaturePoint(e){if(!signatureDrag)return null;if(signatureDrag.pointer&&e.pointerId!==signatureDrag.id)return null;if(signatureDrag.touch){var list=e.changedTouches||[];for(var i=0;i<list.length;i++)if(list[i].identifier===signatureDrag.id)return list[i];return null;}return e;}
    function moveSignatureDrag(e){var p=signaturePoint(e);if(!p)return;if(locked||mediaBusy||preferenceBusy||!document.documentElement.contains(signaturePicture))return endSignatureDrag(false);e.preventDefault();var d=signatureDrag,delta=((p.clientX-d.x)+(p.clientY-d.y)*d.ratio)/(1+d.ratio*d.ratio);state.signatureImageWidth=String(Math.round(Math.max(40,Math.min(d.limit,d.width+delta))));renderSignature();}
    function finishSignatureDrag(e){if(signaturePoint(e))endSignatureDrag(true);}
    function cancelSignatureDrag(e){if(e&&e.type==="pointercancel"&&signatureDrag&&signatureDrag.pointer&&e.pointerId!==signatureDrag.id)return;endSignatureDrag(false);}
    function signatureDragKey(e){if(e.key==="Escape"){e.preventDefault();e.stopPropagation();endSignatureDrag(false);}}
    function startSignatureDrag(e){
      if(locked||mediaBusy||preferenceBusy||signatureDrag||!state.signatureImage||(e.button!=null&&e.button!==0))return;
      var touch=e.type==="touchstart",p=touch?e.changedTouches[0]:e,r=signaturePicture.getBoundingClientRect(),pointer=e.type==="pointerdown";
      if(!p)return;e.preventDefault();
      signatureDrag={id:touch?p.identifier:pointer?e.pointerId:null,touch:touch,pointer:pointer,x:p.clientX,y:p.clientY,width:r.width||Number(state.signatureImageWidth),ratio:r.width?r.height/r.width:1,original:state.signatureImageWidth,limit:Math.max(40,Math.min(1280,signaturePreview.clientWidth?signaturePreview.clientWidth-24:1280)),move:touch?"touchmove":pointer?"pointermove":"mousemove",up:touch?"touchend":pointer?"pointerup":"mouseup",userSelect:signaturePreview.style.userSelect};
      signaturePreview.style.userSelect="none";if(pointer&&signatureHandle.setPointerCapture){try{signatureHandle.setPointerCapture(e.pointerId);}catch(_){}}
      document.addEventListener(signatureDrag.move,moveSignatureDrag,signatureListenerOptions);document.addEventListener(signatureDrag.up,finishSignatureDrag);document.addEventListener("pointercancel",cancelSignatureDrag);document.addEventListener("touchcancel",cancelSignatureDrag);document.addEventListener("keydown",signatureDragKey);window.addEventListener("blur",cancelSignatureDrag);window.addEventListener("resize",cancelSignatureDrag);signatureControls();
    }
    signatureHandle.addEventListener(window.PointerEvent?"pointerdown":"mousedown",startSignatureDrag);if(!window.PointerEvent)signatureHandle.addEventListener("touchstart",startSignatureDrag,signatureListenerOptions);
    signatureHandle.addEventListener("lostpointercapture",function(){endSignatureDrag(false);});
    signatureHandle.onkeydown=function(e){var delta=/^(ArrowRight|ArrowDown)$/.test(e.key)?1:/^(ArrowLeft|ArrowUp)$/.test(e.key)?-1:0;if(!delta||locked||mediaBusy||preferenceBusy||signatureDrag)return;e.preventDefault();signatureWidth.value=Math.max(40,Math.min(1280,Number(state.signatureImageWidth)+delta*(e.shiftKey?10:1)));resizeSignature(signatureWidth);};
    function pasteSignature(e){
      var data=e.clipboardData||window.clipboardData,file=null,i,html;
      if(data&&data.items)for(i=0;i<data.items.length;i++)if(data.items[i].kind==="file"&&/^image\//.test(data.items[i].type)){file=data.items[i].getAsFile();break;}
      if(!file&&data&&data.files)for(i=0;i<data.files.length;i++)if(/^image\//.test(data.files[i].type)){file=data.files[i];break;}
      if(file){e.preventDefault();uploadImage(file,true);return;}
      html=data&&data.getData&&data.getData("text/html");
      if(html&&/<img\b/i.test(html)){
        e.preventDefault();if(locked||mediaBusy||preferenceBusy||signatureDrag)return;
        try{if(html.length>maxHtml)throw Error();var t=node("template");t.innerHTML=html;var img=t.content.querySelector("img"),src=img&&img.getAttribute("src"),m=/^data:image\/(png|jpeg);base64,([A-Za-z0-9+/]*={0,2})$/.exec(src||"");if(!m)throw Error();var raw=atob(m[2]);if(raw.length>5*1024*1024)throw Error();var bytes=new Uint8Array(raw.length);for(i=0;i<raw.length;i++)bytes[i]=raw.charCodeAt(i);uploadImage(new Blob([bytes],{type:"image/"+m[1]}),true);}catch(_){feedback.textContent="이미지 자체를 복사하거나 PNG/JPEG 파일로 선택하세요. 외부 이미지 링크는 불러오지 않습니다.";}
      }
    }
    signature.addEventListener("paste",pasteSignature);signaturePreview.addEventListener("paste",pasteSignature);
    function signatureMarkup(value){var src=value.signatureImage||"",width=value.signatureImageWidth||"320";if(!src)return "";if(!/^data:image\/(png|jpeg);base64,[A-Za-z0-9+/]*={0,2}$/.test(src))throw Error("서명에는 PNG/JPEG 이미지만 등록하세요.");if(!/^\d{2,4}$/.test(width)||Number(width)<40||Number(width)>1280)throw Error("서명 이미지 표시 폭은 40~1,280px입니다.");return '<img src="'+src+'" width="'+width+'" alt="메일 서명 이미지">';}
    function renderSignature(){
      signatureText.textContent=signature.value;signatureText.hidden=!signature.value;signaturePreview.hidden=!signature.value&&!state.signatureImage;
      signatureSize.hidden=signatureImageWrap.hidden=signaturePicture.hidden=!state.signatureImage;
      if(state.signatureImage){if(signaturePicture.getAttribute("src")!==state.signatureImage)signaturePicture.src=state.signatureImage;signaturePicture.width=Number(state.signatureImageWidth)||320;}else signaturePicture.removeAttribute("src");
      signatureWidth.value=signatureSlider.value=state.signatureImageWidth||"320";signatureControls();
    }
    function signatureControls(){Array.prototype.forEach.call(signatureBox.querySelectorAll("button,input,textarea,select"),function(e){if(e!==signatureHandle)e.disabled=locked||mediaBusy||preferenceBusy||!!signatureDrag;});removeSignatureImage.disabled=locked||mediaBusy||preferenceBusy||!!signatureDrag||!state.signatureImage;signatureHandle.disabled=locked||mediaBusy||preferenceBusy||!state.signatureImage;if(library)libraryControls();if(templates)templates.lock(locked||mediaBusy||preferenceBusy||!!signatureDrag);}
    function preference(method,done){
      if(locked||mediaBusy||preferenceBusy||signatureDrag)return;
      var payload={signature:signature.value,signatureImage:state.signatureImage||"",signatureImageWidth:state.signatureImageWidth||"320"},version=loadVersion;
      try{if(payload.signature.length>2000)throw Error("서명은 2,000자 이하로 입력하세요.");clean(signatureMarkup(payload));}catch(error){feedback.textContent=error.message;return;}
      preferenceBusy=true;signatureControls();feedback.textContent=method==="PUT"?"기본 서명을 저장하는 중입니다…":"기본 서명을 불러오는 중입니다…";
      var xhr=new XMLHttpRequest();xhr.open(method,"/api/v1/report-mail/signature",true);xhr.timeout=15000;xhr.setRequestHeader("X-Requested-With","RIST-Mail-Editor");if(method==="PUT")xhr.setRequestHeader("Content-Type","application/json");
      function end(){preferenceBusy=false;signatureControls();}
      xhr.onload=function(){end();var value;try{value=JSON.parse(xhr.responseText);if(!value)throw Error();}catch(_){feedback.textContent="서명 응답을 확인할 수 없습니다. 현재 서명은 유지됩니다.";return;}if(xhr.status<200||xhr.status>=300){feedback.textContent=value.message||"기본 서명은 로그인한 회원만 저장·불러올 수 있습니다.";return;}if(typeof value.signature!=="string"){feedback.textContent="서명 응답을 확인할 수 없습니다. 현재 서명은 유지됩니다.";return;}if(method==="GET"&&(locked||version!==loadVersion)){feedback.textContent="보고서 편집 상태가 바뀌었습니다. 기본 서명을 다시 불러오세요.";return;}done(value);};
      xhr.onerror=xhr.ontimeout=function(){end();feedback.textContent="서명 연결 실패. 현재 입력한 서명은 유지됩니다.";};xhr.send(method==="PUT"?JSON.stringify(payload):null);
    }
    button(actions,"기본 서명 저장","내 기본 서명 저장",function(){if(locked)return;preference("PUT",function(){feedback.textContent="내 기본 서명을 저장했습니다. 4개 실험에서 같은 계정으로 불러올 수 있습니다.";if(!library.hidden)loadSignatures();});},"save");
    button(actions,"기본 서명 불러오기","내 기본 서명 불러오기",function(){if(locked||mediaBusy||preferenceBusy)return;if((signature.value||state.signatureImage)&&!window.confirm("현재 보고서의 서명을 기본 서명으로 바꿀까요?"))return;preference("GET",function(value){
      var candidate={signatureImage:value.signatureImage||"",signatureImageWidth:value.signatureImageWidth||"320"};
      try{clean((sourceMode?plain(body.value):rich.innerHTML)+signatureMarkup(candidate));}catch(error){feedback.textContent=error.message+" 현재 서명은 유지됩니다.";return;}
      signature.value=state.signature=value.signature;state.signatureImage=candidate.signatureImage;state.signatureImageWidth=candidate.signatureImageWidth;renderSignature();notify();feedback.textContent="기본 서명을 현재 보고서에 적용했습니다.";
    });},"folder");
    node("p",signatureBox,"서명 입력란 또는 미리보기를 클릭하고 Ctrl/Cmd+V로 이미지를 붙여넣으세요. 이미지 오른쪽 아래 ↘를 드래그하면 비율을 유지해 크기가 바뀝니다. Esc: 드래그 취소. PNG/JPEG 5MB 이하 선택 후 256KB로 자동 압축합니다.").className="mail-note";
    var libraryToggle=button(actions,"목록","서명 목록 관리",function(){if(locked||mediaBusy||preferenceBusy||signatureDrag)return;library.hidden=!library.hidden;libraryToggle.setAttribute("aria-expanded",String(!library.hidden));if(!library.hidden)loadSignatures();},"bullets");libraryToggle.setAttribute("aria-expanded","false");
    var library=node("div",signatureBox);library.className="mail-signature-library";library.hidden=true;
    var signatureBook={revision:0,items:[],defaultId:null},libraryLoaded=false,loadedSignature=null;
    var libraryLabel=node("label",library,"저장된 서명"),librarySelect=node("select",libraryLabel);librarySelect.setAttribute("aria-label","저장된 서명 선택");
    var libraryNameLabel=node("label",library,"서명 이름"),libraryName=node("input",libraryNameLabel);libraryName.maxLength=80;libraryName.setAttribute("aria-label","서명 이름");libraryName.placeholder="예: 기본 명함, 영문 서명";
    var libraryActions=node("div",library);libraryActions.className="mail-toolbar";
    node("p",library,"서명을 불러온 뒤 위에서 텍스트·이미지를 편집하고 수정 저장하세요. 새 항목 추가는 현재 서명을 새 이름으로 저장합니다. 삭제·기본값 변경은 이미 작성한 보고서를 바꾸지 않습니다. 최대 20개.").className="mail-note";
    function signatureContent(){return {signature:signature.value,signatureImage:state.signatureImage||"",signatureImageWidth:state.signatureImageWidth||"320"};}
    function libraryControls(){var off=locked||mediaBusy||preferenceBusy||!!signatureDrag,selected=!!librarySelect.value;libraryApply.disabled=libraryDelete.disabled=libraryDefault.disabled=off||!libraryLoaded||!selected;libraryUpdate.disabled=off||!libraryLoaded||!selected||loadedSignature!==librarySelect.value;libraryAdd.disabled=off||!libraryLoaded||signatureBook.items.length>=20;}
    function renderLibrary(){var keep=librarySelect.value;librarySelect.textContent="";var empty=node("option",librarySelect,"서명을 선택하세요");empty.value="";signatureBook.items.forEach(function(item){var o=node("option",librarySelect,item.name+(item.isDefault?" (기본)":"")+(item.hasImage?" · 이미지":""));o.value=item.id;});librarySelect.value=signatureBook.items.some(function(i){return i.id===keep;})?keep:"";if(!librarySelect.value)loadedSignature=null;signatureControls();}
    librarySelect.onchange=function(){loadedSignature=null;var item=signatureBook.items.filter(function(i){return i.id===librarySelect.value;})[0];libraryName.value=item?item.name:"";libraryControls();};
    function libraryRequest(method,suffix,payload,done){
      if(locked||mediaBusy||preferenceBusy||signatureDrag)return;preferenceBusy=true;signatureControls();feedback.textContent=method==="GET"?"서명을 불러오는 중입니다…":"서명을 저장하는 중입니다…";
      var xhr=new XMLHttpRequest();xhr.open(method,"/api/v1/report-mail/signatures"+suffix,true);xhr.timeout=15000;
      if(payload){xhr.setRequestHeader("Content-Type","application/json");xhr.setRequestHeader("X-Requested-With","RIST-Mail-Editor");}
      function finish(error,value){preferenceBusy=false;signatureControls();if(error){feedback.textContent=error;return;}done(value);}
      xhr.onerror=xhr.ontimeout=function(){finish("서명 서버에 연결하지 못했습니다. 현재 입력은 유지됩니다.");};
      xhr.onload=function(){var value;try{value=JSON.parse(xhr.responseText);}catch(_){finish("서명 응답을 확인할 수 없습니다.");return;}if(xhr.status<200||xhr.status>=300){finish(value.message||"서명 요청에 실패했습니다.");return;}finish(null,value);};xhr.send(payload?JSON.stringify(payload):null);
    }
    function loadSignatures(){libraryRequest("GET","",null,function(value){if(!value||!Array.isArray(value.items)||typeof value.revision!=="number"){feedback.textContent="서명 목록 응답을 확인하세요.";return;}signatureBook=value;libraryLoaded=true;renderLibrary();feedback.textContent="서명 목록을 불러왔습니다.";});}
    var libraryApply=button(libraryActions,"불러오기","선택한 서명 불러오기",function(){
      if(!librarySelect.value||((signature.value||state.signatureImage)&&!window.confirm("현재 보고서의 서명을 선택한 서명으로 바꿀까요?")))return;var id=librarySelect.value,version=loadVersion;
      libraryRequest("GET","/"+encodeURIComponent(id),null,function(value){if(locked||version!==loadVersion){feedback.textContent="보고서 편집 상태가 바뀌었습니다. 서명을 다시 불러오세요.";return;}var c=value.content;
        try{if(!c||typeof c.signature!=="string")throw Error("서명 응답을 확인하세요.");clean((sourceMode?plain(body.value):rich.innerHTML)+signatureMarkup(c));}catch(error){feedback.textContent=error.message;return;}
        signatureBook.revision=value.revision;signature.value=state.signature=c.signature;state.signatureImage=c.signatureImage||"";state.signatureImageWidth=c.signatureImageWidth||"320";loadedSignature=id;libraryName.value=value.name;renderSignature();notify();feedback.textContent="선택한 서명을 적용했습니다. 편집 후 수정 저장할 수 있습니다.";
      });
    },"folder");
    function saveNamed(update){var label=libraryName.value.trim();if(!label){feedback.textContent="서명 이름을 입력하세요.";libraryName.focus();return;}var content=signatureContent();try{if(content.signature.length>2000)throw Error("서명은 2,000자 이하로 입력하세요.");clean(signatureMarkup(content));}catch(error){feedback.textContent=error.message;return;}
      var id=update?librarySelect.value:null;if(update&&(!id||loadedSignature!==id))return;
      libraryRequest(update?"PUT":"POST",id?"/"+encodeURIComponent(id):"",{revision:signatureBook.revision,name:label,content:content},function(value){signatureBook=value;libraryLoaded=true;renderLibrary();var item=value.items.filter(function(i){return i.name===label;})[0];if(item){librarySelect.value=item.id;loadedSignature=item.id;}signatureControls();feedback.textContent=update?"선택한 서명을 수정했습니다.":"새 서명을 추가했습니다.";});
    }
    var libraryAdd=button(libraryActions,"추가","현재 서명을 새 항목으로 추가",function(){saveNamed(false);},"plus"),libraryUpdate=button(libraryActions,"수정 저장","선택한 서명 수정 저장",function(){saveNamed(true);},"save");
    var libraryDefault=button(libraryActions,"기본값","선택한 서명 기본값 지정",function(){var id=librarySelect.value;if(!id)return;libraryRequest("PUT","/"+encodeURIComponent(id)+"/default",{revision:signatureBook.revision},function(value){signatureBook=value;renderLibrary();feedback.textContent="기본 서명을 변경했습니다. 현재 보고서에는 불러오기로 적용하세요.";});},"star");
    var libraryDelete=button(libraryActions,"삭제","선택한 저장 서명 삭제",function(){var id=librarySelect.value;if(!id||!window.confirm("선택한 서명을 목록에서 삭제할까요? 현재 보고서의 서명은 유지됩니다."))return;libraryRequest("DELETE","/"+encodeURIComponent(id),{revision:signatureBook.revision},function(value){signatureBook=value;renderLibrary();feedback.textContent="서명을 목록에서 삭제했습니다.";});},"trash");
    button(libraryActions,"새로고침","서명 목록 새로고침",loadSignatures,"refresh");
    function load(value){endSignatureDrag(false);loadVersion++;tables.reset();state={};keys.forEach(function(k){state[k]=k==="to"||k==="recipientName"?"":String((value||{})[k]||(k==="signatureImageWidth"?"320":""));if(k==="cc"||k==="bcc")fields[k].value=copyIds(state[k]);else if(fields[k]&&k!=="to"&&k!=="recipientName")fields[k].value=state[k];});state.bodyHtml=clean(state.bodyHtml);clean(signatureMarkup(state));rich.innerHTML=state.bodyHtml||plain(body.value);range=null;selectedImage=null;mediaTools.hidden=true;renderSignature();refreshRecipient();}
    var templates=options.subject&&window.RistMailTemplates?new window.RistMailTemplates({host:host,before:options.subject.parentNode,
      experiment:options.experiment,context:options.context||function(){return {};},clean:clean,
      current:function(){return {subject:options.subject.value,bodyHtml:sourceMode?plain(body.value):rich.innerHTML};},
      validate:function(v){if(locked||mediaBusy||preferenceBusy||signatureDrag)throw Error("현재 처리가 끝난 뒤 다시 적용하세요.");clean(v.bodyHtml+signatureMarkup(state));},
      apply:function(v){options.subject.value=v.subject;body.value=v.body;state.bodyHtml=v.bodyHtml;rich.innerHTML=v.bodyHtml;
        sourceMode=false;body.hidden=true;rich.hidden=false;window.RistMailIcons(toggle,"plain-text","메일 본문 일반 텍스트 전환");toggle.setAttribute("aria-pressed","false");tables.reset();selectedImage=null;range=null;mediaTools.hidden=true;notify();}
    }):null;
    load(options.value);
    return {value:function(){var result={};keys.forEach(function(k){result[k]=k==="to"||k==="recipientName"?"":k==="cc"||k==="bcc"?copyAddresses(fields[k].value):fields[k]?fields[k].value:state[k]||"";});return result;},load:load,refreshRecipient:refreshRecipient,
      lock:function(value){if(value)endSignatureDrag(false);locked=!!value;addressBook.lock(locked);tables.lock();rich.contentEditable=String(!locked);Array.prototype.forEach.call(host.querySelectorAll(".mail-addresses input,.mail-toolbar button,.mail-toolbar input,.mail-resize-handle,.mail-signature textarea"),function(e){e.disabled=locked;});imageButton.disabled=locked||mediaBusy;signatureControls();},
      validate:function(){if(signatureDrag)return "이미지 크기 조절을 마친 후 저장하세요.";if(mediaBusy)return "이미지 처리가 끝난 후 저장하세요.";if(body.value.length>4000)return "메일 본문은 4,000자 이하로 입력하세요.";if(signature.value.length>2000)return "서명은 2,000자 이하로 입력하세요.";var invalid=copyError(fields.cc.value)||copyError(fields.bcc.value);if(invalid)return invalid;try{clean((sourceMode?plain(body.value):rich.innerHTML)+signatureMarkup(state));}catch(error){return error.message;}return "";}};
  };
  window.RistMailEditor.clean=clean;
  window.RistMailEditor.plainText=plainHtml;
}());
