/* ES5: also runs without the app runtime on Chrome 49 and offline document URIs. */
(function () {
  'use strict';
  var payload = null, current = null, index = 0, trigger = null, previousOverflow = '', downloadUrl = null;
  function el(id) { return document.getElementById('portable-' + id); }
  function data() {
    if (!payload) {
      var node = document.getElementById('xrd-portable-data');
      payload = JSON.parse(node.textContent);
      node.textContent = ''; // release the serialized copy after first use
    }
    return payload;
  }
  function clearBody() { el('body').innerHTML = ''; }
  function close() {
    clearBody(); el('dialog').hidden = true; document.body.style.overflow = previousOverflow;
    current = null;
    if (trigger) trigger.focus();
  }
  function open(title, source) {
    if (el('dialog').hidden) previousOverflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden'; clearBody(); current = source; index = 0;
    el('title').textContent = title; el('notice').textContent = source && source.notice || '';
    el('dialog').hidden = false; el('pager').hidden = true; el('download').hidden = !source;
    el('close').focus();
  }
  function showPage() {
    clearBody();
    var count, node;
    if (current.pages && current.pages.length) {
      count = current.pages.length; node = document.createElement('img');
      node.alt = current.name + ' · ' + (index + 1) + '페이지';
      node.src = 'data:image/jpeg;base64,' + current.pages[index];
    } else if (typeof current.text === 'string') {
      // Only one slice enters the DOM; very large raw text remains downloadable unchanged.
      if (!current.lines) { current.lines = current.text.split(/\r\n|\r|\n/); delete current.text; }
      count = Math.max(1, Math.ceil(current.lines.length / 500));
      node = document.createElement('pre'); node.textContent = current.lines.slice(index * 500, (index + 1) * 500).join('\n');
    } else if (current.lines) {
      count = Math.max(1, Math.ceil(current.lines.length / 500));
      node = document.createElement('pre'); node.textContent = current.lines.slice(index * 500, (index + 1) * 500).join('\n');
    } else {
      count = 1; node = document.createElement('p'); node.textContent = '원본 저장을 눌러 파일을 저장하세요.';
    }
    el('body').appendChild(node); el('body').scrollTop = 0;
    el('pager').hidden = count < 2; el('page').textContent = (index + 1) + ' / ' + count;
    el('prev').disabled = index <= 0; el('next').disabled = index >= count - 1;
  }
  function viewer(sample) {
    open(sample ? sample.name + ' · 그래프' : '전체 비교 그래프', null);
    var frame = document.createElement('iframe'); frame.title = el('title').textContent;
    frame.setAttribute('sandbox', 'allow-scripts allow-downloads');
    var selection = sample ? {view:'single', sample:sample.id} : {view:'multi'};
    var script = '<script>window.XRD_OFFLINE_SELECTION=' + JSON.stringify(selection).replace(/</g, '\\u003c') + ';<\/script>';
    frame.srcdoc = data().viewer.replace('</head>', script + '</head>');
    el('body').appendChild(frame);
  }
  function path(href) {
    // Inspect the literal href: resolving it against content:// would lose the file identity.
    try { return decodeURIComponent(href.split('#')[0].split('?')[0]).replace(/^\.\//, ''); }
    catch (_) { return ''; }
  }
  document.addEventListener('click', function (event) {
    var node = event.target;
    while (node && node.tagName !== 'A') node = node.parentNode;
    if (!node || el('dialog').contains(node)) return;
    var target = path(node.getAttribute('href') || ''), bundle = data(), sample = bundle.samples[target];
    if (target !== '뷰어.html' && !sample && !bundle.files[target]) return;
    event.preventDefault(); trigger = node;
    if (sample || target === '뷰어.html') viewer(sample);
    else { open(bundle.files[target].name, bundle.files[target]); showPage(); }
  });
  el('close').onclick = close;
  el('prev').onclick = function () { index--; showPage(); };
  el('next').onclick = function () { index++; showPage(); };
  el('download').onclick = function () {
    if (!current) return;
    try {
      var parts = [], size = 65536, raw = current.data;
      for (var i = 0; i < raw.length; i += size) {
        var decoded = atob(raw.slice(i, i + size)), bytes = new Uint8Array(decoded.length);
        for (var j = 0; j < decoded.length; j++) bytes[j] = decoded.charCodeAt(j);
        parts.push(bytes);
      }
      var blob = new Blob(parts, {type:current.mime}), api = window.URL || window.webkitURL;
      if (downloadUrl) api.revokeObjectURL(downloadUrl);
      downloadUrl = api.createObjectURL(blob);
      var link = document.createElement('a'); link.href = downloadUrl; link.download = current.name;
      document.body.appendChild(link); link.click(); document.body.removeChild(link);
      el('notice').textContent = '저장을 요청했습니다. 다운로드되지 않으면 다운로드를 지원하는 브라우저에서 이 HTML을 열어주세요.';
    } catch (_) { el('notice').textContent = '이 앱에서 파일 저장을 지원하지 않습니다. 브라우저에서 HTML을 열거나 ZIP의 원본 파일을 사용하세요.'; }
  };
  document.addEventListener('keydown', function (event) {
    if (el('dialog').hidden) return;
    if (event.key === 'Escape' || event.keyCode === 27) { event.preventDefault(); close(); }
    if (event.key === 'Tab' || event.keyCode === 9) {
      var buttons = el('dialog').querySelectorAll('button'), active = [];
      for (var i = 0; i < buttons.length; i++) if (!buttons[i].disabled && buttons[i].offsetHeight) active.push(buttons[i]);
      if (event.shiftKey && document.activeElement === active[0]) { event.preventDefault(); active[active.length - 1].focus(); }
      else if (!event.shiftKey && document.activeElement === active[active.length - 1]) { event.preventDefault(); active[0].focus(); }
    }
  });
})();
