// Pure helpers for the comparison-figure settings (no DOM). They edit the figure they are given in
// place (or return a copy, as documented) and never touch measurements.
export function defaultFigure() {
  return {
    include: false,
    order_mode: 'report',
    spacing: null,
    x_range: null,
    normalize: false,
    show_markers: true,
    show_reference: true,
    caption: '',
    groups: [],
  };
}
// Reports saved before figures existed have no figure block: they open with the figure off.
export function ensureFigure(doc) {
  if (!doc.figure) doc.figure = defaultFigure();
  return doc.figure;
}
function takeOut(figure, ids) {
  const moving = new Set(ids);
  figure.groups = figure.groups
    .map((g) => ({ ...g, samples: g.samples.filter((id) => !moving.has(id)) }))
    .filter((g) => g.samples.length);
}
export function addGroup(figure, title, ids) {
  const name = String(title || '').trim();
  if (!name) throw new Error('그룹 이름을 입력하세요.');
  if (!ids.length) throw new Error('묶을 시료를 선택하세요.');
  takeOut(figure, ids);
  figure.groups.push({ title: name, samples: [...ids] });
  return figure;
}
export function removeGroup(figure, index) {
  figure.groups.splice(index, 1);
  return figure;
}
export function renameGroup(figure, index, title) {
  const name = String(title || '').trim();
  if (!name) throw new Error('그룹 이름을 입력하세요.');
  figure.groups[index].title = name;
  return figure;
}
// Replace the members of one group. A sample moves out of any other group, and a group left
// without members disappears.
export function setGroupMembers(figure, index, ids) {
  const group = figure.groups[index];
  const moving = new Set(ids);
  for (const g of figure.groups)
    if (g !== group) g.samples = g.samples.filter((id) => !moving.has(id));
  group.samples = [...ids];
  figure.groups = figure.groups.filter((g) => g.samples.length);
  return figure;
}
export function moveGroup(figure, index, direction) {
  const other = index + direction;
  if (other < 0 || other >= figure.groups.length) return figure;
  [figure.groups[index], figure.groups[other]] = [figure.groups[other], figure.groups[index]];
  return figure;
}
// Copy the viewer's current view (intensity mode, offset spacing, x range, marker and peakbar
// switches) into a copy of the figure settings. Groups, caption and inclusion stay as they are.
export function figureFromViewer(figure, state) {
  // The viewer's spacing is signed (left of the slider reverses the order); the report figure
  // always draws the first sample on top, so only the size is taken. Intensity is always raw.
  const raw = state.offsets?.raw?.spacing;
  const spacing = Number.isFinite(raw) ? Math.abs(raw) : raw;
  const range = state.xRange;
  const validRange =
    Array.isArray(range) &&
    range.length === 2 &&
    range.every(Number.isFinite) &&
    range[0] < range[1];
  return {
    ...figure,
    normalize: false,
    spacing: Number.isFinite(spacing) ? spacing : null,
    x_range: validRange ? [range[0], range[1]] : null,
    show_markers: Boolean(state.markers),
    show_reference: Boolean(state.peakbar),
  };
}
// Write the similarity order into the report's sample order: included samples take the new order
// in the slots that included samples already occupy; excluded samples stay where they are.
export function applyOrder(doc, order) {
  const rank = new Map(order.map((id, i) => [id, i]));
  const included = doc.samples
    .filter((s) => rank.has(s.sample_id))
    .sort((a, b) => rank.get(a.sample_id) - rank.get(b.sample_id));
  let next = 0;
  doc.samples = doc.samples.map((s) => (rank.has(s.sample_id) ? included[next++] : s));
  ensureFigure(doc).order_mode = 'report';
  return doc;
}
