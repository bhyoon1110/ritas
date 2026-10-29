// Rebase only unsaved author changes; source identities/evidence come from the server.
import { mergeEmailFields } from './email.mjs';
export function mergeDraftChanges(latest, current, baseline) {
  const out = structuredClone(latest),
    same = (a, b) => JSON.stringify(a) === JSON.stringify(b);
  for (const group of ['metadata', 'llm'])
    for (const key of Object.keys(current[group]))
      if (!same(current[group][key], baseline[group][key]))
        out[group][key] = structuredClone(current[group][key]);
  if (!same(current.summary, baseline.summary)) out.summary = current.summary;
  if ((current.template_version || 'legacy') !== (baseline.template_version || 'legacy'))
    out.template_version = current.template_version || 'legacy';
  if (!same(current.pptxProtection || {}, baseline.pptxProtection || {}))
    out.pptxProtection = structuredClone(current.pptxProtection || {});
  for (const key of ['pageEdits','canvasEdits'])
    if (!same(current[key] || {}, baseline[key] || {})) out[key]=structuredClone(current[key] || {});
  if (latest.email || current.email) out.email = mergeEmailFields(latest.email, current.email, baseline.email);
  if (!same(current.figure, baseline.figure)) out.figure = structuredClone(current.figure);
  if (!same(current.quantity_table, baseline.quantity_table))
    out.quantity_table = structuredClone(current.quantity_table);
  if (!same(current.phase_classes, baseline.phase_classes))
    out.phase_classes = structuredClone(current.phase_classes);
  const base = new Map(baseline.samples.map((s) => [s.sample_id, s]));
  for (const c of current.samples) {
    const b = base.get(c.sample_id),
      n = out.samples.find((s) => s.sample_id === c.sample_id);
    if (!b || !n) continue;
    for (const key of [
      'display_name',
      'included',
      'summary_included',
      'include_phase_image',
      'phase_image_source_id',
      'include_quantity_image',
      'phase_caption',
      'quantity_caption',
      'manual_note',
    ])
      if (!same(c[key], b[key])) n[key] = structuredClone(c[key]);
    if (
      c.reviewed !== b.reviewed &&
      c.analysis_fingerprint === n.analysis_fingerprint &&
      !n.analysis_review_required
    )
      n.reviewed = c.reviewed;
    for (const note of c.notes) {
      const previous = b.notes.find((v) => v.kind === note.kind),
        now = n.notes.find((v) => v.kind === note.kind);
      if (!previous) continue;
      if (now) {
        if (note.text !== previous.text) {
          now.text = note.text;
          now.edited = true;
          now.origin = 'user';
        }
        if (note.selected !== previous.selected) {
          now.selected = note.selected;
          now.selection_edited = true;
        }
      } else if (note.text !== previous.text) {
        const retained = n.retained_notes.findLast((v) => (v.candidate_id || v.id) === note.id);
        if (retained) {
          retained.text = note.text;
          retained.selected = note.selected;
        } else
          n.retained_notes.push({
            ...Object.fromEntries(
              ['id', 'title', 'text', 'selected', 'evidence'].map((k) => [
                k,
                structuredClone(note[k]),
              ]),
            ),
            id: 'draft-' + crypto.randomUUID(),
            candidate_id: note.id,
          });
      }
    }
    for (const note of c.retained_notes || []) {
      const previous = (b.retained_notes || []).find((v) => v.id === note.id),
        now = n.retained_notes.find((v) => v.id === note.id);
      if (previous && now) {
        if (note.text !== previous.text) now.text = note.text;
        if (note.selected !== previous.selected) now.selected = note.selected;
      }
    }
    if (
      !same(
        c.notes.map((v) => v.kind),
        b.notes.map((v) => v.kind),
      )
    ) {
      const order = c.notes.map((v) => v.kind);
      n.notes.sort(
        (a, b) =>
          (order.includes(a.kind) ? order.indexOf(a.kind) : 999) -
          (order.includes(b.kind) ? order.indexOf(b.kind) : 999),
      );
      n.note_order_edited = true;
    }
  }
  if (
    !same(
      current.samples.map((s) => s.sample_id),
      baseline.samples.map((s) => s.sample_id),
    )
  ) {
    const order = current.samples.map((s) => s.sample_id);
    out.samples.sort((a, b) => order.indexOf(a.sample_id) - order.indexOf(b.sample_id));
  }
  return out;
}
