// Pure helpers for settings presets (no DOM). The same rules as xrd.core.presets
// (tests/cases/preset_cases.json is checked on both sides).
import { escapeHTML } from '../shared/dom.mjs';
import { defaultFigure, ensureFigure } from './figure.mjs';
import { ensurePhaseClasses } from './phase_classes.mjs';
import { ensureQuantityTable } from './quantity_table_view.mjs';

const FIGURE_KEYS = [
  'include',
  'order_mode',
  'spacing',
  'x_range',
  'normalize',
  'show_markers',
  'show_reference',
  'caption',
];

// The settings of a report that a preset can hold: figure options (no groups), the table switch,
// the criteria text, and a default percent for the trace rule. `percentText` is the text in the
// threshold box; when it is a number from 0 to 100 it wins over the rule applied last.
export function settingsFromDoc(doc, percentText = '') {
  const figure = doc.figure || defaultFigure();
  const classes = doc.phase_classes || { criteria_text: '', rule: null };
  const typed = String(percentText).trim() === '' ? NaN : Number(percentText);
  const percent =
    Number.isFinite(typed) && typed >= 0 && typed <= 100
      ? typed
      : (classes.rule?.trace_max_percent ?? null);
  return {
    figure: Object.fromEntries(FIGURE_KEYS.map((k) => [k, structuredClone(figure[k])])),
    quantity_table: structuredClone(doc.quantity_table || { include: true }),
    phase_classes: { criteria_text: classes.criteria_text, trace_percent: percent },
  };
}

// Apply a preset to the document in place. Groups, classes, the applied rule, cover information
// and sample text are not touched, and no class is ever filled by applying a preset.
export function applyPreset(doc, settings) {
  const figure = ensureFigure(doc);
  for (const key of FIGURE_KEYS) figure[key] = structuredClone(settings.figure[key]);
  ensureQuantityTable(doc);
  doc.quantity_table = structuredClone(settings.quantity_table);
  ensurePhaseClasses(doc).criteria_text = settings.phase_classes.criteria_text;
  return doc;
}

export function presetOptionsHTML(presets, selectedId) {
  if (!presets.length) return '<option value="">저장된 프리셋 없음</option>';
  return (
    '<option value="">프리셋 선택…</option>' +
    presets
      .map(
        (p) =>
          `<option value="${escapeHTML(p.id)}" ${p.id === selectedId ? 'selected' : ''}>${escapeHTML(p.name)}</option>`,
      )
      .join('')
  );
}
