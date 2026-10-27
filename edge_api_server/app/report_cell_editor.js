/* Plain-text, author-only input over the actual PPT cell. ES5 for lab clients. */
(function () {
  "use strict";
  window.RistCellEditor = function (options) {
    var stage=document.getElementById("slide-stage"), box=null, input=null, entry=null, composing=false;
    function position() {
      if(!entry)return;
      var rect=entry.rect();if(!rect){close(false);return;}
      box.style.left=rect.x*100+"%";box.style.top=rect.y*100+"%";
      box.style.width=rect.w*100+"%";box.style.height=rect.h*100+"%";
      if(entry.sync)entry.sync(input);
    }
    function close(cancel) {
      if(!entry)return;
      var old=entry, value=input.value;entry=null;composing=false;
      box.parentNode.removeChild(box);box=null;input=null;
      old.end(!!cancel,value);
      if(options.editing)options.editing(false);
    }
    function outside(event) {if(entry&&!box.contains(event.target))close(false);}
    document.addEventListener("mousedown",outside,true);
    document.addEventListener("touchstart",outside,true);
    window.addEventListener("resize",position);
    window.addEventListener("blur",function(){close(false);});
    return {
      open:function(value) {
        close(false);entry=value;composing=false;
        box=document.createElement("div");box.className="ppt-cell-editor";
        input=document.createElement(value.singleLine?"input":"textarea");
        if(value.singleLine)input.type=value.inputType||"text";
        input.className="ppt-cell-input";input.value=value.text||"";
        input.maxLength=value.limit||4000;input.readOnly=!!value.readOnly;
        input.setAttribute("aria-label",value.label);
        input.setAttribute("aria-describedby","ppt-cell-help");
        input.title="Enter: 입력 완료 · Shift+Enter: 줄바꿈 · Tab: 다음 셀 · Esc: 이번 입력 취소";
        if(value.list)input.setAttribute("list",value.list);
        input.addEventListener("input",function(){if(entry&&!input.readOnly)entry.change(input.value);});
        input.addEventListener("compositionstart",function(){composing=true;});
        input.addEventListener("compositionend",function(){composing=false;if(entry&&!input.readOnly)entry.change(input.value);});
        input.addEventListener("keydown",function(event){
          event.stopPropagation();
          if(composing||event.isComposing||event.keyCode===229)return;
          if(event.keyCode===27){event.preventDefault();close(true);return;}
          if(event.keyCode===13&&!event.shiftKey){event.preventDefault();close(false);return;}
          if(event.keyCode===9){
            event.preventDefault();var next=entry.next,delta=event.shiftKey?-1:1;close(false);if(next)next(delta);
          }
        });
        input.addEventListener("blur",function(){close(false);});
        box.appendChild(input);stage.appendChild(box);position();if(!entry)return;
        if(options.editing)options.editing(true);
        input.focus();if(input.type!=="date")input.setSelectionRange(input.value.length,input.value.length);
      },
      close:close,position:position,active:function(){return !!entry;},
      lock:function(value){if(input)input.disabled=value;}
    };
  };
}());
