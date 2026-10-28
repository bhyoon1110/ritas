/* Account-scoped RIST address book. Offline assets; no external address services. */
(function () {
  "use strict";
  function node(tag,parent,text){var e=document.createElement(tag);if(text!=null)e.textContent=text;if(parent)parent.appendChild(e);return e;}
  function styles(){
    if(document.getElementById("rist-mail-book-styles"))return;
    var s=node("style",document.head);s.id="rist-mail-book-styles";
    s.textContent=".mail-book{margin:10px 0;max-width:100%;color:#20384b}.mail-book button{white-space:nowrap;min-height:36px;cursor:pointer}.mail-book-toggle{display:inline-flex;align-items:center;padding:6px 12px}.mail-book-toggle svg{width:20px;height:20px;margin-right:6px}.mail-book-panel{border:1px solid #cbd5e1;border-radius:6px;padding:12px;margin-top:8px;background:#f8fafc}.mail-book-panel[hidden],.mail-book-form[hidden]{display:none!important}.mail-book-row{display:flex;flex-wrap:wrap;align-items:center;margin:4px 0}.mail-book-row>*{margin:3px}.mail-book-row input{min-width:0;flex:1;width:120px}.mail-book-list{max-height:280px;overflow:auto;overscroll-behavior:contain}.mail-book-item{display:flex;align-items:center;border-bottom:1px solid #e2e8f0;padding:6px 0}.mail-book-person{display:flex;align-items:center;flex:1;min-width:0;cursor:pointer;margin:0}.mail-book-person input[type=checkbox]{width:18px;height:18px;flex:none;margin:6px}.mail-book-person span{min-width:0;overflow-wrap:break-word;word-break:break-word}.mail-book-person strong,.mail-book-person small{display:block}.mail-book-person small{color:#526277}.mail-book .mail-book-icon{display:inline-flex;align-items:center;justify-content:center;flex:none;width:36px;height:36px;min-width:36px;padding:6px;margin:3px}.mail-book-icon svg{width:22px;height:22px}.mail-book-status{min-height:20px;font-size:13px;margin:6px 0;overflow-wrap:break-word}.mail-book-status.is-error{color:#b42318}.mail-book-form{border-top:1px solid #cbd5e1;margin-top:10px;padding-top:8px}.mail-book-form label{display:block;min-width:0;flex:1 1 160px;margin:4px}.mail-book-form input{display:block;width:100%;box-sizing:border-box}.mail-book-form .mail-copy-address input{display:inline-block}.mail-book-hint{font-size:12px;color:#526277;margin:6px 0}.mail-book-page{font-size:13px}.mail-book-footer{display:flex;flex-wrap:wrap;align-items:center;justify-content:space-between}.mail-book-selection{font-size:13px}.mail-book button:disabled{opacity:.45;cursor:default}@media(max-width:600px),(pointer:coarse){.mail-book button{min-height:44px}.mail-book .mail-book-icon{width:44px;height:44px;min-width:44px}}";
    s.textContent+=".mail-book input[type=search],.mail-book-form input{min-height:36px;padding:7px 9px;border:1px solid #94a3b8;border-radius:5px;background:white;color:#20384b;font:inherit;box-sizing:border-box}.mail-book .mail-icon-danger{color:#b42318}@media(max-width:600px),(pointer:coarse){.mail-book input[type=search],.mail-book-form input{min-height:44px}}";
  }
  window.RistMailAddressBook=function(options){
    styles();var root=node("div"),panel,book={revision:0,contacts:[]},loaded=false,busy=false,locked=false,selected={},page=0,editing=null,dirty=false;
    root.className="mail-book";options.host.insertBefore(root,options.before);
    function button(parent,label,action,icon){
      var b=node("button",parent,label);b.type="button";b.title=label;b.setAttribute("aria-label",label);b.onclick=action;
      if(icon){window.RistMailIcons(b,icon,label);b.classList.remove("mail-icon-button");b.classList.add("mail-book-icon");}return b;
    }
    var toggle=button(root,"주소록",function(){panel.hidden=!panel.hidden;toggle.setAttribute("aria-expanded",String(!panel.hidden));if(!panel.hidden&&!loaded)load();});
    toggle.className="mail-book-toggle";toggle.setAttribute("aria-expanded","false");
    panel=node("div",root);panel.className="mail-book-panel";panel.hidden=true;
    node("p",panel,"내 주소록 · 같은 계정으로 FTIR·Raman·TEM·XRD에서 공통 사용합니다.").className="mail-book-hint";
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
    var formActions=node("div",form);formActions.className="mail-book-row";
    button(formActions,"주소록 저장",save,"save");button(formActions,"주소록 편집 취소",function(){edit(null,true);},"close");
    node("p",panel,"최대 500명 · 주소록에서 삭제해도 현재 메일에 이미 넣은 참조·비밀참조는 유지됩니다.").className="mail-book-hint";
    function message(text,error){status.textContent=text;status.classList.toggle("is-error",!!error);}
    function selectionCount(){return book.contacts.filter(function(c){return !!selected[c.id];}).length;}
    function controls(){
      toggle.disabled=locked;
      Array.prototype.forEach.call(panel.querySelectorAll("button,input"),function(e){e.disabled=busy||locked;});
      previous.disabled=busy||locked||page===0;next.disabled=busy||locked||page+1>=Math.ceil(filtered().length/50);
      cc.disabled=bcc.disabled=clear.disabled=busy||locked||!selectionCount();add.disabled=busy||locked||!loaded;search.disabled=busy||locked||!loaded;
      selection.textContent=selectionCount()+"명 선택";panel.setAttribute("aria-busy",String(busy));
    }
    function filtered(){var term=search.value.trim().toLowerCase();return book.contacts.filter(function(c){return (c.name+" "+c.department+" "+c.email).toLowerCase().indexOf(term)>=0;});}
    function render(){
      var items=filtered();page=Math.max(0,Math.min(page,Math.ceil(items.length/50)-1));list.textContent="";
      if(!items.length)node("p",list,book.contacts.length?"검색 결과가 없습니다.":"등록된 주소가 없습니다. 새 주소 버튼으로 추가하세요.");
      items.slice(page*50,page*50+50).forEach(function(c){
        var row=node("div",list);row.className="mail-book-item";
        var label=node("label",row);label.className="mail-book-person";
        var check=node("input",label);check.type="checkbox";check.checked=!!selected[c.id];check.setAttribute("aria-label",c.name+" · "+c.email+" 선택");
        check.onchange=function(){selected[c.id]=check.checked;controls();};
        var text=node("span",label);node("strong",text,c.name);node("small",text,(c.department?c.department+" · ":"")+c.email);
        button(row,c.name+" 주소 수정",function(){edit(c);},"edit");
        button(row,c.name+" 주소 삭제",function(){if(!busy&&!locked&&window.confirm(c.name+" 주소를 내 주소록에서 삭제할까요?"))mutate("DELETE",c.id,null,false);},"trash");
      });
      pageLabel.textContent=(items.length?page+1:0)+" / "+Math.ceil(items.length/50)+" · 전체 "+book.contacts.length+"명";controls();
    }
    function request(method,url,value,done){
      busy=true;controls();message(method==="GET"?"주소록을 불러오는 중입니다…":"주소록을 저장하는 중입니다…");
      var xhr=new XMLHttpRequest();xhr.open(method,url,true);xhr.timeout=15000;
      if(value){xhr.setRequestHeader("Content-Type","application/json");xhr.setRequestHeader("X-Requested-With","RIST-Mail-Editor");}
      function end(error,data){busy=false;controls();if(error){message(error,true);return;}done(data);}
      xhr.onerror=xhr.ontimeout=function(){end("주소록 서버에 연결하지 못했습니다. 새로고침 후 확인하세요. 입력 내용은 유지됩니다.");};
      xhr.onload=function(){
        var data;try{data=JSON.parse(xhr.responseText);}catch(_){end("주소록 응답을 읽지 못했습니다. 새로고침하세요.");return;}
        if(xhr.status<200||xhr.status>=300){end(data.message||"주소록 요청에 실패했습니다. 다시 확인하세요.");return;}
        if(!data||!Array.isArray(data.contacts)||typeof data.revision!=="number"){end("주소록 응답을 확인하세요.");return;}end(null,data);
      };xhr.send(value?JSON.stringify(value):null);
    }
    function accept(value){book=value;loaded=true;var keep={};book.contacts.forEach(function(c){if(selected[c.id])keep[c.id]=true;});selected=keep;render();}
    function load(){if(busy||locked)return;request("GET","/api/v1/report-mail/contacts",null,function(value){accept(value);message("주소록을 불러왔습니다."+(dirty?" 입력 중인 내용은 유지됩니다.":""));});}
    function edit(c,close){
      if(busy||locked)return;if(dirty&&!window.confirm("입력 중인 주소 변경을 취소할까요?"))return;
      editing=c?c.id:null;dirty=false;name.value=c?c.name:"";department.value=c?c.department:"";email.value=c?c.email.replace(/@rist\.re\.kr$/i,""):"";
      heading.textContent=c?"주소 수정":"새 주소 등록";form.hidden=!!close;controls();if(!close)name.focus();
    }
    function mutate(method,id,item,close){
      if(busy||locked)return;
      var value={revision:book.revision};if(item)value.contact=item;
      request(method,"/api/v1/report-mail/contacts"+(id?"/"+encodeURIComponent(id):""),value,function(data){
        accept(data);if(close||(method==="DELETE"&&editing===id)){dirty=false;form.hidden=true;editing=null;}
        message(method==="DELETE"?"주소록에서 삭제했습니다.":"주소록에 저장했습니다.");
      });
    }
    function save(){
      var address=email.value.trim();if(address.indexOf("@")<0)address+="@rist.re.kr";
      if(!name.value.trim()){message("이름을 입력하세요.",true);name.focus();return;}
      if(!/^[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@rist\.re\.kr$/i.test(address)||address.split("@")[0].length>64||/^\.|\.@|\.\./.test(address)){message("올바른 RIST 이메일 아이디를 입력하세요.",true);email.focus();return;}
      mutate(editing?"PUT":"POST",editing,{name:name.value.trim(),department:department.value.trim(),email:address},true);
    }
    function apply(target){
      if(busy||locked)return;var emails=book.contacts.filter(function(c){return !!selected[c.id];}).map(function(c){return c.email;});
      var error=options.add(target,emails);if(error){message(error,true);return;}
      selected={};render();message((target==="cc"?"참조":"비밀참조")+"에 추가했습니다. 중복 주소는 한 번만 반영합니다.");
    }
    render();return {lock:function(value){locked=!!value;controls();}};
  };
}());
