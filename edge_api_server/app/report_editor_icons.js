/* Local SVG icons: no CDN, fonts, or modern browser API requirement. */
(function () {
  "use strict";
  var paths = {
    edit:'<path d="m16 3 5 5-12 12-6 1 1-6Z M14 5l5 5"/>',
    preview:'<path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12Z"/><circle cx="12" cy="12" r="3"/>',
    download:'<path d="M12 3v12 m-5-5 5 5 5-5 M4 15v6h16v-6"/>',
    text:'<path d="M4 5h16 M12 5v15 M8 20h8 M4 5v3 M20 5v3"/>',
    rect:'<rect x="4" y="5" width="16" height="14" rx="1"/>',
    ellipse:'<ellipse cx="12" cy="12" rx="9" ry="7"/>',
    line:'<path d="m4 20 16-16"/><circle cx="4" cy="20" r="1"/><circle cx="20" cy="4" r="1"/>',
    image:'<rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="8" cy="8" r="1.5"/><path d="m3 17 5-5 4 4 4-6 5 7"/>',
    undo:'<path d="m8 4-5 5 5 5 M3 9h11a6 6 0 0 1 0 12"/>',
    redo:'<path d="m16 4 5 5-5 5 M21 9H10a6 6 0 0 0 0 12"/>',
    trash:'<path d="M3 6h18 M9 6V3h6v3 M6 6l1 15h10l1-15 M10 10v7 M14 10v7"/>',
    front:'<rect x="3" y="3" width="12" height="12" rx="1"/><path d="M18 9h3v12H9v-3 M7 9h4 M9 7v4"/>',
    back:'<rect x="9" y="9" width="12" height="12" rx="1"/><path d="M6 15H3V3h12v3 M13 15h4"/>',
    reset:'<path d="M3 4v6h6 M3 10a9 9 0 1 1 1 8"/>',
    add:'<path d="M13 3H4v18h16V10 M15 3v6h6 M8 15h8 M12 11v8"/>',
    plus:'<path d="M12 5v14 M5 12h14"/>',
    up:'<path d="m6 14 6-6 6 6"/>',
    down:'<path d="m6 10 6 6 6-6"/>',
    pages:'<rect x="3" y="3" width="18" height="18" rx="2"/><path d="M9 3v18 M5 7h2 M5 12h2 M5 17h2"/>'
  };
  window.RistReportIcons = function (button, icon, label) {
    button.innerHTML = '<svg viewBox="0 0 24 24" width="22" height="22" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false">' + (paths[icon] || paths.edit) + '</svg>';
    button.classList.add("report-icon-button");
    button.setAttribute("aria-label", label); button.title = label;
    return button;
  };
}());
