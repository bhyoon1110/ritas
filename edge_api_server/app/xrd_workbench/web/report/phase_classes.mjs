// Pure helpers for the author's phase classification (주요상 / 미량상 / 추정상). No DOM. The
// functions edit the given phase_classes block in place; nothing here classifies a phase by
// itself except applyTrace, which only follows the author's threshold.
export const CLASS_NAMES = { major: '주요상', trace: '미량상', presumed: '추정상' };

export function defaultPhaseClasses() {
  return { criteria_text: '', rule: null, labels: [], retained: [] };
}
export function ensurePhaseClasses(doc) {
  if (!doc.phase_classes) doc.phase_classes = defaultPhaseClasses();
  return doc.phase_classes;
}
export function samePhase(a, b) {
  return a.name === b.name && a.card === b.card && a.db === b.db;
}
export function labelOf(pc, sampleId, phase) {
  return pc.labels.find((l) => l.sample_id === sampleId && samePhase(l.phase, phase)) || null;
}
// cls '' explicitly clears a classification (미분류); other values are direct choices.
export function setLabel(pc, sampleId, phase, cls) {
  if (cls && !CLASS_NAMES[cls]) throw new Error('알 수 없는 분류입니다.');
  const existing = labelOf(pc, sampleId, phase);
  if (!cls) {
    if (existing) pc.labels.splice(pc.labels.indexOf(existing), 1);
  } else if (existing) {
    existing.class = cls;
    existing.origin = 'manual';
    delete existing.percent;
  } else {
    pc.labels.push({ sample_id: sampleId, phase: { ...phase }, class: cls, origin: 'manual' });
  }
  return pc;
}
// The same phase (name, card and database all equal) in every listed sample.
export function setRowLabel(pc, phase, sampleIds, cls) {
  for (const id of sampleIds) setLabel(pc, id, phase, cls);
  return pc;
}
export function setSampleLabel(pc, sampleId, phases, cls) {
  for (const phase of phases) setLabel(pc, sampleId, phase, cls);
  return pc;
}
function checkPercent(percent) {
  if (typeof percent !== 'number' || !Number.isFinite(percent) || percent < 0 || percent > 100)
    throw new Error('미량상 기준 비율은 0 이상 100 이하의 숫자입니다.');
  return percent;
}
// Same rule as xrd.core.report.apply_trace_verdicts (tests/cases/phase_class_cases.json checks both):
// only `trace` phases get 미량상 (origin rule), direct choices are never overwritten, a class an
// earlier run set is removed when the phase no longer qualifies.
export function applyTrace(pc, samples, percent) {
  const limit = checkPercent(percent);
  for (const sample of samples) {
    for (const item of sample.phases) {
      const status = item.status ?? item.trace?.status;
      if (!status) throw new Error('규칙 판정 결과가 없습니다.');
      const existing = labelOf(pc, sample.sample_id, item.phase);
      if (existing && existing.origin === 'manual') continue;
      if (status === 'trace') {
        if (existing) Object.assign(existing, { class: 'trace', origin: 'rule', percent: limit });
        else
          pc.labels.push({
            sample_id: sample.sample_id,
            phase: { ...item.phase },
            class: 'trace',
            origin: 'rule',
            percent: limit,
          });
      } else if (existing && existing.origin === 'rule') pc.labels.splice(pc.labels.indexOf(existing), 1);
    }
  }
  pc.rule = { trace_max_percent: limit };
  return pc;
}
// The samples the tab offers: those the server evaluated that the editor still includes in the
// report. Every lookup (view and handlers) goes through this and then by sample id.
export function visibleClassSamples(data, samples) {
  const included = new Set(samples.filter((s) => s.included).map((s) => s.sample_id));
  return (data?.samples || []).filter((s) => included.has(s.sample_id));
}
// Distinct phases over the evaluated samples, in order of first appearance.
export function distinctPhases(samples) {
  const out = [];
  for (const sample of samples)
    for (const item of sample.phases)
      if (!out.some((p) => samePhase(p, item.phase))) out.push(item.phase);
  return out;
}
