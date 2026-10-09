// Keep browser API fallbacks separate from syntax compilation. No network/CDN.
(function () {
  function read(blob, method) {
    return new Promise(function (resolve, reject) {
      var reader = new FileReader();
      reader.onload = function () { resolve(reader.result); };
      reader.onerror = function () { reject(reader.error || new Error('파일을 읽지 못했습니다.')); };
      reader.onabort = function () { reject(new Error('파일 읽기가 취소되었습니다.')); };
      reader[method](blob);
    });
  }
  if (!Blob.prototype.arrayBuffer) Blob.prototype.arrayBuffer = function () { return read(this, 'readAsArrayBuffer'); };
  if (!Blob.prototype.text) Blob.prototype.text = function () { return read(this, 'readAsText'); };
  [Element.prototype, Document.prototype, DocumentFragment.prototype].forEach(function (proto) {
    if (!proto.replaceChildren) proto.replaceChildren = function () {
      while (this.firstChild) this.removeChild(this.firstChild);
      for (var i = 0; i < arguments.length; i++) {
        var item = arguments[i];
        this.appendChild(item instanceof Node ? item : document.createTextNode(String(item)));
      }
    };
    if (!proto.append) proto.append = function () {
      for (var i = 0; i < arguments.length; i++) {
        var item = arguments[i];
        this.appendChild(item instanceof Node ? item : document.createTextNode(String(item)));
      }
    };
  });
  if (!Element.prototype.toggleAttribute) Element.prototype.toggleAttribute = function (name, force) {
    var add = arguments.length > 1 ? !!force : !this.hasAttribute(name);
    if (add) this.setAttribute(name, ''); else this.removeAttribute(name);
    return add;
  };
  if (!NodeList.prototype.forEach) NodeList.prototype.forEach = Array.prototype.forEach;
  if (!('isConnected' in Node.prototype)) Object.defineProperty(Node.prototype, 'isConnected', {
    get: function () { return !!(this.ownerDocument && this.ownerDocument.documentElement.contains(this)); }
  });
  if (!HTMLCanvasElement.prototype.toBlob) HTMLCanvasElement.prototype.toBlob = function (callback, type, quality) {
    var data = atob(this.toDataURL(type || 'image/png', quality).split(',')[1]);
    var bytes = new Uint8Array(data.length);
    for (var i = 0; i < data.length; i++) bytes[i] = data.charCodeAt(i);
    setTimeout(function () { callback(new Blob([bytes], {type:type || 'image/png'})); }, 0);
  };
  if (!Element.prototype.matches) Element.prototype.matches = Element.prototype.webkitMatchesSelector;
  if (!Element.prototype.closest) Element.prototype.closest = function (selector) {
    var el = this;
    while (el && el.nodeType === 1) { if (el.matches(selector)) return el; el = el.parentElement; }
    return null;
  };
  // Chrome 49 treats {capture:false, passive:false} as the truthy boolean
  // capture=true. Normalize options or removeEventListener will leak handlers.
  var nativeAdd = EventTarget.prototype.addEventListener;
  var nativeRemove = EventTarget.prototype.removeEventListener;
  var supportsOptions = false;
  try {
    var probe = Object.defineProperty({}, 'passive', {get: function () { supportsOptions = true; }});
    var noop = function () {};
    nativeAdd.call(window, 'rist-options-probe', noop, probe);
    nativeRemove.call(window, 'rist-options-probe', noop, probe);
  } catch (_) {}
  if (!supportsOptions) {
    var registrations = new WeakMap();
    EventTarget.prototype.addEventListener = function (type, handler, options) {
      var capture = typeof options === 'object' && options ? !!options.capture : !!options;
      var entries = registrations.get(this) || [];
      if (entries.some(function (item) { return item.type === type && item.handler === handler && item.capture === capture; })) return;
      if (options && options.once && handler) {
        var target = this;
        var wrapped = function (event) {
          target.removeEventListener(type, handler, capture);
          if (typeof handler === 'function') handler.call(target, event);
          else handler.handleEvent(event);
        };
        entries.push({type:type,handler:handler,capture:capture,wrapped:wrapped});
        registrations.set(this,entries);
        return nativeAdd.call(this,type,wrapped,capture);
      }
      if (handler) {
        entries.push({type:type,handler:handler,capture:capture,wrapped:handler});
        registrations.set(this,entries);
      }
      return nativeAdd.call(this, type, handler, capture);
    };
    EventTarget.prototype.removeEventListener = function (type, handler, options) {
      var capture = typeof options === 'object' && options ? !!options.capture : !!options;
      var entries = registrations.get(this) || [];
      for (var i = entries.length - 1; i >= 0; i--) {
        if (entries[i].type === type && entries[i].handler === handler && entries[i].capture === capture) {
          nativeRemove.call(this,type,entries[i].wrapped,capture);
          entries.splice(i,1);
        }
      }
      return nativeRemove.call(this, type, handler, capture);
    };
  }
}());
