/* Private/shared RIST address books. Server filters public visibility on every load. */
(function () {
  "use strict";
  function node(tag,parent,text){var e=document.createElement(tag);if(text!=null)e.textContent=text;if(parent)parent.appendChild(e);return e;}
  function styles(){
    if(document.getElementById("rist-mail-book-styles"))return;
    var s=node("style",document.head);s.id="rist-mail-book-styles";
    s.textContent=".mail-book{margin:10px 0;max-width:100%;color:#20384b}.mail-book button{white-space:nowrap;min-height:36px;cursor:pointer}.mail-book-toggle{display:inline-flex;align-items:center;padding:6px 12px}.mail-book-toggle svg{width:20px;height:20px;margin-right:6px}.mail-book-panel{border:1px solid #cbd5e1;border-radius:6px;padding:12px;margin-top:8px;background:#f8fafc}.mail-book-panel[hidden],.mail-book-form[hidden]{display:none!important}.mail-book-row{display:flex;flex-wrap:wrap;align-items:center;margin:4px 0}.mail-book-row>*{margin:3px}.mail-book-row input{min-width:0;flex:1;width:120px}.mail-book-list{max-height:280px;overflow:auto;overscroll-behavior:contain}.mail-book-item{display:flex;align-items:center;border-bottom:1px solid #e2e8f0;padding:6px 0}.mail-book-person{display:flex;align-items:center;flex:1;min-width:0;cursor:pointer;margin:0}.mail-book-person input[type=checkbox]{width:18px;height:18px;flex:none;margin:6px}.mail-book-person span{min-width:0;overflow-wrap:break-word;word-break:break-word}.mail-book-person strong,.mail-book-person small{display:block}.mail-book-person small{color:#526277}.mail-book .mail-book-icon{display:inline-flex;align-items:center;justify-content:center;flex:none;width:36px;height:36px;min-width:36px;padding:6px;margin:3px}.mail-book-icon svg{width:22px;height:22px}.mail-book-status{min-height:20px;font-size:13px;margin:6px 0;overflow-wrap:break-word}.mail-book-status.is-error{color:#b42318}.mail-book-form{border-top:1px solid #cbd5e1;margin-top:10px;padding-top:8px}.mail-book-form label{display:block;min-width:0;flex:1 1 160px;margin:4px}.mail-book-form input{display:block;width:100%;box-sizing:border-box}.mail-book-form .mail-copy-address input{display:inline-block}.mail-book-hint{font-size:12px;color:#526277;margin:6px 0}.mail-book-page{font-size:13px}.mail-book-footer{display:flex;flex-wrap:wrap;align-items:center;justify-content:space-between}.mail-book-selection{font-size:13px}.mail-book button:disabled{opacity:.45;cursor:default}@media(max-width:600px),(pointer:coarse){.mail-book button{min-height:44px}.mail-book .mail-book-icon{width:44px;height:44px;min-width:44px}}";
    s.textContent+=".mail-book input[type=search],.mail-book-form input{min-height:36px;padding:7px 9px;border:1px solid #94a3b8;border-radius:5px;background:white;color:#20384b;font:inherit;box-sizing:border-box}.mail-book .mail-icon-danger{color:#b42318}@media(max-width:600px),(pointer:coarse){.mail-book input[type=search],.mail-book-form input{min-height:44px}}";
    s.textContent+=".mail-book-row button[aria-pressed=true]{background:#e4f1f7;border-color:#23759f;color:#174760;font-weight:bold}.mail-book-visibility{padding:8px 0}.mail-book-visibility select{display:block;width:100%;min-height:36px;margin-top:5px;padding:6px;border:1px solid #94a3b8;border-radius:5px;background:white;font:inherit}.mail-book-projects{display:flex;flex-wrap:wrap}.mail-book-projects label{display:inline-flex;align-items:center;flex:none;margin:6px 12px 6px 4px}.mail-book-projects input[type=checkbox]{display:inline-block;width:18px;height:18px;min-height:18px;margin:0 6px 0 0;padding:0}@media(max-width:600px),(pointer:coarse){.mail-book-visibility select{min-height:44px}}";
  }
  var suggestionId=0;
  // Keep suggestions local to this editor/focus session; never persist contacts in the browser.
  window.RistMailContactSuggestions=function(options){
    if(!document.getElementById("rist-mail-suggestion-styles")){
      var style=node("style",document.head);style.id="rist-mail-suggestion-styles";
      style.textContent=".mail-addresses label.mail-contact-field{position:relative}.mail-contact-popup{position:absolute;top:100%;left:6px;right:6px;z-index:30;background:white;border:1px solid #94a3b8;border-radius:6px;box-shadow:0 4px 12px #0002;color:#20384b;white-space:normal}.mail-contact-popup[hidden]{display:none!important}.mail-contact-list{max-height:240px;overflow-y:auto;overscroll-behavior:contain}.mail-contact-popup .mail-contact-option{display:block;width:100%;height:auto;min-height:44px;padding:8px 10px;border:0;border-bottom:1px solid #e2e8f0;border-radius:0;background:white;text-align:left;color:#20384b;white-space:normal;font:inherit;cursor:pointer}.mail-contact-option strong,.mail-contact-option small{display:block;overflow-wrap:break-word;word-break:break-word}.mail-contact-option small{font-size:12px;color:#526277}.mail-contact-popup .mail-contact-option:hover,.mail-contact-popup .mail-contact-option[aria-selected=true]{background:#e4f1f7}.mail-contact-status{padding:7px 10px;margin:0;font-size:12px;line-height:1.5;overflow-wrap:break-word}";
    }
    var active=null,locked=false,timer=null,sequence=0,pending=[],books=null,loading=false,choosing=false;
    function token(input){
      var value=input.value,pos=input.selectionStart==null?value.length:input.selectionStart;
      var before=value.slice(0,pos),start=Math.max(before.lastIndexOf(","),before.lastIndexOf(";"))+1;
      var tail=value.slice(pos).search(/[,;]/),end=tail<0?value.length:pos+tail;
      return {value:value,start:start,end:end,term:value.slice(start,end).trim().toLowerCase()};
    }
    function cancel(){sequence++;window.clearTimeout(timer);pending.forEach(function(xhr){xhr.abort();});pending=[];loading=false;choosing=false;}
    function hide(){
      cancel();if(active){active.popup.hidden=true;active.list.textContent="";active.input.setAttribute("aria-expanded","false");active.input.removeAttribute("aria-activedescendant");active.items=[];}
      active=null;books=null;
    }
    function message(text){if(active){active.popup.hidden=false;active.input.setAttribute("aria-expanded","true");active.status.textContent=text;}}
    function request(scope,done){
      var xhr=new XMLHttpRequest();pending.push(xhr);var version=sequence;
      xhr.open("GET","/api/v1/report-mail/"+(scope==="shared"?"shared-contacts":"contacts"),true);xhr.timeout=15000;
      function finish(items){pending=pending.filter(function(p){return p!==xhr;});if(version===sequence)done(items);}
      xhr.onerror=xhr.ontimeout=function(){finish(null);};
      xhr.onload=function(){var value;try{value=JSON.parse(xhr.responseText);}catch(_){finish(null);return;}
        finish(xhr.status>=200&&xhr.status<300&&value&&Array.isArray(value.contacts)?value.contacts:null);
      };xhr.send();
    }
    function highlight(index){
      var b=active;if(!b||!b.items.length)return;b.index=Math.max(0,Math.min(index,b.items.length-1));
      Array.prototype.forEach.call(b.list.children,function(row,i){row.setAttribute("aria-selected",String(i===b.index));});
      var row=b.list.children[b.index];b.input.setAttribute("aria-activedescendant",row.id);
      if(row.offsetTop<b.list.scrollTop)b.list.scrollTop=row.offsetTop;
      else if(row.offsetTop+row.offsetHeight>b.list.scrollTop+b.list.clientHeight)b.list.scrollTop=row.offsetTop+row.offsetHeight-b.list.clientHeight;
    }
    function render(){
      if(!active||active.composing||locked||!books||choosing)return;
      var b=active,t=token(b.input),seen={},matches=[];b.list.textContent="";b.items=[];b.index=-1;b.input.removeAttribute("aria-activedescendant");
      if(!t.term){hide();return;}b.snapshot=t;
      ["private","shared"].forEach(function(scope){(books[scope]||[]).forEach(function(c){
        if(!c||typeof c.name!=="string"||typeof c.email!=="string"||!/@rist\.re\.kr$/i.test(c.email))return;
        if((c.name+" "+(c.department||"")+" "+c.email).toLowerCase().indexOf(t.term)<0||seen["$"+c.email.toLowerCase()])return;
        seen["$"+c.email.toLowerCase()]=true;matches.push({contact:c,scope:scope});
      });});
      b.items=matches.slice(0,20);b.items.forEach(function(item,i){
        var c=item.contact,row=node("button",b.list);row.type="button";row.className="mail-contact-option";row.tabIndex=-1;row.id=b.list.id+"-"+i;
        row.setAttribute("role","option");row.setAttribute("aria-selected","false");
        node("strong",row,c.name);node("small",row,(c.department?c.department+" · ":"")+c.email+" · "+(item.scope==="private"?"내 주소록":"공용 주소록"));
        row.onmousedown=function(e){e.preventDefault();};row.onclick=function(e){e.preventDefault();e.stopPropagation();choose(i);};
      });
      var note=matches.length?matches.length+"명 · 이름과 이메일을 확인해 선택하세요. ↑↓ 이동 · Enter 선택":"검색 결과가 없습니다. 이름·부서·이메일 또는 RIST 아이디를 입력하세요.";
      if(matches.length>20)note="전체 "+matches.length+"명 중 20명 표시 · 검색어를 더 입력하세요.";
      if(!books.private||!books.shared)note+=" 일부 주소록을 불러오지 못했습니다. 입력창을 다시 선택해 재시도하세요.";
      message(note);
    }
    function search(b){
      if(locked||b.composing||document.activeElement!==b.input)return;
      if(active!==b){hide();active=b;}var t=token(b.input);
      if(!t.term){hide();return;}
      if(books){render();return;}if(loading)return;
      loading=true;message("주소록을 검색하는 중입니다…");var result={},remaining=2;
      ["private","shared"].forEach(function(scope){request(scope,function(items){result[scope]=items;if(--remaining===0){loading=false;books=result;render();}});});
    }
    function schedule(b){
      if(locked||document.activeElement!==b.input)return;
      // Hide the previous query immediately so a fast Enter/click cannot pick a stale name.
      if(choosing){hide();}if(active===b){b.list.textContent="";b.items=[];b.index=-1;b.input.removeAttribute("aria-activedescendant");b.popup.hidden=true;b.input.setAttribute("aria-expanded","false");}
      window.clearTimeout(timer);timer=window.setTimeout(function(){search(b);},150);
    }
    function choose(index){
      var b=active;if(!b||locked||choosing||b.composing||!b.items[index])return;
      var picked=b.items[index],t=token(b.input),saved=b.snapshot;
      if(t.value!==saved.value||t.start!==saved.start||t.end!==saved.end){schedule(b);return;}
      choosing=true;message("선택한 주소를 확인하는 중입니다…");
      request(picked.scope,function(items){
        choosing=false;if(!active||active!==b||locked)return;
        var current=token(b.input);if(current.value!==t.value||current.start!==t.start||current.end!==t.end){schedule(b);return;}
        var match=(items||[]).filter(function(c){return c.id===picked.contact.id&&c.email===picked.contact.email&&c.name===picked.contact.name;})[0];
        if(!match){books=null;b.items=[];b.list.textContent="";b.input.removeAttribute("aria-activedescendant");message(items?"주소 또는 공개 범위가 변경되었습니다. 검색어를 다시 입력하세요.":"주소를 확인하지 못했습니다. 잠시 후 다시 선택하세요.");return;}
        var value=t.value.slice(0,t.start)+match.email+t.value.slice(t.end),error=options.replace(b.target,value);
        if(error){message(error);return;}hide();b.input.focus();window.clearTimeout(timer);b.input.setSelectionRange(b.input.value.length,b.input.value.length);
      });
    }
    Object.keys(options.fields).forEach(function(target){
      var input=options.fields[target],label=input.parentNode.parentNode;label.classList.add("mail-contact-field");
      var popup=node("div",label);popup.className="mail-contact-popup";popup.hidden=true;
      var list=node("div",popup);list.className="mail-contact-list";list.id="rist-mail-contact-list-"+(++suggestionId);list.setAttribute("role","listbox");list.setAttribute("aria-label",input.getAttribute("aria-label")+" 주소록 검색 결과");
      var status=node("p",popup);status.className="mail-contact-status";status.setAttribute("role","status");status.setAttribute("aria-live","polite");
      var b={target:target,input:input,popup:popup,list:list,status:status,items:[],index:-1,composing:false};
      input.setAttribute("role","combobox");input.setAttribute("aria-autocomplete","list");input.setAttribute("aria-expanded","false");input.setAttribute("aria-controls",list.id);input.setAttribute("aria-haspopup","listbox");
      input.addEventListener("focus",function(){hide();schedule(b);});
      input.addEventListener("input",function(){schedule(b);});input.addEventListener("click",function(){schedule(b);});
      input.addEventListener("compositionstart",function(){b.composing=true;schedule(b);});input.addEventListener("compositionend",function(){b.composing=false;schedule(b);});
      input.addEventListener("blur",function(){window.setTimeout(function(){if(active===b&&document.activeElement!==input&&!popup.contains(document.activeElement))hide();},0);});
      input.addEventListener("keydown",function(e){
        if(b.composing||e.isComposing||e.keyCode===229)return;
        if(e.key==="Escape"&&active===b){e.preventDefault();e.stopPropagation();hide();return;}
        if(e.key==="Tab"){if(active===b)hide();return;}
        if(active!==b||b.popup.hidden||choosing)return;
        if((e.key==="ArrowDown"||e.key==="ArrowUp")&&b.items.length){e.preventDefault();e.stopPropagation();highlight(b.index<0?(e.key==="ArrowDown"?0:b.items.length-1):b.index+(e.key==="ArrowDown"?1:-1));}
        else if(e.key==="Enter"&&b.index>=0){e.preventDefault();e.stopPropagation();choose(b.index);}
        else if(e.key==="ArrowLeft"||e.key==="ArrowRight"||e.key==="Home"||e.key==="End")schedule(b);
      });
    });
    return {reset:hide,lock:function(value){locked=!!value;if(locked)hide();}};
  };
  window.RistMailAddressBook=function(options){
    styles();var root=node("div"),panel,book={revision:0,contacts:[]},loaded=false,busy=false,locked=false,selected={},page=0,editing=null,dirty=false,scope="private",canManage=true;
    root.className="mail-book";options.host.insertBefore(root,options.before);
    function button(parent,label,action,icon){
      var b=node("button",parent,label);b.type="button";b.title=label;b.setAttribute("aria-label",label);b.onclick=action;
      if(icon){window.RistMailIcons(b,icon,label);b.classList.remove("mail-icon-button");b.classList.add("mail-book-icon");}return b;
    }
    var toggle=button(root,"주소록",function(){panel.hidden=!panel.hidden;toggle.setAttribute("aria-expanded",String(!panel.hidden));if(!panel.hidden&&(!loaded||scope==="shared"))load();});
    toggle.className="mail-book-toggle";toggle.setAttribute("aria-expanded","false");
    panel=node("div",root);panel.className="mail-book-panel";panel.hidden=true;
    var tabs=node("div",panel);tabs.className="mail-book-row";
    var privateTab=button(tabs,"내 주소록",function(){switchScope("private");}),sharedTab=button(tabs,"공용 주소록",function(){switchScope("shared");});
    var hint=node("p",panel);hint.className="mail-book-hint";
    var tools=node("div",panel);tools.className="mail-book-row";
    var search=node("input",tools);search.type="search";search.placeholder="이름·부서·이메일 검색";search.setAttribute("aria-label","주소록 검색");
    search.oninput=function(){page=0;render();};
    var add=button(tools,"주소록 새 주소",function(){edit(null);},"plus");
    var reload=button(tools,"주소록 새로고침",load,"refresh");
    var status=node("p",panel);status.className="mail-book-status";status.setAttribute("role","status");status.setAttribute("aria-live","polite");
    var list=node("div",panel);list.className="mail-book-list";list.setAttribute("role","group");list.setAttribute("aria-label","주소록 목록");
    var footer=node("div",panel);footer.className="mail-book-footer";
    var pages=node("div",footer);pages.className="mail-book-row";
    var previous=button(pages,"주소록 이전 목록",function(){if(page>0){page--;render();}},"previous"),pageLabel=node("span",pages),next=button(pages,"주소록 다음 목록",function(){page++;render();},"next");pageLabel.className="mail-book-page";
    var actions=node("div",footer);actions.className="mail-book-row";
    var selection=node("span",actions);selection.className="mail-book-selection";
    var clear=button(actions,"주소록 선택 해제",function(){selected={};render();},"deselect");
    var cc=button(actions,"참조에 추가",function(){apply("cc");}),bcc=button(actions,"비밀참조에 추가",function(){apply("bcc");});
    var form=node("div",panel);form.className="mail-book-form";form.hidden=true;
    var heading=node("strong",form),fields=node("div",form);fields.className="mail-book-row";
    function field(label,max){var l=node("label",fields,label),input=node("input",l);input.type="text";input.maxLength=max;input.setAttribute("aria-label","주소록 "+label);input.oninput=function(){dirty=true;};return input;}
    var name=field("이름",100),department=field("부서",120),email=field("이메일 아이디",254);
    var emailWrap=node("span");emailWrap.className="mail-copy-address";email.parentNode.insertBefore(emailWrap,email);emailWrap.appendChild(email);node("span",emailWrap,"@rist.re.kr").className="mail-copy-domain";
    email.placeholder="RIST 이메일 아이디";email.autocomplete="off";
    var visibility=node("div",form);visibility.className="mail-book-visibility";visibility.hidden=true;
    var visibilityLabel=node("label",visibility,"공개 범위"),visibilityMode=node("select",visibilityLabel);visibilityMode.setAttribute("aria-label","주소록 공개 범위");
    [["admins","관리자만"],["all","전체 실험 회원"],["projects","선택한 실험 권한 보유자"]].forEach(function(item){var option=node("option",visibilityMode,item[1]);option.value=item[0];});
    var projectBox=node("div",visibility);projectBox.className="mail-book-projects";var projects={};
    ["FTIR","RAMAN","TEM","XRD"].forEach(function(code){var label=node("label",projectBox),check=node("input",label);check.type="checkbox";check.setAttribute("aria-label",code+" 주소록 공개");node("span",label,code);projects[code]=check;check.onchange=function(){dirty=true;};});
    visibilityMode.onchange=function(){dirty=true;visibilityControls();};
    var formActions=node("div",form);formActions.className="mail-book-row";
    button(formActions,"주소록 저장",save,"save");button(formActions,"주소록 편집 취소",function(){edit(null,true);},"close");
    node("p",panel,"최대 500명 · 주소록에서 삭제해도 현재 메일에 이미 넣은 참조·비밀참조는 유지됩니다.").className="mail-book-hint";
    function message(text,error){status.textContent=text;status.classList.toggle("is-error",!!error);}
    function selectionCount(){return book.contacts.filter(function(c){return !!selected[c.id];}).length;}
    function controls(){
      toggle.disabled=locked;
      Array.prototype.forEach.call(panel.querySelectorAll("button,input,select"),function(e){e.disabled=busy||locked;});
      previous.disabled=busy||locked||page===0;next.disabled=busy||locked||page+1>=Math.ceil(filtered().length/50);
      cc.disabled=bcc.disabled=clear.disabled=busy||locked||!selectionCount();add.disabled=busy||locked||!loaded||!canManage;add.hidden=!canManage;search.disabled=busy||locked||!loaded;
      privateTab.setAttribute("aria-pressed",String(scope==="private"));sharedTab.setAttribute("aria-pressed",String(scope==="shared"));
      hint.textContent=scope==="private"?"내 주소록 · 같은 계정으로 FTIR·Raman·TEM·XRD에서 공통 사용합니다.":canManage?"공용 주소록 · 관리자만 주소와 공개 범위를 변경할 수 있습니다.":"공용 주소록 · 공개 범위에 포함된 주소만 표시됩니다. 관리는 관리자에게 요청하세요.";
      selection.textContent=selectionCount()+"명 선택";panel.setAttribute("aria-busy",String(busy));
    }
    function filtered(){var term=search.value.trim().toLowerCase();return book.contacts.filter(function(c){return (c.name+" "+c.department+" "+c.email).toLowerCase().indexOf(term)>=0;});}
    function render(){
      var items=filtered();page=Math.max(0,Math.min(page,Math.ceil(items.length/50)-1));list.textContent="";
      if(!items.length)node("p",list,book.contacts.length?"검색 결과가 없습니다.":canManage?"등록된 주소가 없습니다. 새 주소 버튼으로 추가하세요.":"공개된 주소가 없습니다.");
      items.slice(page*50,page*50+50).forEach(function(c){
        var row=node("div",list);row.className="mail-book-item";
        var label=node("label",row);label.className="mail-book-person";
        var check=node("input",label);check.type="checkbox";check.checked=!!selected[c.id];check.setAttribute("aria-label",c.name+" · "+c.email+" 선택");
        check.onchange=function(){selected[c.id]=check.checked;controls();};
        var text=node("span",label);node("strong",text,c.name);node("small",text,(c.department?c.department+" · ":"")+c.email);
        if(canManage){
          if(scope==="shared"&&c.visibility)node("small",text,"공개: "+(c.visibility.mode==="all"?"전체 실험 회원":c.visibility.mode==="admins"?"관리자만":c.visibility.projects.join(" · ")));
          button(row,c.name+" 주소 수정",function(){edit(c);},"edit");
          button(row,c.name+" 주소 삭제",function(){if(!busy&&!locked&&window.confirm(c.name+" 주소를 "+(scope==="shared"?"공용 주소록":"내 주소록")+"에서 삭제할까요?"))mutate("DELETE",c.id,null,false);},"trash");
        }
      });
      pageLabel.textContent=(items.length?page+1:0)+" / "+Math.ceil(items.length/50)+" · 전체 "+book.contacts.length+"명";controls();
    }
    function request(method,url,value,done){
      busy=true;controls();message(method==="GET"?"주소록을 불러오는 중입니다…":"주소록을 저장하는 중입니다…");
      var xhr=new XMLHttpRequest();xhr.open(method,url,true);xhr.timeout=15000;
      if(value){xhr.setRequestHeader("Content-Type","application/json");xhr.setRequestHeader("X-Requested-With","RIST-Mail-Editor");}
      function end(error,data){busy=false;if(error&&method==="GET"&&scope==="shared"){book={revision:0,contacts:[]};selected={};canManage=false;loaded=false;form.hidden=true;render();}controls();if(error){message(error,true);return;}done(data);}
      xhr.onerror=xhr.ontimeout=function(){end("주소록 서버에 연결하지 못했습니다. 새로고침 후 확인하세요. 입력 내용은 유지됩니다.");};
      xhr.onload=function(){
        var data;try{data=JSON.parse(xhr.responseText);}catch(_){end("주소록 응답을 읽지 못했습니다. 새로고침하세요.");return;}
        if(xhr.status<200||xhr.status>=300){end(data.message||"주소록 요청에 실패했습니다. 다시 확인하세요.");return;}
        if(!data||!Array.isArray(data.contacts)||typeof data.revision!=="number"){end("주소록 응답을 확인하세요.");return;}end(null,data);
      };xhr.send(value?JSON.stringify(value):null);
    }
    function endpoint(){return "/api/v1/report-mail/"+(scope==="shared"?"shared-contacts":"contacts");}
    function accept(value){book=value;loaded=true;canManage=scope==="private"||value.canManage===true;if(!canManage)form.hidden=true;var keep={};book.contacts.forEach(function(c){if(selected[c.id])keep[c.id]=true;});selected=keep;render();}
    function load(){if(busy||locked)return;request("GET",endpoint(),null,function(value){accept(value);message("주소록을 불러왔습니다."+(dirty?" 입력 중인 내용은 유지됩니다.":""));});}
    function switchScope(value){
      if(busy||locked||scope===value)return;if(dirty&&!window.confirm("입력 중인 주소 변경을 취소하고 주소록을 전환할까요?"))return;
      scope=value;book={revision:0,contacts:[]};selected={};loaded=false;canManage=scope==="private";dirty=false;editing=null;form.hidden=true;search.value="";page=0;render();load();
    }
    function visibilityControls(){visibility.hidden=scope!=="shared";projectBox.hidden=visibilityMode.value!=="projects";}
    function edit(c,close){
      if(busy||locked||!canManage)return;if(dirty&&!window.confirm("입력 중인 주소 변경을 취소할까요?"))return;
      editing=c?c.id:null;dirty=false;name.value=c?c.name:"";department.value=c?c.department:"";email.value=c?c.email.replace(/@rist\.re\.kr$/i,""):"";
      var access=c&&c.visibility||{mode:"admins",projects:[]};visibilityMode.value=access.mode;Object.keys(projects).forEach(function(code){projects[code].checked=access.projects.indexOf(code)>=0;});visibilityControls();
      heading.textContent=c?"주소 수정":"새 주소 등록";form.hidden=!!close;controls();if(!close)name.focus();
    }
    function mutate(method,id,item,close){
      if(busy||locked||!canManage)return;
      var value={revision:book.revision};if(item)value.contact=item;
      request(method,endpoint()+(id?"/"+encodeURIComponent(id):""),value,function(data){
        accept(data);if(close||(method==="DELETE"&&editing===id)){dirty=false;form.hidden=true;editing=null;}
        message(method==="DELETE"?"주소록에서 삭제했습니다.":"주소록에 저장했습니다.");
      });
    }
    function save(){
      var address=email.value.trim();if(address.indexOf("@")<0)address+="@rist.re.kr";
      if(!name.value.trim()){message("이름을 입력하세요.",true);name.focus();return;}
      if(!/^[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@rist\.re\.kr$/i.test(address)||address.split("@")[0].length>64||/^\.|\.@|\.\./.test(address)){message("올바른 RIST 이메일 아이디를 입력하세요.",true);email.focus();return;}
      var item={name:name.value.trim(),department:department.value.trim(),email:address};
      if(scope==="shared"){
        var chosen=visibilityMode.value==="projects"?Object.keys(projects).filter(function(code){return projects[code].checked;}):[];
        if(visibilityMode.value==="projects"&&!chosen.length){message("공개할 실험을 하나 이상 선택하세요.",true);return;}
        item.visibility={mode:visibilityMode.value,projects:chosen};
      }
      mutate(editing?"PUT":"POST",editing,item,true);
    }
    function apply(target){
      if(busy||locked)return;var chosen=book.contacts.filter(function(c){return !!selected[c.id];});
      function insert(items){
        if(locked){message("보고서 처리 후 다시 선택하세요.",true);return;}
        var error=options.add(target,items.map(function(c){return c.email;}));if(error){message(error,true);return;}
        selected={};render();message((target==="cc"?"참조":"비밀참조")+"에 추가했습니다. 중복 주소는 한 번만 반영합니다.");
      }
      if(scope!=="shared"){insert(chosen);return;}
      request("GET",endpoint(),null,function(value){
        accept(value);var current={};book.contacts.forEach(function(c){current[c.id]=c;});
        if(chosen.some(function(c){return !current[c.id]||current[c.id].email!==c.email||current[c.id].name!==c.name;})){
          selected={};render();message("주소 또는 공개 범위가 변경되었습니다. 목록에서 다시 선택하세요.",true);return;
        }
        insert(chosen);
      });
    }
    render();return {lock:function(value){locked=!!value;controls();}};
  };
}());
