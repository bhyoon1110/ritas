/* Offline SVGs matching the report editor's 24px outline icon style. */
(function () {
  "use strict";
  var table='<rect x="3" y="4" width="18" height="16" rx="1"/><path d="M3 9h18 M9 4v16"/>',
      rowBefore='<rect x="3" y="10" width="18" height="11" rx="1"/><path d="M3 15h18 M9 10v11 M12 2v6 M9 5h6"/>',
      rowDelete='<rect x="3" y="3" width="18" height="12" rx="1"/><path d="M3 9h18 M9 3v12 M8 20h8"/>';
  var paths={
    bold:'<path d="M7 3h6a4.5 4.5 0 0 1 0 9H7V3Z M7 12h7a4.5 4.5 0 0 1 0 9H7v-9Z"/>',
    italic:'<path d="M10 3h10 M4 21h10 M15 3 9 21"/>',
    underline:'<path d="M6 3v9a6 6 0 0 0 12 0V3 M4 22h16"/>',
    bullets:'<circle cx="4" cy="6" r="1"/><circle cx="4" cy="12" r="1"/><circle cx="4" cy="18" r="1"/><path d="M9 6h12 M9 12h12 M9 18h12"/>',
    numbered:'<path d="m2 4 2-1v6 M2 9h4 M2 15c0-3 5-3 4 0l-4 5h4 M10 6h11 M10 12h11 M10 18h11"/>',
    clear:'<path d="m4 15 9-11 7 6-9 11H8l-4-4v-2Z M8 11l7 6 M15 21h7"/>',
    image:'<rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="8" cy="8" r="1.5"/><path d="m3 17 5-5 4 4 4-6 5 7"/>',
    trash:'<path d="M3 6h18 M9 6V3h6v3 M6 6l1 15h10l1-15 M10 10v7 M14 10v7"/>',
    table:table,
    'insert-table':table+'<path d="M17 11v7 M14 14.5h6"/>',
    close:'<path d="m5 5 14 14 M19 5 5 19"/>',
    'select-range':'<rect x="3" y="3" width="18" height="18" rx="1" stroke-dasharray="3 3"/><rect x="7" y="7" width="10" height="10" rx="1"/><path d="M7 12h10 M12 7v10"/>',
    'select-all':table+'<path d="m12 14 3 3 4-5"/>',
    deselect:'<path d="M8 3H3v5 M16 3h5v5 M21 16v5h-5 M8 21H3v-5 M9 9l6 6 M15 9l-6 6"/>',
    'align-left':'<path d="M3 4h18 M3 9h12 M3 14h18 M3 19h12"/>',
    'align-center':'<path d="M3 4h18 M6 9h12 M3 14h18 M6 19h12"/>',
    'align-right':'<path d="M3 4h18 M9 9h12 M3 14h18 M9 19h12"/>',
    'align-justify':'<path d="M3 4h18 M3 9h18 M3 14h18 M3 19h18"/>',
    'align-top':'<path d="M3 3h18 M7 8h10 M7 12h10 M12 21v-6 m-3 3 3-3 3 3"/>',
    'align-middle':'<path d="M7 10h10 M7 14h10 M12 2v5 m-3-3 3 3 3-3 M12 22v-5 m-3 3 3-3 3 3"/>',
    'align-bottom':'<path d="M3 21h18 M7 12h10 M7 16h10 M12 3v6 m-3-3 3 3 3-3"/>',
    'row-before':rowBefore,
    'row-after':'<g transform="translate(0 24) scale(1 -1)">'+rowBefore+'</g>',
    'column-before':'<g transform="rotate(-90 12 12)">'+rowBefore+'</g>',
    'column-after':'<g transform="rotate(90 12 12)">'+rowBefore+'</g>',
    'delete-row':rowDelete,
    'delete-column':'<g transform="rotate(90 12 12)">'+rowDelete+'</g>',
    'equal-columns':'<rect x="3" y="5" width="18" height="14" rx="1"/><path d="M9 5v14 M15 5v14"/>',
    'auto-height':'<path d="M3 3h18 M3 21h18 M12 6v12 m-4-8 4-4 4 4 m-8 4 4 4 4-4"/>',
    'fit-width':'<path d="M3 3v18 M21 3v18 M6 12h12 m-8-4-4 4 4 4 m4-8 4 4-4 4"/>',
    'plain-text':'<path d="M4 5h16 M12 5v15 M8 20h8 M4 5v3 M20 5v3"/>',
    'rich-text':'<path d="M4 4h16 M4 9h10 M4 14h7 M4 19h5 m8-7 4 4-5 5h-4v-4Z"/>',
    save:'<path d="M4 3h13l4 4v14H3V3h1 M7 3v6h10V3 M7 21v-8h10v8"/>',
    folder:'<path d="M3 18V5h7l2 3h9v3 M3 21l3-10h16l-3 10H3Z"/>'
  };
  window.RistMailIcons=function(button,icon,label){
    if(!paths[icon])return button;
    button.innerHTML='<svg viewBox="0 0 24 24" width="22" height="22" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false">'+paths[icon]+'</svg>';
    button.classList.add("mail-icon-button");button.setAttribute("data-mail-icon",icon);
    if(icon==="trash"||icon.indexOf("delete-")===0)button.classList.add("mail-icon-danger");
    button.title=label;button.setAttribute("aria-label",label);return button;
  };
}());
