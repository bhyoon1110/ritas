// DOM helpers shared by every screen and by the offline viewer bundle.
export const $ = (id) => document.getElementById(id);

export function escapeHTML(value) {
  return String(value ?? '').replace(
    /[&<>"']/g,
    (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c],
  );
}
