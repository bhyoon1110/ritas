// PEP normally observes mouse events in the document's bubbling phase. Plotly
// has already started its native mouse drag by then. Dispatch pointers first,
// and honor pointerdown.preventDefault() just as native Pointer Events do.
module.exports = function (dispatcher) {
  var source = dispatcher.eventSources.mouse;
  if (!source) return; // Native Pointer Events / Edge's MS pointer implementation.
  var suppressed = false;
  var lastMouse = null;
  source.events.forEach(function (type) {
    document.removeEventListener(type, dispatcher.boundHandler, false);
    window.addEventListener(type, function (event) {
      lastMouse = event;
      dispatcher.eventHandler(event);
      if (type === 'mousedown') suppressed = event.defaultPrevented;
      if (suppressed && /^(mousedown|mousemove|mouseup)$/.test(type)) {
        event.preventDefault();
        event.stopImmediatePropagation();
      }
      if (type === 'mouseup' && !event.buttons) suppressed = false;
    }, true);
  });
  window.addEventListener('blur', function () {
    if (lastMouse && dispatcher.pointermap.has(source.POINTER_ID)) source.cancel(lastMouse);
    suppressed = false;
    lastMouse = null;
  });
};
